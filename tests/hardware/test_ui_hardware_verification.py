import os
import subprocess
import sys
import time


def run_applescript(script):
    result = subprocess.run(['osascript', '-e', script], capture_output=True, text=True)
    return result.stdout.strip(), result.stderr.strip()

def test_ui_workflow():
    print("--- STARTING HARDWARE UI VERIFICATION ---")
    
    app_path = "/Users/jeremiahsummers/Work/git/Python/QEMULauncher/QEMU Launcher.app"
    if not os.path.exists(app_path):
        print(f"ERROR: App bundle not found at {app_path}")
        sys.exit(1)

    # 1. Ensure app is closed
    print("-> Closing existing instances...")
    subprocess.run(["pkill", "-9", "QEMU Launcher"], capture_output=True)
    subprocess.run(["pkill", "-9", "qemu-system"], capture_output=True)
    time.sleep(2)

    # 2. Launch the app
    print(f"-> Launching {app_path}...")
    subprocess.run(["open", app_path])
    time.sleep(5)

    # 3. Verify Main Window via System Events
    print("-> Verifying Main Window presence...")
    script = 'tell application "System Events" to tell process "QEMU Launcher" to count windows'
    count, err = run_applescript(script)
    if count == "0":
        print(f"FAILED: Main window not found. Error: {err}")
        sys.exit(1)
    print(f"SUCCESS: Found {count} window(s).")

    # 4. Verify Buttons
    print("-> Checking for UI elements (scanning hierarchy)...")
    script = '''
    tell application "System Events"
        tell process "QEMU Launcher"
            set allButtons to every button of every window
            set buttonNames to {}
            repeat with b in allButtons
                copy name of b to end of buttonNames
            end repeat
            return buttonNames
        end tell
    end tell
    '''
    buttons, _ = run_applescript(script)
    print(f"Found buttons: {buttons}")
    
    # 5. Simulate Launch Click
    print("-> Clicking 'Launch' button...")
    script = 'tell application "System Events" to tell process "QEMU Launcher" to click (first button of (first window whose name is "QEMU Launcher") whose name is "Launch")'
    _, err = run_applescript(script)
    if err:
        print(f"ERROR clicking button: {err}")
        # Try finding by index if name fails
        run_applescript('tell application "System Events" to tell process "QEMU Launcher" to click button 1 of (first window whose name is "QEMU Launcher")')

    # 6. Detect Authorization Prompt with 3-minute timeout
    print("--- WAITING FOR USER PASSWORD INPUT (3 MINUTE TIMEOUT) ---")
    start_time = time.time()
    timeout = 180  # 3 minutes
    prompt_authorized = False
    
    while time.time() - start_time < timeout:
        # Check if SecurityAgent is active
        script = 'tell application "System Events" to count (every process whose name is "SecurityAgent" or name is "CoreServicesUIAgent")'
        count, _ = run_applescript(script)
        
        # Check if helper was installed (means user finished)
        if os.path.exists("/usr/local/bin/qemu-launcher-helper"):
            print("\nSUCCESS: Helper detected! Authorization complete.")
            prompt_authorized = True
            break
            
        remaining = int(timeout - (time.time() - start_time))
        sys.stdout.write(f"\rWaiting for interaction... {remaining}s remaining   ")
        sys.stdout.flush()
        time.sleep(2)
    
    if not prompt_authorized:
        print("\nTIMEOUT: No interaction detected after 3 minutes. Skipping networking test.")

    # 7. Verify VM Process Start
    print("-> Verifying VM execution...")
    qemu_started = False
    for _ in range(20):
        result = subprocess.run(["pgrep", "qemu-system"], capture_output=True)
        if result.returncode == 0:
            qemu_started = True
            break
        time.sleep(1)
    
    if qemu_started:
        print("SUCCESS: QEMU process is running.")
    else:
        print("FAILED: QEMU process failed to start.")
        # Check app logs
        sys.exit(1)

    # 8. Verify Window Position (Final test of display.py logic)
    print("-> Verifying VM window placement...")
    script = 'tell application "System Events" to tell process "qemu-system-aarch64" to get position of window 1'
    pos, _ = run_applescript(script)
    print(f"VM Window Position: {pos}")

    print("--- HARDWARE UI VERIFICATION COMPLETE ---")

if __name__ == "__main__":
    test_ui_workflow()
