import Quartz
from ApplicationServices import (
    AXUIElementCreateApplication,
    AXUIElementCopyAttributeValue,
    AXUIElementSetAttributeValue,
    AXValueCreate,
    kAXValueCGPointType,
)
import subprocess
import time
import sys


def test_move():
    try:
        out = subprocess.check_output(["pgrep", "-f", "qemu"]).decode().strip()
        pids = [int(p) for p in out.split("\n") if p]
        if not pids:
            print("QEMU not running")
            return

        pid = pids[0]
        app = AXUIElementCreateApplication(pid)

        windows = None
        for _ in range(20):
            err, wins = AXUIElementCopyAttributeValue(app, "AXWindows", None)
            if err == 0 and wins:
                windows = wins
                break
            time.sleep(0.5)

        if not windows:
            print("No windows found")
            return

        win = windows[0]
        # Target (100, 100) - local to primary
        pos = Quartz.CGPoint(x=100.0, y=100.0)
        ax_pos = AXValueCreate(kAXValueCGPointType, pos)
        err = AXUIElementSetAttributeValue(win, "AXPosition", ax_pos)
        print(f"Move to (100,100) Result: {err}")

        # Target (-100, 100) - cross display?
        pos2 = Quartz.CGPoint(x=-100.0, y=100.0)
        ax_pos2 = AXValueCreate(kAXValueCGPointType, pos2)
        err2 = AXUIElementSetAttributeValue(win, "AXPosition", ax_pos2)
        print(f"Move to (-100,100) Result: {err2}")

    except Exception as e:
        print(f"Error: {e}")


if __name__ == "__main__":
    test_move()
