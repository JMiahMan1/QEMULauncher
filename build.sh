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

echo "--- Starting Manual .app Build ---"

# 1. Clean up old build and create directory structure
rm -rf "$OUTPUT_APP"
mkdir -p "$MACOS_DIR"
mkdir -p "$RESOURCES_DIR"

# 2. Create the main executable (the small wrapper that runs the launcher.sh)
cat > "$MACOS_DIR/$EXECUTABLE_NAME" << EOF
#!/bin/bash
# Set the current working directory to the Resources folder
CWD_TO_RESOURCES=\$(dirname "\$0")/../Resources
# Execute the launcher.sh script directly from the Resources folder.
# The 'exec' command replaces the current shell process with the new script.
# CRITICAL FIX: The path to Resources is just one directory up.
exec "\$CWD_TO_RESOURCES/$MAIN_SCRIPT" "\$@"
EOF

# 3. Copy ALL resources and set permissions
echo "-> Copying resources and setting permissions..."
cp "$MAIN_SCRIPT" "$RESOURCES_DIR/"
cp "$PYTHON_APP" "$RESOURCES_DIR/"
cp "$ICON_FILE" "$RESOURCES_DIR/"

# Set executable permissions on the wrapper and the target script
chmod +x "$MACOS_DIR/$EXECUTABLE_NAME"
chmod +x "$RESOURCES_DIR/$MAIN_SCRIPT" 

# 4. Create the Info.plist file
echo "-> Generating Info.plist..."
cat > "$CONTENTS_DIR/Info.plist" << EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
	<key>CFBundleExecutable</key>
	<string>$EXECUTABLE_NAME</string>
	<key>CFBundleIdentifier</key>
	<string>$BUNDLE_ID</string>
	<key>CFBundleIconFile</key>
	<string>$(basename "$ICON_FILE" .icns)</string>
	<key>CFBundleVersion</key>
	<string>$APP_VERSION</string>
	<key>CFBundleName</key>
	<string>$APP_NAME</string>
	<key>NSHighResolutionCapable</key>
	<true/>
	<key>LSUIElement</key>
	<false/>
	
    <key>NSMicrophoneUsageDescription</key>
	<string>QEMU needs microphone access to route your audio input to the guest virtual machine for applications like Teams.</string>
</dict>
</plist>
EOF

echo "--- Success! Built version $APP_VERSION ---"
