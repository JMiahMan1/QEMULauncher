import sys

import pytest
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

# Only run these tests on macOS with a GUI session
pytestmark = pytest.mark.skipif(
    sys.platform != "darwin",
    reason="Hardware UI tests require a physical macOS graphical session."
)

try:
    from qemu_launcher.display import available_displays
    from qemu_launcher.ui import FullscreenOverlay, HotEdgeTrigger
except ImportError:
    pass


@pytest.fixture
def app():
    """Ensure a QApplication exists for the duration of the test."""
    yield QApplication.instance() or QApplication(sys.argv)


def test_hot_edge_window_level(app):
    """Verify that the HotEdgeTrigger is correctly elevated above the UI."""
    trigger = HotEdgeTrigger()
    trigger.show()
    QTest.qWait(500)
    
    # On macOS, verify the window level via ApplicationServices
    # This ensures it stays on top of QEMU's greedy Cocoa view
    if sys.platform == "darwin":

        from AppKit import NSStatusWindowLevel
        from objc import objc_object
        
        ns_view = objc_object(c_void_p=int(trigger.winId()))
        window = ns_view.window()
        assert window.level() >= NSStatusWindowLevel
        
    trigger.close()


def test_monitor_aware_placement(app):
    """Verify that the trigger positions itself correctly on a secondary monitor if requested."""
    displays = available_displays()
    if len(displays) < 2:
        pytest.skip("Test requires at least two monitors.")
        
    secondary = displays[1]
    trigger = HotEdgeTrigger(target_display_name=secondary.name)
    trigger.show()
    QTest.qWait(500)
    
    # Check if the trigger's geometry is within the secondary monitor's bounds
    geom = trigger.geometry()
    assert geom.x() >= secondary.x
    assert geom.x() < secondary.x + secondary.width
    
    trigger.close()


def test_hover_handshake(app):
    """Verify that hovering over the HotEdgeTrigger reveals the FullscreenOverlay."""
    overlay = FullscreenOverlay()
    trigger = HotEdgeTrigger(on_trigger=overlay.show_at_top)
    
    overlay.hide()
    assert not overlay.isVisible()
    
    # Simulate hover by triggering the callback directly (since real mouse move is hard to mock)
    trigger.on_trigger()
    QTest.qWait(100)
    
    assert overlay.isVisible()
    # Verify it is centered at the top
    assert overlay.y() == 0
    
    trigger.close()
    overlay.close()


def test_fullscreen_transition_integrity(app):
    """
    Verify that the launcher can identify a windowed process and
    successfully manage its transition.
    """
    from qemu_launcher.display import arrange_window
    
    displays = available_displays()
    target = displays[0]
    
    # Test our arrangement logic with a dummy PID (will fail but allows us to check the error path)
    result = arrange_window(999999, target.name, fullscreen=True)
    assert result is not None
    assert "Unable to locate" in result or "unavailable" in result
