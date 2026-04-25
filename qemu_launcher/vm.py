from __future__ import annotations

import json
import os
import shlex
import shutil
import signal
import socket
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from .capabilities import QemuCapabilities, probe_qemu
from .config import AppPaths, VMProfile
from .display import arrange_window, should_qemu_handle_fullscreen


class ConfigurationError(RuntimeError):
    pass


@dataclass(slots=True)
class RuntimeArtifacts:
    qmp_socket: Path
    pidfile: Path
    log_file: Path
    stderr_log_file: Path
    virtiofs_socket: Path | None = None


def _clean_env() -> dict[str, str]:
    env = os.environ.copy()
    for key in ["PYTHONPATH", "PYTHONHOME", "DYLD_LIBRARY_PATH", "LD_LIBRARY_PATH"]:
        env.pop(key, None)
    return env


def _default_machine(profile: VMProfile) -> str:
    if profile.machine != "auto":
        return profile.machine
    return "virt" if profile.architecture == "aarch64" else "q35"


def _accel_option(profile: VMProfile, caps: QemuCapabilities, host_platform: str) -> str:
    if host_platform == "darwin":
        if caps.supports_accel("hvf"):
            return "hvf:tcg"
        return "tcg"
    if host_platform.startswith("linux"):
        if profile.architecture == "x86_64" and caps.supports_accel("kvm"):
            return "kvm:tcg"
        return "tcg"
    return "tcg"


def _cpu_option(profile: VMProfile, caps: QemuCapabilities, host_platform: str) -> str:
    if host_platform == "darwin" and caps.supports_accel("hvf"):
        return "host"
    if host_platform.startswith("linux") and profile.architecture == "x86_64" and caps.supports_accel("kvm"):
        return "host"
    return "max"


def _display_backend(profile: VMProfile, caps: QemuCapabilities, host_platform: str) -> str:
    if profile.display_backend != "auto":
        return _display_backend_options(profile.display_backend, host_platform)
    if host_platform == "darwin" and caps.supports_display("cocoa"):
        return _display_backend_options("cocoa", host_platform)
    if host_platform.startswith("linux"):
        if caps.supports_display("gtk"):
            return _display_backend_options("gtk", host_platform)
        if caps.supports_display("sdl"):
            return _display_backend_options("sdl", host_platform)
    return "none"


def _display_backend_options(name: str, host_platform: str) -> str:
    if name == "cocoa":
        return "cocoa,show-cursor=on,zoom-to-fit=on,left-command-key=on,full-grab=on"
    if name == "gtk":
        return "gtk,gl=on,show-tabs=off,show-menubar=off,zoom-to-fit=on"
    if name == "sdl":
        return "sdl,gl=on,show-cursor=on"
    if name == "none":
        return "none"
    return name


def _graphics_device(profile: VMProfile, backend: str) -> list[str]:
    if profile.architecture == "x86_64":
        if backend.startswith("gtk") or backend.startswith("sdl"):
            return ["-device", "virtio-vga-gl"]
        return ["-device", "virtio-vga"]
    return ["-device", "virtio-gpu-pci"]


def _audio_backend(profile: VMProfile, caps: QemuCapabilities, host_platform: str) -> str:
    if not profile.enable_audio:
        return "none"
    if host_platform == "darwin" and caps.supports_audio("coreaudio"):
        return "coreaudio"
    for candidate in ["pipewire", "pa", "alsa", "sdl"]:
        if caps.supports_audio(candidate):
            return candidate
    return "none"


def _audio_args(profile: VMProfile, caps: QemuCapabilities, host_platform: str) -> list[str]:
    backend = _audio_backend(profile, caps, host_platform)
    if backend == "none":
        return []
    audio = [f"{backend},id=snd0,out.frequency=48000,out.channels=2,out.format=s16"]
    if profile.enable_microphone:
        audio.append("in.frequency=48000,in.channels=1,in.format=s16")
    return ["-audiodev", ",".join(audio), "-device", "virtio-sound-pci,audiodev=snd0"]


def _network_mode(profile: VMProfile, caps: QemuCapabilities, host_platform: str) -> str:
    if profile.network_mode != "auto":
        return profile.network_mode
    if host_platform.startswith("linux") and caps.has_passt:
        return "passt"
    return "user"


