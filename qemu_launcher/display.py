from __future__ import annotations

import shutil
import subprocess
import sys
import time
from dataclasses import dataclass

PRIMARY_DISPLAY_NAME = "Primary Display"


@dataclass(slots=True)
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
    return enable_fullscreen and is_primary_display_name(target_display_name)


def available_displays() -> list[DisplayTarget]:
    app = _qgui_application_instance()
    if app:
        displays = _qt_available_displays(app)
        if displays:
            return displays
    if sys.platform == "darwin":
        return _available_displays_macos()
    return []


def _qgui_application_instance():
    try:
        from PySide6.QtGui import QGuiApplication
    except Exception:
        return None
    return QGuiApplication.instance()


def _qt_available_displays(app) -> list[DisplayTarget]:
    displays: list[DisplayTarget] = []
    screens = app.screens()
    for index, screen in enumerate(screens):
        geometry = screen.geometry()
        name = screen.name() or f"Display {index + 1}"
        is_primary = index == 0
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
    return displays


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
    screens = AppKit.NSScreen.screens()
    if not screens:
        return []
    # The first screen in the array is always the 'primary' screen (origin 0,0 with menu bar)
    primary_screen = screens[0]
    primary_height = primary_screen.frame().size.height

    for index, screen in enumerate(screens):
        frame = screen.frame()
        name = getattr(screen, "localizedName", lambda: None)() or f"Display {index + 1}"
        is_primary = screen == primary_screen
        if is_primary:
            name = f"{name} (Primary)"

        # NSScreen uses bottom-left coordinates; AX/Quartz uses top-left.
        # We convert to top-left relative to the primary screen.
        y_top_left = int(primary_height - (frame.origin.y + frame.size.height))

        displays.append(
            DisplayTarget(
                name=str(name),
                x=int(frame.origin.x),
                y=y_top_left,
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
            AXUIElementSetAttributeValue,
            AXValueCreate,
            kAXFullScreenAttribute,
            kAXPositionAttribute,
            kAXSizeAttribute,
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

    position = AXValueCreate(kAXValueCGPointType, CGPointMake(target.x + 40, target.y + 40))
    size = AXValueCreate(
        kAXValueCGSizeType,
        CGSizeMake(max(target.width - 80, 640), max(target.height - 80, 480)),
    )
    AXUIElementSetAttributeValue(window, kAXPositionAttribute, position)
    AXUIElementSetAttributeValue(window, kAXSizeAttribute, size)
    if fullscreen:
        AXUIElementSetAttributeValue(window, kAXFullScreenAttribute, True)
    return None


def _command_exists(name: str) -> bool:
    return shutil.which(name) is not None
