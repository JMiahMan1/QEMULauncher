import os
import subprocess
import sys
import time


def run_applescript(script):
    result = subprocess.run(['osascript', '-e', script], capture_output=True, text=True)
    return result.stdout.strip(), result.stderr.strip()


def test_ui_workflow():
    print("--- STARTING MULTI-ARCH HARDWARE VERIFICATION ---")
    
    app_path = "/Users/jeremiahsummers/Work/git/Python/QEMULauncher/QEMU Launcher.app"
    if not os.path.exists(app_path):
        print(f"ERROR: App bundle not found at {app_path}")
        sys.exit(1)

    # 1. Create Native and Cross-Arch Profiles
    print("-> Preparing Multi-Arch environment...")
    import platform as py_platform
    host_arch = py_platform.machine() # 'arm64' or 'x86_64'
    native_qemu = "aarch64" if host_arch == "arm64" else "x86_64"
    cross_qemu = "x86_64" if host_arch == "arm64" else "aarch64"
    
    test_disk = "/tmp/smoke.qcow2"
    if not os.path.exists(test_disk):
        subprocess.run(["qemu-img", "create", "-f", "qcow2", test_disk, "1M"], capture_output=True)

    home = os.environ.get("HOME")
    profiles_dir = f"{home}/Library/Application Support/QEMU Launcher/profiles"
    os.makedirs(profiles_dir, exist_ok=True)

    def create_profile(name, arch):
        qemu_bin = f"/opt/homebrew/bin/qemu-system-{arch}"
        if not os.path.exists(qemu_bin):
             qemu_bin = f"/usr/local/bin/qemu-system-{arch}"
        
        content = f"""
name = "{name}"
architecture = "{arch}"
qemu_executable = "{qemu_bin}"
disk_path = "{test_disk}"
memory_mib = 512
cpu_cores = 1
network_mode = "user"
enable_fullscreen = false
"""
        with open(f"{profiles_dir}/{name.lower().replace(' ', '_')}.toml", "w") as f:
            f.write(content)
        return name, qemu_bin

    native_name, native_bin = create_profile("Native VM", native_qemu)
    cross_name, cross_bin = create_profile("Cross VM", cross_qemu)

    def run_verification(target_name, expected_bin):
        print(f"\n--- VERIFYING {target_name} ({expected_bin}) ---")
        # Ensure app is closed
        subprocess.run(["pkill", "-9", "QEMU Launcher"], capture_output=True)
        subprocess.run(["pkill", "-9", "qemu-system"], capture_output=True)
        time.sleep(2)

        # Launch
        subprocess.run(["open", app_path])
        time.sleep(5)
        
        # Launch via CLI to verify the backend logic correctly consumes the profile
        print(f"-> Launching {target_name} via CLI to verify logic...")
        cli_exe = f"{app_path}/Contents/MacOS/QEMU Launcher"
        subprocess.Popen([cli_exe, "--launch", "--profile", target_name])
        time.sleep(8)

        # Verify Process
        result = subprocess.run(["pgrep", "-f", expected_bin], capture_output=True)
        if result.returncode == 0:
            print(f"SUCCESS: {target_name} is running with {expected_bin}")
        else:
            print(f"FAILED: {target_name} is NOT running with {expected_bin}")
            # Try to get logs
            sys.exit(1)

    # Test both
    run_verification(native_name, native_bin)
    run_verification(cross_name, cross_bin)

    print("\n--- MULTI-ARCH VERIFICATION COMPLETE ---")


if __name__ == "__main__":
    test_ui_workflow()
