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

echo "--- Running ShellCheck ---"
shellcheck test.sh test_build.sh build.sh lint.sh

echo "--- Linting Passed! ---"
