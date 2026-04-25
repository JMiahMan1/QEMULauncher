# QEMU Launcher Architecture

This document describes the technical implementation details that enable QEMU Launcher to provide high-performance virtualization with a seamless macOS/Linux desktop experience.

## Core Design Principles

1.  **User-Session Integrity**: The main QEMU process always runs as the logged-in user. This ensures it has a valid connection to the WindowServer, enabling standard window management, multi-monitor placement, and input capture.
2.  **Privilege Separation**: Root-level tasks (like networking) are delegated to a minimal, self-contained C helper.
3.  **Seamless Integration**: The launcher manages the lifecycle of all VM-related components (QMP, logs, networking, and UI overlays).

---

## 1. Networking: The Privileged Handshake

To achieve near-native networking performance without running the entire VM as root, the launcher uses a **Privileged Handshake** pattern.

### Components:
*   **Launcher (Python)**: The user-facing UI.
*   **Helper (`qemu_launcher_helper.c`)**: A minimal C binary compiled with SUID-root permissions.
*   **QEMU**: The virtualization engine.

### The Flow:
1.  **Initialization**: The Launcher starts the `qemu_launcher_helper` in the background.
2.  **Creation**: The Helper uses Apple's `vmnet.framework` to initialize a `vmnet-shared` or `vmnet-bridged` interface.
3.  **Communication**: The Helper creates a Unix Domain Socket at `/tmp/qemu-launcher-net.sock`.
4.  **Handoff**: QEMU is launched with the `-netdev stream` backend, pointing to the Unix socket.
5.  **Connection**: When QEMU connects, the Helper sends the `vmnet` file descriptor over the socket using `SCM_RIGHTS`.
6.  **Persistence**: The Helper remains active as long as the connection is open, keeping the network interface alive.

---

## 2. Window Management & Multi-Monitor Support

On macOS, QEMU's Cocoa interface enforces strict internal constraints that often conflict with native OS window management.

### Aspect-Ratio-Aware Placement
To prevent the common "Ding" sound and OS-level blocks during boot, the launcher performs a geometric handshake via the Accessibility API (AX):
1.  It queries the VM's current aspect ratio.
2.  It calculates the largest possible frame that fits the target monitor while respecting that ratio.
3.  It performs the move and resize in a single atomic AX operation.

### Legacy vs. Native Fullscreen
*   **Primary Monitor**: Uses QEMU's internal `-full-screen` (Legacy) for maximum compatibility.
*   **Secondary Monitors**: Launches windowed, moves the frame via AX, and then triggers a Native Space transition via the `kAXFullScreenAttribute`.

---

## 3. UI Overlays & Window Leveling

The "Exit Fullscreen" menu and the "Hotspot" trigger must remain visible even when QEMU is in its greedy legacy fullscreen mode.

### NSStatusWindowLevel
The launcher uses native Cocoa API calls to elevate these triggers:
*   **Level**: `NSStatusWindowLevel + 1` (Level 26). This is higher than standard app windows and stays above the QEMU Cocoa view.
*   **Collection Behavior**: `NSWindowCollectionBehaviorCanJoinAllSpaces`. This ensures the exit menu follows you even if you switch virtual desktops or monitors.

---

## 4. State Management (QMP)

The launcher communicates with QEMU in real-time via the **QEMU Machine Protocol (QMP)** over a Unix socket. This enables:
*   **Graceful Shutdown**: Sending the `system_powerdown` command.
*   **Status Monitoring**: Detecting if the guest has paused or crashed.
*   **Snapshots**: Automating the save/load process for the 'Auto-Resume' feature.
