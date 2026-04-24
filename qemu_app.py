#!/usr/bin/env python3
import argparse
import configparser
import os
import subprocess
import sys
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

try:
    import AppKit
except ImportError:
    AppKit = None

# ================================================================
# BUNDLE HARDENING: Early imports for PyInstaller
# ================================================================
try:
    import encodings.ascii
    import encodings.latin_1
    import encodings.utf_8

    # Dummy use to prevent linter stripping
    _ = [encodings.utf_8.getregentry(), encodings.latin_1.getregentry(), encodings.ascii.getregentry()]
except ImportError:
    pass

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
# ================================================================
# WINDOW & DISPLAY MANAGEMENT
# ================================================================
class DisplayManager:
    @staticmethod
    def get_displays():
        if AppKit is None:
            return [{"x": 0, "y": 0, "width": 1920, "height": 1080, "is_primary": True}]

        try:
            screens = AppKit.NSScreen.screens()
            displays = []
            for i, screen in enumerate(screens):
                frame = screen.frame()
                displays.append(
                    {
                        "index": i,
                        "x": int(frame.origin.x),
                        "y": int(frame.origin.y),
                        "width": int(frame.size.width),
                        "height": int(frame.size.height),
                        "is_primary": i == 0,
                    }
                )
            return displays
        except Exception:
            return [{"x": 0, "y": 0, "width": 1920, "height": 1080, "is_primary": True}]

    @staticmethod
    def get_target_display():
        displays = DisplayManager.get_displays()
        if len(displays) > 1:
            return displays[1]  # Return secondary
        return displays[0]  # Return primary


class WindowManager:
    @staticmethod
    def orchestrate_window(window_pid, fullscreen=True):
        def _orchestrate():
            time.sleep(2)  # Wait for window to appear
            target = DisplayManager.get_target_display()
            script = f"""
            tell application "System Events"
                set qemuWin to first window of (first process whose unix id is {window_pid})
                set position of qemuWin to {{ {target["x"]}, {target["y"]} }}
                set size of qemuWin to {{ {target["width"]}, {target["height"]} }}
            end tell
            """
            try:
                subprocess.run(["osascript", "-e", script], check=True, capture_output=True)
                debug_print(f"Orchestrated window {window_pid} to display at {target['x']},{target['y']}")
            except Exception as e:
                debug_print(f"Orchestration failed: {e}")

        import threading

        threading.Thread(target=_orchestrate, daemon=True).start()


class GestureMonitor:
    @staticmethod
    def start(settings_callback):
        def _monitor():
            if AppKit is None:
                debug_print("AppKit not available, GestureMonitor exiting.")
                return

            hover_start = None
            debug_print("GestureMonitor started.")

            while True:
                try:
                    # Get mouse location relative to primary screen
                    loc = AppKit.NSEvent.mouseLocation()
                    screen = AppKit.NSScreen.screens()[0]
                    screen_w = screen.frame().size.width
                    screen_h = screen.frame().size.height

                    # Target: Top center zone (15px height, center 16% width)
                    in_x = (screen_w * 0.42) < loc.x < (screen_w * 0.58)
                    in_y = loc.y >= (screen_h - 15)

                    if in_x and in_y:
                        if hover_start is None:
                            hover_start = time.time()
                        elif time.time() - hover_start >= 3:  # 3s hover trigger
                            debug_print("Gesture trigger activated!")
                            settings_callback()
                            hover_start = None
                            time.sleep(10)  # Cooldown
                    else:
                        hover_start = None
                except Exception:
                    pass
                time.sleep(0.5)

        import threading

        threading.Thread(target=_monitor, daemon=True).start()


def validate_qemu_executable(executable_path):
    if not executable_path or not os.path.exists(executable_path):
        return False, "Executable file not found"
    try:
        result = subprocess.run([executable_path, "--version"], capture_output=True, text=True, timeout=3)
        if result.returncode == 0:
            return True, ""
        return False, f"QEMU exited with an error:\n{result.stderr.strip()}"
    except Exception as e:
        return False, f"An unexpected validation error occurred: {e}"


