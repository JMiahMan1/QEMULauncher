# QEMU macOS Launcher

This project provides a native macOS application (.app bundle) for launching QEMU virtual machines with a focus on running ARM64 (e.g., Linux or macOS guests) or x86\_64 virtual machines using Homebrew dependencies.

The application automatically checks for and manages key dependencies (Homebrew, python3-tk, and QEMU) on first run, offering to install them via Terminal commands if necessary.

## Features

* **Native macOS Application:** Built as a standard `.app` bundle, allowing for easy launch via Finder or Spotlight.
* **Dynamic Dependency Checking:** The embedded `launcher.sh` script checks for essential tools (`brew`, `python3` with `tkinter`, `qemu-system-*`) in the correct path.
* **Guided Setup:** If dependencies are missing, the app uses macOS dialogs (`osascript`) to prompt the user to install them via Homebrew in a new Terminal window.
* **Python GUI Configuration:** A built-in Python/Tkinter application (`qemu_app.py`) provides a persistent GUI for setting VM parameters (Architecture, Disk Path, Firmware, Shared Folder).
* **Automated Fullscreen Launch:** Launches the QEMU VM directly into fullscreen mode upon clicking "Save and Launch."
* **CI/CD Ready:** The project includes a GitHub Actions workflow (`.github/workflows/mac-build.yml`) to automate the build and release process.

## Project Files

The core automation files are:
* `build.sh`: The automated script used for creating the `.app` bundle locally or on the CI runner.
* `launcher.sh`: The bash script that is the main executable inside the `.app` bundle; it handles all environment setup and dependency checks.
* `qemu_app.py`: The Python/Tkinter GUI application for VM configuration.
* `RunLinux.icns`: The application icon file.
* `.github/workflows/mac-build.yml`: The GitHub Actions workflow file for automated building.

## Requirements & Dependencies

The target user environment requires the following tools to run the VM:

1.  **Homebrew:** The package manager is required for installing QEMU and Python dependencies.
2.  **QEMU:** Specifically `qemu-system-aarch64` or `qemu-system-x86_64` (installed via `brew install qemu`).
3.  **Python 3:** The interpreter must have the Tkinter library installed (`brew install python-tk`).

***Note: The application will prompt the user to install any missing dependencies on first launch.***

## Automated Build (GitHub Actions)

The application build is fully automated using GitHub Actions on a macOS runner.

### How to Trigger a Release

The build workflow is triggered whenever a new version tag is pushed to the repository.

1.  Ensure all your code changes are committed to the `main` branch.
2.  Create a version tag and push it:

    ```bash
    # Example: Create a tag named v1.0.0
    git tag v1.0.0
    git push origin v1.0.0
    ```

3.  The workflow will run, build the `QEMU Launcher.app` with the version number, package it, and create a release on GitHub with the `.zip` file attached.

## Local Manual Build

If you need to test the build locally, you can run the `build.sh` script directly.

1.  **Ensure Dependencies:** Make sure you have the core dependencies installed (or trust that your `launcher.sh` will handle them later):
    ```bash
    brew install qemu python-tk
    ```

2.  **Make Script Executable:**
    ```bash
    chmod +x build.sh
    ```

3.  **Run the Build Script:** Pass the desired version number as an argument.

    ```bash
    # This command creates the QEMU\ Launcher.app file in the current directory
    ./build.sh 1.0.0
    ```

4.  **Launch:** Double-click `QEMU Launcher.app` to run the application and begin the configuration process.