def _network_args(profile: VMProfile, caps: QemuCapabilities, host_platform: str) -> list[str]:
    mode = _network_mode(profile, caps, host_platform)
    if mode == "passt":
        return ["-netdev", "passt,id=net0", "-device", "virtio-net-pci,netdev=net0"]
    if mode == "bridge":
        if not profile.bridge_name:
            raise ConfigurationError("Bridge networking requires a bridge name.")
        return ["-netdev", f"bridge,id=net0,br={profile.bridge_name}", "-device", "virtio-net-pci,netdev=net0"]
    if mode == "vmnet-shared":
        return ["-netdev", "vmnet-shared,id=net0", "-device", "virtio-net-pci,netdev=net0"]
    if mode == "vmnet-bridged":
        if not profile.bridge_name:
            raise ConfigurationError("vmnet bridged networking requires an interface name.")
        return [
            "-netdev",
            f"vmnet-bridged,id=net0,ifname={profile.bridge_name}",
            "-device",
            "virtio-net-pci,netdev=net0",
        ]
    return ["-netdev", "user,id=net0", "-device", "virtio-net-pci,netdev=net0"]


def _network_requires_elevation(profile: VMProfile, caps: QemuCapabilities, host_platform: str) -> bool:
    mode = _network_mode(profile, caps, host_platform)
    return host_platform == "darwin" and mode in {"vmnet-shared", "vmnet-bridged"}


def _sharing_backend(profile: VMProfile, caps: QemuCapabilities) -> str:
    if not profile.shared_dir_path:
        return "none"
    if profile.sharing_backend != "auto":
        return profile.sharing_backend
    return "virtiofs" if caps.has_virtiofsd else "9p"


def _sharing_args(profile: VMProfile, caps: QemuCapabilities, artifacts: RuntimeArtifacts) -> list[str]:
    mode = _sharing_backend(profile, caps)
    shared_path = profile.expanded_shared_dir_path()
    if mode == "none":
        return []
    if not shared_path:
        raise ConfigurationError("A shared folder path is required when sharing is enabled.")
    if not Path(shared_path).is_dir():
        raise ConfigurationError(f"Shared folder does not exist: {shared_path}")
    if mode == "virtiofs":
        if not artifacts.virtiofs_socket:
            raise ConfigurationError("virtiofs backend selected but no virtiofs socket is available.")
        return [
            "-chardev",
            f"socket,id=charfs0,path={artifacts.virtiofs_socket}",
            "-device",
            "vhost-user-fs-pci,chardev=charfs0,tag=" + profile.mount_tag,
        ]
    return [
        "-fsdev",
        f"local,id=fsdev0,path={shared_path},security_model=mapped-xattr",
        "-device",
        f"virtio-9p-pci,fsdev=fsdev0,mount_tag={profile.mount_tag}",
    ]


def resolve_sharing(profile: VMProfile, caps: QemuCapabilities) -> tuple[str, str]:
    mode = _sharing_backend(profile, caps)
    if mode == "none":
        return mode, "No shared folder configured."
    mount_dir = f"/mnt/{profile.mount_tag}"
    if mode == "virtiofs":
        return (
            mode,
            f"sudo mkdir -p {mount_dir} && sudo mount -t virtiofs {profile.mount_tag} {mount_dir}",
        )
    return (
        mode,
        f"sudo mkdir -p {mount_dir} && sudo mount -t 9p -o trans=virtio,version=9p2000.L "
        f"{profile.mount_tag} {mount_dir}",
    )


