#!/usr/bin/env python3
import subprocess
import sys
import os
from pathlib import Path
import configparser
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import AppKit
import time

# ================================================================
# CONFIGURATION FILE
# ================================================================
CONFIG_DIR = Path.home() / ".config" / "qemu_launcher"
CONFIG_FILE = CONFIG_DIR / "config.ini"

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
    """Return number of connected screens."""
    try:
        return len(AppKit.NSScreen.screens())
    except Exception:
        return 1

def move_qemu_to_screen(window_pid, screen_index=1):
    """Move QEMU window to the specified screen index and maximize."""
    screens = AppKit.NSScreen.screens()
    if screen_index >= len(screens):
        screen_index = 0
    screen = screens[screen_index]
    frame = screen.frame()
    script = f'''
    tell application "System Events"
        set qemuWin to first window of (first process whose unix id is {window_pid})
        set position of qemuWin to {{ {int(frame.origin.x)}, {int(frame.origin.y)} }}
        set size of qemuWin to {{ {int(frame.size.width)}, {int(frame.size.height)} }}
    end tell
    '''
    try:
        subprocess.run(['osascript', '-e', script])
        debug_print(f"Moved QEMU window {window_pid} to screen {screen_index} at {frame.size.width}x{frame.size.height}")
    except Exception as e:
        debug_print(f"Failed to move QEMU window {window_pid}: {e}")

def validate_qemu_executable(executable_path):
    """Checks if the QEMU executable is valid by running '--version'."""
    if not executable_path or not os.path.exists(executable_path):
        return False, "Executable file not found at the specified path."
    
    try:
        debug_print(f"Validating QEMU executable: {executable_path}")
        result = subprocess.run(
            [executable_path, "--version"],
            capture_output=True,
            text=True,
            timeout=3
        )
        if result.returncode == 0:
            debug_print("Validation successful. QEMU version:", result.stdout.strip())
            return True, ""
        else:
            error_message = f"QEMU exited with an error:\n{result.stderr.strip()}"
            debug_print(f"Validation failed. {error_message}")
            return False, error_message
    except (FileNotFoundError, PermissionError) as e:
        return False, f"Could not run executable. Error: {e}"
    except Exception as e:
        return False, f"An unexpected validation error occurred: {e}"

def check_sdl_support(qemu_executable):
    """Check if SDL audio backend is available."""
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
    defaults = {
        'qemu_executable': '',
        'firmware_path': '',
        'arch': '',
        'shared_dir_path': str(Path.home() / "Documents"),
        'mount_tag': 'host_share'
    }
    try:
        defaults['arch'] = for_arch or ('aarch64' if os.uname().machine == 'arm64' else 'x86_64')
        qemu_exe_name = f"qemu-system-{defaults['arch']}"
        firmware_file_name = f"edk2-{defaults['arch']}-code.fd"

        prefix = subprocess.check_output(['brew', '--prefix']).decode('utf-8').strip()
        qemu_path = Path(prefix) / "bin" / qemu_exe_name
        firmware_path = Path(prefix) / "share" / "qemu" / firmware_file_name

        if qemu_path.is_file(): defaults['qemu_executable'] = str(qemu_path)
        if firmware_path.is_file(): defaults['firmware_path'] = str(firmware_path)
    except Exception:
        pass
    return defaults

def load_config():
    config = configparser.ConfigParser()
    if not CONFIG_FILE.is_file(): return None
    config.read(CONFIG_FILE)
    return {
        'arch': config.get('VM', 'arch', fallback='aarch64'),
        'qemu_executable': config.get('VM', 'qemu_executable', fallback=''),
        'disk_path': config.get('VM', 'disk_path', fallback=''),
        'firmware_path': config.get('VM', 'firmware_path', fallback=''),
        'shared_dir_path': config.get('VM', 'shared_dir_path', fallback=str(Path.home() / "Documents")),
        'mount_tag': config.get('VM', 'mount_tag', fallback='host_share'),
        'enable_webcam': config.getboolean('VM', 'enable_webcam', fallback=False),
    }

def save_config(values):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    config = configparser.ConfigParser()
    config['VM'] = {k: str(v) for k, v in values.items()}
    with open(CONFIG_FILE, 'w') as f:
        config.write(f)

