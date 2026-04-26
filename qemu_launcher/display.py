from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass

from PySide6.QtGui import QGuiApplication

PRIMARY_DISPLAY_NAME = "Primary Display"
logger = logging.getLogger("qemu-launcher")


@dataclass
class DisplayTarget:
    name: str
    x: int
    y: int
    width: int
    height: int
    primary: bool = False


def is_primary_display_name(name: str | None) -> bool:
    if not name:
        return True
    return name == PRIMARY_DISPLAY_NAME or name.endswith(" (Primary)")


def should_qemu_handle_fullscreen(target_display_name: str | None, enable_fullscreen: bool) -> bool:
    """Return True if QEMU itself should handle the fullscreen transition."""
    if not enable_fullscreen:
        return False
    # On Mac, Cocoa backend only handles Primary correctly.
    # Secondary monitors on Mac use SDL + env var (handled in launch()).
    if sys.platform == "darwin":
        return is_primary_display_name(target_display_name)
    # On Linux, GTK/SDL handle fullscreen well natively if we pass the flag.
    return True


def get_display_index(target_name: str | None) -> int:
    """Return the system index of the display matching target_name."""
    if is_primary_display_name(target_name):
        return 0
    displays = available_displays()
    for i, d in enumerate(displays):
        if d.name == target_name:
            return i
    return 0


def available_displays() -> list[DisplayTarget]:
    app = QGuiApplication.instance()
    if app:
        displays: list[DisplayTarget] = []
        primary = app.primaryScreen()
        for index, screen in enumerate(app.screens()):
            geometry = screen.geometry()
            name = screen.name() or f"Display {index + 1}"
            is_primary = screen is primary
            if is_primary:
                name = f"{name} (Primary)"
            displays.append(
                DisplayTarget(
                    name=name,
                    x=geometry.x(),
                    y=geometry.y(),
                    width=geometry.width(),
                    height=geometry.height(),
                    primary=is_primary,
                )
            )
        if displays:
            return displays
    if sys.platform == "darwin":
        return _available_displays_macos()
    return []


def resolve_display(name: str | None) -> DisplayTarget | None:
    displays = available_displays()
    if not displays:
        return None
    if is_primary_display_name(name):
        return next((display for display in displays if display.primary), displays[0])
    normalized = _normalize_name(name)
    for display in displays:
        if _normalize_name(display.name) == normalized:
            return display
    for display in displays:
        if normalized in _normalize_name(display.name):
            return display
    return next((display for display in displays if display.primary), displays[0])


def arrange_window(pid: int, target_display_name: str | None, fullscreen: bool) -> str | None:
    target = resolve_display(target_display_name)
    if not target:
        return "Display placement unavailable."
    if sys.platform == "darwin":
        return _arrange_window_macos(pid, target, fullscreen)
    if sys.platform.startswith("linux"):
        return _arrange_window_linux(pid, target, fullscreen)
    return None


def _normalize_name(name: str | None) -> str:
    if not name:
        return ""
    return name.removesuffix(" (Primary)").strip().lower()


def _available_displays_macos() -> list[DisplayTarget]:
    try:
        import Quartz
    except Exception:
        return []
    displays: list[DisplayTarget] = []

    # Get all online displays
    max_displays = 32
    err, online_displays, display_count = Quartz.CGGetOnlineDisplayList(max_displays, None, None)
    if err != 0:
        return []

    main_display = Quartz.CGMainDisplayID()

    # Create mapping from display ID to localized name using NSScreen
    names = {}
    try:
        import AppKit

        for screen in AppKit.NSScreen.screens():
            desc = screen.deviceDescription()
            d_id = desc.objectForKey_("NSScreenNumber")
            names[d_id] = screen.localizedName()
    except Exception:
        pass

    for i in range(display_count):
        display_id = online_displays[i]
        bounds = Quartz.CGDisplayBounds(display_id)

        # Determine name
        name = names.get(display_id, f"Display {i + 1}")
        if display_id == main_display:
            name = f"{name} (Primary)"

        displays.append(
            DisplayTarget(
                name=name,
                x=int(bounds.origin.x),
                y=int(bounds.origin.y),
                width=int(bounds.size.width),
                height=int(bounds.size.height),
                primary=(display_id == main_display),
            )
        )
    return displays


