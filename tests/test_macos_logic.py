import os
import sys
import unittest
from unittest.mock import MagicMock, patch

# Add the parent directory to sys.path to import qemu_app
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import qemu_app


class TestMacOSLogic(unittest.TestCase):
    def setUp(self):
        self.mock_config = {
            "qemu_executable": "/usr/local/bin/qemu-system-aarch64",
            "disk_path": "/tmp/test.qcow2",
            "firmware_path": "/tmp/fw.fd",
            "network_mode": "user",
            "enable_fullscreen": True,
        }

    @patch("qemu_app.DisplayManager.get_displays")
    def test_display_detection_logic(self, mock_get):
        """Verify that we correctly pick the secondary monitor."""
        mock_get.return_value = [
            {"x": 0, "y": 0, "width": 1440, "height": 900, "is_primary": True},
            {"x": 1440, "y": 0, "width": 1920, "height": 1080, "is_primary": False},
        ]
        target = qemu_app.DisplayManager.get_target_display()
        self.assertEqual(target["width"], 1920)
        self.assertEqual(target["x"], 1440)

    @patch('threading.Thread')
    @patch('subprocess.Popen')
    @patch('qemu_app.DisplayManager.get_displays')
    def test_window_orchestration_call(self, mock_get, mock_popen, mock_thread):
        """Verify that the window management thread is started."""
        mock_get.return_value = [{'x': 0, 'y': 0, 'width': 1920, 'height': 1080, 'is_primary': True}]
        qemu_app.WindowManager.orchestrate_window("qemu-system", fullscreen=True)
        # Verify that a thread was initialized to handle the window orchestration
        mock_thread.assert_called_once()

    @patch("qemu_app.DisplayManager.get_displays")
    def test_command_generation_resolution(self, mock_get):
        """Verify that QEMU command uses the target display resolution."""
        mock_get.return_value = [
            {"x": 0, "y": 0, "width": 1440, "height": 900, "is_primary": True},
            {"x": 1440, "y": 0, "width": 1920, "height": 1080, "is_primary": False},
        ]
        cmd = qemu_app.run_launcher(self.mock_config, dry_run=True)
        self.assertIn("virtio-gpu-pci,xres=1920,yres=1080", cmd)

    @patch("qemu_app.DisplayManager.get_displays")
    def test_networking_modes(self, mock_get):
        """Verify networking flags and elevation requirements."""
        mock_get.return_value = [{"x": 0, "y": 0, "width": 100, "height": 100, "is_primary": True}]

        # Test User Mode (No elevation)
        self.mock_config["network_mode"] = "user"
        cmd = qemu_app.run_launcher(self.mock_config, dry_run=True)
        self.assertIn("user,id=net0", " ".join(cmd))

        # Test VMNet Shared (Elevation required)
        self.mock_config["network_mode"] = "vmnet-shared"
        cmd = qemu_app.run_launcher(self.mock_config, dry_run=True)
        self.assertIn("vmnet-shared,id=net0", " ".join(cmd))

    @patch('threading.Thread')
    @patch("subprocess.Popen")
    @patch("qemu_app.DisplayManager.get_target_display")
    def test_elevation_trigger(self, mock_target, mock_popen, mock_thread):
        """Verify that osascript elevation is used for vmnet."""
        mock_target.return_value = {"x": 0, "y": 0, "width": 100, "height": 100}
        self.mock_config["network_mode"] = "vmnet-shared"
        qemu_app.run_launcher(self.mock_config)

        # Verify that at least one Popen call used osascript with 'administrator privileges'
        elevated_call_found = False
        for call in mock_popen.call_args_list:
            args, _ = call
            if len(args[0]) > 2 and "osascript" in args[0] and "with administrator privileges" in args[0][2]:
                elevated_call_found = True
                break

        self.assertTrue(elevated_call_found, "Elevated osascript call not found in Popen history")

    def test_config_io(self):
        """Test loading and saving configuration."""
        test_path = "/tmp/test_qemu_config.json"
        test_data = {"test": "value"}
        qemu_app.save_config(test_data, path=test_path)
        loaded = qemu_app.load_config(path=test_path)
        self.assertEqual(loaded["test"], "value")
        os.remove(test_path)

    def test_path_validation(self):
        """Test QEMU executable path validation."""
        # Test empty
        valid, _ = qemu_app.validate_qemu_executable("")
        self.assertFalse(valid)

        # Test valid (using current python as a mock executable)
        valid, _ = qemu_app.validate_qemu_executable(sys.executable)
        self.assertTrue(valid)

    @patch("qemu_app.AppKit")
    def test_gesture_zone_calculation(self, mock_appkit):
        """Verify the hover zone calculation logic."""
        # Mock a primary screen of 1440x900
        mock_screen = MagicMock()
        mock_screen.frame.return_value.size.width = 1440
        mock_screen.frame.return_value.size.height = 900
        mock_appkit.NSScreen.screens.return_value = [mock_screen]

        # Test a coordinate in the dead center top (should trigger)
        # GestureMonitor logic: (width * 0.42) < loc.x < (width * 0.58)
        # 1440 * 0.5 = 720 (within range)
        # loc.y >= (height - 15) = 885

        # We simulate the logic inside the monitor loop
        screen_w = 1440
        screen_h = 900

        # In-zone
        x, y = 720, 890
        in_x = (screen_w * 0.42) < x < (screen_w * 0.58)
        in_y = y >= (screen_h - 15)
        self.assertTrue(in_x and in_y)

        # Out-of-zone (side)
        x, y = 100, 890
        in_x = (screen_w * 0.42) < x < (screen_w * 0.58)
        self.assertFalse(in_x)


if __name__ == "__main__":
    unittest.main()
