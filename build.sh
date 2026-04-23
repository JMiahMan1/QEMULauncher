#!/bin/bash

# --- Configuration Variables ---
APP_NAME="QEMU Launcher"
BUNDLE_ID="org.yourcompany.qemulauncher"
APP_VERSION="${1:-1.0}"
OUTPUT_APP="$APP_NAME.app"

# --- Source Files (Must be in the current directory) ---
MAIN_SCRIPT="launcher.sh"
PYTHON_APP="qemu_app.py"
ICON_FILE="RunLinux.icns"

# --- Directory Paths inside the bundle ---
CONTENTS_DIR="$OUTPUT_APP/Contents"
MACOS_DIR="$CONTENTS_DIR/MacOS"
RESOURCES_DIR="$CONTENTS_DIR/Resources"
EXECUTABLE_NAME="QEMU Launcher"

# --- Pre-flight Check ---
if [ ! -f "$MAIN_SCRIPT" ] || [ ! -f "$PYTHON_APP" ] || [ ! -f "$ICON_FILE" ]; then
    echo "Error: Missing required source file(s) in the current directory."
    exit 1
fi

set -e

# 1. Clean up old build
echo "-> Cleaning previous builds..."
rm -rf build dist "$OUTPUT_APP"

# 2. Build the app using py2app
echo "-> Building self-contained app with py2app..."
# Ensure dependencies are installed for the build process
pip3 install -q -r requirements.txt
python3 setup.py py2app --quiet

# 3. Robust move: Find whatever .app was created in dist/ and move/rename it
GENERATED_APP=$(ls -d dist/*.app | head -n 1)
if [ -n "$GENERATED_APP" ]; then
    echo "-> Moving $GENERATED_APP to $OUTPUT_APP..."
    mv "$GENERATED_APP" "./$OUTPUT_APP"
else
    echo "Error: No .app bundle found in dist/ directory."
    exit 1
fi

# 4. Cleanup
rm -rf build dist

echo "--- Success! Built self-contained version $APP_VERSION ---"