# ================================================================
# TKINTER CONFIG UI
# ================================================================
def show_config_ui(existing_config=None):
    root = tk.Tk()
    root.title("QEMU Launcher Settings")
    cfg = existing_config or get_smart_defaults()

    arch_var = tk.StringVar(root, value=cfg.get('arch', 'aarch64'))
    qemu_var = tk.StringVar(root, value=cfg.get('qemu_executable', ''))
    disk_var = tk.StringVar(root, value=cfg.get('disk_path', ''))
    fw_var = tk.StringVar(root, value=cfg.get('firmware_path', ''))
    share_path_var = tk.StringVar(root, value=cfg.get('shared_dir_path', ''))
    share_name_var = tk.StringVar(root, value=cfg.get('mount_tag', ''))
    webcam_var = tk.BooleanVar(root, value=cfg.get('enable_webcam', False))

    frame = tk.Frame(root, padx=10, pady=10)
    frame.pack()

    def on_arch_change(event):
        new_defaults = get_smart_defaults(for_arch=arch_var.get())
        qemu_var.set(new_defaults.get('qemu_executable', ''))
        fw_var.set(new_defaults.get('firmware_path', ''))

    tk.Label(frame, text="Architecture:").grid(row=0, column=0, sticky='w', pady=2)
    arch_combo = ttk.Combobox(frame, textvariable=arch_var, values=['aarch64', 'x86_64'], state='readonly')
    arch_combo.grid(row=0, column=1, sticky='ew', padx=5)
    arch_combo.bind('<<ComboboxSelected>>', on_arch_change)

    tk.Label(frame, text="QEMU Executable:").grid(row=1, column=0, sticky='w', pady=2)
    tk.Entry(frame, textvariable=qemu_var, width=50).grid(row=1, column=1, padx=5)
    tk.Button(frame, text="Browse...", command=lambda: qemu_var.set(filedialog.askopenfilename() or qemu_var.get())).grid(row=1, column=2)

    tk.Label(frame, text="VM Disk Image:").grid(row=2, column=0, sticky='w', pady=2)
    tk.Entry(frame, textvariable=disk_var, width=50).grid(row=2, column=1, padx=5)
    tk.Button(frame, text="Browse...", command=lambda: disk_var.set(filedialog.askopenfilename() or disk_var.get())).grid(row=2, column=2)

    tk.Label(frame, text="UEFI Firmware:").grid(row=3, column=0, sticky='w', pady=2)
    tk.Entry(frame, textvariable=fw_var, width=50).grid(row=3, column=1, padx=5)
    tk.Button(frame, text="Browse...", command=lambda: fw_var.set(filedialog.askopenfilename() or fw_var.get())).grid(row=3, column=2)

    tk.Label(frame, text="Shared Directory:").grid(row=4, column=0, sticky='w', pady=2)
    tk.Entry(frame, textvariable=share_path_var, width=50).grid(row=4, column=1, padx=5)
    tk.Button(frame, text="Browse...", command=lambda: share_path_var.set(filedialog.askdirectory() or share_path_var.get())).grid(row=4, column=2)

    tk.Label(frame, text="Share Name (Tag):").grid(row=5, column=0, sticky='w', pady=2)
    tk.Entry(frame, textvariable=share_name_var, width=50).grid(row=5, column=1, padx=5)

    tk.Checkbutton(frame, text="Enable Webcam Passthrough", variable=webcam_var).grid(row=6, column=0, columnspan=2, sticky='w', pady=(10,0))

    result = {}
    def on_save():
        values = {
            'arch': arch_var.get(),
            'qemu_executable': qemu_var.get(),
            'disk_path': disk_var.get(),
            'firmware_path': fw_var.get(),
            'shared_dir_path': share_path_var.get(),
            'mount_tag': share_name_var.get(),
            'enable_webcam': webcam_var.get()
        }

        if not all(values[k] for k in ['qemu_executable', 'disk_path', 'firmware_path']):
            messagebox.showerror("Error", "QEMU, Disk, and Firmware paths must be specified.")
            return

        if not os.path.isfile(os.path.expanduser(values['disk_path'])):
            messagebox.showerror("Validation Failed", f"VM Disk Image file not found:\n{values['disk_path']}")
            return
        if not os.path.isfile(os.path.expanduser(values['firmware_path'])):
            messagebox.showerror("Validation Failed", f"UEFI Firmware file not found:\n{values['firmware_path']}")
            return
        if values['shared_dir_path'] and not os.path.isdir(os.path.expanduser(values['shared_dir_path'])):
            messagebox.showerror("Validation Failed", f"Shared Directory not found:\n{values['shared_dir_path']}")
            return

        is_valid, error_msg = validate_qemu_executable(values['qemu_executable'])
        if not is_valid:
            messagebox.showerror("QEMU Validation Failed", f"The specified QEMU executable is not valid.\n\n{error_msg}")
            return
            
        save_config(values)
        result['status'] = 'ok'
        root.destroy()

    def on_cancel():
        result['status'] = 'cancel'
        root.destroy()

    button_frame = tk.Frame(frame)
    button_frame.grid(row=7, column=1, columnspan=2, sticky='e', pady=(10,0))
    tk.Button(button_frame, text="Save and Launch", command=on_save).pack(side='right', padx=5)
    tk.Button(button_frame, text="Cancel", command=on_cancel).pack(side='right')

    root.protocol("WM_DELETE_WINDOW", on_cancel)
    root.mainloop()
    
    return load_config() if result.get('status') == 'ok' else None