def _arrange_window_linux(pid: int, target: DisplayTarget, fullscreen: bool) -> str | None:
    if not _command_exists("wmctrl"):
        return "Install wmctrl to enable non-primary display placement on Linux."
    window_id = _find_wmctrl_window_id(pid)
    if not window_id:
        return "Unable to locate the QEMU window for display placement."
    x = target.x + 40
    y = target.y + 40
    width = max(target.width - 80, 640)
    height = max(target.height - 80, 480)
    subprocess.run(
        ["wmctrl", "-i", "-r", window_id, "-e", f"0,{x},{y},{width},{height}"],
        check=False,
        capture_output=True,
        text=True,
    )
    subprocess.run(["wmctrl", "-i", "-a", window_id], check=False, capture_output=True, text=True)
    if fullscreen:
        subprocess.run(
            ["wmctrl", "-i", "-r", window_id, "-b", "add,fullscreen"],
            check=False,
            capture_output=True,
            text=True,
        )
    return None


def _find_wmctrl_window_id(pid: int, timeout: float = 10.0) -> str | None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        result = subprocess.run(["wmctrl", "-lp"], check=False, capture_output=True, text=True)
        for line in (result.stdout or "").splitlines():
            parts = line.split(None, 4)
            if len(parts) >= 3 and parts[2].isdigit() and int(parts[2]) == pid:
                return parts[0]
        time.sleep(0.25)
    return None


def _arrange_window_macos(pid: int, target: DisplayTarget, fullscreen: bool) -> str | None:
    """Move and optionally fullscreen the QEMU window on macOS using native AX API."""
    logger.info(f"Arranging window for PID {pid} on display {target.name} (FS={fullscreen})")
    try:
        import Quartz
        from ApplicationServices import (
            AXIsProcessTrustedWithOptions,
            AXUIElementCopyAttributeValue,
            AXUIElementCreateApplication,
            AXUIElementSetAttributeValue,
            AXValueGetValue,
            kAXFrontmostAttribute,
            kAXTrustedCheckOptionPrompt,
            kAXValueCGPointType,
        )

        # Accessibility attribute names are strings. Some bridge versions miss the constants.
        AX_FULLSCREEN = "AXFullScreen"
    except Exception as exc:
        logger.error(f"Failed to import ApplicationServices/Quartz: {exc}")
        return f"macOS display placement unavailable: {exc}"

    # 1. Check/Prompt for permissions
    options = {kAXTrustedCheckOptionPrompt: True}
    if not AXIsProcessTrustedWithOptions(options):
        logger.warning("Accessibility permissions NOT granted.")
        return "Grant Accessibility permission in System Settings to enable display placement."

    # 2. Create the AX application element
    app = AXUIElementCreateApplication(pid)

    # 3. Wait for the window to appear
    window = None
    deadline = time.time() + 15.0
    while time.time() < deadline:
        # 3a. Try AX first
        elements = []
        for attr in ["AXWindows", "AXChildren"]:
            err_v, vals = AXUIElementCopyAttributeValue(app, attr, None)
            if err_v == 0 and vals:
                elements.extend(vals)

        for i, win in enumerate(elements):
            e_role, role_val = AXUIElementCopyAttributeValue(win, "AXRole", None)
            if role_val == "AXWindow":
                window = win
                logger.info("Located QEMU display window via AX.")
                break

        if window:
            break

        # 3b. Fallback: Use Quartz to find the window handle if AX is blind
        # This gives us the window ID which we might be able to use?
        # Actually, we need an AXUIElement to move it.
        # So we'll keep trying AX but maybe the window is delayed.

        # Log Quartz info for debugging
        window_list = Quartz.CGWindowListCopyWindowInfo(Quartz.kCGWindowListOptionAll, Quartz.kCGNullWindowID)
        for win_info in window_list:
            if win_info.get(Quartz.kCGWindowOwnerPID) == pid:
                w_id = win_info.get(Quartz.kCGWindowNumber)
                w_name = win_info.get(Quartz.kCGWindowName, "")
                w_bounds = win_info.get(Quartz.kCGWindowBounds)
                logger.info(f"Quartz saw window for PID {pid}: ID={w_id}, Name='{w_name}', Bounds={w_bounds}")

        time.sleep(0.5)

    # 4. Bring to front
    AXUIElementSetAttributeValue(app, kAXFrontmostAttribute, True)
    time.sleep(0.5)

    # 5. Set Position & Size with AppleScript Fallback
    # On macOS, native AX moves often fail over SSH/Headless due to TCC restrictions.
    # AppleScript (System Events) is more robust in these environments.
    logger.info(f"Moving window for PID {pid} to ({target.x}, {target.y}) via AppleScript fallback...")
    script = f"""
    tell application "System Events"
        set qemu_proc to first process whose unix id is {pid}
        set win_list to windows of qemu_proc
        if (count of win_list) > 0 then
            set win to item 1 of win_list
            set position of win to {{{target.x}, {target.y}}}
            set size of win to {{{target.width - 40}, {target.height - 40}}}
        end if
    end tell
    """
    try:
        subprocess.run(["osascript", "-e", script], check=True, capture_output=True)
        logger.info("AppleScript move command sent.")
    except Exception as e:
        logger.warning(f"AppleScript move failed: {e}")

    # 6. Verification & AX Fullscreen Toggle
    success_move = False
    deadline_move = time.time() + 5.0
    while time.time() < deadline_move:
        # Re-check AX for verification
        elements = []
        for attr in ["AXWindows", "AXChildren"]:
            err_v, vals = AXUIElementCopyAttributeValue(app, attr, None)
            if err_v == 0 and vals:
                elements.extend(vals)

        for win in elements:
            e_role, role_val = AXUIElementCopyAttributeValue(win, "AXRole", None)
            if role_val == "AXWindow":
                window = win
                break

        if window:
            err_check, current_pos_val = AXUIElementCopyAttributeValue(window, "AXPosition", None)
            if err_check == 0 and current_pos_val:
                ok, current_pos = AXValueGetValue(current_pos_val, kAXValueCGPointType, None)
                if ok and abs(current_pos.x - target.x) < 100:
                    logger.info(f"Confirmed window moved to {current_pos.x}, {current_pos.y}")
                    success_move = True
                    break

        time.sleep(1.0)

    # 6. Toggle Fullscreen if requested
    if fullscreen:
        logger.info("Requesting fullscreen toggle.")
        time.sleep(1.0)

        # Try AX first
        err_fs = -1
        if window:
            err_fs = AXUIElementSetAttributeValue(window, AX_FULLSCREEN, True)

        # Fallback to AppleScript if AX failed or window not found
        if err_fs != 0:
            logger.info("AX fullscreen toggle failed or window not found; using AppleScript fallback.")
            script_fs = f"""
            tell application "System Events"
                set qemu_proc to first process whose unix id is {pid}
                set win_list to windows of qemu_proc
                if (count of win_list) > 0 then
                    set win to item 1 of win_list
                    set value of attribute "AXFullScreen" of win to true
                end if
            end tell
            """
            try:
                subprocess.run(["osascript", "-e", script_fs], check=True, capture_output=True)
                logger.info("AppleScript fullscreen command sent.")
            except Exception as e:
                logger.warning(f"AppleScript fullscreen failed: {e}")
        # 7. Verify Fullscreen
        time.sleep(2.0)
        err_fs_check = -1
        if window:
            err_fs_check, fs_val = AXUIElementCopyAttributeValue(window, "AXFullScreen", None)
            if err_fs_check == 0 and fs_val:
                logger.info(f"Confirmed Fullscreen status via AX: {fs_val}")

        if err_fs_check != 0:
            # Try AppleScript check
            script_check = f"""
            tell application "System Events"
                set qemu_proc to first process whose unix id is {pid}
                set win to first window of qemu_proc
                return value of attribute "AXFullScreen" of win
            end tell
            """
            try:
                out_fs = subprocess.check_output(["osascript", "-e", script_check]).decode().strip()
                logger.info(f"AppleScript confirmed Fullscreen status: {out_fs}")
            except Exception:
                logger.warning("Could not verify fullscreen status via AX or AppleScript.")

    if not success_move:
        logger.error("Failed to move window to target monitor after multiple attempts.")
        return "macOS AX placement failed to verify move."

    logger.info("Window arrangement complete.")
    return None


