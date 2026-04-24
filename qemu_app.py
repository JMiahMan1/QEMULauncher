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

try:
    import Quartz
except ImportError:
    Quartz = None

# Global Process Tracker
CURRENT_QEMU_PROCESS = None

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
            primary_frame = screens[0].frame()
            max_y = int(primary_frame.size.height)

            displays = []
            for i, screen in enumerate(screens):
                frame = screen.frame()
                # Convert AppKit (bottom-up) to Tkinter (top-down)
                # Tkinter Y = 0 is the top of the primary screen
                tk_y = int(max_y - (frame.origin.y + frame.size.height))

                displays.append(
                    {
                        "index": i,
                        "x": int(frame.origin.x),
                        "y": tk_y,
                        "width": int(frame.size.width),
                        "height": int(frame.size.height),
                        "is_primary": i == 0,
                    }
                )
            return displays
        except Exception as e:
            debug_print(f"Display detection failed: {e}")
            return [{"x": 0, "y": 0, "width": 1920, "height": 1080, "is_primary": True}]

    @staticmethod
    def get_target_display():
        displays = DisplayManager.get_displays()
        if len(displays) > 1:
            return displays[1]  # Return secondary
        return displays[0]  # Return primary


class WindowManager:
    @staticmethod
    def _safe_import_ax():
        """Safely imports AXUIElement functions from AppKit or ApplicationServices."""
        try:
            from AppKit import AXUIElementCopyAttributeValue, AXUIElementCreateApplication, AXUIElementSetAttributeValue

            return AXUIElementCopyAttributeValue, AXUIElementCreateApplication, AXUIElementSetAttributeValue
        except ImportError:
            try:
                from ApplicationServices import (
                    AXUIElementCopyAttributeValue,
                    AXUIElementCreateApplication,
                    AXUIElementSetAttributeValue,
                )

                return AXUIElementCopyAttributeValue, AXUIElementCreateApplication, AXUIElementSetAttributeValue
            except ImportError:
                debug_print("Critical Error: Could not find AXUIElement functions in AppKit or ApplicationServices.")
                return None, None, None

    @staticmethod
    def toggle_fullscreen(config=None):
        """Toggles fullscreen state of the QEMU window."""
        if AppKit is None or Quartz is None:
            return

        AXCopy, AXCreate, AXSet = WindowManager._safe_import_ax()
        if not AXCopy:
            return

        try:
            from Quartz import CGWindowListCopyWindowInfo, kCGNullWindowID, kCGWindowListOptionAll, kCGWindowOwnerPID

            window_list = CGWindowListCopyWindowInfo(kCGWindowListOptionAll, kCGNullWindowID)
            for window in window_list:
                owner_name = window.get("kCGWindowOwnerName", "").lower()
                if "qemu-system" in owner_name:
                    pid = window.get(kCGWindowOwnerPID)

                    # Try native Accessibility first
                    app_ref = AXCreate(pid)
                    error, windows = AXCopy(app_ref, "AXWindows", None)

                    if error != 0 or not windows:
                        # Fallback to focused window of that app
                        error, focused = AXCopy(app_ref, "AXFocusedWindow", None)
                        windows = [focused] if error == 0 and focused else []

                    if error == 0 and windows:
                        win = windows[0]
                        error, current = AXCopy(win, "AXFullScreen", None)
                        AXSet(win, "AXFullScreen", not current)
                        debug_print(f"Toggled fullscreen via AXUIElement for PID {pid}")
                        return
                    else:
                        # Fallback to Native Quartz Keystroke (Cmd+Ctrl+F)
                        # 'f' is keycode 3
                        from Quartz import (
                            CGEventCreateKeyboardEvent,
                            CGEventPost,
                            CGEventSetFlags,
                            kCGEventFlagMaskCommand,
                            kCGEventFlagMaskControl,
                            kCGHIDEventTap,
                        )

                        f_down = CGEventCreateKeyboardEvent(None, 3, True)
                        CGEventSetFlags(f_down, kCGEventFlagMaskCommand | kCGEventFlagMaskControl)
                        CGEventPost(kCGHIDEventTap, f_down)

                        f_up = CGEventCreateKeyboardEvent(None, 3, False)
                        CGEventPost(kCGHIDEventTap, f_up)
                        debug_print(f"Toggled fullscreen via Quartz event for PID {pid}")
                        return
            debug_print("Toggle Fullscreen: QEMU window not found.")
        except Exception as e:
            debug_print(f"Fullscreen toggle failed: {e}")

    @staticmethod
    def orchestrate_window(pid=None, target_display=None, fullscreen=False):
        """Finds the QEMU window and applies position/fullscreen."""
        if AppKit is None or Quartz is None:
            return

        AXCopy, AXCreate, AXSet = WindowManager._safe_import_ax()
        if not AXCopy:
            return

        from Quartz import (
            CGWindowListCopyWindowInfo,
            kCGNullWindowID,
            kCGWindowListOptionAll,
            kCGWindowOwnerPID,
        )

        def _orchestrate():
            start_time = time.time()
            actual_pid = pid

            debug_print(f"Orchestrating window for target PID {pid} (fullscreen={fullscreen})...")

            while time.time() - start_time < 20:
                window_list = CGWindowListCopyWindowInfo(kCGWindowListOptionAll, kCGNullWindowID)
                for window in window_list:
                    owner_name = window.get("kCGWindowOwnerName", "").lower()
                    if "qemu-system" in owner_name:
                        win_pid = window.get(kCGWindowOwnerPID)

                        # Match by PID if provided, otherwise grab first QEMU window
                        if actual_pid is None or win_pid == actual_pid:
                            actual_pid = win_pid

                            app_ref = AXCreate(actual_pid)
                            error, windows = AXCopy(app_ref, "AXWindows", None)

                            if error == 0 and windows:
                                win = windows[0]
                                debug_print(f"Applying orchestration to PID {actual_pid}")

                                # 1. Position
                                if target_display:
                                    AXSet(win, "AXPosition", (target_display["x"], target_display["y"]))

                                # 2. Fullscreen
                                if fullscreen:
                                    # Wait a tiny bit for the window to stabilize
                                    time.sleep(1)
                                    AXSet(win, "AXFullScreen", True)

                                return
                time.sleep(1)
            debug_print("Orchestration timeout: Window not found.")

        import threading

        threading.Thread(target=_orchestrate, daemon=True).start()


