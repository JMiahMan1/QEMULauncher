from PyInstaller.__main__ import run


def build():
    opts = [
        "qemu_app.py",
        "--name=QEMU Launcher",
        "--windowed",
        "--onedir",
        "--icon=RunLinux.icns",
        "--add-data=RunLinux.icns:.",
        "--collect-all=PySide6",
        "--collect-all=encodings",
        "--osx-bundle-identifier=com.qemu.launcher",
        "--clean",
        "--noconfirm",
    ]

    run(opts)


if __name__ == "__main__":
    build()