def check_sdl_support(qemu_executable):
    """Check if SDL audio backend is available in QEMU."""
    try:
        result = subprocess.run(
            [qemu_executable, "-audiodev", "help"], capture_output=True, text=True, check=True, timeout=5
        )
        debug_print("Available audio backends:\n", result.stdout)
        return "sdl" in result.stdout.lower()
    except Exception as e:
        debug_print("SDL support check failed:", e)
        return False


def get_smart_defaults(for_arch=None):
    defaults = {
        "qemu_executable": "",
        "firmware_path": "",
        "arch": "",
        "shared_dir_path": str(Path.home() / "Documents"),
        "mount_tag": "host_share",
        "network_mode": "user",
    }
    try:
        defaults["arch"] = for_arch or ("aarch64" if os.uname().machine == "arm64" else "x86_64")
        prefix = subprocess.check_output(["brew", "--prefix"]).decode("utf-8").strip()
        qemu_path = Path(prefix) / "bin" / f"qemu-system-{defaults['arch']}"
        firmware_path = Path(prefix) / "share" / "qemu" / f"edk2-{defaults['arch']}-code.fd"
        if qemu_path.is_file():
            defaults["qemu_executable"] = str(qemu_path)
        if firmware_path.is_file():
            defaults["firmware_path"] = str(firmware_path)
    except Exception:
        pass
    return defaults


def load_config(path=None):
    config_path = Path(path) if path else CONFIG_FILE
    config = configparser.ConfigParser()
    if not config_path.is_file():
        return None
    config.read(config_path)

    # Return all keys from 'VM' section to support arbitrary test keys
    if "VM" not in config:
        return {}

    result = dict(config["VM"])
    # Convert booleans back
    for key in ["enable_webcam", "enable_guest_agent", "enable_microphone", "enable_fullscreen"]:
        if key in result:
            result[key] = config.getboolean("VM", key)
    return result


def save_config(values, path=None):
    config_path = Path(path) if path else CONFIG_FILE
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config = configparser.ConfigParser()
    config["VM"] = {k: str(v) for k, v in values.items()}
    with open(config_path, "w") as f:
        config.write(f)


def show_error(title, message):
    root = tk.Tk()
    root.withdraw()
    messagebox.showerror(title, message)
    root.destroy()


