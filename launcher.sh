#!/bin/bash

# --- Environment Setup ---
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"

# Setup logging
LOG_FILE="/tmp/qemu_launcher.log"
echo "--- Launcher Started at $(date) ---" > "$LOG_FILE"

# --- Discovery ---
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ "$SCRIPT_DIR" == *"Contents/MacOS"* ]]; then
    PYTHON_SCRIPT="$(cd "$SCRIPT_DIR/../Resources" && pwd)/qemu_app.py"
else
    PYTHON_SCRIPT="$SCRIPT_DIR/qemu_app.py"
fi

# Find python3
PYTHON_EXEC=$(which python3)
[ -z "$PYTHON_EXEC" ] && PYTHON_EXEC="/usr/bin/python3"

# --- Launch GUI as User ---
echo "Launching GUI as $(whoami)..." >> "$LOG_FILE"
exec "$PYTHON_EXEC" "$PYTHON_SCRIPT" "$@" >> "$LOG_FILE" 2>&1
