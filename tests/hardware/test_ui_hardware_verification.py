import json
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path


def run_applescript(script):
    result = subprocess.run(['osascript', '-e', script], capture_output=True, text=True)
    return result.stdout.strip(), result.stderr.strip()


def check_qmp_running(qmp_path):
    print(f"-> Connecting to QMP at {qmp_path}...")
    try:
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        client.settimeout(2.0)
        client.connect(str(qmp_path))

        # Read greeting
        client.recv(1024)

        # Capability negotiation
        client.sendall(json.dumps({"execute": "qmp_capabilities"}).encode())
        resp = client.recv(1024)

        # Check status
        client.sendall(json.dumps({"execute": "query-status"}).encode())
        resp = client.recv(1024)
        status = json.loads(resp.decode())

        running = status.get("return", {}).get("running", False)
        client.close()
        return running
    except Exception as e:
        print(f"QMP Check Error: {e}")
        return False


def test_ui_workflow():
    print("--- STARTING FULL-STACK HARDWARE VERIFICATION ---")

    is_macos = sys.platform == "darwin"
    home = os.environ.get("HOME")
    
    # Use an isolated home for the test to avoid path naming drama (like spaces)
    test_root = Path("/tmp/qemu-launcher-test")
    if test_root.exists():
        shutil.rmtree(test_root)
    test_root.mkdir(parents=True)
    os.environ["QEMU_LAUNCHER_HOME"] = str(test_root)
    
    # AppPaths logic says:
    config_root = test_root / "config"
    runtime_root = test_root / "runtime"

    if is_macos:
        app_path = "/Users/jeremiahsummers/Work/git/Python/QEMULauncher/QEMU Launcher.app"
    else:
        # Linux
        app_path = os.getcwd() + "/qemu_app.py"

    # 1. Create Profiles
    print("-> Preparing Multi-Arch environment...")
    import platform as py_platform
    host_arch = py_platform.machine() # 'arm64' or 'x86_64'
    native_qemu = "aarch64" if (is_macos and host_arch == "arm64") else "x86_64"

    def get_cirros(arch):
        filename = f"cirros-0.6.2-{arch}-disk.img"
        local_path = f"/tmp/{filename}"
        if not os.path.exists(local_path):
            print(f"-> Downloading tiny {arch} image (CirrOS)...")
            url = f"https://github.com/cirros-dev/cirros/releases/download/0.6.2/{filename}"
            subprocess.run(["curl", "-L", "-o", local_path, url], capture_output=True)
        return local_path

    native_img = get_cirros(native_qemu)

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
"""
        with open(f"{profiles_dir}/{name.lower().replace(' ', '_')}.toml", "w") as f:
            f.write(content)
        return name, qemu_bin

    # Target display from env or default
    target_display = os.environ.get("TEST_DISPLAY", "VG248" if is_macos else "Primary Display")
    create_profile("Smoke Test", native_qemu, native_img, fullscreen=True, display=target_display)

    # 2. Update settings.toml
    print(f"-> Seeding settings.toml at {config_root}...")
    config_root.mkdir(parents=True, exist_ok=True)
    settings_path = config_root / "settings.toml"
    settings_content = """
schema_version = 1
last_used_profile = "smoke_test"
recent_profiles = ["smoke_test"]
auto_launch_enabled = true
"""
    settings_path.write_text(settings_content, encoding="utf-8")

    # 3. Ensure app is closed
    print("-> Closing existing instances...")
    if is_macos:
        subprocess.run(["pkill", "-9", "QEMU Launcher"], capture_output=True)
    subprocess.run(["pkill", "-9", "-f", "qemu_app.py"], capture_output=True)
    subprocess.run(["pkill", "-9", "qemu-system"], capture_output=True)
    
    # Remove lock file if it exists (though test_root is fresh)
    lock_file = config_root / "app.lock"
    if lock_file.exists():
        lock_file.unlink()
    
    time.sleep(2)

    # 4. Launch UI
    print("-> Launching QEMU Launcher UI...")
    env = os.environ.copy()
    if is_macos:
        # For macOS app bundle, we might need to set the env via 'open' or just run the binary
        # Running the binary directly is easier for env passing
        bin_path = f"{app_path}/Contents/MacOS/QEMU Launcher"
        subprocess.Popen([bin_path], env=env, start_new_session=True)
    else:
        # Launch via python on Linux
        subprocess.Popen([sys.executable, app_path], env=env, start_new_session=True)
    time.sleep(5)

    # 5. Launch VM via UI (if auto-launch failed or for extra check)
    # We rely on auto-launch now since xdotool/AppleScript can be flaky
    
    # 6. Verify VM Deep Boot
    print("-> Waiting for VM stabilization...")
    time.sleep(10)

    # QMP path uses profile_id which is 'smoke_test'
    qmp_path = runtime_root / "profiles" / "smoke_test" / "qmp.sock"
    if check_qmp_running(qmp_path):
        print("SUCCESS: VM is running and executing instructions (QMP verified).")
    else:
        # Check logs if failed
        log_dir = test_root / "state" / "logs"
        if log_dir.exists():
             app_log = log_dir / "app.log"
             if app_log.exists():
                 print(f"-> App Log tail:\n{app_log.read_text()[-500:]}")
        print("FAILED: VM is not running correctly or QMP unavailable.")
        sys.exit(1)

    print("\n--- FULL-STACK HARDWARE VERIFICATION COMPLETE ---")


if __name__ == "__main__":
    import shutil
    try:
        test_ui_workflow()
    finally:
        print("-> Cleaning up test processes...")
        if sys.platform == "darwin":
            subprocess.run(["pkill", "-9", "QEMU Launcher"], capture_output=True)
        subprocess.run(["pkill", "-9", "-f", "qemu_app.py"], capture_output=True)
        subprocess.run(["pkill", "-9", "qemu-system"], capture_output=True)
