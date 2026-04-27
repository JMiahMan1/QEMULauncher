from __future__ import annotations

import configparser
import os
import secrets
import string
import sys

try:
    import tomllib
except ImportError:
    import tomli as tomllib
from pathlib import Path
from typing import Any

from platformdirs import PlatformDirs
from pydantic import BaseModel, Field

from .capabilities import find_default_qemu

APP_NAME = "QEMU Launcher"
APP_AUTHOR = "QEMULauncher"
SCHEMA_VERSION = 1
LEGACY_CONFIG_DIR = Path.home() / ".config" / "qemu_launcher"
LEGACY_CONFIG_FILE = LEGACY_CONFIG_DIR / "config.ini"


class AppPaths:
    def __init__(
        self,
        config_dir: str | Path | None = None,
        state_dir: str | Path | None = None,
        runtime_dir: str | Path | None = None,
    ) -> None:
        override_root = os.environ.get("QEMU_LAUNCHER_HOME")
        if override_root:
            root = Path(override_root)
            self.config_dir = root / "config"
            self.data_dir = root / "data"
            self.state_dir = root / "state"
            self.runtime_dir = root / "runtime"
        else:
            dirs = PlatformDirs(APP_NAME, APP_AUTHOR, ensure_exists=False)
            self.config_dir = Path(dirs.user_config_path)
            self.data_dir = Path(dirs.user_data_path)
            self.state_dir = Path(dirs.user_state_path)
            runtime = getattr(dirs, "user_runtime_path", None)
            self.runtime_dir = Path(runtime) if runtime else self.state_dir / "runtime"

        # Explicit overrides
        if config_dir:
            self.config_dir = Path(config_dir)
        if state_dir:
            self.state_dir = Path(state_dir)
        if runtime_dir:
            self.runtime_dir = Path(runtime_dir)

        self.logs_dir = self.state_dir / "logs"
        self.profiles_dir = self.config_dir / "profiles"
        self.settings_file = self.config_dir / "settings.toml"
        self._ensure_directories()

    def _ensure_directories(self) -> None:
        for path in [
            self.config_dir,
            self.data_dir,
            self.state_dir,
            self.runtime_dir,
            self.logs_dir,
            self.profiles_dir,
        ]:
            path.mkdir(parents=True, exist_ok=True)

    def profile_state_dir(self, profile_id: str) -> Path:
        path = self.state_dir / "profiles" / profile_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def profile_data_dir(self, profile_id: str) -> Path:
        path = self.data_dir / "profiles" / profile_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def profile_runtime_dir(self, profile_id: str) -> Path:
        path = self.runtime_dir / "profiles" / profile_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def qmp_socket(self, profile_id: str) -> Path:
        return self.profile_runtime_dir(profile_id) / "qmp.sock"

    def pid_file(self, profile_id: str) -> Path:
        return self.profile_runtime_dir(profile_id) / "qemu.pid"

    def log_file(self, profile_id: str) -> Path:
        return self.profile_state_dir(profile_id) / "qemu.log"

    def stderr_log_file(self, profile_id: str) -> Path:
        return self.profile_state_dir(profile_id) / "stderr.log"


def _random_profile_id() -> str:
    alphabet = string.ascii_lowercase + string.digits
    return "vm-" + "".join(secrets.choice(alphabet) for _ in range(8))


def default_architecture() -> str:
    return "aarch64" if sys.platform == "darwin" and os.uname().machine == "arm64" else "x86_64"


def build_default_profile(name: str = "Default VM") -> "VMProfile":
    architecture = default_architecture()
    return VMProfile(
        name=name,
        architecture=architecture,
        qemu_executable=find_default_qemu(architecture),
    )


def apply_profile_defaults(profile: "VMProfile") -> "VMProfile":
    if not profile.architecture:
        profile.architecture = default_architecture()
    if not profile.qemu_executable:
        profile.qemu_executable = find_default_qemu(profile.architecture)
    if not profile.target_display_name:
        profile.target_display_name = "Primary Display"
    return profile


