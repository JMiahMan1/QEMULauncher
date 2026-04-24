#!/bin/bash

# --- Configuration Variables ---
APP_NAME="QEMU Launcher"
APP_VERSION="${1:-1.0}"
OUTPUT_APP="$APP_NAME.app"

# --- Source Files (Must be in the current directory) ---
PYTHON_APP="qemu_app.py"
ICON_FILE="RunLinux.icns"

# --- Pre-flight Check ---
if [ ! -f "$PYTHON_APP" ] || [ ! -f "$ICON_FILE" ]; then
    echo "Error: Missing required source file(s) in the current directory."
    exit 1
fi

set -e

# 1. Clean up old build
echo "-> Cleaning previous builds..."
rm -rf build dist "$OUTPUT_APP"

if [[ "$OSTYPE" == "darwin"* ]]; then
    echo "-> Building standalone macOS application with PyInstaller..."
    python3 build_pyinstaller.py

    if [ -d "dist/$OUTPUT_APP" ]; then
        echo "-> PyInstaller build successful. Moving to root..."
        mv "dist/$OUTPUT_APP" "./$OUTPUT_APP"
    else
        echo "Error: PyInstaller failed to create $OUTPUT_APP"
        exit 1
    fi

    # 4. Add Plist Keys for Hardware Access
    echo "-> Adding usage descriptions to Info.plist..."
    PLIST="./$OUTPUT_APP/Contents/Info.plist"
    plutil -replace NSMicrophoneUsageDescription -string "QEMU needs microphone access to route your audio input to the guest VM." "$PLIST"
    plutil -replace NSCameraUsageDescription -string "QEMU needs camera access to route your video input to the guest VM." "$PLIST"
    plutil -replace NSHighResolutionCapable -bool YES "$PLIST"

    # 5. Ad-hoc Signing with Entitlements
    echo "-> Applying ad-hoc signature with entitlements..."
    codesign --force --deep --sign - --entitlements entitlements.plist "./$OUTPUT_APP"

    # 6. Integrity Check: Verify internal structure
    echo "-> Verifying internal bundle structure..."
    if [ ! -f "./$OUTPUT_APP/Contents/MacOS/QEMU Launcher" ]; then
         echo "Error: Main binary missing from bundle."
         exit 1
    fi
else
    echo "-> Building Linux directory artifact with PyInstaller..."
    python3 -m PyInstaller --noconfirm --clean --onedir --windowed --name "qemu-launcher-linux" qemu_app.py
fi

# 4. Cleanup
rm -rf build dist

echo "--- Success! Built self-contained version $APP_VERSION ---"
