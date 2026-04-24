import sys
from PyInstaller.__main__ import run

def build():
    opts = [
        'qemu_app.py',
        '--name=QEMU Launcher',
        '--windowed',
        '--onefile',
        '--icon=RunLinux.icns',
        '--add-data=RunLinux.icns:.',
        '--hidden-import=pynput.keyboard._darwin',
        '--hidden-import=pynput.mouse._darwin',
        '--hidden-import=screeninfo.drivers.osx',
        '--collect-all=encodings',
        '--clean',
        '--noconfirm',
    ]
    
    # Add macOS specific codesign identity if needed, but we do it in build.sh
    
    run(opts)

if __name__ == '__main__':
    build()
