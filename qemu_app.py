#!/usr/bin/env python3
import argparse
import configparser
import json
import os
import subprocess
import sys
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

try:
    import AppKit
except ImportError:
    AppKit = None

# ================================================================
# CONFIGURATION FILE
# ================================================================
CONFIG_DIR = Path.home() / ".config" / "qemu_launcher"
CONFIG_FILE = CONFIG_DIR / "config.ini"
SETUP_COMPLETE_FILE = CONFIG_DIR / ".setup_complete"

# ================================================================
# DEBUG FLAG
# ================================================================
DEBUG = "--debug" in sys.argv

def debug_print(*args, **kwargs):
    if DEBUG:
        print("[DEBUG]", *args, **kwargs)

# ================================================================
# UTILITY FUNCTIONS
# ================================================================
def get_screen_count():
    if not AppKit: return 1
    try: return len(AppKit.NSScreen.screens())
    except Exception: return 1


def validate_qemu_executable(executable_path):
    if not executable_path or not os.path.exists(executable_path): return False, "Executable file not found"
    try:
        result = subprocess.run([executable_path, "--version"], capture_output=True, text=True, timeout=3)
        if result.returncode == 0: return True, ""
        return False, f"QEMU exited with an error:\n{result.stderr.strip()}"
    except Exception as e: return False, f"An unexpected validation error occurred: {e}"

# --- MODIFICATION: Restored the SDL support check ---
def check_sdl_support(qemu_executable):
    """Check if SDL audio backend is available in QEMU."""
    try:
        result = subprocess.run(
            [qemu_executable, "-audiodev", "help"],
            capture_output=True, text=True, check=True, timeout=5
        )
        debug_print("Available audio backends:\n", result.stdout)
        return "sdl" in result.stdout.lower()
    except Exception as e:
        debug_print("SDL support check failed:", e)
        return False

def get_smart_defaults(for_arch=None):
    defaults = {'qemu_executable': '', 'firmware_path': '', 'arch': '', 'shared_dir_path': str(Path.home() / "Documents"), 'mount_tag': 'host_share', 'network_mode': 'user'}
    try:
        defaults['arch'] = for_arch or ('aarch64' if os.uname().machine == 'arm64' else 'x86_64')
        # Check if brew exists before calling it
        if subprocess.run(['which', 'brew'], capture_output=True).returncode == 0:
            prefix = subprocess.check_output(['brew', '--prefix']).decode('utf-8').strip()
            qemu_path = Path(prefix) / "bin" / f"qemu-system-{defaults['arch']}"
            firmware_path = Path(prefix) / "share" / "qemu" / f"edk2-{defaults['arch']}-code.fd"
            if qemu_path.is_file(): defaults['qemu_executable'] = str(qemu_path)
            if firmware_path.is_file(): defaults['firmware_path'] = str(firmware_path)
    except Exception: pass
    return defaults

def load_config(config_path=None):
    path = Path(config_path) if config_path else CONFIG_FILE
    config = configparser.ConfigParser()
    if not path.is_file(): return None
    config.read(path)
    return {
        'arch': config.get('VM', 'arch', fallback='aarch64'),
        'qemu_executable': config.get('VM', 'qemu_executable', fallback=''), 'disk_path': config.get('VM', 'disk_path', fallback=''),
        'firmware_path': config.get('VM', 'firmware_path', fallback=''), 'shared_dir_path': config.get('VM', 'shared_dir_path', fallback=str(Path.home() / "Documents")),
        'mount_tag': config.get('VM', 'mount_tag', fallback='host_share'), 'enable_webcam': config.getboolean('VM', 'enable_webcam', fallback=False),
        'network_mode': config.get('VM', 'network_mode', fallback='user'), 'bridge_name': config.get('VM', 'bridge_name', fallback='bridge100'),
        'enable_guest_agent': config.getboolean('VM', 'enable_guest_agent', fallback=False),
        'enable_microphone': config.getboolean('VM', 'enable_microphone', fallback=False),
        'enable_fullscreen': config.getboolean('VM', 'enable_fullscreen', fallback=True)
    }

def validate_command_integrity(command):
    """Checks the generated QEMU command for common errors or conflicts."""
    issues = []
    
    # Check for multiple drives sharing the same index/unit without explicit setting
    pflash_count = 0
    for i, arg in enumerate(command):
        if arg == "-drive":
            params = command[i+1]
            if "if=pflash" in params:
                pflash_count += 1
                if pflash_count > 2:
                    issues.append("Error: Too many pflash drives defined (max 2 for ARM virt).")
            
    # Check for missing required components if it were a real run
    if "-M" not in command:
        issues.append("Warning: Machine type (-M) not specified.")
        
    return issues

