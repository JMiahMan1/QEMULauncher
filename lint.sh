#!/bin/bash
set -e

# --- Configuration ---
PYTHON_FILES="qemu_app.py"

echo "--- Running Ruff Linter ---"
if ! command -v ruff &> /dev/null; then
    echo "Ruff not found. Installing..."
    pip install ruff
fi

ruff check $PYTHON_FILES

echo "--- Linting Passed! ---"
