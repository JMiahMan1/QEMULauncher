from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass

from PySide6.QtGui import QGuiApplication

PRIMARY_DISPLAY_NAME = "Primary Display"


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
        import AppKit
    except Exception:
        return []
    displays: list[DisplayTarget] = []
    main_screen = AppKit.NSScreen.mainScreen()
    for index, screen in enumerate(AppKit.NSScreen.screens()):
        frame = screen.frame()
        name = getattr(screen, "localizedName", lambda: None)() or f"Display {index + 1}"
        is_primary = screen == main_screen
        if is_primary:
            name = f"{name} (Primary)"
        displays.append(
            DisplayTarget(
                name=str(name),
                x=int(frame.origin.x),
                y=int(frame.origin.y),
                width=int(frame.size.width),
                height=int(frame.size.height),
                primary=is_primary,
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
    try:
        from ApplicationServices import (
            AXIsProcessTrusted,
            AXUIElementCopyAttributeValue,
            AXUIElementCreateApplication,
            AXUIElementPerformAction,
            AXUIElementSetAttributeValue,
            AXValueCreate,
            AXValueGetValue,
            kAXChildrenAttribute,
            kAXFrontmostAttribute,
            kAXFullScreenAttribute,
            kAXMenuBarAttribute,
            kAXPositionAttribute,
            kAXPressAction,
            kAXSizeAttribute,
            kAXTitleAttribute,
            kAXValueCGPointType,
            kAXValueCGSizeType,
            kAXWindowsAttribute,
        )
        from Quartz import CGPointMake, CGSizeMake
    except Exception as exc:
        return f"macOS display placement unavailable: {exc}"

    if not AXIsProcessTrusted():
        return "Grant Accessibility permission to enable display placement on macOS."

    app = AXUIElementCreateApplication(pid)
    deadline = time.time() + 10.0
    window = None
    while time.time() < deadline:
        error, windows = AXUIElementCopyAttributeValue(app, kAXWindowsAttribute, None)
        if error == 0 and windows:
            window = windows[0]
            break
        time.sleep(0.25)
    if window is None:
        return "Unable to locate the QEMU window for display placement."

    # 1. Bring QEMU to the front
    AXUIElementSetAttributeValue(app, kAXFrontmostAttribute, True)
    time.sleep(0.5)

    # 2. Get current aspect ratio to avoid 'ding'/conflict
    # QEMU Cocoa enforces an aspect ratio constraint. We must respect it
    # during the move to avoid the OS rejecting the resize.
    _, current_size_val = AXUIElementCopyAttributeValue(window, kAXSizeAttribute, None)
    if current_size_val:
        ok, current_size = AXValueGetValue(current_size_val, kAXValueCGSizeType, None)
        if ok:
            ratio = current_size.width / current_size.height
            # Calculate largest width that fits the target monitor's height with this ratio
            new_h = target.height - 40
            new_w = new_h * ratio
            if new_w > target.width:
                new_w = target.width - 40
                new_h = new_w / ratio

            position = AXValueCreate(
                kAXValueCGPointType,
                CGPointMake(target.x + (target.width - new_w) / 2, target.y + (target.height - new_h) / 2),
            )
            size = AXValueCreate(kAXValueCGSizeType, CGSizeMake(new_w, new_h))

            AXUIElementSetAttributeValue(window, kAXPositionAttribute, position)
            AXUIElementSetAttributeValue(window, kAXSizeAttribute, size)

    # 3. Wait for the Window Manager to settle
    time.sleep(1.0)

    if fullscreen:
        # Strategy 1: Direct AXFullScreen attribute
        AXUIElementSetAttributeValue(window, kAXFullScreenAttribute, True)
        time.sleep(0.5)
        _, is_fs = AXUIElementCopyAttributeValue(window, kAXFullScreenAttribute, None)
        if is_fs:
            return None

        # Strategy 2: AppleScript Keystroke (QEMU Cocoa uses Cmd+F)
        script = f"""
        tell application "System Events"
            set proc to first process whose unix id is {pid}
            set frontmost of proc to true
            keystroke "f" using {{command down}}
        end tell
        """
        subprocess.run(["osascript", "-e", script], capture_output=True)
        time.sleep(1.0)

        # Check again
        _, is_fs = AXUIElementCopyAttributeValue(window, kAXFullScreenAttribute, None)
        if is_fs:
            return None

        # Strategy 3: Menu Bar Traversal (Final Fallback)
        _, menubar = AXUIElementCopyAttributeValue(app, kAXMenuBarAttribute, None)
        if menubar:
            _, items = AXUIElementCopyAttributeValue(menubar, kAXChildrenAttribute, None)
            for item in items or []:
                _, title = AXUIElementCopyAttributeValue(item, kAXTitleAttribute, None)
                if title == "View":
                    _, menu_children = AXUIElementCopyAttributeValue(item, kAXChildrenAttribute, None)
                    if menu_children:
                        _, menu_items = AXUIElementCopyAttributeValue(menu_children[0], kAXChildrenAttribute, None)
                        for m_item in menu_items or []:
                            _, m_title = AXUIElementCopyAttributeValue(m_item, kAXTitleAttribute, None)
                            if m_title and ("Full Screen" in m_title or "Fullscreen" in m_title):
                                AXUIElementPerformAction(m_item, kAXPressAction)
                                return None
    else:
        # Exit fullscreen: Try AX first, then AppleScript
        AXUIElementSetAttributeValue(window, kAXFullScreenAttribute, False)
        time.sleep(0.5)
        _, is_fs = AXUIElementCopyAttributeValue(window, kAXFullScreenAttribute, None)
        if is_fs:
            # Still FS? Try AppleScript toggle
            script = f"""
            tell application "System Events"
                set proc to first process whose unix id is {pid}
                set frontmost of proc to true
                keystroke "f" using {{command down}}
            end tell
            """
            subprocess.run(["osascript", "-e", script], capture_output=True)

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
