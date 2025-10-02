#!/usr/bin/env python3
import subprocess
import re
import time
import sys
import os
import shutil
import platform
from pathlib import Path
import configparser
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import AppKit

# ================================================================
# CONFIGURATION FILE SETUP
# ================================================================
CONFIG_DIR = Path.home() / ".config" / "qemu_launcher"
CONFIG_FILE = CONFIG_DIR / "config.ini"

# ================================================================
# UI AND CONFIGURATION LOGIC
# ================================================================

def get_smart_defaults(for_arch=None):
    """Tries to find Homebrew, detects arch, and generates default paths."""
    defaults = {
        'qemu_executable': '',
        'firmware_path': '',
        'arch': '',
        'shared_dir_path': str(Path.home() / "Documents"),
        'mount_tag': 'host_share'
    }
    try:
        if not for_arch:
            host_arch = platform.machine()
            defaults['arch'] = 'aarch64' if host_arch == 'arm64' else 'x86_64'
        else:
            defaults['arch'] = for_arch

        qemu_exe_name = f"qemu-system-{defaults['arch']}"
        firmware_file_name = f"edk2-{defaults['arch']}-code.fd"

        prefix = subprocess.check_output(['brew', '--prefix']).decode('utf-8').strip()
        qemu_path = Path(prefix) / "bin" / qemu_exe_name
        firmware_path = Path(prefix) / "share" / "qemu" / firmware_file_name
        
        if qemu_path.is_file(): defaults['qemu_executable'] = str(qemu_path)
        if firmware_path.is_file(): defaults['firmware_path'] = str(firmware_path)
    except Exception as e:
        print(f"Could not determine smart defaults: {e}", file=sys.stderr)
    return defaults

def load_config():
    """Loads settings from the config.ini file."""
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
    }

def save_config(values):
    """Saves settings to the config.ini file."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    config = configparser.ConfigParser()
    config['VM'] = {
        'arch': values['arch'],
        'qemu_executable': values['qemu_executable'],
        'disk_path': values['disk_path'],
        'firmware_path': values['firmware_path'],
        'shared_dir_path': values['shared_dir_path'],
        'mount_tag': values['mount_tag']
    }
    with open(CONFIG_FILE, 'w') as f:
        config.write(f)

def show_config_ui(existing_config=None):
    """Displays the Tkinter configuration window."""
    root = tk.Tk()
    root.title("QEMU Launcher Settings")
    cfg = existing_config or get_smart_defaults()
    
    arch_var = tk.StringVar(root, value=cfg.get('arch', 'aarch64'))
    qemu_var = tk.StringVar(root, value=cfg.get('qemu_executable', ''))
    disk_var = tk.StringVar(root, value=cfg.get('disk_path', ''))
    fw_var = tk.StringVar(root, value=cfg.get('firmware_path', ''))
    share_path_var = tk.StringVar(root, value=cfg.get('shared_dir_path', ''))
    share_name_var = tk.StringVar(root, value=cfg.get('mount_tag', ''))
    
    frame = tk.Frame(root, padx=10, pady=10)
    frame.pack()

    def on_arch_change(event):
        selected_arch = arch_var.get()
        new_defaults = get_smart_defaults(for_arch=selected_arch)
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
    
    result = {}
    def on_save():
        nonlocal result
        values = {'arch': arch_var.get(), 'qemu_executable': qemu_var.get(), 'disk_path': disk_var.get(), 'firmware_path': fw_var.get(), 'shared_dir_path': share_path_var.get(), 'mount_tag': share_name_var.get()}
        if not all(values[k] for k in ['qemu_executable', 'disk_path', 'firmware_path']):
            messagebox.showerror("Error", "QEMU, Disk, and Firmware paths must be specified.")
            return
        save_config(values)
        result['status'] = 'ok'
        root.destroy()
    def on_cancel():
        nonlocal result
        result['status'] = 'cancel'
        root.destroy()
        
    button_frame = tk.Frame(frame)
    button_frame.grid(row=6, column=1, columnspan=2, sticky='e', pady=(10,0))
    tk.Button(button_frame, text="Save and Launch", command=on_save).pack(side='right', padx=5)
    tk.Button(button_frame, text="Cancel", command=on_cancel).pack(side='right')
    
    root.protocol("WM_DELETE_WINDOW", on_cancel)
    root.mainloop()
    
    return load_config() if result.get('status') == 'ok' else None

def run_launcher(config):
    """Builds the QEMU command and launches it directly into fullscreen."""
    
    qemu_command = [
        config['qemu_executable'],
        "-M", "virt", "-accel", "hvf", "-cpu", "host", "-smp", "4", "-m", "16G",
        "-drive", f"if=pflash,format=raw,readonly=on,file={os.path.expanduser(config['firmware_path'])}",
        "-device", "virtio-blk-pci,drive=disk0",
        "-drive", f"id=disk0,if=none,format=vmdk,file={os.path.expanduser(config['disk_path'])}",
        "-display", "cocoa,show-cursor=on,full-screen=on",
        "-device", "virtio-gpu-pci",
        "-device", "virtio-keyboard-pci", "-device", "virtio-tablet-pci",
        "-netdev", "user,id=net0", "-device", "virtio-net-pci,netdev=net0",
        "-audiodev", "coreaudio,id=snd0,out.frequency=48000,out.channels=2,out.format=s16,in.frequency=48000,in.channels=1,in.format=s16",
        "-device", "intel-hda", 
        "-device", "hda-output,audiodev=snd0",
        "-device", "hda-input,audiodev=snd0",
    ]
    
    if config.get('shared_dir_path') and config.get('mount_tag'):
        qemu_command.extend([
            "-fsdev", f"local,id=fsdev0,path={os.path.expanduser(config['shared_dir_path'])},security_model=passthrough",
            "-device", f"virtio-9p-pci,fsdev=fsdev0,mount_tag={config['mount_tag']}",
        ])
        
    # We no longer need the complex "launch and detach" logic. We just run the command.
    subprocess.Popen(qemu_command)
    sys.exit(0)


# ================================================================
# MAIN EXECUTION BLOCK
# ================================================================
if __name__ == "__main__":
    # The script is now much simpler. It no longer needs different modes.
    config = load_config()
    
    # Show the UI if no config exists, or if the user passes a '--config' flag
    if not config or (len(sys.argv) > 1 and sys.argv[1] == '--config'):
        config = show_config_ui(config)
    
    # If we have a valid config, launch the VM.
    if config and config.get('disk_path') and config.get('qemu_executable'):
        run_launcher(config)
    else:
        sys.exit(0)
