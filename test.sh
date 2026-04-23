#!/bin/bash
set -e

echo "--- Starting Comprehensive Master Test Suite ---"

# 1. Linting
echo "[1/3] Running Linting..."
if command -v ruff &> /dev/null; then
    ruff check .
    echo "  - Linting passed."
else
    echo "  - Ruff not found, skipping lint."
fi

# 2. Logic Tests
echo "[2/3] Running Python Unit Tests..."
python3 -m unittest discover tests
echo "  - Unit tests passed."

# 3. Build & Runtime Smoke Tests
echo "[3/3] Running Build and Packaging Tests..."
if [ -f "./test_build.sh" ]; then
    # We pass 'ci-test' as a dummy version
    ./test_build.sh "ci-test"
else
    echo "Error: test_build.sh not found."
    exit 1
fi

echo ""
echo "--- ALL TESTS PASSED SUCCESSFULLY ---"