class VMProfile(BaseModel):
    profile_id: str = Field(default_factory=_random_profile_id)
    name: str = "Default VM"
    architecture: str = "x86_64"
    machine: str = "auto"
    qemu_executable: str = ""
    disk_path: str = ""
    firmware_path: str = ""
    memory_mib: int = 4096
    cpu_cores: int = 4
    enable_fullscreen: bool = True
    show_fullscreen_overlay: bool = False
    target_display_name: str = "Primary Display"
    display_backend: str = "auto"
    graphics_mode: str = "auto"
    shared_dir_path: str = ""
    sharing_backend: str = "auto"
    mount_tag: str = "host_share"
    network_mode: str = "auto"
    bridge_interface: str = ""
    enable_audio: bool = True
    enable_microphone: bool = False
    enable_usb: bool = False
    enable_webcam: bool = False
    usb_devices: list[str] = Field(default_factory=list)
    auto_resume: bool = True
    resume_snapshot_name: str = "resume"
    extra_args: list[str] = Field(default_factory=list)

    def expanded_disk_path(self) -> str:
        return os.path.expanduser(self.disk_path)

    def expanded_firmware_path(self) -> str:
        return os.path.expanduser(self.firmware_path)

    def expanded_shared_dir_path(self) -> str:
        return os.path.expanduser(self.shared_dir_path)


class AppSettings(BaseModel):
    schema_version: int = SCHEMA_VERSION
    last_used_profile: str | None = None
    recent_profiles: list[str] = Field(default_factory=list)
    auto_launch_enabled: bool = False
    auto_launch_profile: str | None = None