# ================================================================
# QEMU LAUNCHER
# ================================================================
def run_launcher(config, dry_run=False):
    if not config or not config.get("disk_path") or not config.get("qemu_executable"):
        debug_print("Launch cancelled: configuration is invalid.")
        return

    qemu_executable = config["qemu_executable"]
    firmware_path = config["firmware_path"]
    disk_path = config["disk_path"]

    # Base Command
    qemu_command = [
        qemu_executable,
        "-M",
        "virt",
        "-accel",
        "hvf",
        "-cpu",
        "host",
        "-smp",
        "8",
        "-m",
        "24G",
        "-drive",
        f"if=pflash,format=raw,readonly=on,file={os.path.expanduser(firmware_path)}",
        "-device",
        "virtio-blk-pci,drive=disk0",
        "-drive",
        f"id=disk0,if=none,format=qcow2,file={os.path.expanduser(disk_path)}",
        "-display",
        "cocoa,show-cursor=on,zoom-to-fit=on",
        "-device",
        f"virtio-gpu-pci,xres={DisplayManager.get_target_display()['width']},yres={DisplayManager.get_target_display()['height']}",
        "-device",
        "virtio-keyboard-pci",
        "-device",
        "virtio-tablet-pci",
    ]

    if config.get("enable_webcam"):
        qemu_command.extend(["-device", "nec-usb-xhci,id=usb", "-device", "usb-camera,id=mycam,bus=usb.0"])

    if config.get("shared_dir_path"):
        qemu_command.extend(
            [
                "-fsdev",
                f"local,id=fsdev0,path={os.path.expanduser(config['shared_dir_path'])},security_model=mapped-xattr",
                "-device",
                f"virtio-9p-pci,fsdev=fsdev0,mount_tag={config.get('mount_tag', 'host_share')}",
            ]
        )

    if config.get("enable_guest_agent"):
        qemu_command.extend(
            [
                "-device",
                "virtio-serial",
                "-chardev",
                "spicevmc,id=spicechannel0,name=vdagent",
                "-device",
                "virtserialport,chardev=spicechannel0,name=com.redhat.spice.0",
            ]
        )

    # Audio Setup
    sdl_supported = check_sdl_support(qemu_executable)
    enable_mic = config.get("enable_microphone", False)
    backend = "sdl" if sdl_supported else "coreaudio"

    if enable_mic:
        audio_config = f"{backend},id=snd0,out.frequency=48000,out.channels=2,out.format=s16,in.frequency=48000,in.channels=1,in.format=s16"
    else:
        audio_config = f"{backend},id=snd0,out.frequency=48000,out.channels=2,out.format=s16"

    qemu_command.extend(["-audiodev", audio_config, "-device", "virtio-sound-pci,audiodev=snd0"])

    # Network Setup
    net_mode = config.get("network_mode", "user")
    if net_mode == "vmnet-shared":
        qemu_command.extend(["-netdev", "vmnet-shared,id=net0", "-device", "virtio-net-pci,netdev=net0"])
    elif net_mode == "bridge-existing":
        bridge_name = config.get("bridge_name", "bridge100")
        qemu_command.extend(["-netdev", f"bridge,id=net0,br={bridge_name}", "-device", "virtio-net-pci,netdev=net0"])
    else:  # 'user' mode
        qemu_command.extend(["-netdev", "user,id=net0", "-device", "virtio-net-pci,netdev=net0"])

    if dry_run:
        return qemu_command

    # ================================================================
    # ENVIRONMENT ISOLATION
    # ================================================================
    qemu_env = os.environ.copy()
    for var in ["PYTHONPATH", "PYTHONHOME", "DYLD_LIBRARY_PATH", "LD_LIBRARY_PATH"]:
        if var in qemu_env:
            del qemu_env[var]

    needs_root = net_mode != "user"
    try:
        debug_print("Launching QEMU with command:", " ".join(qemu_command))
        if needs_root:
            cmd_str = " ".join(f"'{a}'" for a in qemu_command)
            proc = subprocess.Popen(
                ["osascript", "-e", f'do shell script "{cmd_str}" with administrator privileges'], env=qemu_env
            )
        else:
            proc = subprocess.Popen(qemu_command, env=qemu_env)

        if proc and proc.poll() is None:
            WindowManager.orchestrate_window(proc.pid, fullscreen=config.get("enable_fullscreen", True))

        return proc

    except Exception as e:
        show_error("Launch Error", f"Failed to run QEMU.\n\nError: {e}")
        sys.exit(1)


