import Quartz
import AppKit
import Foundation


def get_display_name(display_id):
    # This is the complex way to get the display name on macOS
    screen = None
    for s in AppKit.NSScreen.screens():
        desc = s.deviceDescription()
        d_id = desc.objectForKey_("NSScreenNumber")
        if d_id == display_id:
            screen = s
            break

    if screen:
        return screen.localizedName()
    return f"Display {display_id}"


def diag():
    print("--- DISPLAY NAMES DIAGNOSTICS ---")
    err, display_ids, count = Quartz.CGGetActiveDisplayList(10, None, None)
    if err == 0:
        for i in range(count):
            d_id = display_ids[i]
            name = get_display_name(d_id)
            bounds = Quartz.CGDisplayBounds(d_id)
            print(f"[{i}] ID: {d_id}, Name: {name}, Bounds: {bounds}")


if __name__ == "__main__":
    diag()
