#!/bin/bash

# --- Environment Setup ---
# Ensure Homebrew paths are available
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"

# Setup logging for debugging
LOG_FILE="/tmp/qemu_launcher.log"
echo "--- Launcher Started at $(date) ---" >> "$LOG_FILE"

# --- Elevation Logic ---
if [ "$EUID" -ne 0 ]; then
    # We are not root. Use osascript to re-run this script with admin privileges.
    # We pass the full path to this script ($0) and any arguments.
    echo "Requesting administrative privileges..." >> "$LOG_FILE"
    osascript -e "do shell script \"$0 $*\" with administrator privileges"
    exit $?
fi

# --- Discovery ---
# Get the directory where the script is located
# If running inside an .app bundle, this is Contents/MacOS
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Identify the Python script location
if [[ "$SCRIPT_DIR" == *"Contents/MacOS"* ]]; then
    # Standard macOS Bundle structure: qemu_app.py is in ../Resources
    PYTHON_SCRIPT="$(cd "$SCRIPT_DIR/../Resources" && pwd)/qemu_app.py"
else
    # Development mode: qemu_app.py is in the same folder
    PYTHON_SCRIPT="$SCRIPT_DIR/qemu_app.py"
fi

echo "Script Dir: $SCRIPT_DIR" >> "$LOG_FILE"
echo "Python Script Path: $PYTHON_SCRIPT" >> "$LOG_FILE"

# --- Dependency Checks ---
function show_dialog() {
    osascript -e "tell app \"System Events\" to display dialog \"$2\" with title \"QEMU Launcher\" with icon $1 buttons {\"OK\"} default button \"OK\"" >/dev/null
}

function ask_yes_no() {
    osascript -e "tell app \"System Events\" to display dialog \"$1\" with title \"QEMU Launcher\" buttons {\"No\", \"Yes\"} default button \"Yes\"" | grep -q "Yes"
    return $?
}

# 1. Check for Homebrew
if ! command -v brew &> /dev/null; then
    show_dialog "stop" "Homebrew is not installed. Please install it from brew.sh to continue."
    exit 1
fi

# 2. Check for Python 3 and Tkinter
PYTHON_EXEC=$(which python3)
if [ -z "$PYTHON_EXEC" ]; then
    show_dialog "stop" "Python 3 was not found. Please install it via Homebrew."
    exit 1
fi

if ! "$PYTHON_EXEC" -c "import tkinter" &> /dev/null; then
    if ask_yes_no "Python is missing the Tkinter GUI toolkit. Install it now?"; then
        osascript -e "tell application \"Terminal\" to activate" -e "tell application \"Terminal\" to do script \"brew install python-tk\""
        show_dialog "note" "Please re-run this app once the installation is finished."
        exit 0
    else
        exit 1
    fi
fi

# 3. Check for QEMU
if ! command -v qemu-system-aarch64 &> /dev/null && ! command -v qemu-system-x86_64 &> /dev/null; then
    if ask_yes_no "QEMU is not installed. Install it now?"; then
        osascript -e "tell application \"Terminal\" to activate" -e "tell application \"Terminal\" to do script \"brew install qemu\""
        show_dialog "note" "Please re-run this app once the installation is finished."
        exit 0
    else
        exit 1
    fi
fi

# --- Launch ---
echo "Launching GUI with: $PYTHON_EXEC $PYTHON_SCRIPT" >> "$LOG_FILE"

# Launch the Python GUI as the current user.
# IMPORTANT: No 'with administrator privileges' here, so the window can open on your desktop.
exec "$PYTHON_EXEC" "$PYTHON_SCRIPT" "$@" >> "$LOG_FILE" 2>&1
