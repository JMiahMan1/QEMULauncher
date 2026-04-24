import argparse
import configparser
import datetime
import encodings  # noqa: F401
import encodings.utf_8  # noqa: F401
import json
import os
import subprocess
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

# --- Environment and Logging Setup (Migrated from launcher.sh) ---
os.environ["PATH"] = f"/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:{os.environ.get('PATH', '')}"
LOG_FILE = Path("/tmp/qemu_launcher.log")


def log_message(msg):
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        with open(LOG_FILE, "a") as f:
            f.write(f"[{timestamp}] {msg}\n")
    except Exception:
        pass


# Initialize log
try:
    if not LOG_FILE.exists() or LOG_FILE.stat().st_size > 1024 * 1024:
        LOG_FILE.write_text(f"--- QEMU Launcher Started at {datetime.datetime.now()} ---\n")
except Exception:
    pass

# Native MacOS frameworks
try:
    import AppKit
except ImportError:
    AppKit = None

# ================================================================
# CONSTANTS & CONFIG
# ================================================================
CONFIG_DIR = Path.home() / ".qemu_launcher"
CONFIG_FILE = CONFIG_DIR / "config.json"
SETUP_COMPLETE_FILE = CONFIG_DIR / ".setup_done"
DEBUG = False


def debug_print(msg):
    if DEBUG or os.environ.get("DEBUG") == "1":
        print(f"[DEBUG] {msg}")


def get_smart_defaults():
    return {
        "arch": "aarch64",
        "qemu_executable": "/usr/local/bin/qemu-system-aarch64",
        "disk_path": "",
        "firmware_path": "",
        "network_mode": "vmnet-shared",
        "enable_fullscreen": True,
        "enable_webcam": False,
        "enable_microphone": False,
        "enable_guest_agent": False,
    }


# ================================================================
# SYSTEM UTILS
# ================================================================


class DisplayManager:
    @staticmethod
    def get_displays():
        """Detects displays using AppKit for macOS with top-left coordinate conversion."""
        if AppKit:
            try:
                screens = AppKit.NSScreen.screens()
                # NSScreen index 0 is always the primary display.
                primary_height = screens[0].frame().size.height
                displays = []
                for s in screens:
                    f = s.frame()
                    displays.append(
                        {
                            "x": int(f.origin.x),
                            "y": int(primary_height - (f.origin.y + f.size.height)),
                            "width": int(f.size.width),
                            "height": int(f.size.height),
                            "is_primary": f.origin.x == 0 and f.origin.y == 0,
                        }
                    )
                return displays
            except Exception as e:
                debug_print(f"AppKit detection failed: {e}")

        return [{"x": 0, "y": 0, "width": 1920, "height": 1080, "is_primary": True}]

    @staticmethod
    def get_target_display():
        """Intelligently picks the best display (the first non-primary/external one)."""
        displays = DisplayManager.get_displays()
        secondary = [d for d in displays if not d["is_primary"]]
        # If no secondary, use primary. Otherwise use the first secondary.
        return secondary[0] if secondary else displays[0]


class WindowManager:
    @staticmethod
    def orchestrate_window(proc_name, fullscreen=True):
        """Moves window to the target secondary screen and triggers fullscreen."""
        target = DisplayManager.get_target_display()
        x, y, w, h = target["x"], target["y"], target["width"], target["height"]

        debug_print(f"Moving {proc_name} to {w}x{h} at {x},{y}")

        script = f'''
        tell application "System Events"
            repeat 30 times
                set qProcs to (every process whose name contains "{proc_name}")
                if (count of qProcs) > 0 then
                    set qP to item 1 of qProcs
                    set frontmost of qP to true
                    if (count of windows of qP) > 0 then
                        set qW to window 1 of qP
                        set position of qW to {{ {x}, {y} }}
                        set size of qW to {{ {w}, {h} }}
                        if {str(fullscreen).lower()} then
                            delay 1.5
                            try
                                set value of attribute "AXFullScreen" of qW to true
                            end try
                        end if
                        return true
                    end if
                end if
                delay 0.5
            end repeat
        end tell
        '''
        threading.Thread(target=lambda: subprocess.run(["osascript", "-e", script]), daemon=True).start()


class GestureMonitor:
    @staticmethod
    def start(proc, on_trigger):
        def monitor():
            hover_start = None
            while proc.poll() is None:
                try:
                    if AppKit:
                        loc = AppKit.NSEvent.mouseLocation()
                        screens = AppKit.NSScreen.screens()
                        if screens:
                            p_f = screens[0].frame()
                            # Trigger: top 15px, middle 15%
                            in_x = (p_f.size.width * 0.42) < loc.x < (p_f.size.width * 0.58)
                            in_y = loc.y >= (p_f.size.height - 15)

                            if in_x and in_y:
                                if not hover_start:
                                    hover_start = time.time()
                                elif time.time() - hover_start >= 5:
                                    on_trigger()
                                    hover_start = None
                                    time.sleep(5)
                            else:
                                hover_start = None
                except Exception:
                    pass
                time.sleep(0.5)

        threading.Thread(target=monitor, daemon=True).start()


