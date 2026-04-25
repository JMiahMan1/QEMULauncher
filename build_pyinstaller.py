import os

from PyInstaller.__main__ import run


def build():
    opts = [
        "qemu_app.py",
        "--name=QEMU Launcher",
        "--windowed",
        "--onedir",
        "--icon=RunLinux.icns",
        "--add-data=RunLinux.icns:.",
        # Add the networking helper if it exists in the source directory
        "--add-binary=qemu-launcher-helper:." if os.path.exists("qemu-launcher-helper") else "",
        "--collect-all=PySide6",
        "--collect-all=encodings",
        "--osx-bundle-identifier=com.qemu.launcher",
        "--clean",
        "--noconfirm",
    ]
    # Filter out empty options
    opts = [opt for opt in opts if opt]
    run(opts)


if __name__ == "__main__":
    build()
