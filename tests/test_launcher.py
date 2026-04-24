from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from qemu_launcher.capabilities import QemuCapabilities, find_default_qemu, parse_keyed_list, probe_qemu
from qemu_launcher.config import (
    AppPaths,
    AppSettings,
    VMProfile,
    load_profile,
    load_settings,
    migrate_legacy_ini,
    save_profile,
    save_settings,
)
from qemu_launcher.display import should_qemu_handle_fullscreen
from qemu_launcher.vm import RuntimeArtifacts, VMController, build_command, profile_readiness, resolve_sharing


class DummyPaths:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.logs_dir = root / "logs"
        self.logs_dir.mkdir(parents=True, exist_ok=True)

    def profile_runtime_dir(self, profile_id: str) -> Path:
        path = self.root / "runtime" / profile_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def profile_state_dir(self, profile_id: str) -> Path:
        path = self.root / "state" / profile_id
        path.mkdir(parents=True, exist_ok=True)
        return path


def fake_caps(**overrides) -> QemuCapabilities:
    caps = QemuCapabilities(
        executable="/usr/bin/qemu-system-x86_64",
        version="QEMU 10.1",
        accelerators={"kvm", "tcg"},
        displays={"gtk", "sdl"},
        audio_drivers={"pipewire", "pa", "alsa"},
        netdev_backends={"user", "bridge", "passt"},
        has_virtiofsd=False,
        has_passt=True,
    )
    for key, value in overrides.items():
        setattr(caps, key, value)
    return caps


def test_parse_keyed_list():
    output = """
    Available netdev backend types:
    socket
    passt
    user
    """
    assert parse_keyed_list(output, "Available netdev backend") == {"socket", "passt", "user"}


def test_settings_round_trip(tmp_path: Path):
    settings = AppSettings(last_used_profile="vm-1", recent_profiles=["vm-1"])
    path = tmp_path / "settings.toml"
    save_settings(type("P", (), {"settings_file": path})(), settings)
    loaded = load_settings(path)
    assert loaded.last_used_profile == "vm-1"
    assert loaded.recent_profiles == ["vm-1"]


def test_profile_round_trip(tmp_path: Path):
    class Paths:
        profiles_dir = tmp_path

    profile = VMProfile(name="Linux VM", architecture="x86_64", qemu_executable="/usr/bin/qemu-system-x86_64")
    path = save_profile(Paths(), profile)
    loaded = load_profile(path)
    assert loaded.name == "Linux VM"
    assert loaded.architecture == "x86_64"


