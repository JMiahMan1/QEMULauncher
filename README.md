# QEMU macOS Launcher

A professional, native macOS orchestration tool for QEMU virtual machines. This application streamlines the configuration and launching of ARM64 (Apple Silicon) or x86_64 guests with a focus on ease of use, multi-monitor productivity, and full hardware integration.

## 🌟 Key Features

*   **Intelligent Window Orchestration:** Automatically detects secondary displays and seamlessly launches your VM into fullscreen on the monitor of your choice.
*   **Professional CLI Interface:** Supports `--dry-run` to inspect generated commands, `--config` for custom settings, and `--setup` for guided reconfiguration.
*   **Hardware Passthrough:** Full support for Webcam, Microphone, High-Definition Audio (HDA), and Shared Folders (`virtio-9p-pci`).
*   **Automatic Migration:** Seamlessly upgrades legacy `.ini` configurations to the modern `.json` standard.
*   **Native Experience:** Built as a standard `.app` bundle with automated dependency checks and guided Homebrew installation.
*   **CI/CD Hardened:** Fully validated through a cross-platform GitHub Actions pipeline using logic-based verification and automated linting.

## 🛠️ Requirements & Installation

The application requires the following core components:

1.  **Homebrew:** For managing QEMU and Python binaries.
2.  **Python 3.12+:** Required for modern `pyobjc` and `py2app` stability.
3.  **QEMU:** Installed via `brew install qemu`.

> [!NOTE]
> The application will automatically detect and offer to install missing dependencies on its first launch.

## 🚀 Usage

### Graphical Mode
Simply double-click **QEMU Launcher.app** to open the configuration GUI.

### Command Line Mode
The underlying Python engine supports advanced CLI flags:
```bash
# Preview the exact QEMU command without launching
python3 qemu_app.py --dry-run

# Run the setup UI to reconfigure your VM
python3 qemu_app.py --setup

# Load a specific configuration file
python3 qemu_app.py --config my_custom_vm.json
```

## 🏗️ Development & Building

### Quality Assurance
We maintain high code quality through automated testing and linting:
*   **Linting:** `ruff check .`
*   **Unit Tests:** `python3 -m unittest discover tests`
*   **Build Verification:** `./test_build.sh` (validates command generation logic)

### Local Manual Build
To build the `.app` bundle manually on macOS:
```bash
# Pin dependencies
pip install -r requirements.txt

# Run the build script with a version tag
./build.sh 1.2.0
```

## 🤖 Automated Releases (GitHub Actions)
The build is fully automated. To trigger a new release:
1.  Commit your changes to `main`.
2.  Tag the commit: `git tag v1.2.0 && git push origin v1.2.0`.
3.  The CI will build, test, and package the release automatically.
