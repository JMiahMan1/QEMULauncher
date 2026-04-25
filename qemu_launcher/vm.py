from __future__ import annotations

import os
import shlex
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .capabilities import QemuCapabilities
    from .config import VMProfile
from .display import arrange_window, should_qemu_handle_fullscreen


class ConfigurationError(Exception):
    """Raised when the VM configuration is invalid for the current platform."""


@dataclass(slots=True)
class RuntimeArtifacts:
    qmp_socket: Path
    pidfile: Path
    log_file: Path
    stderr_log_file: Path


@dataclass
class VMController:
    profile: VMProfile
    capabilities: QemuCapabilities
    artifacts: RuntimeArtifacts
    process: subprocess.Popen | None = None
    last_command: list[str] | None = None
    display_note: str | None = None
    host_platform: str = field(default_factory=lambda: sys.platform)

    def launch(self, restore_state: bool = True) -> subprocess.Popen:
        # Check if we need the networking helper and ensure it's installed
        if _network_requires_elevation(self.profile, self.capabilities, self.host_platform):
            _ensure_helper_installed()

        self.artifacts.qmp_socket.parent.mkdir(parents=True, exist_ok=True)
        command = build_command(self.profile, self.capabilities, self.artifacts, host_platform=self.host_platform, restore_state=restore_state)
        self.last_command = command
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

    def preview_command(self) -> list[str]:
        """Return the command line that would be used to launch the VM."""
        return build_command(self.profile, self.capabilities, self.artifacts, host_platform=self.host_platform, restore_state=True)

    def is_running(self) -> bool:
        """Check if the VM is currently running."""
        if self.process and self.process.poll() is None:
            return True
        pid = self._read_pid()
        if not pid:
            return False
        # Verify if the PID is actually a qemu process
        try:
            result = subprocess.run(["ps", "-p", str(pid), "-o", "comm="], capture_output=True, text=True, check=False)
            return "qemu" in result.stdout.lower()
        except Exception:
            return False

    def status(self) -> dict:
        """Query QEMU for the current status via QMP."""
        if not self.is_running():
            return {"status": "stopped"}
        # Basic status query implementation
        return {"status": "running"}

    def save_state(self) -> None:
        """Trigger a snapshot save via QMP."""
        if not self.is_running():
            return
        # QMP snapshot implementation would go here
        pass

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
        self.display_note = (
            f"Placing VM on {self.profile.target_display_name}; host accessibility permission may be required."
        )

        def worker() -> None:
            # 1. Wait for PID
            pid = self.process.pid if self.process and self.process.poll() is None else None
            deadline = time.time() + 5.0
            while not pid and time.time() < deadline:
                pid = self._read_pid()
                if pid:
                    break
                time.sleep(0.2)

            if not pid:
                return

            # 2. Arrange the window
            error = arrange_window(pid, self.profile.target_display_name, self.profile.enable_fullscreen)
            if error:
                self.display_note = f"Placement note: {error}"

        threading.Thread(target=worker, daemon=True).start()

    def _read_pid(self) -> int | None:
        if self.artifacts.pidfile.exists():
            try:
                return int(self.artifacts.pidfile.read_text().strip())
            except Exception:
                pass
        return None

    def stop(self) -> None:
        if self.process:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
        self._stop_virtiofsd()

    def _start_virtiofsd(self) -> None:
        if _sharing_backend(self.profile, self.capabilities) != "virtiofs":
            return
        # virtiofsd implementation would go here
        pass

    def _stop_virtiofsd(self) -> None:
        # virtiofsd cleanup would go here
        pass


def build_command(
    profile: VMProfile,
    caps: QemuCapabilities,
    artifacts: RuntimeArtifacts,
    host_platform: str | None = None,
    restore_state: bool = False,
) -> list[str]:
    platform = host_platform or sys.platform
    command = [profile.qemu_executable]

    if profile.architecture == "aarch64":
        command.extend(["-machine", "virt,accel=hvf:tcg" if platform == "darwin" else "virt,accel=kvm:tcg"])
    else:
        command.extend(["-machine", "q35,accel=hvf:tcg" if platform == "darwin" else "q35,accel=kvm:tcg"])

    command.extend(["-cpu", "host"])
    command.extend(["-smp", str(profile.cpu_cores)])
    command.extend(["-m", str(profile.memory_mib)])

    display = _display_args(profile, caps, platform)
    command.extend(["-display", display])

    command.extend(
        [
            "-pidfile",
            str(artifacts.pidfile),
            "-D",
            str(artifacts.log_file),
            "-qmp",
            f"unix:{artifacts.qmp_socket},server=on,wait=off",
        ]
    )

    if profile.enable_fullscreen and display != "none":
        # Only use internal fullscreen for primary display
        if should_qemu_handle_fullscreen(profile.target_display_name, profile.enable_fullscreen):
            command.append("-full-screen")

    if profile.firmware_path:
        command.extend(["-drive", f"if=pflash,format=raw,readonly=on,file={profile.firmware_path}"])

    command.extend(["-device", "virtio-blk-pci,drive=disk0"])
    command.extend(["-drive", f"id=disk0,if=none,format=raw,file={profile.disk_path}"])

    command.extend(_audio_args(profile, caps, platform))
    command.extend(_network_args(profile, caps, platform))

    if restore_state and profile.auto_resume and profile.resume_snapshot_name:
        command.extend(["-loadvm", profile.resume_snapshot_name])

    if profile.extra_args:
        command.extend(profile.extra_args)

    return command


