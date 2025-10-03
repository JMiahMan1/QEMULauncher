#!/bin/bash
set -e

VERSION=${1:-"dev-$(git rev-parse --short HEAD 2>/dev/null || echo unknown)"}
echo "--- Running Build Tests for Version: $VERSION ---"

# Helper function for test reporting with command echo
run_test() {
    desc=$1
    shift
    echo "[Running Test] $desc"
    echo "Command: $*"
    if "$@" >/dev/null 2>&1; then
        printf "  - %-65s [PASS]\n" "$desc"
    else
        printf "  - %-65s [FAIL]\n" "$desc"
        echo ">>> Command failed: $* <<<"
        exit 1
    fi
}

# -----------------------------
# Detect host architecture
# -----------------------------
HOST_ARCH=$(uname -m)
if [[ "$HOST_ARCH" == "arm64" ]]; then
    QEMU_BIN="qemu-system-aarch64"
else
    QEMU_BIN="qemu-system-x86_64"
fi
echo "[Host Architecture Detected: $HOST_ARCH -> Using $QEMU_BIN]"

# -----------------------------
# Check HVF support
# -----------------------------
HVF_FLAG="-accel hvf"
echo "[Checking HVF acceleration support]"
TMPDISK="/tmp/qemu_test.img"
if [ ! -f "$TMPDISK" ]; then
    qemu-img create -f qcow2 "$TMPDISK" 16M >/dev/null
fi

if $QEMU_BIN -M virt -cpu host $HVF_FLAG -nographic -hda "$TMPDISK" -snapshot -no-reboot >/dev/null 2>&1; then
    echo "  - HVF acceleration is available                             [PASS]"
else
    echo "  - HVF acceleration not available, falling back to default  [WARN]"
    HVF_FLAG=""  # Let QEMU pick default acceleration (TCG)
fi

# -----------------------------
# Build script test
# -----------------------------
chmod +x build.sh
echo "[Testing Build Script]"
if ./build.sh "$VERSION" >/dev/null 2>&1; then
    echo "  - Build script executes successfully                          [PASS]"
else
    echo "  - Build script failed                                         [FAIL]"
    exit 1
fi

# -----------------------------
# Verify bundle structure
# -----------------------------
echo
echo "[Verifying Bundle Structure and Files]"
run_test "App bundle directory created" test -d "QEMU Launcher.app"
run_test "'QEMU Launcher.app/Contents' exists" test -d "QEMU Launcher.app/Contents"
run_test "'QEMU Launcher.app/Contents/MacOS' exists" test -d "QEMU Launcher.app/Contents/MacOS"
run_test "'QEMU Launcher.app/Contents/Resources' exists" test -d "QEMU Launcher.app/Contents/Resources"
run_test "Info.plist file exists" test -f "QEMU Launcher.app/Contents/Info.plist"
run_test "Main executable is executable" test -x "QEMU Launcher.app/Contents/MacOS/QEMU Launcher"
run_test "launcher.sh is executable" test -x "QEMU Launcher.app/Contents/Resources/launcher.sh"
grep -q "$VERSION" "QEMU Launcher.app/Contents/Info.plist" \
    && echo "  - Info.plist contains correct version                         [PASS]" \
    || { echo "  - Info.plist missing version                                 [FAIL]"; exit 1; }

# -----------------------------
# Incremental QEMU feature tests
# -----------------------------
echo
echo "[Verifying QEMU Feature Commands]"

# Step 1: QEMU binary availability
run_test "QEMU binary is available" $QEMU_BIN --version

# Step 2: Base command
run_test "QEMU base command works" \
    $QEMU_BIN -M virt -cpu host $HVF_FLAG -nographic -hda "$TMPDISK" -no-reboot -snapshot

# Step 3: Networking
run_test "VirtIO Network command works" \
    $QEMU_BIN -M virt -cpu host $HVF_FLAG -nographic -hda "$TMPDISK" \
    -nic user,model=virtio-net-pci -no-reboot -snapshot

# Step 4: Shared Folder
run_test "Shared Folder command works" \
    $QEMU_BIN -M virt -cpu host $HVF_FLAG -nographic -hda "$TMPDISK" \
    -nic user,model=virtio-net-pci \
    -fsdev local,id=fsdev0,path=/tmp,security_model=none \
    -device virtio-9p-pci,fsdev=fsdev0,mount_tag=host_share \
    -no-reboot -snapshot

# Step 5: Webcam passthrough (stub)
# Note: VendorID/productID should be replaced with actual device IDs of host webcam
run_test "Webcam passthrough command works (stub)" \
    $QEMU_BIN -M virt -cpu host $HVF_FLAG -nographic -hda "$TMPDISK" \
    -nic user,model=virtio-net-pci \
    -fsdev local,id=fsdev0,path=/tmp,security_model=none \
    -device virtio-9p-pci,fsdev=fsdev0,mount_tag=host_share \
    -device ich9-usb-ehci1 \
    -device ich9-usb-uhci1 \
    -device usb-host,vendorid=0x046d,productid=0x0825 \
    -no-reboot -snapshot

echo
echo "--- All tests completed successfully ---"