def profile_readiness(profile: VMProfile, caps: QemuCapabilities) -> tuple[list[str], list[str], list[str]]:
    issues: list[str] = []
    highlights: list[str] = []
    notes: list[str] = []
    has_launch_basics = True

    qemu_path = Path(profile.qemu_executable).expanduser() if profile.qemu_executable else None
    if not profile.qemu_executable:
        issues.append("Choose a QEMU binary.")
        has_launch_basics = False
    elif not qemu_path or not qemu_path.exists():
        issues.append(f"QEMU binary does not exist: {profile.qemu_executable}")
        has_launch_basics = False
    elif caps.version:
        highlights.append(caps.version)

    disk_path = Path(profile.expanded_disk_path()) if profile.disk_path else None
    if not profile.disk_path:
        issues.append("Choose a disk image.")
        has_launch_basics = False
    elif not disk_path or not disk_path.exists():
        issues.append(f"Disk image does not exist yet: {profile.disk_path}")
        has_launch_basics = False
    else:
        highlights.append(f"Disk image: {disk_path.name}")

    if profile.firmware_path:
        firmware_path = Path(profile.expanded_firmware_path())
        if not firmware_path.exists():
            issues.append(f"Firmware file does not exist: {profile.firmware_path}")

    sharing_mode, mount_help = resolve_sharing(profile, caps)
    if sharing_mode == "none":
        notes.append("No shared folder configured yet.")
    else:
        shared_path = Path(profile.expanded_shared_dir_path())
        if not shared_path.is_dir():
            issues.append(f"Shared folder does not exist: {profile.shared_dir_path}")
        else:
            highlights.append(f"Shared folder: {shared_path}")
            notes.append(f"Guest mount command: {mount_help}")
        if sharing_mode == "virtiofs" and not caps.has_virtiofsd:
            issues.append("virtiofs is selected but virtiofsd is not available on the host.")

    if has_launch_basics and profile.enable_fullscreen:
        highlights.append(f"Fullscreen target: {profile.target_display_name}")
        if not should_qemu_handle_fullscreen(profile.target_display_name, True):
            notes.append("Non-primary display fullscreen is applied after launch by the host window manager.")

    if profile.display_backend not in {"auto", "none"} and not caps.supports_display(profile.display_backend):
        issues.append(f"Display backend '{profile.display_backend}' is not available on this host.")
    if has_launch_basics and profile.enable_audio and caps.audio_drivers:
        highlights.append(f"Audio: {', '.join(sorted(caps.audio_drivers))}")
    if profile.network_mode in {"bridge", "vmnet-bridged"} and not profile.bridge_name:
        issues.append("Bridge / interface name is required for bridged networking.")
    if caps.platform == "darwin" and _network_mode(profile, caps, caps.platform) in {"vmnet-shared", "vmnet-bridged"}:
        notes.append("macOS vmnet networking may require administrator approval on launch.")
    if has_launch_basics and profile.auto_resume:
        highlights.append(f"Resume snapshot: {profile.resume_snapshot_name}")
        if not snapshot_exists(profile):
            notes.append(
                f"Snapshot '{profile.resume_snapshot_name}' does not exist yet, so launch will start from a cold boot until you save state."
            )

    return issues, highlights, notes


def _usb_args(profile: VMProfile) -> list[str]:
    args: list[str] = []
    if profile.enable_usb or profile.enable_webcam or profile.usb_devices:
        args.extend(["-device", "qemu-xhci"])
    for usb_ref in profile.usb_devices:
        if usb_ref:
            args.extend(["-device", f"usb-host,{usb_ref}"])
    return args


def build_command(
    profile: VMProfile,
    caps: QemuCapabilities,
    artifacts: RuntimeArtifacts,
    host_platform: str | None = None,
    restore_state: bool = False,
) -> list[str]:
    host_platform = host_platform or caps.platform
    machine = _default_machine(profile)
    accel = _accel_option(profile, caps, host_platform)
    cpu = _cpu_option(profile, caps, host_platform)
    display = _display_backend(profile, caps, host_platform)
    firmware = profile.expanded_firmware_path()
    disk = profile.expanded_disk_path()
    if not profile.qemu_executable:
        raise ConfigurationError("QEMU executable is required.")
    if not disk:
        raise ConfigurationError("Disk path is required.")

    command = [
        profile.qemu_executable,
        "-machine",
        f"{machine},accel={accel}",
        "-cpu",
        cpu,
        "-smp",
        str(profile.cpu_cores),
        "-m",
        str(profile.memory_mib),
        "-display",
        display,
        "-pidfile",
        str(artifacts.pidfile),
        "-D",
        str(artifacts.log_file),
        "-qmp",
        f"unix:{artifacts.qmp_socket},server=on,wait=off",
    ]

    if should_qemu_handle_fullscreen(profile.target_display_name, profile.enable_fullscreen) and display != "none":
        command.append("-full-screen")

    if firmware:
        command.extend(["-drive", f"if=pflash,format=raw,readonly=on,file={firmware}"])

    command.extend(
        [
            "-device",
            "virtio-blk-pci,drive=disk0",
            "-drive",
            f"id=disk0,if=none,format=qcow2,file={disk}",
        ]
    )

    command.extend(_graphics_device(profile, display))
    command.extend(["-device", "virtio-keyboard-pci", "-device", "virtio-tablet-pci"])
    command.extend(_audio_args(profile, caps, host_platform))
    command.extend(_sharing_args(profile, caps, artifacts))
    command.extend(_network_args(profile, caps, host_platform))
    command.extend(_usb_args(profile))

    if restore_state and profile.auto_resume and profile.resume_snapshot_name and snapshot_exists(profile):
        command.extend(["-loadvm", profile.resume_snapshot_name])

    if profile.extra_args:
        command.extend(profile.extra_args)
    return command