def save_config(values):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    config = configparser.ConfigParser()
    config['VM'] = {k: str(v) for k, v in values.items()}
    with open(CONFIG_FILE, 'w') as f: config.write(f)

def show_error(title, message):
    root = tk.Tk(); root.withdraw()
    messagebox.showerror(title, message)
    root.destroy()

# ================================================================
# QEMU LAUNCHER
# ================================================================
def get_display_info():
    """Detects available displays using native AppKit for maximum reliability."""
    if not AppKit:
        return [{'x': 0, 'y': 0, 'width': 1920, 'height': 1080}]
    
    try:
        screens = AppKit.NSScreen.screens()
        displays = []
        # Primary screen is index 0. Coordinates are in points.
        # macOS origin is bottom-left, but System Events uses top-left.
        primary_height = screens[0].frame().size.height
        
        for s in screens:
            f = s.frame()
            displays.append({
                'x': int(f.origin.x),
                'y': int(primary_height - (f.origin.y + f.size.height)), # Convert to top-left origin
                'width': int(f.size.width),
                'height': int(f.size.height)
            })
        return displays
    except Exception as e:
        debug_print(f"AppKit display detection failed: {e}")
        return [{'x': 0, 'y': 0, 'width': 1920, 'height': 1080}]

def move_qemu_to_screen(screen_index=1, fullscreen=True):
    """Moves the QEMU window to a specific screen with proper coordinates."""
    displays = get_display_info()
    if screen_index >= len(displays):
        screen_index = 0
    
    target = displays[screen_index]
    x, y, w, h = target['x'], target['y'], target['width'], target['height']

    script = f'''
    tell application "System Events"
        repeat 30 times
            set qemuProcs to (every process whose name contains "qemu-system")
            if (count of qemuProcs) > 0 then
                set qemuProc to item 1 of qemuProcs
                set frontmost of qemuProc to true
                if (count of windows of qemuProc) > 0 then
                    set qemuWin to window 1 of qemuProc
                    -- Move and size first, then try fullscreen
                    set position of qemuWin to {{ {x}, {y} }}
                    set size of qemuWin to {{ {w}, {h} }}
                    if {str(fullscreen).lower()} then
                        delay 1.5
                        try
                            set value of attribute "AXFullScreen" of qemuWin to true
                        end try
                    end if
                    return true
                end if
            end if
            delay 0.5
        end repeat
    end tell
    '''
    subprocess.Popen(['osascript', '-e', script])

