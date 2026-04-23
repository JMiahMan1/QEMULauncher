#!/bin/bash
set -e

echo "--- Running Build and Packaging Test Suite ---"

# We pass 'ci-test' as a dummy version to verify the build script logic
if [ -f "./test_build.sh" ]; then
    ./test_build.sh "ci-test"
else
    echo "Error: test_build.sh not found."
    exit 1
fi

echo "--- Build Verification Successful ---"
