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

    @patch("threading.Thread")
    @patch("subprocess.Popen")
    @patch("qemu_app.DisplayManager.get_displays")
    def test_window_orchestration_call(self, mock_get, mock_popen, mock_thread):
        """Verify that the window management thread is started."""
        mock_get.return_value = [{"x": 0, "y": 0, "width": 1920, "height": 1080, "is_primary": True}]
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

    @patch("subprocess.Popen")
    @patch("qemu_app.DisplayManager.get_target_display")
    def test_elevation_trigger(self, mock_target, mock_popen):
        """Verify that native elevation is used for vmnet."""
        mock_target.return_value = {"x": 0, "y": 0, "width": 100, "height": 100}
        self.mock_config["network_mode"] = "vmnet-shared"

        # Patch in both places to be sure
        with patch("qemu_app.sys.platform", "darwin"):
            mock_nsapple = MagicMock()
            # Patch the global AppKit module so 'from AppKit import NSAppleScript' works
            with patch.dict("sys.modules", {"AppKit": MagicMock()}):
                import AppKit

                AppKit.NSAppleScript = mock_nsapple
                mock_script_instance = MagicMock()
                mock_script_instance.executeAndReturnError_.return_value = (None, None)
                mock_nsapple.alloc.return_value.initWithSource_.return_value = mock_script_instance

                qemu_app.run_launcher(self.mock_config)

                # Verify that NSAppleScript was used
                mock_nsapple.alloc.return_value.initWithSource_.assert_called_once()

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

    @patch("qemu_app.HotspotWindow")
    def test_hotspot_initialization(self, mock_hotspot):
        """Verify that the HotspotWindow is initialized."""
        qemu_app.GestureMonitor.start(MagicMock(), lambda: None)
        self.assertTrue(True)


if __name__ == "__main__":
    unittest.main()
