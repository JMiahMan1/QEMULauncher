import json
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

# Global test root for cleanup access
TEST_ROOT = Path("/tmp/qemu-launcher-test")


def run_applescript(script):
    result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    return result.stdout.strip(), result.stderr.strip()


def check_qmp_running(qmp_path):
    print(f"-> Connecting to QMP at {qmp_path}...")
    for _ in range(3):
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                client.settimeout(2.0)
                client.connect(str(qmp_path))
                with client.makefile("rw", encoding="utf-8") as f:
                    # Read greeting
                    greeting = f.readline()
                    if not greeting:
                        time.sleep(1)
                        continue

                    # Capability negotiation
                    f.write(json.dumps({"execute": "qmp_capabilities"}) + "\n")
                    f.flush()
                    for line in f:
                        if "return" in json.loads(line.strip()):
                            break

                    # Check status
                    f.write(json.dumps({"execute": "query-status"}) + "\n")
                    f.flush()

                    for line in f:
                        status = json.loads(line.strip())
                        if "return" in status:
                            return status.get("return", {}).get("running", False)

            return False
        except Exception as e:
            print(f"QMP Check Error: {e}")
            time.sleep(1)
    return False


def cleanup_all():
    print("-> Cleaning up all test processes (Surgical + Scoped)...")
    # 1. Surgical Kill via PID files
    profiles_runtime = TEST_ROOT / "runtime" / "profiles"
    if profiles_runtime.exists():
        for pid_file in profiles_runtime.glob("**/qemu.pid"):
            try:
                pid = int(pid_file.read_text().strip())
                print(f"-> Killing QEMU PID {pid}...")
                os.kill(pid, 9)
            except Exception:
                pass

    # 2. Kill anything related to this test module or temporary path, EXCEPT ourselves
    current_pid = os.getpid()

    # Use pgrep to find processes referencing our TEST_ROOT
    try:
        pids = subprocess.check_output(["pgrep", "-f", str(TEST_ROOT)]).decode().split()
        for pid_str in pids:
            pid = int(pid_str)
            if pid != current_pid:
                try:
                    os.kill(pid, 9)
                except Exception:
                    pass
    except subprocess.CalledProcessError:
        pass

    time.sleep(1)