def test_legacy_ini_migration(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    legacy_dir = tmp_path / "legacy"
    legacy_dir.mkdir()
    legacy_file = legacy_dir / "config.ini"
    legacy_file.write_text(
        "[VM]\nqemu_executable = /usr/bin/qemu-system-x86_64\ndisk_path = /tmp/test.qcow2\nenable_fullscreen = True\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("qemu_launcher.config.LEGACY_CONFIG_FILE", legacy_file)
    monkeypatch.setattr("qemu_launcher.config.LEGACY_CONFIG_DIR", legacy_dir)
    monkeypatch.setenv("QEMU_LAUNCHER_HOME", str(tmp_path / "app-home"))
    paths = AppPaths()
    migrated = migrate_legacy_ini(paths)
    assert migrated is not None
    settings, profiles = migrated
    assert settings.last_used_profile == profiles[0].profile_id
    assert profiles[0].disk_path == "/tmp/test.qcow2"


def test_linux_command_uses_linux_backends(tmp_path: Path):
    shared = tmp_path / "share"
    shared.mkdir()
    profile = VMProfile(
        name="Linux VM",
        architecture="x86_64",
        qemu_executable="/usr/bin/qemu-system-x86_64",
        disk_path="/tmp/disk.qcow2",
        firmware_path="/tmp/OVMF.fd",
        network_mode="auto",
        shared_dir_path=str(shared),
        sharing_backend="auto",
    )
    artifacts = RuntimeArtifacts(
        qmp_socket=tmp_path / "qmp.sock",
        pidfile=tmp_path / "qemu.pid",
        log_file=tmp_path / "qemu.log",
    )
    command = build_command(profile, fake_caps(), artifacts, host_platform="linux")
    cmd = " ".join(command)
    assert "-machine q35,accel=kvm:tcg" in cmd
    assert "-full-screen" in cmd
    assert "gtk,gl=on" in cmd
    assert "pipewire" in cmd
    assert "passt,id=net0" in cmd
    assert "cocoa" not in cmd
    assert "coreaudio" not in cmd
    assert "hvf" not in cmd
    assert "virtio-9p-pci" in cmd


def test_macos_command_uses_macos_backends(tmp_path: Path):
    profile = VMProfile(
        name="macOS VM",
        architecture="aarch64",
        qemu_executable="/opt/homebrew/bin/qemu-system-aarch64",
        disk_path="/tmp/disk.qcow2",
        firmware_path="/tmp/edk2.fd",
        network_mode="vmnet-shared",
        sharing_backend="none",
    )
    caps = fake_caps(
        executable="/opt/homebrew/bin/qemu-system-aarch64",
        accelerators={"hvf", "tcg"},
        displays={"cocoa"},
        audio_drivers={"coreaudio"},
        netdev_backends={"user", "vmnet-shared"},
        has_passt=False,
    )
    artifacts = RuntimeArtifacts(
        qmp_socket=tmp_path / "qmp.sock",
        pidfile=tmp_path / "qemu.pid",
        log_file=tmp_path / "qemu.log",
    )
    command = build_command(profile, caps, artifacts, host_platform="darwin")
    cmd = " ".join(command)
    assert "-machine virt,accel=hvf:tcg" in cmd
    assert "-full-screen" in cmd
    assert "cocoa,show-cursor=on,zoom-to-fit=on,left-command-key=on,full-grab=on" in cmd
    assert "coreaudio,id=snd0" in cmd
    assert "vmnet-shared,id=net0" in cmd
    assert "gtk" not in cmd
    assert "pipewire" not in cmd
    assert "kvm" not in cmd


def test_non_primary_display_uses_post_launch_fullscreen(tmp_path: Path):
    profile = VMProfile(
        name="Linux VM",
        architecture="x86_64",
        qemu_executable="/usr/bin/qemu-system-x86_64",
        disk_path="/tmp/disk.qcow2",
        target_display_name="Projector",
        enable_fullscreen=True,
    )
    artifacts = RuntimeArtifacts(
        qmp_socket=tmp_path / "qmp.sock",
        pidfile=tmp_path / "qemu.pid",
        log_file=tmp_path / "qemu.log",
    )
    command = build_command(profile, fake_caps(), artifacts, host_platform="linux")
    assert "-full-screen" not in command
    assert should_qemu_handle_fullscreen(profile.target_display_name, profile.enable_fullscreen) is False


def test_virtiofs_requires_socket(tmp_path: Path):
    shared = tmp_path / "share"
    shared.mkdir()
    profile = VMProfile(
        qemu_executable="/usr/bin/qemu-system-x86_64",
        disk_path="/tmp/disk.qcow2",
        shared_dir_path=str(shared),
        sharing_backend="virtiofs",
    )
    artifacts = RuntimeArtifacts(
        qmp_socket=tmp_path / "qmp.sock",
        pidfile=tmp_path / "qemu.pid",
        log_file=tmp_path / "qemu.log",
    )
    with pytest.raises(Exception):
        build_command(profile, fake_caps(has_virtiofsd=True), artifacts, host_platform="linux")


def test_shared_folder_must_exist(tmp_path: Path):
    profile = VMProfile(
        qemu_executable="/usr/bin/qemu-system-x86_64",
        disk_path="/tmp/disk.qcow2",
        shared_dir_path=str(tmp_path / "missing-share"),
        sharing_backend="9p",
    )
    artifacts = RuntimeArtifacts(
        qmp_socket=tmp_path / "qmp.sock",
        pidfile=tmp_path / "qemu.pid",
        log_file=tmp_path / "qemu.log",
    )
    with pytest.raises(Exception):
        build_command(profile, fake_caps(), artifacts, host_platform="linux")


def test_resolve_sharing_mount_hint(tmp_path: Path):
    shared = tmp_path / "share"
    shared.mkdir()
    profile = VMProfile(
        qemu_executable="/usr/bin/qemu-system-x86_64",
        disk_path="/tmp/disk.qcow2",
        shared_dir_path=str(shared),
        sharing_backend="9p",
        mount_tag="host_share",
    )
    mode, mount_help = resolve_sharing(profile, fake_caps())
    assert mode == "9p"
    assert "mount -t 9p" in mount_help
    assert "/mnt/host_share" in mount_help


def test_profile_readiness_reports_missing_basics(tmp_path: Path):
    profile = VMProfile()
    issues, highlights, notes = profile_readiness(profile, fake_caps())
    assert "Choose a QEMU binary." in issues
    assert "Choose a disk image." in issues
    assert any("No shared folder configured" in note for note in notes)
    assert not highlights


def test_profile_readiness_highlights_fullscreen_and_share(tmp_path: Path):
    shared = tmp_path / "share"
    shared.mkdir()
    disk = tmp_path / "disk.qcow2"
    disk.write_text("", encoding="utf-8")
    qemu_bin = tmp_path / "qemu-system-x86_64"
    qemu_bin.write_text("", encoding="utf-8")
    profile = VMProfile(
        qemu_executable=str(qemu_bin),
        disk_path=str(disk),
        shared_dir_path=str(shared),
        enable_fullscreen=True,
        target_display_name="Projector",
    )
    issues, highlights, notes = profile_readiness(profile, fake_caps())
    assert not issues
    assert any("Fullscreen target: Projector" == item for item in highlights)
    assert any("Shared folder:" in item for item in highlights)
    assert any("Non-primary display fullscreen" in note for note in notes)


def test_probe_qemu_uses_real_binary():
    executable = find_default_qemu("x86_64")
    if not executable:
        pytest.skip("qemu-system-x86_64 not installed")
    caps = probe_qemu(executable)
    assert caps.version
    assert "kvm" in caps.accelerators or "tcg" in caps.accelerators
    assert "user" in caps.netdev_backends


def test_qmp_smoke(tmp_path: Path):
    executable = find_default_qemu("x86_64")
    if not executable:
        pytest.skip("qemu-system-x86_64 not installed")
    process = subprocess.Popen(
        [
            executable,
            "-machine",
            "q35,accel=tcg",
            "-display",
            "none",
            "-nodefaults",
            "-S",
            "-qmp",
            "stdio",
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert process.stdout is not None
        assert process.stdin is not None
        greeting = process.stdout.readline()
        assert "QMP" in greeting
        process.stdin.write('{"execute":"qmp_capabilities"}\n')
        process.stdin.flush()
        capabilities = process.stdout.readline()
        assert '"return"' in capabilities
        process.stdin.write('{"execute":"query-status"}\n')
        process.stdin.flush()
        status_line = process.stdout.readline()
        status = json.loads(status_line)["return"]
        assert status["status"] in {"prelaunch", "paused", "running"}
        process.stdin.write('{"execute":"quit"}\n')
        process.stdin.flush()
    finally:
        process.wait(timeout=10)


def test_vm_controller_preview_paths(tmp_path: Path):
    profile = VMProfile(
        profile_id="vm-test",
        qemu_executable="/usr/bin/qemu-system-x86_64",
        disk_path="/tmp/disk.qcow2",
    )
    controller = VMController(DummyPaths(tmp_path), profile)
    preview = controller.preview_command()
    joined = " ".join(preview)
    assert "qmp.sock" in joined
    assert "qemu.pid" in joined
    assert "virtio-net-pci" in joined


def test_vm_controller_detects_running_pid(tmp_path: Path):
    profile = VMProfile(
        profile_id="vm-test",
        qemu_executable="/usr/bin/qemu-system-x86_64",
        disk_path="/tmp/disk.qcow2",
    )
    controller = VMController(DummyPaths(tmp_path), profile)
    controller.artifacts.pidfile.write_text(str(os.getpid()), encoding="utf-8")
    assert controller.is_running() is True