def _display_args(profile: VMProfile, caps: QemuCapabilities, platform: str) -> str:
    if platform == "darwin":
        # Always use zoom-to-fit to enable resizable styleMask in Cocoa
        return "cocoa,show-cursor=on,zoom-to-fit=on,left-command-key=on"
    if "gtk" in caps.displays:
        return "gtk,gl=on"
    return "sdl"


def _audio_args(profile: VMProfile, caps: QemuCapabilities, platform: str) -> list[str]:
    driver = "coreaudio" if platform == "darwin" else "pa"
    return ["-audiodev", f"{driver},id=snd0", "-device", "virtio-sound-pci,audiodev=snd0"]


def _network_mode(profile: VMProfile, caps: QemuCapabilities, platform: str) -> str:
    if profile.network_mode == "auto" and platform == "darwin":
        return "vmnet-shared"
    return profile.network_mode


def _network_requires_elevation(profile: VMProfile, caps: QemuCapabilities, platform: str) -> bool:
    mode = _network_mode(profile, caps, platform)
    return platform == "darwin" and mode in {"vmnet-shared", "vmnet-bridged"}


def _network_args(profile: VMProfile, caps: QemuCapabilities, platform: str) -> list[str]:
    mode = profile.network_mode
    if platform == "darwin" and mode in {"vmnet-shared", "vmnet-bridged"}:
        helper_path = "/usr/local/bin/qemu-launcher-helper"
        if os.path.exists(helper_path):
            # 1. Start the helper as a background process to initialize the FD
            # We don't need sudo here because the helper is SUID root
            arg = "shared" if mode == "vmnet-shared" else "bridged"
            if mode == "vmnet-bridged" and profile.bridge_name:
                subprocess.Popen(
                    [helper_path, arg, profile.bridge_name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                )
            else:
                subprocess.Popen([helper_path, arg], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

            # 2. Return the socket-based netdev. QEMU will connect to the helper's Unix socket.
            socket_path = "/tmp/qemu-launcher-net.sock"
            # Wait a moment for helper to listen
            time.sleep(0.5)
            return [
                "-netdev",
                f"stream,id=net0,addr.type=unix,addr.path={socket_path}",
                "-device",
                "virtio-net-pci,netdev=net0",
            ]

        # Fallback for dev/uninstalled state
        return ["-netdev", "user,id=net0", "-device", "virtio-net-pci,netdev=net0"]

    return ["-netdev", "user,id=net0", "-device", "virtio-net-pci,netdev=net0"]


def _ensure_helper_installed() -> bool:
    """Prompts the user to authorize the one-time installation of the networking helper."""
    target_path = "/usr/local/bin/qemu-launcher-helper"
    if os.path.exists(target_path):
        return True

    # Try to find the bundled helper
    # In a PyInstaller bundle, bundled files are in sys._MEIPASS or Resources
    bundled_helper = None
    if hasattr(sys, "_MEIPASS"):
        path = Path(sys._MEIPASS) / "qemu-launcher-helper"
        if path.exists():
            bundled_helper = path
    
    if not bundled_helper:
        # Check macOS bundle Resources folder
        try:
            from AppKit import NSBundle
            path = Path(NSBundle.mainBundle().resourcePath()) / "qemu-launcher-helper"
            if path.exists():
                bundled_helper = path
        except ImportError:
            pass

    if not bundled_helper:
        # Check relative to script
        path = Path(__file__).parent.parent / "qemu-launcher-helper"
        if path.exists():
            bundled_helper = path

    if not bundled_helper:
        return False

    script = f'do shell script "mkdir -p /usr/local/bin && cp {bundled_helper} {target_path} && chown root {target_path} && chmod 4755 {target_path}" with administrator privileges'
    try:
        subprocess.run(["osascript", "-e", script], check=True, capture_output=True)
        return True
    except Exception:
        return False


def _sharing_backend(profile: VMProfile, caps: QemuCapabilities) -> str:
    return "none"


def profile_readiness(
    profile: VMProfile, caps: QemuCapabilities, artifacts: RuntimeArtifacts
) -> tuple[list[str], list[str], list[str]]:
    """Validate profile and return (highlights, notes, issues)."""
    highlights: list[str] = []
    notes: list[str] = []
    issues: list[str] = []

    # Move all potential blockers to notes/advice to ensure the UI is never "locked"
    if not profile.qemu_executable or not os.path.exists(profile.qemu_executable):
        notes.append(f"QEMU not found at: {profile.qemu_executable or 'Empty'}")

    if not profile.disk_path or not os.path.exists(profile.disk_path):
        notes.append("No disk image selected. VM may fail to boot.")

    if profile.architecture == "aarch64" and "virt" not in profile.machine:
        notes.append("AArch64 usually requires the 'virt' machine type.")

    return highlights, notes, issues


def resolve_sharing(profile: VMProfile, caps: QemuCapabilities) -> str:
    """Return the best sharing backend for the profile."""
    if not profile.shared_dir_path:
        return "none"
    return "9p"


def shell_join(args: list[str]) -> str:
    return " ".join(shlex.quote(arg) for arg in args)


def _clean_env() -> dict[str, str]:
    env = os.environ.copy()
    # Ensure standard locales and path
    env["LC_ALL"] = "C"
    return env


def _tail_text(path: Path, lines: int = 10) -> str:
    if not path.exists():
        return ""
    try:
        with path.open("r", encoding="utf-8") as f:
            return "".join(f.readlines()[-lines:])
    except Exception:
        return ""


def _osascript_shell_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


def snapshot_exists(profile: VMProfile) -> bool:
    # Snapshot check implementation would go here
    return False
