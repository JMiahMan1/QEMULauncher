import Quartz
from ApplicationServices import (
    AXUIElementCreateApplication,
    AXUIElementCopyAttributeValue,
    AXValueGetValue,
    kAXValueCGPointType,
    kAXValueCGSizeType,
)
import os
import sys


def diag():
    print("--- DISPLAY DIAGNOSTICS ---")
    err, display_ids, count = Quartz.CGGetActiveDisplayList(10, None, None)
    if err != 0:
        print(f"Error getting display list: {err}")
    else:
        for i in range(count):
            d_id = display_ids[i]
            bounds = Quartz.CGDisplayBounds(d_id)
            is_main = Quartz.CGDisplayIsMain(d_id)
            print(f"[{i}] ID: {d_id}, Bounds: {bounds}, Main: {is_main}")

    print("\n--- WINDOW DIAGNOSTICS ---")
    # Search for qemu processes
    import subprocess

    try:
        out = subprocess.check_output(["pgrep", "-f", "qemu"]).decode().strip()
        pids = [int(p) for p in out.split("\n") if p]
        for pid in pids:
            print(f"\nPID: {pid}")
            app = AXUIElementCreateApplication(pid)
            err, windows = AXUIElementCopyAttributeValue(app, "AXWindows", None)
            if err == 0 and windows:
                for j, win in enumerate(windows):
                    e_pos, p_val = AXUIElementCopyAttributeValue(win, "AXPosition", None)
                    e_size, s_val = AXUIElementCopyAttributeValue(win, "AXSize", None)
                    e_fs, fs_val = AXUIElementCopyAttributeValue(win, "AXFullScreen", None)

                    pos = "unknown"
                    if e_pos == 0 and p_val:
                        ok, p = AXValueGetValue(p_val, kAXValueCGPointType, None)
                        pos = (p.x, p.y)

                    size = "unknown"
                    if e_size == 0 and s_val:
                        ok, s = AXValueGetValue(s_val, kAXValueCGSizeType, None)
                        size = (s.width, s.height)

                    print(f"  Window[{j}]: Pos={pos}, Size={size}, FullScreen={fs_val}")
            else:
                print(f"  No windows found (Error: {err})")
    except Exception as e:
        print(f"Error: {e}")


if __name__ == "__main__":
    diag()