# ================================================================
# CORE LOGIC
# ================================================================


def load_config(path=None):
    # If path is provided, use it exactly (for CLI --config)
    if path:
        file_path = Path(path)
    else:
        # Check for new JSON config first
        if CONFIG_FILE.exists():
            file_path = CONFIG_FILE
        else:
            # Check for legacy INI config in multiple standard locations
            legacy_paths = [CONFIG_DIR / "config.ini", Path.home() / ".config" / "qemu_launcher" / "config.ini"]

            file_path = None
            for p in legacy_paths:
                if p.exists():
                    file_path = p
                    break

            if not file_path:
                return None

    # Load from file
    config_data = None

    # Try JSON
    try:
        with open(file_path, "r") as f:
            config_data = json.load(f)
    except Exception:
        # Try INI
        try:
            parser = configparser.ConfigParser()
            parser.read(file_path)
            # Support both sectioned [VM] and flat INI
            if "VM" in parser:
                config_data = {k: v.strip("\"'") for k, v in parser["VM"].items()}
            elif parser.sections():
                sect = parser.sections()[0]
                config_data = {k: v.strip("\"'") for k, v in parser[sect].items()}
        except Exception:
            pass

    # Migration Logic: If we loaded from a legacy path or non-default path, save as new JSON default
    if config_data:
        # Merge with defaults so missing fields don't cause crashes or force setup
        full_config = get_smart_defaults()
        full_config.update(config_data)

        if not CONFIG_FILE.exists():
            debug_print(f"Migrating config from {file_path} to {CONFIG_FILE}")
            save_config(full_config)

        return full_config

    return None


def save_config(config, path=None):
    file_path = Path(path) if path else CONFIG_FILE
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(file_path, "w") as f:
        json.dump(config, f, indent=4)


def validate_qemu_executable(path):
    if not path:
        return False, "Path is empty"
    p = Path(os.path.expanduser(path))
    if not p.is_file():
        return False, f"Not a file: {p}"
    if not os.access(p, os.X_OK):
        return False, f"Not executable: {p}"
    return True, ""


def run_launcher(config, dry_run=False):
    if not config or not config.get("disk_path"):
        return None

    target_display = DisplayManager.get_target_display()

    qemu_cmd = [
        config.get("qemu_executable", "qemu-system-aarch64"),
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
        f"if=pflash,format=raw,readonly=on,file={os.path.expanduser(config['firmware_path'])}",
        "-device",
        "virtio-blk-pci,drive=disk0",
        "-drive",
        f"id=disk0,if=none,format=qcow2,file={os.path.expanduser(config['disk_path'])}",
        "-display",
        "cocoa,show-cursor=on,zoom-to-fit=on",
        "-device",
        f"virtio-gpu-pci,xres={target_display['width']},yres={target_display['height']}",
        "-device",
        "virtio-keyboard-pci",
        "-device",
        "virtio-tablet-pci",
        "-device",
        "virtio-sound-pci,audiodev=snd0",
        "-audiodev",
        "coreaudio,id=snd0,in.frequency=48000,out.frequency=48000",
    ]

    # Shared Folders
    if config.get("shared_dir_path") and config.get("mount_tag"):
        qemu_cmd.extend(
            [
                "-fsdev",
                f"local,id=fsdev0,path={os.path.expanduser(config['shared_dir_path'])},security_model=mapped-xattr",
                "-device",
                f"virtio-9p-pci,fsdev=fsdev0,mount_tag={config['mount_tag']}",
            ]
        )

    # Hardware Passthrough
    if config.get("enable_webcam"):
        qemu_cmd.extend(["-device", "usb-ehci,id=usb", "-device", "usb-camera,audiodev=snd0"])

    if config.get("enable_microphone"):
        # Audio is already handled via snd0, but we can add specific mic flags if needed
        pass

    net_mode = config.get("network_mode", "vmnet-shared")
    if net_mode == "vmnet-shared":
        qemu_cmd.extend(["-netdev", "vmnet-shared,id=net0", "-device", "virtio-net-pci,netdev=net0"])
    elif net_mode == "bridge-existing":
        qemu_cmd.extend(
            [
                "-netdev",
                f"bridge,id=net0,br={config.get('bridge_name', 'bridge100')}",
                "-device",
                "virtio-net-pci,netdev=net0",
            ]
        )
    else:  # user (standard NAT, no root needed)
        qemu_cmd.extend(["-netdev", "user,id=net0", "-device", "virtio-net-pci,netdev=net0"])

    if dry_run:
        return qemu_cmd

    # Environment Isolation for QEMU
    # Prevent PyInstaller's library paths from interfering with QEMU's plugin loading
    qemu_env = os.environ.copy()
    for var in ["PYTHONPATH", "PYTHONHOME", "DYLD_LIBRARY_PATH", "LD_LIBRARY_PATH"]:
        if var in qemu_env:
            del qemu_env[var]

    needs_root = net_mode != "user"
    try:
        debug_print(f"Launching QEMU for target: {target_display['width']}x{target_display['height']}")
        if needs_root:
            cmd_str = " ".join(f"'{a}'" for a in qemu_cmd)
            proc = subprocess.Popen(
                ["osascript", "-e", f'do shell script "{cmd_str}" with administrator privileges'],
                env=qemu_env,
            )
        else:
            proc = subprocess.Popen(qemu_cmd, env=qemu_env)

        WindowManager.orchestrate_window("qemu-system", fullscreen=config.get("enable_fullscreen", True))
        return proc
    except Exception as e:
        messagebox.showerror("Error", str(e))
        return None


