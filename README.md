# QEMU Launcher

A premium, native-feeling virtualization manager for macOS and Linux. QEMU Launcher provides a streamlined interface for managing high-performance VMs with native-grade monitor placement, seamless networking, and a state-of-the-art UI.

## 📸 UI Preview

<p align="center">
  <img src="assets/screenshots/01_overview.png" width="45%" alt="Overview Tab">
  <img src="assets/screenshots/02_general.png" width="45%" alt="General Configuration">
</p>
<p align="center">
  <img src="assets/screenshots/03_display.png" width="45%" alt="Display & Fullscreen Options">
  <img src="assets/screenshots/04_network.png" width="45%" alt="Native macOS vmnet Networking">
</p>

## 🚀 Key Features

*   **Premium macOS Experience**: Optimized for Apple Silicon and Intel, featuring native window management and multi-monitor support.
*   **Seamless Networking**: High-performance `vmnet-shared` and `bridged` networking using a bundled privileged helper—no root passwords required after a one-time setup.
*   **Intelligent Display Placement**: Automatically moves and resizes VMs to your chosen monitor using pure Accessibility APIs on macOS (no Automation prompts!) and `wmctrl` on Linux X11 (gracefully degrading on Wayland).
*   **Auto-Resume**: Remembers your VM state and automatically resumes where you left off.
*   **Native UI Overlays**: Cross-platform "Hot Edge" triggers and elegant exit menus that automatically ungrab your mouse via QMP and stay visible even in greedy fullscreen modes.
*   **Shared Folders**: Integrated support for `virtio-9p` and `virtiofs` for high-speed file sharing between host and guest.
*   **QMP Integration**: Real-time status monitoring and graceful power management via the QEMU Machine Protocol.

## 📦 Installation (Linux / RPM)

The easiest way to install and stay updated is via our official RPM repository.

### 1. Add the Repository
Create a file at `/etc/yum.repos.d/qemu-launcher.repo` with the following content:

```ini
[qemu-launcher]
name=QEMU Launcher Repository
baseurl=https://JMiahMan1.github.io/QEMULauncher/repo/
enabled=1
gpgcheck=0
```

### 2. Install via DNF
```bash
sudo dnf install qemu-launcher
```

## 📦 Installation (macOS)

1.  **Download the DMG**: Open the `QEMU Launcher.dmg`.
2.  **Drag to Applications**: Move the app to your `/Applications` folder.
3.  **Launch**: Open the app. 

## 🛠 Usage

*   **Select a VM**: Use the sidebar to switch between your configured profiles.
*   **Configure**: Set your CPU, Memory, and target Display.
*   **Launch**: Click the "Launch" button.
*   **Exit Fullscreen**: Move your mouse to the **top-center** of the screen to reveal the "Exit Fullscreen" menu.

## 🏗 Architecture & Testing

*   **[Architecture Documentation](ARCHITECTURE.md)**: Deep dive into networking, window management, and security.
*   **[Testing Framework](TESTING.md)**: Details on local unit tests and remote hardware validation.

## 💻 Development

### Prerequisites
*   Python 3.12+
*   QEMU installed (`brew install qemu` or `dnf install qemu-system-x86`)
*   `wmctrl` (Linux only, for display placement: `dnf install wmctrl`)

### Build from Source
```bash
# Clone the repository
git clone https://github.com/JMiahMan1/QEMULauncher.git
cd QEMULauncher

# Install dependencies
pip install -r requirements.txt

# Build the macOS app bundle
./build.sh
```

---
Built with ❤️ for the QEMU community.