class QmpClient:
    def __init__(self, socket_path: Path) -> None:
        self.socket_path = socket_path
        self._socket: socket.socket | None = None
        self._reader = None

    def connect(self, timeout: float = 5.0) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.socket_path.exists():
                sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                sock.connect(str(self.socket_path))
                self._socket = sock
                self._reader = sock.makefile("r", encoding="utf-8")
                self._read_event()
                self.execute("qmp_capabilities")
                return
            time.sleep(0.1)
        raise TimeoutError(f"QMP socket did not appear: {self.socket_path}")

    def _read_event(self) -> dict:
        if not self._reader:
            raise RuntimeError("QMP is not connected.")
        line = self._reader.readline()
        if not line:
            raise RuntimeError("QMP closed the connection.")
        return json.loads(line)

    def execute(self, name: str, arguments: dict | None = None) -> dict:
        if not self._socket:
            raise RuntimeError("QMP is not connected.")
        payload = {"execute": name}
        if arguments:
            payload["arguments"] = arguments
        self._socket.sendall((json.dumps(payload) + "\n").encode("utf-8"))
        while True:
            message = self._read_event()
            if "return" in message:
                return message["return"]
            if "error" in message:
                raise RuntimeError(str(message["error"]))

    def close(self) -> None:
        if self._reader:
            self._reader.close()
            self._reader = None
        if self._socket:
            self._socket.close()
            self._socket = None


