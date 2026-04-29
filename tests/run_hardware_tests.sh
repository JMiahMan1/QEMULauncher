#!/bin/bash
set -euo pipefail

# QEMU Launcher Hardware UI Test Runner
# Use this script to validate graphical behavior on local macOS hardware.

echo "--- Starting Hardware UI Tests ---"

if [[ "$OSTYPE" != "darwin"* ]]; then
    echo "Error: This test suite must be run on physical macOS hardware with a GUI session."
    exit 1
fi

# 1. Install UI testing dependencies if missing
echo "-> Ensuring dependencies are present..."
pip install pytest pytest-qt PySide6 pyobjc-framework-Quartz pyobjc-framework-ApplicationServices --quiet

# 2. Run the specialized UI suite
echo "-> Running macOS UI integrity tests..."
pytest tests/hardware/test_ui_hardware_verification.py -v

echo "--- Tests Complete ---"
