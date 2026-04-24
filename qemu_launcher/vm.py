from __future__ import annotations

import json
import os
import shlex
import socket
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from .capabilities import QemuCapabilities, probe_qemu
from .config import AppPaths, VMProfile


class ConfigurationError(RuntimeError):
    pass


@dataclass(slots=True)
class RuntimeArtifacts:
    qmp_socket: Path
    pidfile: Path
    log_file: Path
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
        return profile.display_backend
    if host_platform == "darwin" and caps.supports_display("cocoa"):
        return "cocoa,show-cursor=on,zoom-to-fit=on"
    if host_platform.startswith("linux"):
        if caps.supports_display("gtk"):
            return "gtk,gl=on,show-tabs=off,show-menubar=off"
        if caps.supports_display("sdl"):
            return "sdl,gl=on"
    return "none"


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

    if restore_state and profile.auto_resume and profile.resume_snapshot_name:
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
            virtiofs_socket=runtime_dir / "virtiofs.sock" if self.capabilities.has_virtiofsd else None,
        )
        self.state_dir = state_dir
        self.process: subprocess.Popen[str] | None = None
        self.virtiofsd_process: subprocess.Popen[str] | None = None

    def preview_command(self) -> list[str]:
        return build_command(self.profile, self.capabilities, self.artifacts, restore_state=True)

    def _start_virtiofsd(self) -> None:
        if _sharing_backend(self.profile, self.capabilities) != "virtiofs":
            return
        assert self.artifacts.virtiofs_socket is not None
        if self.artifacts.virtiofs_socket.exists():
            self.artifacts.virtiofs_socket.unlink()
        command = [
            "virtiofsd",
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

    def launch(self) -> subprocess.Popen[str]:
        if self.process and self.process.poll() is None:
            return self.process
        self.artifacts.log_file.parent.mkdir(parents=True, exist_ok=True)
        if self.artifacts.qmp_socket.exists():
            self.artifacts.qmp_socket.unlink()
        if self.artifacts.pidfile.exists():
            self.artifacts.pidfile.unlink()
        self._start_virtiofsd()
        command = build_command(self.profile, self.capabilities, self.artifacts, restore_state=True)
        self.process = subprocess.Popen(command, env=_clean_env(), text=True)
        return self.process

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
            return None
        try:
            return self.process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            return None

    def terminate(self) -> None:
        if self.process and self.process.poll() is None:
            self.process.terminate()
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
