import logging
import sys
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QSettings, Qt, QTimer
from PySide6.QtGui import QAction, QGuiApplication
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QSplitter,
    QTabWidget,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from .capabilities import detect_network_interfaces, find_default_qemu
from .config import (
    APP_AUTHOR,
    APP_NAME,
    DWELL_DURATION_MS,
    HOT_EDGE_HEIGHT_LINUX,
    HOT_EDGE_HEIGHT_MACOS,
    POLLING_INTERVAL_MS,
    AppPaths,
    VMProfile,
    build_default_profile,
    ensure_default_profile,
    save_profile,
    save_settings,
)
from .display import PRIMARY_DISPLAY_NAME, available_displays
from .vm import (
    ConfigurationError,
    VMController,
    profile_readiness,
    resolve_sharing,
    shell_join,
)

logger = logging.getLogger("qemu-launcher")


def detect_screens() -> list[str]:
    screens = [display.name for display in available_displays()]
    return screens or [PRIMARY_DISPLAY_NAME]


def smart_profile_defaults() -> VMProfile:
    profile = build_default_profile(name="New VM")
    profile.target_display_name = PRIMARY_DISPLAY_NAME
    return profile


def _make_window_global_macos(win_id: int) -> None:
    """Ensure a window stays on top of fullscreen apps and across all spaces on macOS."""
    try:
        from AppKit import NSStatusWindowLevel, NSWindowCollectionBehaviorCanJoinAllSpaces
        from objc import objc_object

        # win_id is the SIP (pointer) to the NSWindow/NSView
        ns_view = objc_object(c_void_p=win_id)
        # In Qt, winId() might be the view. We need the window.
        window = ns_view.window() if hasattr(ns_view, "window") else ns_view

        if window:
            # NSStatusWindowLevel (25) stays above legacy fullscreen views
            window.setLevel_(NSStatusWindowLevel + 1)
            # Ensure it appears on all desktops/spaces
            window.setCollectionBehavior_(NSWindowCollectionBehaviorCanJoinAllSpaces)
    except Exception:
        # Fallback for non-macOS or if objc/AppKit is missing
        pass


def _rich_list(title: str, items: list[str], empty_text: str) -> str:
    if not items:
        return f"<b>{title}</b><br>{empty_text}"
    rows = "".join(f"<li>{item}</li>" for item in items)
    return f"<b>{title}</b><ul>{rows}</ul>"


class FullscreenOverlay(QWidget):
    """The menu that appears when the hot edge is triggered."""

    def __init__(
        self,
        parent: QWidget | None = None,
        target_display_name: str | None = None,
        on_exit_fs: Callable[[], None] | None = None,
        on_stop: Callable[[], None] | None = None,
        on_show: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self.target_display_name = target_display_name
        self.on_exit_fs = on_exit_fs
        self.on_stop = on_stop
        self.on_show = on_show

        # Persistence Timer (Combat Wayland hiding)
        self.raise_timer = QTimer(self)
        self.raise_timer.timeout.connect(self._persist_on_top)
        self.raise_timer.start(100)

        # ToolTip usually has the highest z-order priority in Qt
        flags = Qt.WindowType.ToolTip | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
        if sys.platform.startswith("linux"):
            flags |= Qt.WindowType.X11BypassWindowManagerHint
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._init_ui()
        self.hide()

        if sys.platform == "darwin":
            _make_window_global_macos(int(self.winId()))

    def _init_ui(self) -> None:
        self.setFixedSize(220, 140)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        # Container for styling
        container = QFrame()
        container.setObjectName("container")
        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(5, 5, 5, 5)

        self.exit_button = QPushButton("Exit Fullscreen")
        self.exit_button.clicked.connect(self._handle_exit)
        container_layout.addWidget(self.exit_button)

        self.stop_button = QPushButton("Stop VM")
        self.stop_button.clicked.connect(self._handle_stop)
        self.stop_button.setStyleSheet("background-color: rgba(180, 50, 50, 200);")
        container_layout.addWidget(self.stop_button)

        help_text = "<b>Hotkeys:</b><br>"
        if sys.platform == "darwin":
            help_text += "Cmd+F: Toggle Fullscreen<br>Ctrl+Alt+G: Release Mouse"
        else:
            help_text += "Ctrl+Alt+F: Toggle Fullscreen<br>Ctrl+Alt+G: Release Mouse"

        self.help_label = QLabel(help_text)
        self.help_label.setStyleSheet("color: #ccc; font-size: 11px;")
        self.help_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        container_layout.addWidget(self.help_label)

        self.setStyleSheet(
            """
            #container {
                background-color: rgba(30, 30, 30, 240);
                border: 1px solid #555;
                border-radius: 12px;
            }
            QPushButton {
                background-color: rgba(60, 60, 60, 200);
                color: #eee;
                border: 1px solid #444;
                border-radius: 6px;
                font-weight: bold;
                padding: 6px;
            }
            QPushButton:hover {
                background-color: rgba(80, 80, 80, 230);
            }
        """
        )
        layout.addWidget(container)

        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self.hide)

    def _handle_exit(self) -> None:
        self.hide()
        self.raise_timer.stop()
        if self.on_exit_fs:
            self.on_exit_fs()

    def _handle_stop(self) -> None:
        self.hide()
        self.raise_timer.stop()
        if self.on_stop:
            self.on_stop()

    def hide(self) -> None:
        super().hide()
        self.raise_timer.stop()

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.hide()
        else:
            super().keyPressEvent(event)

    def _persist_on_top(self) -> None:
        """Force the overlay to stay on top if visible."""
        if self.isVisible():
            self.raise_()
            if sys.platform != "darwin":
                self.activateWindow()

    def show_at_top(self) -> None:
        screens = QGuiApplication.screens()
        if not self.target_display_name:
            screen = screens[0]
        else:
            screen = next((s for s in screens if self.target_display_name in s.name()), screens[0])
        geom = screen.geometry()
        x = geom.x() + (geom.width() - self.width()) // 2
        y = geom.y()
        self.move(x, y)
        self.show()
        if sys.platform == "darwin":
            _make_window_global_macos(int(self.winId()))
        self.raise_()
        self.activateWindow()
        self.raise_timer.start(100)  # Raise every 100ms
        self._hide_timer.start(10000)  # Stay visible for 10s during boot

        if self.on_show:
            self.on_show()


