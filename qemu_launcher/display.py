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

    for i in range(display_count):
        display_id = online_displays[i]
        bounds = Quartz.CGDisplayBounds(display_id)

        # Determine name (simplified)
        name = f"Display {i + 1}"
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
    try:
        import Quartz
        from ApplicationServices import (
            AXUIElementCopyAttributeValue,
            AXUIElementCreateApplication,
            AXUIElementSetAttributeValue,
            AXValueCreate,
            kAXFrontmostAttribute,
            kAXFullScreenAttribute,
            kAXPositionAttribute,
            kAXSizeAttribute,
            kAXValueCGPointType,
            kAXValueCGSizeType,
            kAXWindowsAttribute,
        )
    except Exception as exc:
        return f"macOS display placement unavailable: {exc}"

    # 1. Create the AX application element
    app = AXUIElementCreateApplication(pid)

    # 2. Wait for the window to appear
    window = None
    deadline = time.time() + 10.0
    while time.time() < deadline:
        err, windows = AXUIElementCopyAttributeValue(app, kAXWindowsAttribute, None)
        if err == 0 and windows and len(windows) > 0:
            window = windows[0]
            break
        time.sleep(0.5)

    if not window:
        return "Timeout waiting for QEMU window to appear on macOS."

    # 3. Bring to front
    AXUIElementSetAttributeValue(app, kAXFrontmostAttribute, True)

    # 4. Set Position (Proven Pattern)
    # Origin is top-left in Quartz/AX
    pos = Quartz.CGPoint(x=target.x, y=target.y)
    ax_pos = AXValueCreate(kAXValueCGPointType, pos)
    err_pos = AXUIElementSetAttributeValue(window, kAXPositionAttribute, ax_pos)

    # 5. Set Size (Best Effort)
    # We use a slightly smaller size than the full monitor to avoid 'ding' if ratio doesn't match
    size = Quartz.CGSize(width=target.width - 20, height=target.height - 20)
    ax_size = AXValueCreate(kAXValueCGSizeType, size)
    AXUIElementSetAttributeValue(window, kAXSizeAttribute, ax_size)

    # 6. Toggle Fullscreen if requested
    if fullscreen:
        # Give a moment for the move to settle
        time.sleep(0.5)
        AXUIElementSetAttributeValue(window, kAXFullScreenAttribute, True)

    if err_pos != 0:
        return f"macOS AX placement returned error code: {err_pos}"

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