class HotspotWindow:
    def __init__(self, root, settings_callback):
        debug_print("Initializing HotspotWindow...")
        self.root = root
        self.window = tk.Toplevel(self.root)

        # Use a more robust way to hide title bar on macOS
        self.window.overrideredirect(True)
        self.window.attributes("-topmost", True)
        self.window.attributes("-alpha", 0.6)
        self.window.configure(bg="#1a2b3c")  # Subtle Deep Blue

        # Position at top center of target display
        target = DisplayManager.get_target_display()
        width, height = 400, 15  # Even larger hit area for confirmation
        x = target["x"] + (target["width"] // 2) - (width // 2)
        y = target["y"]

        geo = f"{width}x{height}+{x}+{y}"
        debug_print(f"Applying Hotspot geometry: {geo}")
        self.window.geometry(geo)

        self.window.bind("<Enter>", lambda e: self.on_enter())
        self.window.bind("<Leave>", lambda e: self.on_leave())
        self.settings_callback = settings_callback
        self.hover_start = None

        self.window.lift()
        self.window.update()
        debug_print("HotspotWindow initialized and lifted.")

    def on_enter(self):
        debug_print("Hotspot hover started")
        self.root.lift()
        self.root.focus_force()
        self.window.configure(bg="#00ff00")
        self.hover_start = time.time()
        self.check_hover()

    def on_leave(self):
        self.window.configure(bg="#1a2b3c")
        self.hover_start = None

    def check_hover(self):
        if self.hover_start and (time.time() - self.hover_start >= 0.8):  # Faster trigger (0.8s)
            debug_print("Hotspot trigger activated!")
            self.settings_callback()
            self.hover_start = None
        elif self.hover_start:
            self.window.after(100, self.check_hover)


class GestureMonitor:
    _instance = None

    @staticmethod
    def start(root, settings_callback):
        if GestureMonitor._instance is None:
            GestureMonitor._instance = HotspotWindow(root, settings_callback)
        return GestureMonitor._instance


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


def kill_existing_qemu():
    """Kills any QEMU processes managed by this launcher."""
    global CURRENT_QEMU_PROCESS
    if CURRENT_QEMU_PROCESS:
        try:
            debug_print(f"Terminating existing QEMU process (PID {CURRENT_QEMU_PROCESS.pid})...")
            CURRENT_QEMU_PROCESS.terminate()
            CURRENT_QEMU_PROCESS.wait(timeout=5)
        except Exception as e:
            debug_print(f"Failed to gracefully terminate QEMU: {e}")
            try:
                CURRENT_QEMU_PROCESS.kill()
            except Exception:
                pass
        CURRENT_QEMU_PROCESS = None


def run_launcher(config, dry_run=False):
    if not dry_run:
        kill_existing_qemu()

    if not config or not config.get("disk_path") or not config.get("qemu_executable"):
        debug_print("Launch cancelled: configuration is invalid.")
        return

    qemu_executable = config["qemu_executable"]
    firmware_path = config["firmware_path"]
    disk_path = config["disk_path"]

    # Fullscreen at start
    fs_val = "on" if config.get("enable_fullscreen") else "off"

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
        f"cocoa,show-cursor=on,zoom-to-fit=on,full-screen={fs_val}",
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
    debug_print(f"Loaded network_mode from config: {net_mode}")

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

    # Needs root for anything other than 'user' mode (vmnet requires root)
    needs_root = net_mode != "user"
    try:
        debug_print(f"Final Command Construction: needs_root={needs_root}")

        # Robust shell escaping for AppleScript/Shell
        def sh_escape(s):
            return "'" + s.replace("'", "'\\''") + "'"

        escaped_command = [sh_escape(a) for a in qemu_command]
        debug_print("Full QEMU command:", " ".join(qemu_command))

        global CURRENT_QEMU_PROCESS
        if needs_root:
            if sys.platform == "darwin":
                # macOS Native elevation
                from AppKit import NSAppleScript

                cmd_str = " ".join(escaped_command)
                debug_print(f"Executing elevated script: {cmd_str}")
                script_src = f'do shell script "{cmd_str}" with administrator privileges'
                script = NSAppleScript.alloc().initWithSource_(script_src)

                def _run_elevated():
                    result, error = script.executeAndReturnError_(None)
                    if error:
                        debug_print(f"Elevated launch failed: {error}")
                    else:
                        debug_print("Elevated launch successful.")

                import threading

                threading.Thread(target=_run_elevated, daemon=True).start()
                proc = None
            else:
                # Linux Native elevation via pkexec
                debug_print("Triggering Linux elevation via pkexec...")
                CURRENT_QEMU_PROCESS = subprocess.Popen(["pkexec"] + qemu_command, env=qemu_env)
                proc = CURRENT_QEMU_PROCESS
        else:
            CURRENT_QEMU_PROCESS = subprocess.Popen(qemu_command, env=qemu_env)
            proc = CURRENT_QEMU_PROCESS

        # Trigger Window Orchestration (Background)
        if sys.platform == "darwin":
            target_display = DisplayManager.get_target_display()
            fullscreen = config.get("enable_fullscreen", "False")
            if isinstance(fullscreen, str):
                fullscreen = fullscreen.lower() == "true"
            # If elevated, proc is None, but orchestrate_window will search by name
            pid = proc.pid if proc else None
            WindowManager.orchestrate_window(pid=pid, target_display=target_display, fullscreen=fullscreen)

        return proc

    except Exception as e:
        show_error("Launch Error", f"Failed to run QEMU.\n\nError: {e}")
        sys.exit(1)


# ================================================================
# SETUP UI
# ================================================================
def run_setup_ui(existing_config=None, parent_root=None):
    root = parent_root or tk.Tk()
    if not parent_root:
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

        # Close the dialog but DON'T kill the root if it's the main root
        dialog.destroy()

        # Offer to restart
        if messagebox.askyesno("Settings Saved", "Restart VM now to apply settings?", parent=root):
            # Reload and relaunch
            config = load_config()
            run_launcher(config)
        else:
            debug_print("Settings saved. Changes will apply on next restart.")

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

    if args.dry_run:
        if config:
            cmd = run_launcher(config, dry_run=True)
            print(" ".join(cmd))
            sys.exit(0)
        else:
            print("Error: No configuration found for dry-run.")
            sys.exit(1)

    # --- UI REQUIRED FROM THIS POINT ---
    root = tk.Tk()
    root.title("QEMU Launcher")

    # Configure global styles
    style = ttk.Style()
    if sys.platform == "darwin":
        style.theme_use("aqua")

    # Shared Fullscreen Toggle Logic
    def toggle_fullscreen_callback(event=None):
        WindowManager.toggle_fullscreen(config)

    # Setup Hidden Menu for Hotkey Binding
    menubar = tk.Menu(root)
    view_menu = tk.Menu(menubar, tearoff=0)
    view_menu.add_command(label="Toggle Fullscreen", command=toggle_fullscreen_callback, accelerator="Cmd+Ctrl+F")
    view_menu.add_command(label="Settings...", command=lambda: run_setup_ui(config, parent_root=root))
    menubar.add_cascade(label="View", menu=view_menu)
    root.config(menu=menubar)

    # Bind Key Event
    root.bind_all("<Control-Command-f>", toggle_fullscreen_callback)

    if args.setup or not config:
        # SETUP MODE
        run_setup_ui(config or {}, parent_root=root)
        root.mainloop()
    else:
        # RUN MODE
        root.withdraw()  # Hide launcher root
        debug_print("Starting QEMU Launcher in Run Mode...")

        # Start QEMU
        proc = run_launcher(config)

        # Initialize Hotspot
        _ = GestureMonitor.start(root, lambda: run_setup_ui(config, parent_root=root))

        # Start Global Monitor for macOS
        if sys.platform == "darwin" and AppKit:
            try:
                from AppKit import NSCommandKeyMask, NSControlKeyMask, NSEvent, NSKeyDownMask

                def global_key_handler(event):
                    if event.keyCode() == 3:  # 'F'
                        flags = event.modifierFlags()
                        if (flags & NSCommandKeyMask) and (flags & NSControlKeyMask):
                            debug_print("Global Hotkey Detected: Cmd+Ctrl+F")
                            root.after(0, toggle_fullscreen_callback)
                    return event

                NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(NSKeyDownMask, global_key_handler)
            except Exception as e:
                debug_print(f"Failed to setup global monitor: {e}")

        root.mainloop()