def run_launcher(config, dry_run=False):
    """Assembles and executes the QEMU command with focus on stability and display handling."""
    if not config or not config.get('disk_path') or not config.get('qemu_executable'):
        if not dry_run:
            debug_print("Launch cancelled: configuration is invalid.")
        return None

    # 1. Detect Displays and match resolution
    displays = get_display_info()
    secondary = displays[1] if len(displays) > 1 else displays[0]
    res_width = secondary['width']
    res_height = secondary['height']
    debug_print(f"Targeting display: {res_width}x{res_height} at ({secondary['x']}, {secondary['y']})")

    # 2. Detect disk format
    disk_path = os.path.expanduser(config['disk_path'])
    ext = os.path.splitext(disk_path)[1].lower()
    disk_format = "raw"
    if ext == ".qcow2": disk_format = "qcow2"
    elif ext == ".vmdk": disk_format = "vmdk"
    elif ext == ".vdi": disk_format = "vdi"
    elif ext == ".vhdx": disk_format = "vhdx"

    # 3. Base Command
    # We use cocoa's zoom-to-fit which is very stable for macOS guests.
    qemu_command = [
        config['qemu_executable'], "-M", "virt", "-accel", "hvf", "-cpu", "host", "-smp", "8", "-m", "24G",
        "-drive", f"if=pflash,format=raw,readonly=on,file={os.path.expanduser(config['firmware_path'])}",
        "-device", "virtio-blk-pci,drive=disk0", "-drive", f"id=disk0,if=none,format={disk_format},file={disk_path}",
        "-display", "cocoa,show-cursor=on,zoom-to-fit=on",
        "-device", f"virtio-gpu-pci,xres={res_width},yres={res_height}",
        "-device", "virtio-keyboard-pci", "-device", "virtio-tablet-pci"
    ]
    
    # 3. Add Optional Features
    if config.get('enable_webcam'):
        qemu_command.extend(["-device", "nec-usb-xhci,id=usb", "-device", "usb-camera,id=mycam,bus=usb.0"])
    
    if config.get('shared_dir_path'):
        qemu_command.extend(["-fsdev", f"local,id=fsdev0,path={os.path.expanduser(config['shared_dir_path'])},security_model=mapped-xattr", "-device", f"virtio-9p-pci,fsdev=fsdev0,mount_tag={config.get('mount_tag', 'host_share')}"])

    # 4. Audio Setup
    sdl_supported = check_sdl_support(config['qemu_executable'])
    enable_mic = config.get('enable_microphone', False)
    backend = "sdl" if sdl_supported else "coreaudio"
    audio_config = f"{backend},id=snd0,out.frequency=48000,out.channels=2,out.format=s16"
    if enable_mic:
        audio_config += ",in.frequency=48000,in.channels=1,in.format=s16"
    qemu_command.extend(["-audiodev", audio_config, "-device", "virtio-sound-pci,audiodev=snd0"])

    # 5. Intelligent Network Elevation
    net_mode = config.get('network_mode', 'user')
    needs_root = False
    
    if net_mode == 'vmnet-shared':
        qemu_command.extend(["-netdev", "vmnet-shared,id=net0", "-device", "virtio-net-pci,netdev=net0"])
        needs_root = True
    elif net_mode == 'bridge-existing':
        bridge_name = config.get('bridge_name', 'bridge100')
        qemu_command.extend(["-netdev", f"bridge,id=net0,br={bridge_name}", "-device", "virtio-net-pci,netdev=net0"])
        needs_root = True
    else:
        # Fallback to the user's preferred bridged mode
        qemu_command.extend(["-nic", "vmnet-bridged,ifname=en0"])
        needs_root = True

    if dry_run:
        return qemu_command

    # 6. Final Execution
    try:
        debug_print(f"Executing (needs_root={needs_root}): {' '.join(qemu_command)}")
        if needs_root:
            cmd_str = " ".join(f"'{arg}'" for arg in qemu_command)
            applescript = f'do shell script "{cmd_str}" with administrator privileges'
            proc = subprocess.Popen(["osascript", "-e", applescript])
        else:
            proc = subprocess.Popen(qemu_command)
        
        # Post-launch window management (runs in background)
        if proc:
             move_qemu_to_screen(screen_index=1, fullscreen=config.get('enable_fullscreen', True))
        
        return proc
    except Exception as e:
        show_error("Launch Error", f"Failed to run QEMU.\n\nError: {e}")
        return None

