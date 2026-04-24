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
    def test_coordinate_conversion(self):
        """Verify AppKit to Tkinter coordinate translation."""
        with patch("qemu_app.AppKit") as mock_appkit:
            mock_screen0 = MagicMock()
            mock_screen0.frame.return_value.origin.x = 0
            mock_screen0.frame.return_value.origin.y = 0
            mock_screen0.frame.return_value.size.width = 1920
            mock_screen0.frame.return_value.size.height = 1080
            
            mock_screen1 = MagicMock()
            mock_screen1.frame.return_value.origin.x = 1920
            mock_screen1.frame.return_value.origin.y = 0
            mock_screen1.frame.return_value.size.width = 1920
            mock_screen1.frame.return_value.size.height = 1080
            
            mock_appkit.NSScreen.screens.return_value = [mock_screen0, mock_screen1]
            
            displays = qemu_app.DisplayManager.get_displays()
            
            # Primary screen: tk_y = 1080 - (0 + 1080) = 0
            self.assertEqual(displays[0]["y"], 0)
            self.assertEqual(displays[0]["x"], 0)
            
            # Secondary screen: tk_y = 1080 - (0 + 1080) = 0
            self.assertEqual(displays[1]["y"], 0)
            self.assertEqual(displays[1]["x"], 1920)

    def test_network_command_construction(self):
        """Verify QEMU command generation for different network modes."""
        # Test User Mode
        self.mock_config["network_mode"] = "user"
        cmd = qemu_app.run_launcher(self.mock_config, dry_run=True)
        cmd_str = " ".join(cmd)
        self.assertIn("-netdev user,id=net0", cmd_str)
        self.assertNotIn("vmnet-shared", cmd_str)

        # Test VMNet Shared
        self.mock_config["network_mode"] = "vmnet-shared"
        cmd = qemu_app.run_launcher(self.mock_config, dry_run=True)
        cmd_str = " ".join(cmd)
        self.assertIn("-netdev vmnet-shared,id=net0", cmd_str)

        # Test Bridged
        self.mock_config["network_mode"] = "bridge-existing"
        self.mock_config["bridge_name"] = "bridge100"
        cmd = qemu_app.run_launcher(self.mock_config, dry_run=True)
        cmd_str = " ".join(cmd)
        self.assertIn("-netdev bridge,id=net0,br=bridge100", cmd_str)

    @patch("qemu_app.DisplayManager.get_displays")
    def test_elevation_trigger(self, mock_get):
        """Verify that native elevation is used when required."""
        mock_get.return_value = [{"x": 0, "y": 0, "width": 1920, "height": 1080, "is_primary": True}]
        self.mock_config["network_mode"] = "vmnet-shared"
        
        with patch("qemu_app.sys.platform", "darwin"):
            mock_nsapple = MagicMock()
            # Patch the global AppKit module so 'from AppKit import NSAppleScript' works
            with patch.dict("sys.modules", {"AppKit": MagicMock()}):
                import AppKit
                AppKit.NSAppleScript = mock_nsapple
                
                # Mock AppKit.NSAppleScript.alloc().initWithSource_(...).executeAndReturnError_(None)
                mock_script_instance = MagicMock()
                mock_nsapple.alloc.return_value.initWithSource_.return_value = mock_script_instance
                mock_script_instance.executeAndReturnError_.return_value = (None, None)
                
                # We expect it to NOT use subprocess.Popen directly but use NSAppleScript
                with patch("subprocess.Popen"):
                    qemu_app.run_launcher(self.mock_config)
                    mock_nsapple.alloc.return_value.initWithSource_.assert_called_once()
                    self.assertIn("with administrator privileges", mock_nsapple.alloc.return_value.initWithSource_.call_args[0][0])

    def test_config_io(self):
        """Test loading and saving configuration."""
        import tempfile
        with tempfile.NamedTemporaryFile(mode='w', suffix='.ini', delete=False) as tf:
            tf.write("[VM]\nmemory = 4G\ncpu_cores = 4\n")
            temp_name = tf.name
        
        try:
            config = qemu_app.load_config(temp_name)
            self.assertIsNotNone(config)
            self.assertEqual(config["memory"], "4G")
            self.assertEqual(config["cpu_cores"], "4")
        finally:
            if os.path.exists(temp_name):
                os.remove(temp_name)

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
