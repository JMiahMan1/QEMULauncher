from setuptools import setup

APP = ['qemu_app.py']
DATA_FILES = []
OPTIONS = {
    'argv_emulation': False, # Keep False for CLI/GUI hybrid
    'iconfile': 'RunLinux.icns',
    'plist': {
        'CFBundleIdentifier': 'org.yourcompany.qemulauncher',
        'CFBundleVersion': '1.0',
        'NSHighResolutionCapable': True,
        'NSMicrophoneUsageDescription': 'QEMU needs microphone access to route your audio input to the guest VM.',
        'LSUIElement': False, # Show in Dock
    },
    'packages': ['pynput', 'screeninfo', 'AppKit'],
}

setup(
    app=APP,
    data_files=DATA_FILES,
    options={'py2app': OPTIONS},
    setup_requires=['py2app'],
)
