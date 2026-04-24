#!/bin/bash

set -e

VERSION="${1:-ci-test}"

echo "--- Running Build Verification for Version: $VERSION ---"
pytest -q

if ./build.sh "$VERSION" > /dev/null 2>&1; then
    echo "--- Build completed successfully ---"
else
    echo "--- Build failed ---"
    exit 1
fi
