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
    try: return len(AppKit.NSScreen.screens())
    except Exception: return 1

def move_qemu_to_screen(window_pid, screen_index=1):
    try:
        screens = AppKit.NSScreen.screens()
        if screen_index >= len(screens): screen_index = 0
        screen = screens[screen_index]
        frame = screen.frame()
        script = f'''
        tell application "System Events"
            set qemuWin to first window of (first process whose unix id is {window_pid})
            set position of qemuWin to {{ {int(frame.origin.x)}, {int(frame.origin.y)} }}
            set size of qemuWin to {{ {int(frame.size.width)}, {int(frame.size.height)} }}
        end tell
        '''
        subprocess.run(['osascript', '-e', script], check=True, capture_output=True)
        debug_print(f"Moved QEMU window {window_pid} to screen {screen_index}")
    except Exception as e:
        debug_print(f"Failed to move QEMU window {window_pid}: {e}")

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
        prefix = subprocess.check_output(['brew', '--prefix']).decode('utf-8').strip()
        qemu_path = Path(prefix) / "bin" / f"qemu-system-{defaults['arch']}"
        firmware_path = Path(prefix) / "share" / "qemu" / f"edk2-{defaults['arch']}-code.fd"
        if qemu_path.is_file(): defaults['qemu_executable'] = str(qemu_path)
        if firmware_path.is_file(): defaults['firmware_path'] = str(firmware_path)
    except Exception: pass
    return defaults

def load_config():
    config = configparser.ConfigParser()
    if not CONFIG_FILE.is_file(): return None
    config.read(CONFIG_FILE)
    return {
        'arch': config.get('VM', 'arch', fallback='aarch64'),
        'qemu_executable': config.get('VM', 'qemu_executable', fallback=''), 'disk_path': config.get('VM', 'disk_path', fallback=''),
        'firmware_path': config.get('VM', 'firmware_path', fallback=''), 'shared_dir_path': config.get('VM', 'shared_dir_path', fallback=str(Path.home() / "Documents")),
        'mount_tag': config.get('VM', 'mount_tag', fallback='host_share'), 'enable_webcam': config.getboolean('VM', 'enable_webcam', fallback=False),
        'network_mode': config.get('VM', 'network_mode', fallback='user'), 'bridge_name': config.get('VM', 'bridge_name', fallback='bridge100'),
        'enable_guest_agent': config.getboolean('VM', 'enable_guest_agent', fallback=False),
        'enable_microphone': config.getboolean('VM', 'enable_microphone', fallback=False)
    }

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
def run_launcher(config):
    if not config or not config.get('disk_path') or not config.get('qemu_executable'):
        debug_print("Launch cancelled: configuration is invalid."); return

    qemu_command = [
        config['qemu_executable'], "-M", "virt", "-accel", "hvf", "-cpu", "host", "-smp", "8", "-m", "24G",
        "-drive", f"if=pflash,format=raw,readonly=on,file={os.path.expanduser(config['firmware_path'])}",
        "-device", "virtio-blk-pci,drive=disk0", "-drive", f"id=disk0,if=none,format=vmdk,file={os.path.expanduser(config['disk_path'])}",
        "-display", "cocoa,show-cursor=on,full-screen=on", "-device", "virtio-gpu-pci", "-device", "virtio-keyboard-pci", "-device", "virtio-tablet-pci"
    ]
    
    if config.get('enable_webcam'): qemu_command.extend(["-device", "nec-usb-xhci,id=usb", "-device", "usb-camera,id=mycam,bus=usb.0"])
    if config.get('shared_dir_path'): qemu_command.extend(["-fsdev", f"local,id=fsdev0,path={os.path.expanduser(config['shared_dir_path'])},security_model=mapped-xattr", "-device", f"virtio-9p-pci,fsdev=fsdev0,mount_tag={config.get('mount_tag', 'host_share')}"])
    if config.get('enable_guest_agent'):
        qemu_command.extend(["-device", "virtio-serial", "-chardev", "spicevmc,id=spicechannel0,name=vdagent", "-device", "virtserialport,chardev=spicechannel0,name=com.redhat.spice.0"])

    # --- MODIFICATION: Restored conditional logic to prefer SDL audio ---
    sdl_supported = check_sdl_support(config['qemu_executable'])
    enable_mic = config.get('enable_microphone', False)
    backend = "sdl" if sdl_supported else "coreaudio"

    if enable_mic:
        debug_print(f"Enabling audio input and output via {backend}.")
        audio_config = f"{backend},id=snd0,out.frequency=48000,out.channels=2,out.format=s16,in.frequency=48000,in.channels=1,in.format=s16"
    else:
        debug_print(f"Enabling audio output only via {backend}.")
        audio_config = f"{backend},id=snd0,out.frequency=48000,out.channels=2,out.format=s16"
    qemu_command.extend(["-audiodev", audio_config, "-device", "virtio-sound-pci,audiodev=snd0"])

    network_mode = config.get('network_mode', 'user')
    try:
        proc = None
        if network_mode == 'vmnet-shared':
            qemu_command.extend(["-netdev", "vmnet-shared,id=net0", "-device", "virtio-net-pci,netdev=net0"])
        elif network_mode == 'bridge-existing':
            bridge_name = config.get('bridge_name', 'bridge100')
            qemu_command.extend(["-netdev", f"bridge,id=net0,br={bridge_name}", "-device", "virtio-net-pci,netdev=net0"])
        else: # 'user' mode
            qemu_command.extend(["-nic", "vmnet-bridged,ifname=en0"])
        
        debug_print("Launching QEMU with command:", " ".join(qemu_command))
        proc = subprocess.Popen(qemu_command)

        if proc and proc.poll() is None:
             time.sleep(2)
             if get_screen_count() > 1: move_qemu_to_screen(proc.pid)

    except Exception as e:
        show_error("Launch Error", f"Failed to run QEMU.\n\nError: {e}"); sys.exit(1)
    
    sys.exit(0)

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
    
    def on_save():
        values = {
            'arch': arch_var.get(), 'qemu_executable': qemu_var.get(), 'disk_path': disk_var.get(), 'firmware_path': fw_var.get(),
            'shared_dir_path': share_path_var.get(), 'mount_tag': share_name_var.get(), 'enable_webcam': webcam_var.get(),
            'network_mode': net_modes[net_mode_combo.get()], 'bridge_name': bridge_name_var.get(), 'enable_guest_agent': guest_agent_var.get(),
            'enable_microphone': mic_var.get()
        }
        if not all(values[k] for k in ['qemu_executable', 'disk_path', 'firmware_path']):
            messagebox.showerror("Error", "QEMU, Disk, and Firmware paths must be specified.", parent=dialog); return
        is_valid, error_msg = validate_qemu_executable(values['qemu_executable'])
        if not is_valid: messagebox.showerror("QEMU Validation Failed", f"Invalid QEMU executable.\n\n{error_msg}", parent=dialog); return
        
        save_config(values)
        SETUP_COMPLETE_FILE.touch(exist_ok=True)
        root.destroy()
        run_launcher(load_config())

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
    config = load_config()
    if not SETUP_COMPLETE_FILE.is_file() or not config or '--config' in sys.argv:
        run_setup_ui(config)
    else:
        run_launcher(config)
