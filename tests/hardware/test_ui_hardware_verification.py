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

    # 1. Create Native and Cross-Arch Profiles with real tiny images
    print("-> Preparing Live-Image Multi-Arch environment...")
    import platform as py_platform
    host_arch = py_platform.machine() # 'arm64' or 'x86_64'
    native_qemu = "aarch64" if host_arch == "arm64" else "x86_64"
    cross_qemu = "x86_64" if host_arch == "arm64" else "aarch64"
    
    def get_cirros(arch):
        url_arch = "aarch64" if arch == "aarch64" else "x86_64"
        # Map arch to cirros filenames
        cirros_arch = "aarch64" if arch == "aarch64" else "x86_64"
        filename = f"cirros-0.6.2-{cirros_arch}-disk.img"
        local_path = f"/tmp/{filename}"
        if not os.path.exists(local_path):
            print(f"-> Downloading tiny {arch} image (CirrOS)...")
            url = f"https://github.com/cirros-dev/cirros/releases/download/0.6.2/{filename}"
            subprocess.run(["curl", "-L", "-o", local_path, url], capture_output=True)
        return local_path

    native_img = get_cirros(native_qemu)
    cross_img = get_cirros(cross_qemu)

    home = os.environ.get("HOME")
    profiles_dir = f"{home}/Library/Application Support/QEMU Launcher/profiles"
    os.makedirs(profiles_dir, exist_ok=True)

    def create_profile(name, arch, disk):
        qemu_bin = f"/opt/homebrew/bin/qemu-system-{arch}"
        if not os.path.exists(qemu_bin):
             qemu_bin = f"/usr/local/bin/qemu-system-{arch}"
        
        # CirrOS needs a machine type that supports PCI
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
enable_fullscreen = false
"""
        with open(f"{profiles_dir}/{name.lower().replace(' ', '_')}.toml", "w") as f:
            f.write(content)
        return name, qemu_bin

    native_name, native_bin = create_profile("Native VM", native_qemu, native_img)
    cross_name, cross_bin = create_profile("Cross VM", cross_qemu, cross_img)

    def run_verification(target_name, expected_bin):
        print(f"\n--- VERIFYING {target_name} ({expected_bin}) ---")
        # Ensure app is closed
        subprocess.run(["pkill", "-9", "QEMU Launcher"], capture_output=True)
        subprocess.run(["pkill", "-9", "qemu-system"], capture_output=True)
        time.sleep(2)

        # Launch via CLI and capture output to see why it might be failing
        print(f"-> Launching {target_name} via CLI to verify logic...")
        cli_exe = f"{app_path}/Contents/MacOS/QEMU Launcher"
        
        try:
            # Run with a short timeout to see if it crashes immediately
            result = subprocess.run(
                [cli_exe, "--launch", "--profile", target_name],
                capture_output=True,
                text=True,
                timeout=5
            )
            print(f"Launcher Output: {result.stdout}")
            print(f"Launcher Error: {result.stderr}")
        except subprocess.TimeoutExpired as e:
            # If it times out, it means it's probably running (which is good)
            print("Launcher still running (expected)...")
        except Exception as e:
            print(f"Launcher execution failed: {e}")

        # Wait for QEMU to stabilize
        time.sleep(10)
    
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
