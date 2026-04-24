#!/bin/bash
set -e

echo "--- Running Build and Packaging Test Suite ---"

pytest -q

echo "--- Build Verification Successful ---"
