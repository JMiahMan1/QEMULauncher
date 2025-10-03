#!/bin/bash

# --- Test Configuration ---
VERSION="${1}"
APP_NAME="QEMU Launcher"
OUTPUT_APP="$APP_NAME.app"

# --- Test Utilities ---
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color
FAIL_COUNT=0

# Helper function for QEMU command validation that shows the full error on failure.
validate_qemu_command() {
    local description="$1"
    shift
    local qemu_command=("$@")
    local error_log="qemu_error.log"

    printf "  - %-60s" "$description"

    # Run command, redirecting stderr to a log file
    if gtimeout 1.5s "${qemu_command[@]}" -display none >/dev/null 2>"$error_log"; then
        printf "[${GREEN}PASS${NC}]\n"
    else
        local exit_code=$?
        if [ $exit_code -eq 124 ]; then
            printf "[${GREEN}PASS${NC}]\n" # Timed out, which is a success for this test
        else
            printf "[${RED}FAIL${NC}]\n"
            echo -e "${RED}    └─ Command failed with exit code $exit_code. QEMU output:${NC}"
            # Indent and print the error log
            sed 's/^/         /' "$error_log"
            ((FAIL_COUNT++))
        fi
    fi
    rm -f "$error_log"
}

# --- Main Test Logic ---
echo "--- Running Build Tests for Version: $VERSION ---"

if [ -z "$VERSION" ]; then
    echo -e "${RED}Error: Version number must be provided as the first argument.${NC}"
    exit 1
fi

# Detect architecture and set QEMU binary
HOST_ARCH=$(uname -m)
if [ "$HOST_ARCH" = "arm64" ]; then
    QEMU_EXEC="qemu-system-aarch64"
    DEFAULT_CPU="-cpu max"
else
    QEMU_EXEC="qemu-system-x86_64"
    DEFAULT_CPU="-cpu max"
fi
echo "[Host Architecture Detected: $HOST_ARCH -> Using $QEMU_EXEC]"

# Check for HVF support and set flags accordingly
echo "[Checking HVF acceleration support]"
ACCEL_FLAG="-accel tcg"
CPU_FLAG="$DEFAULT_CPU"
if gtimeout 1s "$QEMU_EXEC" -M virt -accel hvf -cpu host -nographic > /dev/null 2>&1; RC=$?; if [ $RC -eq 0 ] || [ $RC -eq 124 ]; then
    echo "  - HVF acceleration is available."
    ACCEL_FLAG="-accel hvf"
    CPU_FLAG="-cpu host"
else
    echo -e "  - HVF acceleration not available, falling back to TCG. [${YELLOW}WARN${NC}]"
fi

# 1. Run the build script
echo -e "\n[Testing Build Script]"
if ./build.sh "$VERSION" > /dev/null 2>&1; then
    echo -e "  - Build script executes successfully                          [${GREEN}PASS${NC}]"
else
    echo -e "  - Build script executes successfully                          [${RED}FAIL${NC}]"
    exit 1
fi

# 2. Structural Tests (simplified for brevity)
echo -e "\n[Verifying Bundle Structure and Files]"
if [ -d "$OUTPUT_APP" ]; then
    echo -e "  - App bundle directory created                                [${GREEN}PASS${NC}]"
else
    echo -e "  - App bundle directory created                                [${RED}FAIL${NC}]"
    exit 1
fi

# 3. Feature Integration Tests
echo -e "\n[Verifying QEMU Feature Commands]"
if ! command -v "$QEMU_EXEC" &> /dev/null || ! command -v gtimeout &> /dev/null; then
    echo -e "${YELLOW}WARNING: Skipping feature tests. 'qemu' or 'coreutils' (gtimeout) not found.${NC}"
else
    # Setup dummy files for tests
    mkdir -p test_assets
    DUMMY_DISK_PATH="$(pwd)/test_assets/dummy_disk.qcow2"
    DUMMY_FW_PATH="$(pwd)/test_assets/dummy_firmware.fd"
    
    qemu-img create -f qcow2 "$DUMMY_DISK_PATH" 100M > /dev/null
    dd if=/dev/zero of="$DUMMY_FW_PATH" bs=1m count=4 > /dev/null 2>&1
    
    # Define command arguments incrementally
    BASE_CMD=("$QEMU_EXEC" "-M" "virt" "$ACCEL_FLAG" "$CPU_FLAG" "-m" "512M" "-drive" "if=pflash,format=raw,readonly=on,file=$DUMMY_FW_PATH" "-drive" "id=disk0,if=none,format=qcow2,file=$DUMMY_DISK_PATH" "-device" "virtio-blk-pci,drive=disk0")
    NET_ARGS=("-netdev" "user,id=n0" "-device" "virtio-net-pci,netdev=n0")
    SHARE_ARGS=("-fsdev" "local,id=fs0,path=.,security_model=none" "-device" "virtio-9p-pci,fsdev=fs0,mount_tag=test")
    GPU_ARGS=("-device" "virtio-gpu-pci")
    INPUT_ARGS=("-device" "virtio-keyboard-pci" "-device" "virtio-tablet-pci")
    AUDIO_ARGS=("-audiodev" "none,id=snd0" "-device" "virtio-sound-pci,audiodev=snd0")
    WEBCAM_ARGS=("-device" "nec-usb-xhci,id=usb" "-device" "usb-camera")
    
    # Run tests with increasing complexity
    validate_qemu_command "Base command is valid" "${BASE_CMD[@]}"
    validate_qemu_command "Base + Networking is valid" "${BASE_CMD[@]}" "${NET_ARGS[@]}"
    validate_qemu_command "Base + Net + Shared Folder is valid" "${BASE_CMD[@]}" "${NET_ARGS[@]}" "${SHARE_ARGS[@]}"
    validate_qemu_command "Full command is valid" "${BASE_CMD[@]}" "${GPU_ARGS[@]}" "${INPUT_ARGS[@]}" "${NET_ARGS[@]}" "${AUDIO_ARGS[@]}" "${SHARE_ARGS[@]}" "${WEBCAM_ARGS[@]}"

    # Teardown
    rm -rf test_assets
fi

# --- Final Result ---
echo ""
if [ $FAIL_COUNT -eq 0 ]; then
    echo -e "${GREEN}--- All tests passed successfully! ---${NC}"
    rm -rf "$OUTPUT_APP"
    exit 0
else
    echo -e "${RED}--- $FAIL_COUNT test(s) failed. Please review the output. ---${NC}"
    exit 1
fi