# ================================================================
# SETUP UI
# ================================================================
def run_setup_ui(existing_config=None):
    root = tk.Tk()
    root.withdraw()

    # Configure styles
    style = ttk.Style()
    if sys.platform == "darwin":
        style.theme_use("aqua")

    dialog = tk.Toplevel(root)
    dialog.title("QEMU Launcher Settings")
    dialog.resizable(False, False)
    cfg = existing_config or get_smart_defaults()

    # Variables
    arch_var = tk.StringVar(dialog, value=cfg.get("arch", "aarch64"))
    qemu_var = tk.StringVar(dialog, value=cfg.get("qemu_executable", ""))
    disk_var = tk.StringVar(dialog, value=cfg.get("disk_path", ""))
    fw_var = tk.StringVar(dialog, value=cfg.get("firmware_path", ""))
    share_path_var = tk.StringVar(dialog, value=cfg.get("shared_dir_path", ""))
    share_name_var = tk.StringVar(dialog, value=cfg.get("mount_tag", ""))
    webcam_var = tk.BooleanVar(dialog, value=cfg.get("enable_webcam", False))
    net_mode_var = tk.StringVar(dialog, value=cfg.get("network_mode", "user"))
    bridge_name_var = tk.StringVar(dialog, value=cfg.get("bridge_name", "bridge100"))
    guest_agent_var = tk.BooleanVar(dialog, value=cfg.get("enable_guest_agent", False))
    mic_var = tk.BooleanVar(dialog, value=cfg.get("enable_microphone", False))
    fullscreen_var = tk.BooleanVar(dialog, value=cfg.get("enable_fullscreen", True))

    # Main container
    main_frame = ttk.Frame(dialog, padding="20 20 20 20")
    main_frame.pack(fill="both", expand=True)

    row = 0

    def on_net_mode_change(event=None):
        mode_display = net_mode_combo.get()
        mode_value = net_modes.get(mode_display)

        bridge_name_entry.grid_remove()
        bridge_name_label.grid_remove()

        if mode_value == "vmnet-shared":
            net_info_label.config(text="Native macOS sharing. Best performance.", foreground="#2e7d32")
        elif mode_value == "bridge-existing":
            net_info_label.config(text="Uses an existing system bridge.", foreground="#1565c0")
            bridge_name_label.grid(row=row_after_net_mode, column=0, sticky="w", pady=5)
            bridge_name_entry.grid(row=row_after_net_mode, column=1, sticky="ew", padx=5, pady=5)
        else:
            net_info_label.config(text="Simple NAT. No configuration needed.", foreground="#424242")

    # Layout
    ttk.Label(main_frame, text="Architecture:", font=("SF Pro", 12, "bold")).grid(row=row, column=0, sticky="w", pady=5)
    arch_combo = ttk.Combobox(main_frame, textvariable=arch_var, values=["aarch64", "x86_64"], state="readonly")
    arch_combo.grid(row=row, column=1, sticky="ew", padx=10, pady=5)
    row += 1

    ttk.Label(main_frame, text="QEMU Binary:", font=("SF Pro", 12, "bold")).grid(row=row, column=0, sticky="w", pady=5)
    ttk.Entry(main_frame, textvariable=qemu_var, width=45).grid(row=row, column=1, padx=10, pady=5)
    ttk.Button(
        main_frame,
        text="Browse",
        command=lambda: qemu_var.set(filedialog.askopenfilename(parent=dialog) or qemu_var.get()),
    ).grid(row=row, column=2)
    row += 1

    ttk.Label(main_frame, text="Disk Image:", font=("SF Pro", 12, "bold")).grid(row=row, column=0, sticky="w", pady=5)
    ttk.Entry(main_frame, textvariable=disk_var, width=45).grid(row=row, column=1, padx=10, pady=5)
    ttk.Button(
        main_frame,
        text="Browse",
        command=lambda: disk_var.set(filedialog.askopenfilename(parent=dialog) or disk_var.get()),
    ).grid(row=row, column=2)
    row += 1

    ttk.Label(main_frame, text="Firmware (FD):", font=("SF Pro", 12, "bold")).grid(
        row=row, column=0, sticky="w", pady=5
    )
    ttk.Entry(main_frame, textvariable=fw_var, width=45).grid(row=row, column=1, padx=10, pady=5)
    ttk.Button(
        main_frame, text="Browse", command=lambda: fw_var.set(filedialog.askopenfilename(parent=dialog) or fw_var.get())
    ).grid(row=row, column=2)
    row += 1

    ttk.Separator(main_frame, orient="horizontal").grid(row=row, column=0, columnspan=3, sticky="ew", pady=15)
    row += 1

    ttk.Label(main_frame, text="Network Mode:", font=("SF Pro", 12, "bold")).grid(row=row, column=0, sticky="w", pady=5)
    net_modes = {"Shared (vmnet)": "vmnet-shared", "User (NAT)": "user", "Bridged (Existing)": "bridge-existing"}
    net_mode_combo = ttk.Combobox(main_frame, values=list(net_modes.keys()), state="readonly")
    net_mode_combo.grid(row=row, column=1, sticky="ew", padx=10, pady=5)
    net_mode_combo.bind("<<ComboboxSelected>>", on_net_mode_change)
    row += 1

    net_info_label = ttk.Label(main_frame, text="", font=("SF Pro", 10, "italic"))
    net_info_label.grid(row=row, column=1, columnspan=2, sticky="w", padx=10)
    row += 1

    row_after_net_mode = row
    bridge_name_label = ttk.Label(main_frame, text="Bridge Name:", font=("SF Pro", 12, "bold"))
    bridge_name_entry = ttk.Entry(main_frame, textvariable=bridge_name_var, width=45)
    row += 1

    ttk.Label(main_frame, text="Shared Folder:", font=("SF Pro", 12, "bold")).grid(
        row=row, column=0, sticky="w", pady=5
    )
    ttk.Entry(main_frame, textvariable=share_path_var, width=45).grid(row=row, column=1, padx=10, pady=5)
    ttk.Button(
        main_frame,
        text="Browse",
        command=lambda: share_path_var.set(filedialog.askdirectory(parent=dialog) or share_path_var.get()),
    ).grid(row=row, column=2)
    row += 1

    ttk.Label(main_frame, text="Mount Tag:", font=("SF Pro", 12, "bold")).grid(row=row, column=0, sticky="w", pady=5)
    ttk.Entry(main_frame, textvariable=share_name_var, width=45).grid(row=row, column=1, padx=10, pady=5)
    row += 1

    # Options Group
    opt_frame = ttk.LabelFrame(main_frame, text=" Hardware Options ", padding="10 10 10 10")
    opt_frame.grid(row=row, column=0, columnspan=3, sticky="ew", pady=15)
    row += 1

    ttk.Checkbutton(opt_frame, text="Webcam", variable=webcam_var).grid(row=0, column=0, padx=10)
    ttk.Checkbutton(opt_frame, text="Microphone", variable=mic_var).grid(row=0, column=1, padx=10)
    ttk.Checkbutton(opt_frame, text="Clipboard", variable=guest_agent_var).grid(row=0, column=2, padx=10)
    ttk.Checkbutton(opt_frame, text="Fullscreen", variable=fullscreen_var).grid(row=0, column=3, padx=10)

    def on_save():
        # Validate and save logic...
        values = {
            "arch": arch_var.get(),
            "qemu_executable": qemu_var.get(),
            "disk_path": disk_var.get(),
            "firmware_path": fw_var.get(),
            "shared_dir_path": share_path_var.get(),
            "mount_tag": share_name_var.get(),
            "enable_webcam": webcam_var.get(),
            "network_mode": net_modes[net_mode_combo.get()],
            "bridge_name": bridge_name_var.get(),
            "enable_guest_agent": guest_agent_var.get(),
            "enable_microphone": mic_var.get(),
            "enable_fullscreen": fullscreen_var.get(),
        }
        if not all(values[k] for k in ["qemu_executable", "disk_path", "firmware_path"]):
            messagebox.showerror("Error", "Required paths are missing.", parent=dialog)
            return
        save_config(values)
        SETUP_COMPLETE_FILE.touch(exist_ok=True)
        root.destroy()
        run_launcher(load_config())

    # Buttons
    btn_frame = ttk.Frame(main_frame)
    btn_frame.grid(row=row, column=1, columnspan=2, sticky="e", pady=10)
    ttk.Button(btn_frame, text="Cancel", command=root.destroy).pack(side="right", padx=5)
    ttk.Button(btn_frame, text="Save & Launch", style="Accent.TButton", command=on_save).pack(side="right")

    dialog.protocol("WM_DELETE_WINDOW", root.destroy)
    for display, value in net_modes.items():
        if value == net_mode_var.get():
            net_mode_combo.set(display)
    on_net_mode_change()

    dialog.update_idletasks()
    x = (dialog.winfo_screenwidth() // 2) - (dialog.winfo_width() // 2)
    y = (dialog.winfo_screenheight() // 2) - (dialog.winfo_height() // 2)
    dialog.geometry(f"+{x}+{y}")
    root.mainloop()


# ================================================================
# MAIN
# ================================================================
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="QEMU Launcher")
    parser.add_argument("--config", help="Path to config file")
    parser.add_argument("--dry-run", action="store_true", help="Print command and exit")
    parser.add_argument("--setup", action="store_true", help="Force setup UI")
    parser.add_argument("--integrity-check", action="store_true", help="Verify bundle integrity")
    args = parser.parse_args()

    if args.integrity_check:
        print("[INTEGRITY] Success: All core modules loaded.")
        sys.exit(0)

    # Ensure common paths are in PATH
    common_paths = ["/usr/local/bin", "/opt/homebrew/bin", "/usr/bin", "/bin"]
    current_path = os.environ.get("PATH", "")
    for p in common_paths:
        if p not in current_path:
            current_path = f"{p}:{current_path}"
    os.environ["PATH"] = current_path

    config = load_config(args.config)

    # Start background monitor
    GestureMonitor.start(lambda: run_setup_ui(config))

    if args.dry_run:
        if config:
            print(" ".join(run_launcher(config, dry_run=True)))
        else:
            print("Error: No config found for dry-run")
            sys.exit(1)
    elif args.setup or not config or not SETUP_COMPLETE_FILE.is_file():
        run_setup_ui(config)
    else:
        run_launcher(config)