def _toml_quote(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _toml_array(values: list[str]) -> str:
    return "[" + ", ".join(_toml_quote(value) for value in values) + "]"


def write_settings(settings: AppSettings, path: Path) -> None:
    content = [
        f"schema_version = {settings.schema_version}",
        f"last_used_profile = {_toml_quote(settings.last_used_profile or '')}",
        f"recent_profiles = {_toml_array(settings.recent_profiles)}",
        f"auto_launch_enabled = {'true' if settings.auto_launch_enabled else 'false'}",
        f"auto_launch_profile = {_toml_quote(settings.auto_launch_profile or '')}",
        "",
    ]
    path.write_text("\n".join(content), encoding="utf-8")


def write_profile(profile: VMProfile, path: Path) -> None:
    content = [
        f"profile_id = {_toml_quote(profile.profile_id)}",
        f"name = {_toml_quote(profile.name)}",
        f"architecture = {_toml_quote(profile.architecture)}",
        f"machine = {_toml_quote(profile.machine)}",
        f"qemu_executable = {_toml_quote(profile.qemu_executable)}",
        f"disk_path = {_toml_quote(profile.disk_path)}",
        f"firmware_path = {_toml_quote(profile.firmware_path)}",
        f"memory_mib = {profile.memory_mib}",
        f"cpu_cores = {profile.cpu_cores}",
        f"enable_fullscreen = {'true' if profile.enable_fullscreen else 'false'}",
        f"show_fullscreen_overlay = {'true' if getattr(profile, 'show_fullscreen_overlay', False) else 'false'}",
        f"target_display_name = {_toml_quote(profile.target_display_name)}",
        f"display_backend = {_toml_quote(profile.display_backend)}",
        f"graphics_mode = {_toml_quote(profile.graphics_mode)}",
        f"shared_dir_path = {_toml_quote(profile.shared_dir_path)}",
        f"sharing_backend = {_toml_quote(profile.sharing_backend)}",
        f"mount_tag = {_toml_quote(profile.mount_tag)}",
        f"network_mode = {_toml_quote(profile.network_mode)}",
        f"bridge_interface = {_toml_quote(profile.bridge_interface)}",
        f"enable_audio = {'true' if profile.enable_audio else 'false'}",
        f"enable_microphone = {'true' if profile.enable_microphone else 'false'}",
        f"enable_usb = {'true' if profile.enable_usb else 'false'}",
        f"enable_webcam = {'true' if profile.enable_webcam else 'false'}",
        f"usb_devices = {_toml_array(profile.usb_devices)}",
        f"auto_resume = {'true' if profile.auto_resume else 'false'}",
        f"resume_snapshot_name = {_toml_quote(profile.resume_snapshot_name)}",
        f"extra_args = {_toml_array(profile.extra_args)}",
        "",
    ]
    path.write_text("\n".join(content), encoding="utf-8")


def load_settings(path: Path) -> AppSettings:
    if not path.is_file():
        return AppSettings()
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    if not data.get("last_used_profile"):
        data["last_used_profile"] = None
    if not data.get("auto_launch_profile"):
        data["auto_launch_profile"] = None
    return AppSettings.model_validate(data)


def load_profile(path: Path) -> VMProfile:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    profile = VMProfile.model_validate(data)
    # Ensure profile_id matches filename stem for consistency
    profile.profile_id = path.stem
    return apply_profile_defaults(profile)


def load_profiles(paths: AppPaths) -> list[VMProfile]:
    profiles: list[VMProfile] = []
    for path in sorted(paths.profiles_dir.glob("*.toml")):
        profiles.append(load_profile(path))
    return profiles


def save_profile(paths: AppPaths, profile: VMProfile) -> Path:
    path = paths.profiles_dir / f"{profile.profile_id}.toml"
    write_profile(profile, path)
    return path


def save_settings(paths: AppPaths, settings: AppSettings) -> None:
    write_settings(settings, paths.settings_file)


def _legacy_to_profile(data: dict[str, Any]) -> VMProfile:
    arch = data.get("arch") or default_architecture()
    return VMProfile(
        name="Migrated VM",
        architecture=arch,
        qemu_executable=data.get("qemu_executable", "") or find_default_qemu(arch),
        disk_path=data.get("disk_path", ""),
        firmware_path=data.get("firmware_path", ""),
        shared_dir_path=data.get("shared_dir_path", ""),
        mount_tag=data.get("mount_tag", "host_share"),
        network_mode=data.get("network_mode", "auto"),
        bridge_interface=data.get("bridge_interface", data.get("bridge_name", "")),
        enable_microphone=_legacy_bool(data.get("enable_microphone", False)),
        enable_webcam=_legacy_bool(data.get("enable_webcam", False)),
        enable_fullscreen=_legacy_bool(data.get("enable_fullscreen", True)),
    )


def _legacy_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def migrate_legacy_ini(paths: AppPaths) -> tuple[AppSettings, list[VMProfile]] | None:
    if paths.settings_file.exists():
        return None
    if not LEGACY_CONFIG_FILE.is_file():
        return None
    parser = configparser.ConfigParser()
    parser.read(LEGACY_CONFIG_FILE)
    if "VM" not in parser:
        return None
    legacy_data = dict(parser["VM"])
    profile = _legacy_to_profile(legacy_data)
    settings = AppSettings(last_used_profile=profile.profile_id, recent_profiles=[profile.profile_id])
    save_profile(paths, profile)
    save_settings(paths, settings)
    return settings, [profile]


def ensure_default_profile(paths: AppPaths) -> tuple[AppSettings, list[VMProfile]]:
    migrated = migrate_legacy_ini(paths)
    if migrated:
        return migrated

    settings = load_settings(paths.settings_file)
    profiles = load_profiles(paths)
    if profiles:
        return settings, profiles

    profile = build_default_profile()
    save_profile(paths, profile)
    settings.last_used_profile = profile.profile_id
    settings.recent_profiles = [profile.profile_id]
    save_settings(paths, settings)
    return settings, [profile]