def set_fullscreen(pid: int, target_display_name: str | None, enabled: bool) -> str | None:
    if sys.platform == "darwin":
        return _set_fullscreen_macos(pid, target_display_name, enabled)
    if sys.platform.startswith("linux"):
        return _set_fullscreen_linux(pid, enabled)
    return None


def _set_fullscreen_linux(pid: int, enabled: bool) -> str | None:
    session_type = os.environ.get("XDG_SESSION_TYPE", "").lower()
    if session_type == "wayland":
        action = "Enter" if enabled else "Exit"
        return f"Wayland detected: Please use QEMU's internal hotkey (Ctrl+Alt+F) to {action} Fullscreen."

    if not _command_exists("wmctrl"):
        return "Install wmctrl to enable fullscreen control on Linux."
    window_id = _find_wmctrl_window_id(pid)
    if not window_id:
        return "Unable to locate the QEMU window."

    action = "add" if enabled else "remove"
    subprocess.run(
        ["wmctrl", "-i", "-r", window_id, "-b", f"{action},fullscreen"],
        check=False,
        capture_output=True,
        text=True,
    )
    return None


def _set_fullscreen_macos(pid: int, target_display_name: str | None, enabled: bool) -> str | None:
    # On macOS, we reuse the arrange logic but specifically for the toggle
    target = resolve_display(target_display_name)
    if not target:
        return "Display unavailable."
    return _arrange_window_macos(pid, target, enabled)


def _command_exists(name: str) -> bool:
    return shutil.which(name) is not None