class HotEdgeTrigger(QWidget):
    """Transparent trigger at the top edge of the screen to show the exit menu."""

    def __init__(
        self,
        parent: QWidget | None = None,
        target_display_name: str | None = None,
        on_trigger: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self.target_display_name = target_display_name
        self.on_trigger = on_trigger
        self._dwell_active = False
        flags = (
            Qt.WindowType.Window
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        # On Wayland, we need a tiny bit of opacity to ensure the window is 'visible' for events
        self.setStyleSheet("background-color: rgba(0, 0, 0, 1);")

        # Subtle visual feedback label for dwell progress (only shows at final moment)
        self._status_label = QLabel("", self)
        self._status_label.setStyleSheet(
            "color: #fff; background-color: rgba(0, 0, 0, 120); "
            "padding: 1px 6px; border-radius: 3px; font-size: 10px;"
        )
        self._status_label.hide()

        self._update_geometry()
        self.show()

        if sys.platform == "darwin":
            _make_window_global_macos(int(self.winId()))

        # Dwell Logic
        self.dwell_time_ms = 0
        self.trigger_threshold_ms = DWELL_DURATION_MS

        # Persistence Timer (Combat Wayland hiding) + mouse polling
        self.raise_timer = QTimer(self)
        self.raise_timer.timeout.connect(self._persist_on_top)
        self.raise_timer.start(POLLING_INTERVAL_MS)

        # Display change detection
        QGuiApplication.primaryScreen().primaryStateChanged.connect(self._on_display_changed)
        for screen in QGuiApplication.screens():
            screen.geometryChanged.connect(self._on_display_changed)

        # Ensure it starts on the correct monitor
        QTimer.singleShot(500, self._update_geometry)

    def _on_display_changed(self) -> None:
        """Reposition the hot edge when display configuration changes."""
        QTimer.singleShot(100, self._update_geometry)

    def _update_geometry(self) -> None:
        screens = QGuiApplication.screens()
        if not screens:
            return

        if not self.target_display_name:
            screen = screens[0]
        else:
            screen = next((s for s in screens if self.target_display_name in s.name()), screens[0])

        geom = screen.geometry()
        # Use platform-specific heights from constants
        width = geom.width()
        height = HOT_EDGE_HEIGHT_LINUX if sys.platform.startswith("linux") else HOT_EDGE_HEIGHT_MACOS
        self.setGeometry(
            geom.x(),
            geom.y(),
            width,
            height,
        )
        # Position status label at the center-top of the hot edge
        self._status_label.move((width - 120) // 2, 2)

    def _persist_on_top(self) -> None:
        if self.isVisible():
            self.raise_()

        # Cross-platform direct polling for maximum reliability against jitter
        from PySide6.QtGui import QCursor

        pos = QCursor.pos()

        # Find the screen we are supposed to be on
        screens = QGuiApplication.screens()
        if not screens:
            return
        if not self.target_display_name:
            screen = screens[0]
        else:
            screen = next((s for s in screens if self.target_display_name in s.name()), screens[0])

        geom = screen.geometry()
        trigger_height = self.height() + 5

        # Check if mouse is within the horizontal and vertical bounds of the trigger zone
        in_x = geom.x() <= pos.x() <= (geom.x() + geom.width())
        in_y = geom.y() <= pos.y() <= (geom.y() + trigger_height)

        if in_x and in_y:
            self.dwell_time_ms += POLLING_INTERVAL_MS
            # Only show visual feedback in the final 500ms to avoid distracting during fullscreen
            if self.dwell_time_ms >= self.trigger_threshold_ms - 500:
                if not self._dwell_active:
                    self._dwell_active = True
                    self._status_label.setText("Releasing mouse...")
                    self._status_label.show()
            if self.dwell_time_ms >= self.trigger_threshold_ms:
                self.dwell_time_ms = 0
                self._dwell_active = False
                self._status_label.hide()
                QApplication.beep()
                self._on_dwell_complete()
        else:
            if self._dwell_active:
                self._dwell_active = False
                self._status_label.hide()
            self.dwell_time_ms = 0

    def _on_dwell_complete(self) -> None:
        """Trigger the action after the dwell is successful."""
        if self.on_trigger:
            self.on_trigger()


class MainWindow(QMainWindow):
    def __init__(self, paths: AppPaths) -> None:
        super().__init__()
        self.paths = paths
        self.settings, self.profiles = ensure_default_profile(paths)
        self.profile_map = {profile.profile_id: profile for profile in self.profiles}
        self.current_profile_id = self.settings.last_used_profile or self.profiles[0].profile_id
        self.ui_settings = QSettings(APP_AUTHOR, APP_NAME)
        self._startup_launch_done = False
        self.setWindowTitle(APP_NAME)
        self.resize(1220, 860)

        # Start the escape triggers on the correct monitor
        target_display = self._current_profile().target_display_name
        self.fs_overlay = FullscreenOverlay(
            self,
            target_display,
            on_exit_fs=self._exit_fullscreen,
            on_stop=self._stop_profile,
            on_show=self._ungrab_mouse,
        )
        self.hot_edge = HotEdgeTrigger(self, target_display, on_trigger=self._handle_hot_edge)

        self._build_ui()
        self._restore_window_state()
        self._load_profile_into_form(self.profile_map[self.current_profile_id])
        QTimer.singleShot(0, self._maybe_auto_launch_startup_profile)

    def _ungrab_mouse(self) -> None:
        controller = self._create_controller(self._current_profile())
        if controller.is_running():
            logger.info("Auto-ungrabbing mouse via QMP...")
            controller.send_key(["ctrl", "alt", "g"])

    def _create_controller(self, profile: VMProfile) -> VMController:
        """Centralized factory for VMController with correct dependencies."""
        from .capabilities import probe_qemu
        from .vm import RuntimeArtifacts

        capabilities = probe_qemu(profile.qemu_executable)
        artifacts = RuntimeArtifacts(
            qmp_socket=self.paths.qmp_socket(profile.profile_id),
            pidfile=self.paths.pid_file(profile.profile_id),
            log_file=self.paths.log_file(profile.profile_id),
            stderr_log_file=self.paths.stderr_log_file(profile.profile_id),
        )
        return VMController(profile, capabilities, artifacts)

    def closeEvent(self, event) -> None:  # type: ignore[override]
        controller = self._create_controller(self._current_profile())
        if controller.is_running():
            result = self._confirm_close_running_vm(controller)
            if result == QMessageBox.Cancel:
                event.ignore()
                return
        self.ui_settings.setValue("geometry", self.saveGeometry())
        self.ui_settings.setValue("windowState", self.saveState())
        super().closeEvent(event)

    def _restore_window_state(self) -> None:
        geometry = self.ui_settings.value("geometry")
        if geometry:
            self.restoreGeometry(geometry)
        window_state = self.ui_settings.value("windowState")
        if window_state:
            self.restoreState(window_state)

    def _build_ui(self) -> None:
        toolbar = QToolBar("Main")
        toolbar.setMovable(False)
        toolbar.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.addToolBar(toolbar)

        # Profile Actions
        new_action = QAction("New Profile", self)
        new_action.setShortcut("Ctrl+N")
        new_action.triggered.connect(self._create_profile)
        toolbar.addAction(new_action)

        save_action = QAction("Save Profile", self)
        save_action.setShortcut("Ctrl+S")
        save_action.triggered.connect(self._save_current_profile)
        toolbar.addAction(save_action)

        delete_action = QAction("Delete Profile", self)
        delete_action.triggered.connect(self._delete_profile)
        toolbar.addAction(delete_action)

        toolbar.addSeparator()

        # VM Runtime Actions
        launch_action = QAction("Launch VM", self)
        launch_action.setShortcut("Ctrl+L")
        launch_action.triggered.connect(self._launch_profile)
        toolbar.addAction(launch_action)

        stop_action = QAction("Stop VM", self)
        stop_action.setShortcut("Ctrl+T")
        stop_action.triggered.connect(self._stop_profile)
        toolbar.addAction(stop_action)

        snapshot_action = QAction("Save Snapshot", self)
        snapshot_action.triggered.connect(self._save_vm_state)
        toolbar.addAction(snapshot_action)

        exit_fs_action = QAction("Exit Fullscreen", self)
        exit_fs_action.triggered.connect(self._exit_fullscreen)
        toolbar.addAction(exit_fs_action)

        status_action = QAction("Check Status", self)
        status_action.triggered.connect(self._show_vm_status)
        toolbar.addAction(status_action)

        toolbar.addSeparator()

        # App Actions
        quit_action = QAction("Quit", self)
        quit_action.setShortcut("Ctrl+Q")
        quit_action.triggered.connect(self.close)
        toolbar.addAction(quit_action)

        root = QWidget()
        root_layout = QHBoxLayout(root)
        splitter = QSplitter()
        root_layout.addWidget(splitter)
        self.setCentralWidget(root)

        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.addWidget(QLabel("Profiles"))
        self.profile_list = QListWidget()
        self.profile_list.currentItemChanged.connect(self._on_profile_changed)
        left_layout.addWidget(self.profile_list)
        splitter.addWidget(left_panel)

        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        splitter.addWidget(right_panel)
        splitter.setStretchFactor(1, 1)

        self.tabs = QTabWidget()
        right_layout.addWidget(self.tabs, stretch=1)
        self.tabs.addTab(self._build_overview_tab(), "Overview")
        self.tabs.addTab(self._build_general_tab(), "General")
        self.tabs.addTab(self._build_display_tab(), "Display")
        self.tabs.addTab(self._build_sharing_tab(), "Sharing")
        self.tabs.addTab(self._build_audio_tab(), "Audio / USB")
        self.tabs.addTab(self._build_network_tab(), "Network")
        self.tabs.addTab(self._build_advanced_tab(), "Advanced")

        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        right_layout.addWidget(self.status_label)

        self._rebuild_profile_list()

    def _build_overview_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)

        intro = QLabel(
            "Set up two things first for a natural VM experience: "
            "fullscreen on the right display and a shared folder the guest can mount."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        readiness_box = QGroupBox("Launch Readiness")
        readiness_layout = QVBoxLayout(readiness_box)
        self.readiness_summary_label = QLabel("")
        self.readiness_summary_label.setWordWrap(True)
        readiness_layout.addWidget(self.readiness_summary_label)
        self.readiness_issues_label = QLabel("")
        self.readiness_issues_label.setWordWrap(True)
        self.readiness_issues_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        readiness_layout.addWidget(self.readiness_issues_label)
        layout.addWidget(readiness_box)

        experience_box = QGroupBox("Natural Feel")
        experience_layout = QVBoxLayout(experience_box)
        self.highlights_label = QLabel("")
        self.highlights_label.setWordWrap(True)
        self.highlights_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        experience_layout.addWidget(self.highlights_label)
        self.next_steps_label = QLabel("")
        self.next_steps_label.setWordWrap(True)
        self.next_steps_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        experience_layout.addWidget(self.next_steps_label)
        layout.addWidget(experience_box)

        startup_box = QGroupBox("Startup")
        startup_layout = QFormLayout(startup_box)
        self.auto_launch_check = QCheckBox("Launch a VM when the app opens")
        self.auto_launch_check.stateChanged.connect(self._save_app_settings_from_ui)
        self.startup_profile_combo = QComboBox()
        self.startup_profile_combo.currentIndexChanged.connect(self._save_app_settings_from_ui)
        startup_layout.addRow(self.auto_launch_check)
        startup_layout.addRow("Startup Profile", self.startup_profile_combo)
        layout.addWidget(startup_box)
        layout.addStretch(1)
        return tab

    def _build_general_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)

        intro = QLabel("Pick the VM binary and disk image first. Everything else builds on those two paths.")
        intro.setWordWrap(True)
        layout.addWidget(intro)

        box = QGroupBox("VM Identity")
        form = QFormLayout(box)
        self.name_edit = QLineEdit()
        self.name_edit.textChanged.connect(self._refresh_preview)
        self.arch_combo = QComboBox()
        self.arch_combo.addItems(["x86_64", "aarch64"])
        self.arch_combo.currentTextChanged.connect(self._on_arch_changed)
        self.qemu_edit = self._path_row(form, "QEMU Binary", file_mode="file")
        self.disk_edit = self._path_row(form, "Disk Image", file_mode="file")
        self.firmware_edit = self._path_row(form, "Firmware", file_mode="file")
        form.addRow("Name", self.name_edit)
        form.addRow("Architecture", self.arch_combo)

        resources = QGroupBox("Resources")
        grid = QGridLayout(resources)
        self.memory_spin = QSpinBox()
        self.memory_spin.setRange(512, 1048576)
        self.memory_spin.setSuffix(" MiB")
        self.memory_spin.valueChanged.connect(self._refresh_preview)
        self.cpu_spin = QSpinBox()
        self.cpu_spin.setRange(1, 64)
        self.cpu_spin.valueChanged.connect(self._refresh_preview)
        grid.addWidget(QLabel("Memory"), 0, 0)
        grid.addWidget(self.memory_spin, 0, 1)
        grid.addWidget(QLabel("vCPUs"), 1, 0)
        grid.addWidget(self.cpu_spin, 1, 1)

        layout.addWidget(box)
        layout.addWidget(resources)
        layout.addStretch(1)
        return tab

    def _build_display_tab(self) -> QWidget:
        tab = QWidget()
        root_layout = QVBoxLayout(tab)
        intro = QLabel(
            "Use fullscreen for the most natural feel. Primary-display fullscreen is direct; "
            "non-primary placement depends on the host window manager."
        )
        intro.setWordWrap(True)
        root_layout.addWidget(intro)
        layout = QFormLayout()
        self.display_combo = QComboBox()
        self.display_combo.addItems(detect_screens())
        self.display_combo.currentTextChanged.connect(self._refresh_preview)
        self.fullscreen_check = QCheckBox("Launch fullscreen")
        self.fullscreen_check.stateChanged.connect(self._refresh_preview)
        self.overlay_check = QCheckBox("Show 'Exit Fullscreen' Overlay")
        self.overlay_check.setStyleSheet("margin-left: 20px;")
        self.fullscreen_check.toggled.connect(self.overlay_check.setEnabled)
        self.overlay_check.setEnabled(self.fullscreen_check.isChecked())
        self.overlay_check.stateChanged.connect(self._refresh_preview)
        self.display_backend_combo = QComboBox()
        self.display_backend_combo.addItems(["auto", "cocoa", "gtk", "sdl", "none"])
        self.display_backend_combo.currentTextChanged.connect(self._refresh_preview)
        self.graphics_combo = QComboBox()
        self.graphics_combo.addItems(["auto", "virtio"])
        self.graphics_combo.currentTextChanged.connect(self._refresh_preview)
        layout.addRow("Target Display", self.display_combo)
        layout.addRow("Fullscreen", self.fullscreen_check)
        layout.addRow("", self.overlay_check)
        layout.addRow("Display Backend", self.display_backend_combo)
        layout.addRow("Graphics", self.graphics_combo)
        root_layout.addLayout(layout)
        self.display_info_label = QLabel("")
        self.display_info_label.setWordWrap(True)
        self.display_info_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        root_layout.addWidget(self.display_info_label)
        root_layout.addStretch(1)
        return tab

    def _build_sharing_tab(self) -> QWidget:
        tab = QWidget()
        root_layout = QVBoxLayout(tab)
        intro = QLabel(
            "Shared folders are one of the main flows. Pick a host folder here, "
            "then run the guest mount command shown below."
        )
        intro.setWordWrap(True)
        root_layout.addWidget(intro)
        layout = QFormLayout()
        self.shared_dir_edit = self._browse_line_edit(directory=True)
        self.shared_dir_edit.textChanged.connect(self._refresh_preview)
        self.sharing_combo = QComboBox()
        self.sharing_combo.addItems(["auto", "virtiofs", "9p", "none"])
        self.sharing_combo.currentTextChanged.connect(self._refresh_preview)
        self.mount_tag_edit = QLineEdit()
        self.mount_tag_edit.textChanged.connect(self._refresh_preview)
        layout.addRow("Shared Folder", self.shared_dir_edit.parentWidget())
        layout.addRow("Backend", self.sharing_combo)
        layout.addRow("Mount Tag", self.mount_tag_edit)
        root_layout.addLayout(layout)
        self.sharing_info_label = QLabel("")
        self.sharing_info_label.setWordWrap(True)
        self.sharing_info_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        root_layout.addWidget(self.sharing_info_label)
        root_layout.addStretch(1)
        return tab

    def _build_audio_tab(self) -> QWidget:
        tab = QWidget()
        layout = QFormLayout(tab)
        self.audio_check = QCheckBox("Enable audio")
        self.audio_check.stateChanged.connect(self._refresh_preview)
        self.mic_check = QCheckBox("Enable microphone")
        self.mic_check.stateChanged.connect(self._refresh_preview)
        self.usb_check = QCheckBox("Enable USB controller")
        self.usb_check.stateChanged.connect(self._refresh_preview)
        self.webcam_check = QCheckBox("Enable webcam mode")
        self.webcam_check.stateChanged.connect(self._refresh_preview)
        self.usb_devices_edit = QLineEdit()
        self.usb_devices_edit.setPlaceholderText("vendorid=0x1234,productid=0xabcd; hostbus=1,hostaddr=2")
        self.usb_devices_edit.textChanged.connect(self._refresh_preview)
        layout.addRow(self.audio_check)
        layout.addRow(self.mic_check)
        layout.addRow(self.usb_check)
        layout.addRow(self.webcam_check)
        layout.addRow("USB Devices", self.usb_devices_edit)
        return tab

    def _build_network_tab(self) -> QWidget:
        tab = QWidget()
        layout = QFormLayout(tab)
        self.network_combo = QComboBox()
        self.network_combo.addItems(["auto", "user", "passt", "bridge", "vmnet-shared", "vmnet-bridged"])
        self.network_combo.currentTextChanged.connect(self._refresh_preview)
        self.bridge_combo = QComboBox()
        self.bridge_combo.setEditable(True)
        self.bridge_combo.addItems(detect_network_interfaces())
        self.bridge_combo.currentTextChanged.connect(self._refresh_preview)
        self.auto_resume_check = QCheckBox("Save snapshot and resume on next launch")
        self.auto_resume_check.stateChanged.connect(self._refresh_preview)
        self.resume_snapshot_edit = QLineEdit()
        self.resume_snapshot_edit.textChanged.connect(self._refresh_preview)
        layout.addRow("Network Mode", self.network_combo)
        layout.addRow("Bridge / Interface", self.bridge_combo)
        layout.addRow(self.auto_resume_check)
        layout.addRow("Snapshot Name", self.resume_snapshot_edit)
        return tab

    def _build_advanced_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        self.extra_args_edit = QPlainTextEdit()
        self.extra_args_edit.setPlaceholderText("-- Example: one arg per line, or a single shell-like string")
        self.extra_args_edit.textChanged.connect(self._refresh_preview)
        self.preview_edit = QPlainTextEdit()
        self.preview_edit.setReadOnly(True)
        layout.addWidget(QLabel("Extra QEMU Args"))
        layout.addWidget(self.extra_args_edit, stretch=1)
        layout.addWidget(QLabel("Resolved Launch Command"))
        layout.addWidget(self.preview_edit, stretch=2)
        return tab

    def _path_row(self, form: QFormLayout, label: str, file_mode: str = "file") -> QLineEdit:
        edit = self._browse_line_edit(directory=file_mode == "directory")
        edit.textChanged.connect(self._refresh_preview)
        form.addRow(label, edit.parentWidget())
        return edit

    def _browse_line_edit(self, directory: bool = False) -> QLineEdit:
        wrapper = QWidget()
        layout = QHBoxLayout(wrapper)
        layout.setContentsMargins(0, 0, 0, 0)
        edit = QLineEdit()
        edit.setMinimumWidth(560)
        edit.setClearButtonEnabled(True)
        edit.textChanged.connect(lambda text, field=edit: field.setToolTip(text))
        button = QPushButton("Browse")
        button.clicked.connect(lambda: self._browse_into(edit, directory))
        layout.addWidget(edit, stretch=1)
        layout.addWidget(button)
        edit._wrapper = wrapper  # type: ignore[attr-defined]
        return edit

    def _browse_into(self, edit: QLineEdit, directory: bool) -> None:
        if directory:
            value = QFileDialog.getExistingDirectory(self, "Select Folder", edit.text() or str(Path.home()))
        else:
            value, _ = QFileDialog.getOpenFileName(self, "Select File", edit.text() or str(Path.home()))
        if value:
            edit.setText(value)

    def _rebuild_profile_list(self) -> None:
        self.profile_list.blockSignals(True)
        self.profile_list.clear()
        for profile in self.profile_map.values():
            item = QListWidgetItem(profile.name)
            item.setData(Qt.UserRole, profile.profile_id)
            self.profile_list.addItem(item)
            if profile.profile_id == self.current_profile_id:
                self.profile_list.setCurrentItem(item)
        self.profile_list.blockSignals(False)
        self._rebuild_startup_profile_combo()

    def _rebuild_startup_profile_combo(self) -> None:
        current = self.settings.auto_launch_profile or self.current_profile_id
        self.startup_profile_combo.blockSignals(True)
        self.auto_launch_check.blockSignals(True)
        self.startup_profile_combo.clear()
        for profile in self.profile_map.values():
            self.startup_profile_combo.addItem(profile.name, profile.profile_id)
        index = self.startup_profile_combo.findData(current)
        if index >= 0:
            self.startup_profile_combo.setCurrentIndex(index)
        self.auto_launch_check.setChecked(self.settings.auto_launch_enabled)
        self.auto_launch_check.blockSignals(False)
        self.startup_profile_combo.blockSignals(False)

    def _save_app_settings_from_ui(self) -> None:
        self.settings.auto_launch_enabled = self.auto_launch_check.isChecked()
        profile_id = self.startup_profile_combo.currentData()
        self.settings.auto_launch_profile = profile_id if isinstance(profile_id, str) else None
        save_settings(self.paths, self.settings)

    def _current_profile(self) -> VMProfile:
        return self.profile_map[self.current_profile_id]

    def _on_profile_changed(self, current: QListWidgetItem | None, previous: QListWidgetItem | None) -> None:
        if current is None:
            return
        self._save_current_profile(silent=True)
        profile_id = current.data(Qt.UserRole)
        self.current_profile_id = profile_id
        profile = self.profile_map[profile_id]
        self._load_profile_into_form(profile)
        # Update triggers for the new profile
        self.fs_overlay.target_display_name = profile.target_display_name
        self.hot_edge.target_display_name = profile.target_display_name
        self.hot_edge._update_geometry()

    def _load_profile_into_form(self, profile: VMProfile) -> None:
        self.name_edit.setText(profile.name)
        self.arch_combo.setCurrentText(profile.architecture)
        self.qemu_edit.setText(profile.qemu_executable)
        self.disk_edit.setText(profile.disk_path)
        self.bridge_combo.setCurrentText(profile.bridge_interface or "")
        self.firmware_edit.setText(profile.firmware_path)
        self.memory_spin.setValue(profile.memory_mib)
        self.cpu_spin.setValue(profile.cpu_cores)
        self.display_combo.clear()
        self.display_combo.addItems(detect_screens())
        if profile.target_display_name and self.display_combo.findText(profile.target_display_name) == -1:
            self.display_combo.addItem(profile.target_display_name)
        self.display_combo.setCurrentText(profile.target_display_name)
        self.fullscreen_check.setChecked(profile.enable_fullscreen)
        self.overlay_check.setChecked(getattr(profile, "show_fullscreen_overlay", False))
        self.display_backend_combo.setCurrentText(profile.display_backend)
        self.graphics_combo.setCurrentText(profile.graphics_mode)
        self.shared_dir_edit.setText(profile.shared_dir_path)
        self.sharing_combo.setCurrentText(profile.sharing_backend)
        self.mount_tag_edit.setText(profile.mount_tag)
        self.audio_check.setChecked(profile.enable_audio)
        self.mic_check.setChecked(profile.enable_microphone)
        self.usb_check.setChecked(profile.enable_usb)
        self.webcam_check.setChecked(profile.enable_webcam)
        self.usb_devices_edit.setText("; ".join(profile.usb_devices))
        self.network_combo.setCurrentText(profile.network_mode)
        self.bridge_combo.setCurrentText(profile.bridge_interface)
        self.auto_resume_check.setChecked(profile.auto_resume)
        self.resume_snapshot_edit.setText(profile.resume_snapshot_name)
        self.extra_args_edit.setPlainText("\n".join(profile.extra_args))
        self._refresh_preview()

    def _profile_from_form(self) -> VMProfile:
        profile = self._current_profile().model_copy(deep=True)
        profile.name = self.name_edit.text().strip() or "Unnamed VM"
        profile.architecture = self.arch_combo.currentText()
        profile.qemu_executable = self.qemu_edit.text().strip()
        profile.disk_path = self.disk_edit.text().strip()
        profile.bridge_interface = self.bridge_combo.currentText().strip()
        profile.firmware_path = self.firmware_edit.text().strip()
        profile.memory_mib = self.memory_spin.value()
        profile.cpu_cores = self.cpu_spin.value()
        profile.target_display_name = self.display_combo.currentText() or PRIMARY_DISPLAY_NAME
        profile.enable_fullscreen = self.fullscreen_check.isChecked()
        profile.show_fullscreen_overlay = self.overlay_check.isChecked()
        profile.display_backend = self.display_backend_combo.currentText()
        profile.graphics_mode = self.graphics_combo.currentText()
        profile.shared_dir_path = self.shared_dir_edit.text().strip()
        profile.sharing_backend = self.sharing_combo.currentText()
        profile.mount_tag = self.mount_tag_edit.text().strip() or "host_share"
        profile.enable_audio = self.audio_check.isChecked()
        profile.enable_microphone = self.mic_check.isChecked()
        profile.enable_usb = self.usb_check.isChecked()
        profile.enable_webcam = self.webcam_check.isChecked()
        profile.usb_devices = [chunk.strip() for chunk in self.usb_devices_edit.text().split(";") if chunk.strip()]
        profile.network_mode = self.network_combo.currentText()
        profile.bridge_interface = self.bridge_combo.currentText().strip()
        profile.auto_resume = self.auto_resume_check.isChecked()
        profile.resume_snapshot_name = self.resume_snapshot_edit.text().strip() or "resume"
        profile.extra_args = [line.strip() for line in self.extra_args_edit.toPlainText().splitlines() if line.strip()]
        return profile

    def _save_current_profile(self, silent: bool = False) -> bool:
        try:
            profile = self._profile_from_form()
            self.profile_map[profile.profile_id] = profile
            save_profile(self.paths, profile)
            self.settings.last_used_profile = profile.profile_id
            self.settings.recent_profiles = list(dict.fromkeys([profile.profile_id, *self.settings.recent_profiles]))[
                :10
            ]
            if not self.settings.auto_launch_profile:
                self.settings.auto_launch_profile = profile.profile_id
            save_settings(self.paths, self.settings)
            self._rebuild_profile_list()
            self.status_label.setText(f"Saved profile to {self.paths.profiles_dir / (profile.profile_id + '.toml')}")
            return True
        except Exception as exc:
            if not silent:
                QMessageBox.critical(self, "Save Failed", str(exc))
            return False

    def _create_profile(self) -> None:
        profile = smart_profile_defaults()
        self.profile_map[profile.profile_id] = profile
        self.current_profile_id = profile.profile_id
        self._rebuild_profile_list()
        self._load_profile_into_form(profile)

    def _delete_profile(self) -> None:
        if len(self.profile_map) == 1:
            QMessageBox.warning(self, "Cannot Delete", "At least one profile must exist.")
            return
        profile = self._current_profile()
        path = self.paths.profiles_dir / f"{profile.profile_id}.toml"
        if path.exists():
            path.unlink()
        del self.profile_map[profile.profile_id]
        if self.settings.auto_launch_profile == profile.profile_id:
            self.settings.auto_launch_profile = next(iter(self.profile_map.keys()))
            save_settings(self.paths, self.settings)
        self.current_profile_id = next(iter(self.profile_map.keys()))
        self._rebuild_profile_list()
        self._load_profile_into_form(self.profile_map[self.current_profile_id])

    def _refresh_preview(self) -> None:
        try:
            profile = self._profile_from_form()
            controller = self._create_controller(profile)
            preview = shell_join(controller.preview_command())
            caps = controller.capabilities
            running = "running" if controller.is_running() else "stopped"
            sharing_mode, mount_help = resolve_sharing(profile, caps)
            highlights, notes, issues = profile_readiness(profile, caps, controller.artifacts)
            self.preview_edit.setPlainText(preview)
            self.sharing_info_label.setText(f"Sharing backend: {sharing_mode}\nGuest mount: {mount_help}")
            display_note = (
                "Primary display launch uses QEMU fullscreen directly."
                if profile.target_display_name == PRIMARY_DISPLAY_NAME
                or profile.target_display_name.endswith(" (Primary)")
                else f"Target display: {profile.target_display_name}. Host-side placement is used after launch."
            )
            self.display_info_label.setText(display_note)
            self.readiness_summary_label.setText("READY" if not issues else "ATTENTION REQUIRED")
            self.readiness_issues_label.setText(
                _rich_list("Fix Before Launch", issues, "The profile has the required basics.")
            )
            self.highlights_label.setText(
                _rich_list(
                    "Configured Experience",
                    highlights,
                    "Choose fullscreen, sharing, and resume options to shape the VM experience.",
                )
            )
            self.next_steps_label.setText(
                _rich_list("Notes", notes, "Save the profile, then launch when the profile is ready.")
            )
            displays_text = ",".join(sorted(caps.displays)) or "-"
            audio_text = ",".join(sorted(caps.audio_drivers)) or "-"
            net_text = ",".join(sorted(caps.netdev_backends)) or "-"
            self.status_label.setText(
                f"{caps.version or 'QEMU not found'} | state={running} | displays={displays_text} | "
                f"audio={audio_text} | net={net_text} | share={sharing_mode}"
            )
        except Exception as exc:
            self.preview_edit.setPlainText(str(exc))
            self.sharing_info_label.setText(str(exc))
            self.display_info_label.setText(str(exc))
            self.readiness_summary_label.setText("Needs attention before launch.")
            self.readiness_issues_label.setText(_rich_list("Fix Before Launch", [str(exc)], ""))
            self.highlights_label.setText(
                _rich_list("Configured Experience", [], "Preview becomes richer once the required paths are set.")
            )
            self.next_steps_label.setText(_rich_list("Notes", [], "Start by picking a QEMU binary and disk image."))
            self.status_label.setText(str(exc))

    def _launch_profile(self) -> None:
        import logging

        logger = logging.getLogger("qemu-launcher")
        logger.info(f"UI: Launching profile '{self.current_profile_id}'")
        if not self._save_current_profile():
            return
        profile = self._current_profile()
        if profile.enable_fullscreen:
            self.status_label.setText(
                "Launching in Fullscreen. Move mouse to top edge or press Ctrl+Alt+G to release mouse."
            )
        else:
            self.status_label.setText(f"Launching {profile.name}...")
        controller = self._create_controller(profile)
        try:
            launched = controller.launch()
            if launched:
                # Fullscreen is handled by launch() via _start_display_arrangement():
                # - Primary display: QEMU -full-screen flag (already set in build_command)
                # - Non-primary: arrange_window() with fullscreen=True in background thread
                # No need to call arrange_window() or toggle_fullscreen() here.
                if profile.enable_fullscreen:
                    self.hot_edge.show()
                    QTimer.singleShot(2000, lambda: self._delayed_fullscreen(controller))
                self.status_label.setText(f"Launched {profile.name}")
            else:
                self.status_label.setText(f"Failed to launch {profile.name}")
        except (ConfigurationError, OSError, RuntimeError) as exc:
            QMessageBox.critical(self, "Launch Failed", str(exc))

    def _delayed_fullscreen(self, controller: VMController) -> None:
        """Show the fullscreen overlay after a short delay to ensure window is ready."""
        if controller.is_running():
            if getattr(controller.profile, "show_fullscreen_overlay", False):
                self.fs_overlay.show_at_top()

    def _handle_hot_edge(self) -> None:
        """Determines whether to show the overlay or exit directly after 5s dwell."""
        profile = self._current_profile()
        if getattr(profile, "show_fullscreen_overlay", False):
            self.fs_overlay.show_at_top()
        else:
            self._exit_fullscreen()

    def _maybe_auto_launch_startup_profile(self) -> None:
        if self._startup_launch_done or not self.settings.auto_launch_enabled:
            return
        profile_id = self.settings.auto_launch_profile or self.settings.last_used_profile
        logger.info(f"Auto-launch check: enabled={self.settings.auto_launch_enabled}, profile_id={profile_id}")
        if not profile_id or profile_id not in self.profile_map:
            logger.warning(f"Auto-launch profile '{profile_id}' not found in map: {list(self.profile_map.keys())}")
            return
        self._startup_launch_done = True
        logger.info(f"Auto-launching profile '{profile_id}'")
        if profile_id != self.current_profile_id:
            self.current_profile_id = profile_id
            self._rebuild_profile_list()
            self._load_profile_into_form(self.profile_map[profile_id])
        self._launch_profile()

    def _show_vm_status(self) -> None:
        controller = self._create_controller(self._current_profile())
        try:
            status = controller.status()
            self.status_label.setText(f"VM status: {status.get('status', status)}")
        except Exception as exc:
            QMessageBox.information(self, "VM Status", f"Unable to query VM status: {exc}")

    def _save_vm_state(self) -> None:
        if not self._save_current_profile():
            return
        profile = self._current_profile()
        controller = self._create_controller(profile)
        try:
            controller.save_state()
            self.status_label.setText(f"Saved snapshot '{profile.resume_snapshot_name}' for {profile.name}")
        except Exception as exc:
            QMessageBox.critical(self, "Save State Failed", str(exc))

    def _exit_fullscreen(self) -> None:
        profile = self._current_profile()
        controller = self._create_controller(profile)
        if not controller.is_running():
            self.status_label.setText("No running VM found to exit fullscreen.")
            return

        controller.toggle_fullscreen()
        self.status_label.setText(f"Attempted to exit fullscreen for {profile.name}")
        self.hot_edge.hide()

    def _stop_profile(self) -> None:
        if not self._save_current_profile():
            return
        profile = self._current_profile()
        controller = self._create_controller(profile)
        try:
            controller.stop(save_state=True)
            self.status_label.setText(f"Stopped {profile.name} and saved snapshot '{profile.resume_snapshot_name}'")
            self.hot_edge.hide()
        except Exception as exc:
            QMessageBox.critical(self, "Stop Failed", str(exc))

    def _confirm_close_running_vm(self, controller: VMController) -> int:
        profile = controller.profile
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Question)
        box.setWindowTitle("VM Still Running")
        box.setText(f"{profile.name} is still running.")
        box.setInformativeText("Save state and stop the VM before closing the launcher, leave it running, or cancel.")
        save_button = box.addButton("Save && Stop", QMessageBox.AcceptRole)
        leave_button = box.addButton("Leave Running", QMessageBox.DestructiveRole)
        cancel_button = box.addButton(QMessageBox.Cancel)
        box.exec()
        clicked = box.clickedButton()
        if clicked == save_button:
            try:
                controller.stop(save_state=True)
                self.status_label.setText(f"Stopped {profile.name} and saved snapshot '{profile.resume_snapshot_name}'")
                self.hot_edge.hide()
                return QMessageBox.Yes
            except Exception as exc:
                QMessageBox.critical(self, "Stop Failed", str(exc))
                return QMessageBox.Cancel
        if clicked == leave_button:
            return QMessageBox.No
        if clicked == cancel_button:
            return QMessageBox.Cancel
        return QMessageBox.Cancel

    def _on_arch_changed(self, architecture: str) -> None:
        qemu_path = find_default_qemu(architecture)
        if qemu_path and not self.qemu_edit.text().strip():
            self.qemu_edit.setText(qemu_path)
        self._refresh_preview()


def run_ui(paths: AppPaths | None = None) -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    window = MainWindow(paths or AppPaths())
    window.show()
    return app.exec()
