#!/bin/bash
set -e

echo "--- Starting Full Test Suite ---"

# 1. Linting
if [ -f "./lint.sh" ]; then
    ./lint.sh
else
    echo "Warning: lint.sh not found."
fi

# 2. Build and Feature Tests
if [ -f "./test_build.sh" ]; then
    # We pass 'ci-test' as a dummy version
    ./test_build.sh "ci-test"
else
    echo "Error: test_build.sh not found."
    exit 1
fi

echo "--- All Tests Passed Successfully ---"
