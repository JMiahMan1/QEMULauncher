# Testing Framework & Validation

QEMU Launcher uses a multi-tiered testing approach to ensure stability across macOS and Linux, as well as across different privilege levels.

## 1. Local Testing (Unit & Integration)

The local test suite uses `pytest` and is designed to run in any standard Python environment.

### What is tested:
*   **Command Generation**: Verifies that QEMU flags (like `-accel hvf`, `-cpu host`, and `-netdev`) are correctly generated for each platform.
*   **Profile Management**: Ensures VM profiles are correctly saved, loaded, and migrated between versions.
*   **Display Logic**: Validates that monitor resolution and coordinate math are correct before the launcher attempts to move a window.
*   **Permissions**: Mocks different privilege scenarios (sudo vs. user) to ensure the correct execution path is chosen.

### Running Local Tests:
```bash
# Run all tests
pytest

# Run linter (Ruff)
bash lint.sh
```

---

## 2. Remote macOS Smoke Tests (Hardware Validation)

Since many of the launcher's features (Accessibility API, window levels, and `vmnet`) require real macOS hardware, we use a remote smoke testing framework.

### The `remote_macos_smoke.sh` Script:
This script automates the process of validating a build on a physical Mac via SSH. It performs the following steps:
1.  **Repository Sync**: Fetches the latest code from the development branch to the remote host.
2.  **Environment Isolation**: Creates a clean Python virtual environment on the Mac.
3.  **Build Validation**: Runs the full `build.sh` process on the Mac to ensure the bundle, entitlements, and C helper compile correctly.
4.  **Integrity Check**: Verifies the internal bundle structure of the resulting `.app`.

### Configuration (Generic Example):
Remote testing is configured via a `.env` file. **Never commit your actual credentials.**

```bash
# Example .env configuration
MAC_TEST_HOST=10.0.0.1
MAC_TEST_USER=testuser
MAC_TEST_REPO_PATH=/Users/testuser/Code/QEMULauncher
MAC_TEST_BRANCH=main
MAC_TEST_PYTHON=/usr/bin/python3
MAC_TEST_SSH_KEY=~/.ssh/id_ed25519
```

### Running Remote Tests:
```bash
bash scripts/remote_macos_smoke.sh
```

---

## 3. Hardware UI Testing (Local Only)

Because GitHub Actions and remote SSH sessions often lack a physical graphical console, we provide a specialized suite for testing the UI on your local Mac.

### What is tested:
*   **Window Z-Order**: Verifies that the "Hot Edge" and "Exit Menu" overlays are at the correct `NSStatusWindowLevel` to stay on top of fullscreen VMs.
*   **Monitor Accuracy**: Validates that the UI elements correctly follow the VM to secondary monitors.
*   **Hover Logic**: Simulates user interaction to ensure the top-bar reveals itself as expected.

### Running Hardware Tests:
```bash
# Run on your local Mac with the display active
bash tests/run_hardware_tests.sh
```

---

## 4. Visual & UI Integrity Verification (Remote)

*   **`screencapture`**: Captures the remote display to verify that the launcher correctly moved the VM window to the target monitor.
*   **`osascript` (AppleScript)**: Queries the WindowServer to verify that the UI triggers are at the `NSStatusWindowLevel` (Level 26) and are present on the correct coordinates.

---

## 4. Continuous Integration (GitHub Actions)

The `.github/workflows/mac-build.yml` workflow automatically runs the local test suite and performs a dry-run build on every push to ensure no regressions in the build process.
