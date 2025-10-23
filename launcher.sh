#!/bin/bash

# --- DYNAMIC PATH DISCOVERY ---
HOMEBREW_PATHS="/opt/homebrew/bin:/usr/local/bin"

if ! command -v brew &> /dev/null; then
    for dir in ${HOMEBREW_PATHS//:/ }; do
        if [ -x "$dir/brew" ]; then
            export PATH="$HOMEBREW_PATHS:$PATH"
            break
        fi
    done
fi

PYTHON3_PATH=""

if command -v python3 &> /dev/null; then
    PYTHON3_PATH=$(command -v python3)
fi

if [ -z "$PYTHON3_PATH" ] && [ -x "/usr/bin/python3" ]; then
    PYTHON3_PATH="/usr/bin/python3"
fi

if [ -n "$PYTHON3_PATH" ]; then
    export PYTHON3_BIN="$PYTHON3_PATH"
else
    export PYTHON3_BIN="python3"
fi

CONFIG_DIR="${HOME}/.config/qemu_launcher"
FLAG_FILE="${CONFIG_DIR}/.setup_complete"

# --- Context-Aware Path Detection ---
if [[ -n "$RESOURCES" ]]; then
    PYTHON_APP_PATH="$RESOURCES/qemu_app.py"
else
    SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
    PYTHON_APP_PATH="$SCRIPT_DIR/qemu_app.py"
fi


# --- Main Logic ---
if [ -f "$FLAG_FILE" ]; then
    # Build the shell command string for osascript
    cmd="$PYTHON3_BIN \"$PYTHON_APP_PATH\""

    # Append any additional arguments safely
    for arg in "$@"; do
        escaped_arg=$(printf '%s' "$arg" | sed 's/\\/\\\\/g; s/"/\\"/g')
        cmd="$cmd \"$escaped_arg\""
    done

    # Escape double quotes for AppleScript
    cmd_escaped=$(printf '%s' "$cmd" | sed 's/"/\\"/g')

    # Run with admin privileges via osascript
    osascript -e "do shell script \"$cmd_escaped\" with administrator privileges"
    exit $?
fi

# --- First-Run Setup Wizard ---
function show_dialog() {
    osascript -e "tell app \"System Events\" to display dialog \"$2\" with title \"QEMU Launcher Setup\" with icon $1 buttons {\"OK\"} default button \"OK\"" >/dev/null
}
function ask_yes_no() {
    osascript -e "tell app \"System Events\" to display dialog \"$1\" with title \"QEMU Launcher Setup\" buttons {\"No\", \"Yes\"} default button \"Yes\"" | grep -q "Yes"
    return $?
}

if ! command -v brew &> /dev/null; then
    show_dialog "stop" "Homebrew is not installed. Please install it from brew.sh to continue."
    exit 1
fi

if ! "$PYTHON3_BIN" -c "import tkinter" &> /dev/null; then
    if ask_yes_no "Python is missing the Tkinter GUI toolkit. Install it with Homebrew (brew install python-tk)?"; then
        osascript -e "tell application \"Terminal\" to activate" -e "tell application \"Terminal\" to do script \"brew install python-tk\""
        show_dialog "note" "Please re-run this application after the Terminal install is complete."
        exit 0
    else
        exit 1
    fi
fi

if ! command -v qemu-system-aarch64 &> /dev/null && ! command -v qemu-system-x86_64 &> /dev/null; then
    if ask_yes_no "QEMU is not installed. Install it with Homebrew?"; then
        osascript -e "tell application \"Terminal\" to activate" -e "tell application \"Terminal\" to do script \"brew install qemu\""
        show_dialog "note" "Please re-run this application after the Terminal install is complete."
        exit 0
    else
        exit 1
    fi
fi

# --- All checks passed. Create flag file and launch the main app. ---
mkdir -p "$CONFIG_DIR"
touch "$FLAG_FILE"
if [ -f "$FLAG_FILE" ]; then
    # Build the shell command string for osascript
    cmd="$PYTHON3_BIN \"$PYTHON_APP_PATH\""

    # Append any additional arguments safely
    for arg in "$@"; do
        escaped_arg=$(printf '%s' "$arg" | sed 's/\\/\\\\/g; s/"/\\"/g')
        cmd="$cmd \"$escaped_arg\""
    done

    # Escape double quotes for AppleScript
    cmd_escaped=$(printf '%s' "$cmd" | sed 's/"/\\"/g')

    # Run with admin privileges via osascript
    osascript -e "do shell script \"$cmd_escaped\" with administrator privileges"
    exit $?
fi