# ================================================================
# SETUP UI
# ================================================================
def run_setup_ui(existing_config=None):
    root = tk.Tk(); root.withdraw()
    dialog = tk.Toplevel(root); dialog.title("QEMU Launcher Settings")
    cfg = existing_config or get_smart_defaults()

    arch_var = tk.StringVar(dialog, value=cfg.get('arch', 'aarch64'))
    qemu_var = tk.StringVar(dialog, value=cfg.get('qemu_executable', ''))
    disk_var = tk.StringVar(dialog, value=cfg.get('disk_path', ''))
    fw_var = tk.StringVar(dialog, value=cfg.get('firmware_path', ''))
    share_path_var = tk.StringVar(dialog, value=cfg.get('shared_dir_path', ''))
    share_name_var = tk.StringVar(dialog, value=cfg.get('mount_tag', ''))
    webcam_var = tk.BooleanVar(dialog, value=cfg.get('enable_webcam', False))
    net_mode_var = tk.StringVar(dialog, value=cfg.get('network_mode', 'user'))
    bridge_name_var = tk.StringVar(dialog, value=cfg.get('bridge_name', 'bridge100'))
    guest_agent_var = tk.BooleanVar(dialog, value=cfg.get('enable_guest_agent', False))
    mic_var = tk.BooleanVar(dialog, value=cfg.get('enable_microphone', False))
    fullscreen_var = tk.BooleanVar(dialog, value=cfg.get('enable_fullscreen', True))

    frame = tk.Frame(dialog, padx=10, pady=10); frame.pack()
    
    row = 0

    def on_net_mode_change(event=None):
        mode_display = net_mode_combo.get()
        mode_value = net_modes.get(mode_display)
        
        bridge_name_entry.grid_remove(); bridge_name_label.grid_remove()
        if mode_value == 'vmnet-shared':
            net_info_label.config(text="Recommended for Wi-Fi. High performance, no setup needed.", fg="green")
        elif mode_value == 'bridge-existing':
            net_info_label.config(text="Uses an existing bridge (e.g., from macOS Internet Sharing).", fg="blue")
            bridge_name_label.grid(row=row_after_net_mode, column=0, sticky='w', pady=2)
            bridge_name_entry.grid(row=row_after_net_mode, column=1, sticky='ew', padx=5)
        else: # user
            net_info_label.config(text="Simple NAT networking. Good for basic internet access.", fg="black")

    tk.Label(frame, text="Architecture:").grid(row=row, column=0, sticky='w', pady=2); arch_combo = ttk.Combobox(frame, textvariable=arch_var, values=['aarch64', 'x86_64'], state='readonly'); arch_combo.grid(row=row, column=1, sticky='ew', padx=5); row += 1
    tk.Label(frame, text="QEMU Executable:").grid(row=row, column=0, sticky='w', pady=2); tk.Entry(frame, textvariable=qemu_var, width=50).grid(row=row, column=1, padx=5); tk.Button(frame, text="Browse...", command=lambda: qemu_var.set(filedialog.askopenfilename(parent=dialog) or qemu_var.get())).grid(row=row, column=2); row += 1
    tk.Label(frame, text="VM Disk Image:").grid(row=row, column=0, sticky='w', pady=2); tk.Entry(frame, textvariable=disk_var, width=50).grid(row=row, column=1, padx=5); tk.Button(frame, text="Browse...", command=lambda: disk_var.set(filedialog.askopenfilename(parent=dialog) or disk_var.get())).grid(row=row, column=2); row += 1
    tk.Label(frame, text="UEFI Firmware:").grid(row=row, column=0, sticky='w', pady=2); tk.Entry(frame, textvariable=fw_var, width=50).grid(row=row, column=1, padx=5); tk.Button(frame, text="Browse...", command=lambda: fw_var.set(filedialog.askopenfilename(parent=dialog) or fw_var.get())).grid(row=row, column=2); row += 1
    
    tk.Label(frame, text="Network Mode:").grid(row=row, column=0, sticky='w', pady=2)
    net_modes = {'Shared (vmnet)': 'vmnet-shared', 'User (NAT)': 'user', 'Bridged (Existing)': 'bridge-existing'}
    net_mode_combo = ttk.Combobox(frame, values=list(net_modes.keys()), state='readonly'); net_mode_combo.grid(row=row, column=1, sticky='ew', padx=5); net_mode_combo.bind('<<ComboboxSelected>>', on_net_mode_change); row += 1
    for display, value in net_modes.items():
        if value == net_mode_var.get(): net_mode_combo.set(display)
    
    net_info_label = tk.Label(frame, text="", font=('Helvetica', 10)); net_info_label.grid(row=row, column=1, columnspan=2, sticky='w', padx=5, pady=(0, 5)); row += 1
    
    row_after_net_mode = row
    bridge_name_label = tk.Label(frame, text="Bridge Name:"); bridge_name_entry = tk.Entry(frame, textvariable=bridge_name_var, width=50); row += 1

    tk.Label(frame, text="Shared Directory:").grid(row=row, column=0, sticky='w', pady=2); tk.Entry(frame, textvariable=share_path_var, width=50).grid(row=row, column=1, padx=5); tk.Button(frame, text="Browse...", command=lambda: share_path_var.set(filedialog.askdirectory(parent=dialog) or share_path_var.get())).grid(row=row, column=2); row += 1
    tk.Label(frame, text="Share Name (Tag):").grid(row=row, column=0, sticky='w', pady=2); tk.Entry(frame, textvariable=share_name_var, width=50).grid(row=row, column=1, padx=5); row += 1
    
    options_frame = tk.LabelFrame(frame, text="Hardware & Integration", padx=5, pady=5); options_frame.grid(row=row, column=0, columnspan=3, sticky='ew', pady=(10,0)); row += 1
    tk.Checkbutton(options_frame, text="Enable Webcam", variable=webcam_var).pack(side='left')
    tk.Checkbutton(options_frame, text="Enable Clipboard Sharing", variable=guest_agent_var).pack(side='left', padx=10)
    tk.Checkbutton(options_frame, text="Enable Microphone", variable=mic_var).pack(side='left', padx=10)
    tk.Checkbutton(options_frame, text="Fullscreen", variable=fullscreen_var).pack(side='left', padx=10)
    
    def on_save():
        values = {
            'arch': arch_var.get(), 'qemu_executable': qemu_var.get(), 'disk_path': disk_var.get(), 'firmware_path': fw_var.get(),
            'shared_dir_path': share_path_var.get(), 'mount_tag': share_name_var.get(), 'enable_webcam': webcam_var.get(),
            'network_mode': net_modes[net_mode_combo.get()], 'bridge_name': bridge_name_var.get(), 'enable_guest_agent': guest_agent_var.get(),
            'enable_microphone': mic_var.get(), 'enable_fullscreen': fullscreen_var.get()
        }
        if not all(values[k] for k in ['qemu_executable', 'disk_path', 'firmware_path']):
            messagebox.showerror("Error", "QEMU, Disk, and Firmware paths must be specified.", parent=dialog); return
        is_valid, error_msg = validate_qemu_executable(values['qemu_executable'])
        if not is_valid: messagebox.showerror("QEMU Validation Failed", f"Invalid QEMU executable.\n\n{error_msg}", parent=dialog); return
        
        save_config(values)
        SETUP_COMPLETE_FILE.touch(exist_ok=True)
        dialog.withdraw() # Hide the dialog instead of destroying it
        
        proc = run_launcher(load_config())
        
        if proc:
            import threading
            import time
            def monitor_mouse():
                hover_start = None
                while proc.poll() is None:
                    try:
                        if AppKit:
                            loc = AppKit.NSEvent.mouseLocation()
                            screens = AppKit.NSScreen.screens()
                            if screens:
                                primary = screens[0].frame()
                                screen_w = primary.size.width
                                screen_h = primary.size.height
                                
                                # Zone: top 10 pixels, middle 10%
                                in_x = (screen_w * 0.45) < loc.x < (screen_w * 0.55)
                                in_y = loc.y >= (screen_h - 10)
                                
                                if in_x and in_y:
                                    if hover_start is None:
                                        hover_start = time.time()
                                    elif time.time() - hover_start >= 5:
                                        # Restore UI
                                        dialog.after(0, lambda: (dialog.deiconify(), dialog.lift(), dialog.focus_force()))
                                        hover_start = None
                                        time.sleep(5) # Cooldown
                                else:
                                    hover_start = None
                    except Exception: pass
                    time.sleep(0.5)
            threading.Thread(target=monitor_mouse, daemon=True).start()

    button_frame = tk.Frame(frame); button_frame.grid(row=row, column=1, columnspan=2, sticky='e', pady=(10,0))
    tk.Button(button_frame, text="Save and Launch", command=on_save).pack(side='right', padx=5)
    tk.Button(button_frame, text="Cancel", command=root.destroy).pack(side='right')
    dialog.protocol("WM_DELETE_WINDOW", root.destroy)
    on_net_mode_change()
    
    dialog.update_idletasks()
    x = (dialog.winfo_screenwidth() // 2) - (dialog.winfo_width() // 2)
    y = (dialog.winfo_screenheight() // 2) - (dialog.winfo_height() // 2)
    dialog.geometry(f'+{x}+{y}')
    
    root.mainloop()

# ================================================================
# MAIN
# ================================================================
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="QEMU Launcher for macOS")
    parser.add_argument("--config", dest="config_path", help="Path to a custom config.ini file")
    parser.add_argument("--dry-run", action="store_true", help="Print the QEMU command and exit")
    parser.add_argument("--debug", action="store_true", help="Enable debug printing")
    parser.add_argument("--setup", action="store_true", help="Force setup UI")
    args = parser.parse_args()

    if args.debug:
        DEBUG = True

    config = load_config(args.config_path)
    
    if args.dry_run:
        if not config:
            # If no config exists, create a minimal one for dry-run validation
            config = get_smart_defaults()
            config['disk_path'] = "/tmp/test.vmdk" # Dummy path for validation
            config['firmware_path'] = "/tmp/fw.fd"
            
        cmd = run_launcher(config, dry_run=True)
        if cmd:
            print("--- DRY RUN OUTPUT ---")
            print(json.dumps(cmd))
            sys.exit(0)
        else:
            print("Error: Could not generate command. Check your configuration.")
            sys.exit(1)
    elif args.setup or not SETUP_COMPLETE_FILE.is_file() or not config:
        run_setup_ui(config)
    else:
        run_launcher(config)
