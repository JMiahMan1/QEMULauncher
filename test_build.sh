#!/bin/bash

# --- Test Configuration ---
VERSION="${1}"
APP_NAME="QEMU Launcher"
OUTPUT_APP="$APP_NAME.app"
TEST_CONFIG="test_config.ini"

# --- Test Utilities ---
RED='\033[0;31m'
GREEN='\033[0;32m'
NC='\033[0m' # No Color
FAIL_COUNT=0

# Helper function to run the app in dry-run mode and verify its output
validate_app_logic() {
    local description="$1"
    local config_content="$2"
    local expected_flags=("${@:3}")
    
    printf "  - %-60s" "$description"
    
    # Create temp config
    echo "[VM]" > "$TEST_CONFIG"
    echo "$config_content" >> "$TEST_CONFIG"
    
    # Run dry-run
    local output
    output=$(python3 qemu_app.py --config "$TEST_CONFIG" --dry-run 2>&1)
    local exit_code=$?
    
    if [ "$exit_code" -ne 0 ]; then
        printf "[%bFAIL%b] (App crashed)\n" "${RED}" "${NC}"
        # shellcheck disable=SC2001
        echo "$output" | sed 's/^/       /'
        FAIL_COUNT=$((FAIL_COUNT + 1))
        return
    fi
    
    # Check for integrity issues
    if echo "$output" | grep -q "\[INTEGRITY\] Error"; then
        printf "[%bFAIL%b] (Integrity error)\n" "${RED}" "${NC}"
        # shellcheck disable=SC2001
        echo "$output" | grep "\[INTEGRITY\]" | sed 's/^/       /'
        FAIL_COUNT=$((FAIL_COUNT + 1))
        return
    fi
    
    # Verify expected flags
    local missing=()
    for flag in "${expected_flags[@]}"; do
        if [[ ! "$output" == *"$flag"* ]]; then
            missing+=("$flag")
        fi
    done
    
    if [ ${#missing[@]} -eq 0 ]; then
        printf "[%bPASS%b]\n" "${GREEN}" "${NC}"
    else
        printf "[%bFAIL%b] (Missing: %s)\n" "${RED}" "${NC}" "${missing[*]}"
        FAIL_COUNT=$((FAIL_COUNT + 1))
    fi
    
    rm -f "$TEST_CONFIG"
}

# --- Main Test Logic ---
echo "--- Running Logic-Based Build Tests for Version: $VERSION ---"

if [ -z "$VERSION" ]; then
    echo -e "${RED}Error: Version number must be provided as the first argument.${NC}"
    exit 1
fi

# 1. Run the build script
echo -e "\n[Testing Build Script]"
if ./build.sh "$VERSION" > /dev/null 2>&1; then
    echo -e "  - Build script executes successfully                          [${GREEN}PASS${NC}]"
else
    echo -e "  - Build script executes successfully                          [${RED}FAIL${NC}]"
    exit 1
fi

# 2. Structural Tests
echo -e "\n[Verifying Bundle Structure and Files]"
if [ -d "$OUTPUT_APP" ]; then
    echo -e "  - App bundle directory created                                [${GREEN}PASS${NC}]"
else
    echo -e "  - App bundle directory created                                [${RED}FAIL${NC}]"
    exit 1
fi

# 3. Application Logic Tests (The "Golden Command" strategy)
echo -e "\n[Verifying Application Logic & Command Generation]"

# Define common paths for testing
MOCK_QEMU="/usr/local/bin/qemu-system-aarch64"
MOCK_DISK="/tmp/test_disk.vmdk"
MOCK_FW="/tmp/test_fw.fd"

# Ensure mock paths exist (as files) for validation inside the app if needed
touch "$MOCK_DISK" "$MOCK_FW"

# TEST 1: Basic Configuration
BASIC_CONFIG="arch = aarch64
qemu_executable = $MOCK_QEMU
disk_path = $MOCK_DISK
firmware_path = $MOCK_FW
enable_fullscreen = False"
validate_app_logic "Basic configuration (AArch64)" "$BASIC_CONFIG" "-M" "virt" "disk0" "snd0"

# TEST 2: Shared Folder Logic
SHARED_CONFIG="$BASIC_CONFIG
shared_dir_path = /tmp
mount_tag = test_share"
validate_app_logic "Shared folder enabled" "$SHARED_CONFIG" "virtio-9p-pci" "test_share" "mapped-xattr"

# TEST 3: Hardware Features (Webcam, Mic)
HW_CONFIG="$BASIC_CONFIG
enable_webcam = True
enable_microphone = True"
validate_app_logic "Hardware features (Webcam + Mic)" "$HW_CONFIG" "usb-camera" "in.frequency=48000"

# TEST 4: Network Modes
NET_CONFIG="$BASIC_CONFIG
network_mode = vmnet-shared"
validate_app_logic "Network mode: Shared (vmnet)" "$NET_CONFIG" "vmnet-shared"

# TEST 5: Integrity Check (Multiple pflash)
# Note: qemu_app.py only defines one pflash by default, but we can check if our integrity logic works
# by manually verifying it doesn't trigger unexpectedly.
validate_app_logic "Collision check (No false positives)" "$BASIC_CONFIG" "-drive"

# Clean up mock files
rm -f "$MOCK_DISK" "$MOCK_FW"

# --- Final Result ---
echo ""
if [ $FAIL_COUNT -eq 0 ]; then
    # 5. Runtime Smoke Test (macOS only)
    if [[ "$OSTYPE" == "darwin"* ]]; then
        echo "[Runtime Smoke Test]"
        BINARY_PATH="./$OUTPUT_APP/Contents/MacOS/QEMU Launcher"
        if [[ -f "$BINARY_PATH" ]]; then
            echo "  - Executing packaged binary with --dry-run..."
            # We run with --dry-run and a dummy config to see if it even starts up
            # This catches "No module named X" errors
            echo "[VM]" > smoke_test.ini
            echo "arch=aarch64" >> smoke_test.ini
            if "$BINARY_PATH" --config smoke_test.ini --dry-run > /dev/null 2>&1; then
                echo "  - Binary executed successfully (imports OK)      [PASS]"
            else
                echo "  - Binary failed to execute (check dependencies)  [FAIL]"
                rm -f smoke_test.ini
                exit 1
            fi
            rm -f smoke_test.ini
        else
            echo "  - Packaged binary not found at $BINARY_PATH [FAIL]"
            exit 1
        fi
    fi

    echo ""
    echo -e "${GREEN}--- All tests passed successfully! ---${NC}"
    exit 0
else
    echo -e "${RED}--- $FAIL_COUNT test(s) failed. Please review the output. ---${NC}"
    exit 1
fi
