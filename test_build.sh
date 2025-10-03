#!/bin/bash

# --- Test Configuration ---
VERSION="${1}"
APP_NAME="QEMU Launcher"
OUTPUT_APP="$APP_NAME.app"
QEMU_EXEC="qemu-system-aarch64"

# --- Test Utilities ---
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color
FAIL_COUNT=0

# Helper function to run a structural test
run_test() {
    local description="$1"
    local command="$2"
    
    printf "  - %-60s" "$description"
    if eval "$command"; then
        printf "[${GREEN}PASS${NC}]\n"
    else
        printf "[${RED}FAIL${NC}]\n"
        ((FAIL_COUNT++))
    fi
}

# Helper function for QEMU command dry run
validate_qemu_command() {
    local description="$1"
    shift
    local qemu_command=("$@")
    
    printf "  - %-60s" "$description"
    
    if gtimeout 1.5s "${qemu_command[@]}" -display none >/dev/null 2>&1; then
         printf "[${GREEN}PASS${NC}]\n"
    else
         if [ $? -eq 124 ]; then
             printf "[${GREEN}PASS${NC}]\n"
         else
             printf "[${RED}FAIL${NC}]\n"
             ((FAIL_COUNT++))
         fi
    fi
}

# --- Main Test Logic ---
echo "--- Running Build Tests for Version: $VERSION ---"

if [ -z "$VERSION" ]; then
    echo -e "${RED}Error: Version number must be provided as the first argument.${NC}"
    exit 1
fi

# 1. Run the build script
run_test "Build script executes successfully" "./build.sh $VERSION > /dev/null 2>&1"

# --- Structural Tests ---
echo -e "\n[Verifying Bundle Structure and Files]"
CONTENTS_DIR="$OUTPUT_APP/Contents"
MACOS_DIR="$CONTENTS_DIR/MacOS"
RESOURCES_DIR="$CONTENTS_DIR/Resources"
EXECUTABLE_PATH="$MACOS_DIR/$APP_NAME"
PLIST_FILE="$CONTENTS_DIR/Info.plist"
run_test "App bundle directory created" "[ -d '$OUTPUT_APP' ]"
run_test "'$CONTENTS_DIR' exists" "[ -d '$CONTENTS_DIR' ]"
run_test "'$MACOS_DIR' exists" "[ -d '$MACOS_DIR' ]"
run_test "'$RESOURCES_DIR' exists" "[ -d '$RESOURCES_DIR' ]"
run_test "Info.plist file exists" "[ -f '$PLIST_FILE' ]"
run_test "Main executable is executable" "[ -x '$EXECUTABLE_PATH' ]"
run_test "launcher.sh is executable" "[ -x '$RESOURCES_DIR/launcher.sh' ]"
run_test "Info.plist contains correct version" "grep -q '<string>$VERSION</string>' '$PLIST_FILE'"


# --- Feature Integration Tests ---
echo -e "\n[Verifying QEMU Feature Commands]"
if ! command -v $QEMU_EXEC &> /dev/null || ! command -v gtimeout &> /dev/null; then
    echo -e "${YELLOW}WARNING: Skipping feature tests. 'qemu' or 'coreutils' (gtimeout) not found.${NC}"
else
    # Setup dummy files for tests
    mkdir -p test_assets
    DUMMY_DISK_PATH="$(pwd)/test_assets/dummy_disk.qcow2"
    DUMMY_FW_PATH="$(pwd)/test_assets/dummy_firmware.fd"
    
    qemu-img create -f qcow2 "$DUMMY_DISK_PATH" 100M > /dev/null
    dd if=/dev/zero of="$DUMMY_FW_PATH" bs=1m count=4 > /dev/null 2>&1
    
    BASE_CMD=("$QEMU_EXEC" "-M" "virt" "-accel" "hvf" "-m" "512M" "-drive" "if=pflash,format=raw,readonly=on,file=$DUMMY_FW_PATH" "-drive" "id=disk0,if=none,format=qcow2,file=$DUMMY_DISK_PATH")

    validate_qemu_command "Base command is valid" "${BASE_CMD[@]}"
    validate_qemu_command "VirtIO Network command is valid" "${BASE_CMD[@]}" "-netdev" "user,id=n0" "-device" "virtio-net-pci,netdev=n0"
    validate_qemu_command "Shared Folder command is valid" "${BASE_CMD[@]}" "-fsdev" "local,id=fs0,path=.,security_model=none" "-device" "virtio-9p-pci,fsdev=fs0,mount_tag=test"
    validate_qemu_command "Webcam Passthrough command is valid" "${BASE_CMD[@]}" "-device" "nec-usb-xhci,id=usb" "-device" "usb-camera"

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
