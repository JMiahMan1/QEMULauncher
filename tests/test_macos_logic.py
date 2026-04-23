import os
import sys
import unittest
from unittest.mock import patch

# Add the parent directory to sys.path to import qemu_app
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import qemu_app


class TestMacOSLogic(unittest.TestCase):
    def setUp(self):
        self.mock_config = {
            'qemu_executable': '/usr/local/bin/qemu-system-aarch64',
            'disk_path': '/tmp/test.qcow2',
            'firmware_path': '/tmp/fw.fd',
            'network_mode': 'user',
            'enable_fullscreen': True
        }

    @patch('qemu_app.DisplayManager.get_displays')
    def test_display_detection_logic(self, mock_get):
        """Verify that we correctly pick the secondary monitor."""
        mock_get.return_value = [
            {'x': 0, 'y': 0, 'width': 1440, 'height': 900, 'is_primary': True},
            {'x': 1440, 'y': 0, 'width': 1920, 'height': 1080, 'is_primary': False}
        ]
        target = qemu_app.DisplayManager.get_target_display()
        self.assertEqual(target['width'], 1920)
        self.assertEqual(target['x'], 1440)

    @patch('subprocess.Popen')
    @patch('qemu_app.DisplayManager.get_displays')
    def test_window_orchestration_call(self, mock_get, mock_popen):
        """Verify that the window management thread is started."""
        mock_get.return_value = [{'x': 0, 'y': 0, 'width': 1920, 'height': 1080, 'is_primary': True}]
        qemu_app.WindowManager.orchestrate_window("qemu-system", fullscreen=True)
        # Note: Since it's in a thread, we just verify it doesn't crash
        # and the display detection was triggered.
        mock_get.assert_called()

    @patch('qemu_app.DisplayManager.get_displays')
    def test_command_generation_resolution(self, mock_get):
        """Verify that QEMU command uses the target display resolution."""
        mock_get.return_value = [
            {'x': 0, 'y': 0, 'width': 1440, 'height': 900, 'is_primary': True},
            {'x': 1440, 'y': 0, 'width': 1920, 'height': 1080, 'is_primary': False}
        ]
        cmd = qemu_app.run_launcher(self.mock_config, dry_run=True)
        # The xres/yres should match the SECONDARY monitor (1920x1080)
        self.assertIn('virtio-gpu-pci,xres=1920,yres=1080', cmd)

if __name__ == '__main__':
    unittest.main()