class VMController:
    def __init__(self, paths: AppPaths, profile: VMProfile) -> None:
        self.paths = paths
        self.profile = profile
        self.capabilities = probe_qemu(profile.qemu_executable) if profile.qemu_executable else QemuCapabilities("")
        runtime_dir = paths.profile_runtime_dir(profile.profile_id)
        state_dir = paths.profile_state_dir(profile.profile_id)
        self.artifacts = RuntimeArtifacts(
            qmp_socket=runtime_dir / "qmp.sock",
            pidfile=runtime_dir / "qemu.pid",
            log_file=paths.logs_dir / f"{profile.profile_id}.log",
            stderr_log_file=paths.logs_dir / f"{profile.profile_id}-stderr.log",
            virtiofs_socket=runtime_dir / "virtiofs.sock" if self.capabilities.has_virtiofsd else None,
        )
        self.state_dir = state_dir
        self.process: subprocess.Popen[str] | None = None
        self.virtiofsd_process: subprocess.Popen[str] | None = None
        self.display_note: str | None = None

    def preview_command(self) -> list[str]:
        return build_command(self.profile, self.capabilities, self.artifacts, restore_state=True)

    @property
    def host_platform(self) -> str:
        return self.capabilities.platform

    def _start_virtiofsd(self) -> None:
        if _sharing_backend(self.profile, self.capabilities) != "virtiofs":
            return
        assert self.artifacts.virtiofs_socket is not None
        if self.artifacts.virtiofs_socket.exists():
            self.artifacts.virtiofs_socket.unlink()
        command = [
            shutil.which("virtiofsd") or "virtiofsd",
            "--socket-path",
            str(self.artifacts.virtiofs_socket),
            "--shared-dir",
            self.profile.expanded_shared_dir_path(),
        ]
        self.virtiofsd_process = subprocess.Popen(command, env=_clean_env())
        deadline = time.time() + 5
        while time.time() < deadline:
            if self.artifacts.virtiofs_socket.exists():
                return
            time.sleep(0.1)
        raise TimeoutError("virtiofsd did not create its socket.")

    def _read_pid(self) -> int | None:
        if not self.artifacts.pidfile.exists():
            return None
        try:
            pid_str = self.artifacts.pidfile.read_text(encoding="utf-8").strip()
            if not pid_str:
                return None
            pid = int(pid_str)

            # Verify this PID actually belongs to a QEMU process
            # and is not a stale PID from a previous crash.
            if not self._pid_is_running(pid):
                return None

            # On many systems we can check the process name for extra safety
            try:
                import subprocess

                result = subprocess.run(
                    ["ps", "-p", str(pid), "-o", "comm="], capture_output=True, text=True, check=False
                )
                comm = result.stdout.strip().lower()
                if "qemu-system" not in comm and "qemu" not in comm:
                    return None
            except Exception:
                # If ps fails, we still have the _pid_is_running check
                pass

            return pid
        except (OSError, ValueError):
            return None

    def _pid_is_running(self, pid: int | None) -> bool:
        if not pid:
            return False
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    def is_running(self) -> bool:
        if self.process and self.process.poll() is None:
            return True
        return self._pid_is_running(self._read_pid())

    def save_state(self, snapshot_name: str | None = None) -> None:
        snapshot = snapshot_name or self.profile.resume_snapshot_name
        if not snapshot:
            raise ConfigurationError("A snapshot name is required to save state.")
        client = self.connect_qmp()
        try:
            client.execute(
                "human-monitor-command",
                {"command-line": f"savevm {snapshot}"},
            )
        finally:
            client.close()

    def launch(self) -> subprocess.Popen[str] | None:
        if self.process and self.process.poll() is None:
            return self.process
        if self.is_running():
            return None
        self.artifacts.log_file.parent.mkdir(parents=True, exist_ok=True)
        if self.artifacts.qmp_socket.exists():
            self.artifacts.qmp_socket.unlink()
        if self.artifacts.pidfile.exists():
            self.artifacts.pidfile.unlink()
        if self.artifacts.stderr_log_file.exists():
            self.artifacts.stderr_log_file.unlink()
        self._start_virtiofsd()
        command = build_command(self.profile, self.capabilities, self.artifacts, restore_state=True)
        self.last_command = command
        if _network_requires_elevation(self.profile, self.capabilities, self.host_platform) and os.geteuid() != 0:
            self._launch_with_privileges(command)
            self.process = None
            self._ensure_started()
        else:
            with self.artifacts.stderr_log_file.open("w", encoding="utf-8") as stderr_handle:
                self.process = subprocess.Popen(
                    command,
                    env=_clean_env(),
                    text=True,
                    stdout=subprocess.DEVNULL,
                    stderr=stderr_handle,
                )
            self._ensure_started()
        self._start_display_arrangement()
        return self.process

    def _launch_with_privileges(self, command: list[str]) -> None:
        if shutil.which("osascript") is None:
            raise ConfigurationError("macOS administrator approval requires osascript to be available.")
        quoted_command = shell_join(command)
        quoted_stderr = shlex.quote(str(self.artifacts.stderr_log_file))
        script = (
            'do shell script "'
            + _osascript_shell_escape(quoted_command)
            + " >/dev/null 2>>"
            + _osascript_shell_escape(quoted_stderr)
            + ' </dev/null &" with administrator privileges'
        )
        result = subprocess.run(
            ["osascript", "-e", script],
            check=False,
            capture_output=True,
            text=True,
            env=_clean_env(),
        )
        if result.returncode != 0:
            message = (result.stderr or result.stdout or "Administrator-approved launch failed.").strip()
            raise RuntimeError(message)

    def _ensure_started(self, timeout: float = 10.0) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.artifacts.qmp_socket.exists() or self.artifacts.pidfile.exists():
                return
            if self.process and self.process.poll() is not None:
                break
            time.sleep(0.1)
        if self.process and self.process.poll() is None:
            return
        raise RuntimeError(self._startup_error_message())

    def _startup_error_message(self) -> str:
        stderr_text = _tail_text(self.artifacts.stderr_log_file)
        qemu_log_text = _tail_text(self.artifacts.log_file)
        parts = ["QEMU exited before the VM became ready."]
        if self.last_command:
            parts.append(f"command: {shell_join(self.last_command)}")
        if stderr_text:
            parts.append(f"stderr: {stderr_text}")
        if qemu_log_text:
            parts.append(f"log: {qemu_log_text}")
        if not stderr_text and not qemu_log_text:
            parts.append("No diagnostic output was captured.")
        return "\n".join(parts)

    def _start_display_arrangement(self) -> None:
        if not self.profile.target_display_name or should_qemu_handle_fullscreen(
            self.profile.target_display_name, self.profile.enable_fullscreen
        ):
            self.display_note = None
            return
        self.display_note = f"Placing VM on {self.profile.target_display_name}; host accessibility/window-control permission may be required."

        def worker() -> None:
            # Wait for PID if not available yet (race condition with pidfile writing)
            pid = self.process.pid if self.process and self.process.poll() is None else None
            deadline = time.time() + 5.0
            while not pid and time.time() < deadline:
                pid = self._read_pid()
                if pid:
                    break
                time.sleep(0.2)

            if not pid:
                return

            note = arrange_window(
                pid,
                self.profile.target_display_name,
                fullscreen=self.profile.enable_fullscreen,
            )
            if note:
                self.display_note = note

        threading.Thread(target=worker, daemon=True).start()

    def connect_qmp(self) -> QmpClient:
        client = QmpClient(self.artifacts.qmp_socket)
        client.connect()
        return client

    def status(self) -> dict:
        client = self.connect_qmp()
        try:
            return client.execute("query-status")
        finally:
            client.close()

    def stop(self, save_state: bool | None = None) -> None:
        if not self.is_running() and not self.artifacts.qmp_socket.exists():
            return
        save_state = self.profile.auto_resume if save_state is None else save_state
        client = self.connect_qmp()
        try:
            if save_state and self.profile.resume_snapshot_name:
                client.execute(
                    "human-monitor-command",
                    {"command-line": f"savevm {self.profile.resume_snapshot_name}"},
                )
            client.execute("quit")
        finally:
            client.close()
        self.wait()
        self._stop_virtiofsd()

    def wait(self, timeout: float = 10.0) -> int | None:
        if not self.process:
            deadline = time.time() + timeout
            pid = self._read_pid()
            while pid and time.time() < deadline:
                if not self._pid_is_running(pid):
                    return 0
                time.sleep(0.1)
            return None
        try:
            return self.process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            return None

    def terminate(self) -> None:
        if self.process and self.process.poll() is None:
            self.process.terminate()
            self.wait()
        else:
            pid = self._read_pid()
            if pid and self._pid_is_running(pid):
                os.kill(pid, signal.SIGTERM)
                self.wait()
        self._stop_virtiofsd()

    def _stop_virtiofsd(self) -> None:
        if self.virtiofsd_process and self.virtiofsd_process.poll() is None:
            self.virtiofsd_process.terminate()
            try:
                self.virtiofsd_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.virtiofsd_process.kill()
        self.virtiofsd_process = None


def shell_join(command: list[str]) -> str:
    return shlex.join(command)


def _osascript_shell_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _tail_text(path: Path, limit: int = 600) -> str:
    if not path.exists():
        return ""
    try:
        return path.read_text(encoding="utf-8", errors="replace").strip()[-limit:]
    except OSError:
        return ""


def _find_qemu_img(profile: VMProfile) -> str | None:
    qemu_path = Path(profile.qemu_executable).expanduser() if profile.qemu_executable else None
    if qemu_path:
        sibling = qemu_path.with_name("qemu-img")
        if sibling.exists():
            return str(sibling)
    return shutil.which("qemu-img")


def snapshot_exists(profile: VMProfile) -> bool:
    disk = profile.expanded_disk_path()
    snapshot = profile.resume_snapshot_name
    qemu_img = _find_qemu_img(profile)
    if not disk or not snapshot or not qemu_img or not Path(disk).exists():
        return False
    result = subprocess.run(
        [qemu_img, "snapshot", "-l", disk],
        check=False,
        capture_output=True,
        text=True,
        env=_clean_env(),
    )
    if result.returncode != 0:
        return False
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1] == snapshot:
            return True
    return False
