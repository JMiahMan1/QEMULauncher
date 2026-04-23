import os
import sys
import unittest
from unittest.mock import MagicMock, patch

# Add the project directory to sys.path
sys.path.append(os.getcwd())

# Mock AppKit before importing qemu_app
sys.modules['AppKit'] = MagicMock()

import qemu_app  # noqa: E402


class TestMacOSLogic(unittest.TestCase):
    def setUp(self):
        self.mock_config = {
            'qemu_executable': '/usr/local/bin/qemu-system-aarch64',
            'disk_path': '~/test.qcow2',
            'firmware_path': '~/EDK2.fd',
            'network_mode': 'user',
            'enable_fullscreen': True
        }

    def test_command_generation_bridged_fallback(self):
        """Verify that 'user' mode falls back to vmnet-bridged as requested."""
        self.mock_config['network_mode'] = 'user'
        cmd = qemu_app.run_launcher(self.mock_config, dry_run=True)
        
        # Check for the specific bridged flag the user wanted
        self.assertIn("-nic", cmd)
        self.assertIn("vmnet-bridged,ifname=en0", cmd)

    @patch('subprocess.check_output')
    def test_display_detection_parsing(self, mock_output):
        """Verify that display dimensions are parsed correctly from AppleScript output."""
        # Simulate dual 4K monitors: x,y,w,h for each
        # Screen 1: 0,0,1920,1080 -> nums: 0,0,1920,1080
        # Screen 2: 1920,0,3840,1080 -> nums: 1920,0,3840,1080
        mock_output.return_value = "0, 0, 1920, 1080, 1920, 0, 3840, 1080"
        displays = qemu_app.get_display_info()
        
        self.assertEqual(len(displays), 2)
        self.assertEqual(displays[0]['width'], 1920)
        self.assertEqual(displays[1]['x'], 1920)
        self.assertEqual(displays[1]['width'], 1920)

    @patch('subprocess.Popen')
    @patch('qemu_app.get_display_info')
    def test_applescript_window_move_logic(self, mock_displays, mock_popen):
        """Verify the generated AppleScript contains the correct window name and focus logic."""
        mock_displays.return_value = [
            {'x': 0, 'y': 0, 'width': 1920, 'height': 1080},
            {'x': 1920, 'y': 0, 'width': 1920, 'height': 1080}
        ]
        
        qemu_app.move_qemu_to_screen(screen_index=1, fullscreen=True)
        
        # Extract the script sent to osascript
        args, kwargs = mock_popen.call_args
        script = args[0][2]
        
        self.assertIn('set qemuProcs to (every process whose name contains "qemu-system")', script)
        self.assertIn('set frontmost of qemuProc to true', script)
        self.assertIn('set position of qemuWin to { 1920, 0 }', script)
        self.assertIn('set value of attribute "AXFullScreen" of qemuWin to true', script)

if __name__ == '__main__':
    unittest.main()