# ================================================================
# QEMU LAUNCHER
# ================================================================
def run_launcher(config):
    display_params = "cocoa,show-cursor=on,full-screen=on"

    sdl_supported = check_sdl_support(config['qemu_executable'])
    if sdl_supported:
        audio_device_params = [
            "-audiodev", "sdl,id=snd0,out.frequency=48000,out.channels=2,out.format=s16,in.frequency=48000,in.channels=1,in.format=s16",
            "-device", "virtio-sound-pci,audiodev=snd0",
        ]
        debug_print("Using SDL audio backend with input/output:")
        for param in audio_device_params:
            debug_print(param)
    else:
        audio_device_params = [
            "-audiodev", "coreaudio,id=snd0,out.frequency=48000,out.channels=2,out.format=s16",
            "-device", "virtio-sound-pci,audiodev=snd0",
        ]
        debug_print("SDL not available. Using CoreAudio for output only:")
        for param in audio_device_params:
            debug_print(param)

    qemu_command = [
        config['qemu_executable'],
        "-M", "virt", "-accel", "hvf", "-cpu", "host", "-smp", "4", "-m", "16G",
        "-drive", f"if=pflash,format=raw,readonly=on,file={os.path.expanduser(config['firmware_path'])}",
        "-device", "virtio-blk-pci,drive=disk0",
        "-drive", f"id=disk0,if=none,format=vmdk,file={os.path.expanduser(config['disk_path'])}",
        "-display", display_params,
        "-device", "virtio-gpu-pci",
        "-device", "virtio-keyboard-pci", "-device", "virtio-tablet-pci",
        "-netdev", "user,id=net0", "-device", "virtio-net-pci,netdev=net0",
    ]
    
    if config.get('enable_webcam'):
        qemu_command.extend([
            "-device", "nec-usb-xhci,id=usb",
            "-device", "usb-camera,id=mycam,bus=usb.0"
        ])
    
    qemu_command.extend(audio_device_params)

    if config.get('shared_dir_path') and config.get('mount_tag'):
        qemu_command.extend([
            "-fsdev", f"local,id=fsdev0,path={os.path.expanduser(config['shared_dir_path'])},security_model=mapped-xattr",
            "-device", f"virtio-9p-pci,fsdev=fsdev0,mount_tag={config['mount_tag']}",
        ])

    debug_print("Launching QEMU with command:")
    debug_print(" ".join(qemu_command))

    proc = subprocess.Popen(qemu_command)
    time.sleep(1.5)

    if get_screen_count() > 1:
        debug_print("Moving QEMU window to secondary screen.")
        move_qemu_to_screen(proc.pid, screen_index=1)

    sys.exit(0)

# ================================================================
# MAIN
# ================================================================
if __name__ == "__main__":
    config = load_config()
    if not config or (len(sys.argv) > 1 and sys.argv[1] == '--config'):
        config = show_config_ui(config)
    if config and config.get('disk_path') and config.get('qemu_executable'):
        run_launcher(config)
    else:
        sys.exit(1)