@pytest.mark.skipif(os.environ.get("CI") == "true", reason="Hardware UI tests require a physical graphical session.")
def test_ui_workflow():
    is_macos = sys.platform == "darwin"

    try:
        if is_macos:
            # Check if WindowServer is accessible
            try:
                from AppKit import NSScreen

                if not NSScreen.screens():
                    print("-> No screens detected (Headless CI?). Skipping hardware UI test.")
                    return
            except Exception:
                print("-> AppKit failed (Headless CI?). Skipping hardware UI test.")
                return

        print("--- STARTING FULL-STACK HARDWARE VERIFICATION ---")
        if TEST_ROOT.exists():
            shutil.rmtree(TEST_ROOT)
        TEST_ROOT.mkdir(parents=True)
        os.environ["QEMU_LAUNCHER_HOME"] = str(TEST_ROOT)

        config_root = TEST_ROOT / "config"
        runtime_root = TEST_ROOT / "runtime"
        state_dir = TEST_ROOT / "state"

        # 1. Create Profiles
        print("-> Preparing Multi-Arch environment...")
        import platform as py_platform

        host_arch = py_platform.machine()
        native_qemu = "aarch64" if (is_macos and host_arch == "arm64") else "x86_64"

        def get_alpine_test_image(arch):
            variant = "bios-tiny" if arch == "x86_64" else "uefi-tiny"
            filename = f"oci_alpine-3.19.9-{arch}-{variant}-r0.qcow2"
            local_path = f"/tmp/{filename}"
            if not os.path.exists(local_path):
                print(f"-> Downloading minimal Alpine {arch} image...")
                url = f"https://dl-cdn.alpinelinux.org/alpine/v3.19/releases/cloud/{filename}"
                subprocess.run(["curl", "-L", "-o", local_path, url], capture_output=True)
            return local_path

        native_img = get_alpine_test_image(native_qemu)
        profiles_dir = config_root / "profiles"
        profiles_dir.mkdir(parents=True, exist_ok=True)

        def create_profile(name, arch, disk, fullscreen=False, display=""):
            if is_macos:
                qemu_bin = f"/opt/homebrew/bin/qemu-system-{arch}"
                if not os.path.exists(qemu_bin):
                    qemu_bin = f"/usr/local/bin/qemu-system-{arch}"
            else:
                qemu_bin = shutil.which(f"qemu-system-{arch}") or f"/usr/bin/qemu-system-{arch}"

            machine = "virt" if arch == "aarch64" else "q35"
            accel = "hvf" if is_macos else "kvm"
            cpu = "host" if is_macos else "host"

            # UEFI for aarch64 on macOS
            firmware_arg = ""
            if is_macos and arch == "aarch64":
                uefi_path = "/opt/homebrew/share/qemu/edk2-aarch64-code.fd"
                if os.path.exists(uefi_path):
                    firmware_arg = f'\nextra_args = ["-drive", "if=pflash,format=raw,readonly=on,file={uefi_path}", "-serial", "file:{TEST_ROOT}/serial.log"]'

            if not firmware_arg:
                firmware_arg = f'\nextra_args = ["-serial", "file:{TEST_ROOT}/serial.log"]'

            display_backend = "cocoa" if is_macos else "auto"
            content = f"""
name = "{name}"
architecture = "{arch}"
machine = "{machine}"
qemu_executable = "{qemu_bin}"
disk_path = "{disk}"
memory_mib = 512
cpu_cores = 1
network_mode = "user"
enable_audio = false
enable_fullscreen = {"true" if fullscreen else "false"}
target_display_name = "{display}"
display_backend = "{display_backend}"
{firmware_arg}
"""
            with open(f"{profiles_dir}/{name.lower().replace(' ', '_')}.toml", "w") as f:
                f.write(content)
            return name, qemu_bin

        target_display = os.environ.get("TEST_DISPLAY", "VG248" if is_macos else "Primary Display")
        create_profile("Smoke Test", native_qemu, native_img, fullscreen=True, display=target_display)

        # 2. Update settings.toml
        config_root.mkdir(parents=True, exist_ok=True)
        settings_path = config_root / "settings.toml"
        settings_path.write_text(
            """
schema_version = 1
last_used_profile = "smoke_test"
recent_profiles = ["smoke_test"]
auto_launch_enabled = true
""",
            encoding="utf-8",
        )

        # 3. Ensure app is closed
        cleanup_all()

        # 4. Launch UI
        print("-> Launching QEMU Launcher UI...")
        env = os.environ.copy()
        env["PYTHONPATH"] = os.getcwd() + (":" + env.get("PYTHONPATH", "") if env.get("PYTHONPATH") else "")

        subprocess.Popen(
            [
                sys.executable,
                "-m",
                "qemu_launcher.app",
                "--config",
                str(config_root),
                "--state",
                str(state_dir),
                "--runtime",
                str(runtime_root),
            ],
            env=env,
            start_new_session=True,
        )

        time.sleep(5)

        # 5. Verify VM Boot
        print("-> Waiting for VM to initialize (5s)...")
        time.sleep(5)

        qmp_path = runtime_root / "profiles" / "smoke_test" / "qmp.sock"
        if qmp_path.exists():
            print("-> Sending 'Return' key to bypass bootloader...")
            try:
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                    client.settimeout(2.0)
                    client.connect(str(qmp_path))
                    with client.makefile("rw", encoding="utf-8") as f:
                        f.readline()  # Greeting

                        f.write(json.dumps({"execute": "qmp_capabilities"}) + "\n")
                        f.flush()
                        for line in f:
                            if "return" in json.loads(line.strip()):
                                break

                        key_event = {
                            "execute": "input-send-event",
                            "arguments": {
                                "events": [
                                    {"type": "key", "data": {"down": True, "key": {"type": "qcode", "data": "ret"}}},
                                    {"type": "key", "data": {"down": False, "key": {"type": "qcode", "data": "ret"}}},
                                ]
                            },
                        }
                        f.write(json.dumps(key_event) + "\n")
                        f.flush()
            except Exception as e:
                print(f"-> Failed to send Return key: {e}")

        print("-> Waiting for OS to fully boot...")
        serial_log = TEST_ROOT / "serial.log"
        booted = False
        for i in range(60):
            if serial_log.exists():
                content = serial_log.read_text()
                if "login:" in content.lower():
                    print(f"SUCCESS: OS fully booted in {i} seconds.")
                    booted = True
                    break
            time.sleep(1)

        if not booted:
            pytest.fail("OS failed to reach login prompt.")

        if check_qmp_running(qmp_path):
            print("SUCCESS: VM is running and QMP verified.")
        else:
            pytest.fail("QMP verification failed.")

        print("\n--- FULL-STACK HARDWARE VERIFICATION COMPLETE ---")

    finally:
        cleanup_all()


if __name__ == "__main__":
    test_ui_workflow()
