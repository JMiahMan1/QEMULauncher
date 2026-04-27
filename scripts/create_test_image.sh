#!/bin/bash
# create_test_image.sh - Provision a minimal Alpine Linux qcow2 for QEMU testing

IMAGE_DIR="tests/images"
IMAGE_NAME="alpine-test.qcow2"
IMAGE_URL="https://dl-cdn.alpinelinux.org/alpine/v3.19/releases/cloud/oci_alpine-3.19.9-x86_64-bios-tiny-r0.qcow2"

mkdir -p "$IMAGE_DIR"

if [ -f "$IMAGE_DIR/$IMAGE_NAME" ]; then
    echo "-> Test image already exists at $IMAGE_DIR/$IMAGE_NAME"
    exit 0
fi

echo "-> Downloading minimal Alpine Linux test image (~114MB)..."
curl -L -o "$IMAGE_DIR/$IMAGE_NAME" "$IMAGE_URL"

if [ $? -eq 0 ]; then
    echo "-> Success! Minimal test image is ready at $IMAGE_DIR/$IMAGE_NAME"
    echo "-> Default Login: root (no password)"
else
    echo "-> Error: Failed to download the test image."
    exit 1
fi
