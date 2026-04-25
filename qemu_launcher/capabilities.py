from __future__ import annotations

import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path


def _run_text(command: list[str]) -> str:
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=5, check=False)
    except FileNotFoundError:
        return ""
    return (result.stdout or "") + ("\n" + result.stderr if result.stderr else "")


def parse_keyed_list(output: str, prefix: str) -> set[str]:
    items: set[str] = set()
    capture = False
    for line in output.splitlines():
        stripped = line.strip()
        if prefix in stripped:
            capture = True
            continue
        if not capture or not stripped:
            continue
        if stripped.endswith(":"):
            continue
        token = stripped.split()[0]
        if token and token.isascii():
            items.add(token)
    return items


def parse_version(output: str) -> str:
    first_line = output.splitlines()[0] if output.splitlines() else ""
    return first_line.strip()


@dataclass(slots=True)
class QemuCapabilities:
    executable: str
    version: str = ""
    accelerators: set[str] = field(default_factory=set)
    displays: set[str] = field(default_factory=set)
    audio_drivers: set[str] = field(default_factory=set)
    netdev_backends: set[str] = field(default_factory=set)
    has_virtiofsd: bool = False
    has_passt: bool = False

    @property
    def platform(self) -> str:
        return sys.platform

    def supports_accel(self, name: str) -> bool:
        return name in self.accelerators

    def supports_display(self, name: str) -> bool:
        return name in self.displays

    def supports_audio(self, name: str) -> bool:
        return name in self.audio_drivers

    def supports_netdev(self, name: str) -> bool:
        return name in self.netdev_backends


def probe_qemu(executable: str) -> QemuCapabilities:
    caps = QemuCapabilities(executable=executable)
    caps.version = parse_version(_run_text([executable, "--version"]))
    caps.accelerators = parse_keyed_list(_run_text([executable, "-accel", "help"]), "Accelerators supported")
    caps.displays = parse_keyed_list(_run_text([executable, "-display", "help"]), "Available display backend")
    caps.audio_drivers = parse_keyed_list(_run_text([executable, "-audiodev", "help"]), "Available audio drivers")
    caps.netdev_backends = parse_keyed_list(_run_text([executable, "-netdev", "help"]), "Available netdev backend")
    caps.has_virtiofsd = shutil.which("virtiofsd") is not None
    caps.has_passt = shutil.which("passt") is not None and "passt" in caps.netdev_backends
    return caps


def find_default_qemu(architecture: str) -> str:
    candidates = [
        shutil.which(f"qemu-system-{architecture}"),
        str(Path("/opt/homebrew/bin") / f"qemu-system-{architecture}"),
        str(Path("/usr/local/bin") / f"qemu-system-{architecture}"),
        str(Path("/usr/bin") / f"qemu-system-{architecture}"),
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return candidate
    return ""


def detect_network_interfaces() -> list[str]:
    """Return a list of network interfaces suitable for bridging."""
    interfaces: list[str] = ["en0", "en1", "eth0", "wlan0"] # Fallbacks
    
    if sys.platform == "darwin":
        # On macOS, networksetup is the most descriptive
        try:
            result = subprocess.run(["networksetup", "-listallhardwareports"], capture_output=True, text=True, check=False)
            if result.returncode == 0:
                found = []
                current_port = ""
                for line in result.stdout.splitlines():
                    if "Hardware Port:" in line:
                        current_port = line.split(":", 1)[1].strip()
                    elif "Device:" in line and current_port:
                        device = line.split(":", 1)[1].strip()
                        found.append(f"{device} ({current_port})")
                        current_port = ""
                if found:
                    return found
        except Exception:
            pass
            
    # Linux or fallback
    try:
        import os
        if os.path.isdir("/sys/class/net"):
            return sorted([d for d in os.listdir("/sys/class/net") if d != "lo"])
    except Exception:
        pass
        
    return interfaces