# ================================================================
# UI
# ================================================================


def run_setup_ui(existing_config=None):
    root = tk.Tk()
    root.title("QEMU Launcher Settings")
    cfg = existing_config or get_smart_defaults()

    entries = {}
    row = 0

    def add_field(label, key, is_file=False):
        nonlocal row
        tk.Label(root, text=label).grid(row=row, column=0, sticky="w", padx=10, pady=5)
        var = tk.StringVar(value=cfg.get(key, ""))
        tk.Entry(root, textvariable=var, width=40).grid(row=row, column=1, padx=10)
        if is_file:
            tk.Button(root, text="Browse", command=lambda: var.set(filedialog.askopenfilename() or var.get())).grid(
                row=row, column=2, padx=5
            )
        entries[key] = var
        row += 1

    add_field("QEMU Path:", "qemu_executable", True)
    add_field("Disk Image:", "disk_path", True)
    add_field("EFI Firmware:", "firmware_path", True)

    tk.Label(root, text="Net Mode:").grid(row=row, column=0, sticky="w", padx=10)
    net_var = tk.StringVar(value=cfg.get("network_mode", "vmnet-shared"))
    ttk.Combobox(root, textvariable=net_var, values=["vmnet-shared", "bridge-existing", "user"], state="readonly").grid(
        row=row, column=1, sticky="ew", padx=10
    )
    entries["network_mode"] = net_var
    row += 1

    full_var = tk.BooleanVar(value=cfg.get("enable_fullscreen", True))
    tk.Checkbutton(root, text="Auto-Fullscreen on Launch", variable=full_var).grid(row=row, column=1, sticky="w")
    entries["enable_fullscreen"] = full_var
    row += 1

    def launch():
        config = {k: (v.get() if hasattr(v, "get") else v) for k, v in entries.items()}
        for k in ["qemu_executable", "disk_path", "firmware_path"]:
            if not config[k]:
                messagebox.showerror("Error", f"{k} is required")
                return
        save_config(config)
        SETUP_COMPLETE_FILE.touch()
        root.withdraw()
        p = run_launcher(config)
        if p:
            GestureMonitor.start(p, lambda: root.after(0, lambda: (root.deiconify(), root.lift(), root.focus_force())))

    tk.Button(root, text="Launch VM", command=launch, bg="#28a745", fg="white", font=("Arial", 12, "bold")).grid(
        row=row, column=0, columnspan=3, pady=20
    )
    root.mainloop()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="QEMU Launcher")
    parser.add_argument("--config", help="Path to config file")
    parser.add_argument("--dry-run", action="store_true", help="Print command and exit")
    parser.add_argument("--setup", action="store_true", help="Force setup UI")
    parser.add_argument("--integrity-check", action="store_true", help="Verify bundle integrity")
    args = parser.parse_args()

    if args.integrity_check:
        try:
            import encodings.ascii
            import encodings.utf_8

            # Dummy use to prevent linter from stripping "unused" imports
            _ = encodings.ascii.getregentry()
            _ = encodings.utf_8.getregentry()

            print("[INTEGRITY] Success: All core modules loaded.")
            sys.exit(0)
        except Exception as e:
            print(f"[INTEGRITY] Error: {e}")
            sys.exit(64)

    c = load_config(args.config)

    if args.dry_run:
        if c:
            print(" ".join(run_launcher(c, dry_run=True)))
        else:
            print("Error: No config found for dry-run")
            sys.exit(1)
    elif args.setup or not c:
        run_setup_ui(c)
    else:
        # If config exists but setup_done doesn't, create it now that we're successfully loading
        if not SETUP_COMPLETE_FILE.exists():
            SETUP_COMPLETE_FILE.touch()
        run_launcher(c)
