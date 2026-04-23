from setuptools import setup

APP = ["qemu_app.py"]
DATA_FILES = []
OPTIONS = {
    "argv_emulation": False,
    "iconfile": "RunLinux.icns",
    "optimize": 0,
    "plist": {
        "CFBundleName": "QEMU Launcher",
        "CFBundleDisplayName": "QEMU Launcher",
        "CFBundleIdentifier": "com.qemu.launcher",
        "CFBundleVersion": "1.0.0",
        "CFBundleShortVersionString": "1.0.0",
        "LSMinimumSystemVersion": "10.13",
        "NSHighResolutionCapable": True,
        "NSMicrophoneUsageDescription": "QEMU needs microphone access to route your audio input to the guest VM.",
        "LSUIElement": False,  # Show in Dock
    },
    "packages": ["pynput", "screeninfo", "AppKit", "encodings"],
    "includes": ["encodings.*"],
    "site_packages": True,
}

setup(
    name="QEMU Launcher",
    app=APP,
    data_files=DATA_FILES,
    options={"py2app": OPTIONS},
    setup_requires=["py2app"],
)
