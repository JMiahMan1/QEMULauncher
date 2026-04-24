from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QAction, QGuiApplication
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
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

from .capabilities import find_default_qemu
from .config import APP_AUTHOR, APP_NAME, AppPaths, VMProfile, ensure_default_profile, save_profile, save_settings
from .vm import ConfigurationError, VMController, shell_join


def detect_screens() -> list[str]:
    app = QGuiApplication.instance()
    if not app:
        return ["Primary Display"]
    screens = []
    for index, screen in enumerate(app.screens()):
        name = screen.name() or f"Display {index + 1}"
        if screen is app.primaryScreen():
            name = f"{name} (Primary)"
        screens.append(name)
    return screens or ["Primary Display"]


def smart_profile_defaults() -> VMProfile:
    architecture = "aarch64" if Path("/usr/bin/qemu-system-aarch64").exists() and sys.platform == "darwin" else "x86_64"
    return VMProfile(
        name="New VM",
        architecture=architecture,
        qemu_executable=find_default_qemu(architecture),
        target_display_name="Primary Display",
    )


class MainWindow(QMainWindow):
    def __init__(self, paths: AppPaths) -> None:
        super().__init__()
        self.paths = paths
        self.settings, self.profiles = ensure_default_profile(paths)
        self.profile_map = {profile.profile_id: profile for profile in self.profiles}
        self.current_profile_id = self.settings.last_used_profile or self.profiles[0].profile_id
        self.ui_settings = QSettings(APP_AUTHOR, APP_NAME)
        self.setWindowTitle(APP_NAME)
        self.resize(1220, 860)
        self._build_ui()
        self._restore_window_state()
        self._load_profile_into_form(self.profile_map[self.current_profile_id])

    def closeEvent(self, event) -> None:  # type: ignore[override]
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
        self.addToolBar(toolbar)

        new_action = QAction("New Profile", self)
        new_action.triggered.connect(self._create_profile)
        toolbar.addAction(new_action)

        delete_action = QAction("Delete Profile", self)
        delete_action.triggered.connect(self._delete_profile)
        toolbar.addAction(delete_action)

        save_action = QAction("Save", self)
        save_action.triggered.connect(self._save_current_profile)
        toolbar.addAction(save_action)

        preview_action = QAction("Preview Command", self)
        preview_action.triggered.connect(self._refresh_preview)
        toolbar.addAction(preview_action)

        launch_action = QAction("Save && Launch", self)
        launch_action.triggered.connect(self._launch_profile)
        toolbar.addAction(launch_action)

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

    def _build_general_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)

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
        layout = QFormLayout(tab)
        self.display_combo = QComboBox()
        self.display_combo.addItems(detect_screens())
        self.display_combo.currentTextChanged.connect(self._refresh_preview)
        self.fullscreen_check = QCheckBox("Launch fullscreen")
        self.fullscreen_check.stateChanged.connect(self._refresh_preview)
        self.display_backend_combo = QComboBox()
        self.display_backend_combo.addItems(["auto", "cocoa", "gtk", "sdl", "none"])
        self.display_backend_combo.currentTextChanged.connect(self._refresh_preview)
        self.graphics_combo = QComboBox()
        self.graphics_combo.addItems(["auto", "virtio"])
        self.graphics_combo.currentTextChanged.connect(self._refresh_preview)
        layout.addRow("Target Display", self.display_combo)
        layout.addRow("Fullscreen", self.fullscreen_check)
        layout.addRow("Display Backend", self.display_backend_combo)
        layout.addRow("Graphics", self.graphics_combo)
        return tab

    def _build_sharing_tab(self) -> QWidget:
        tab = QWidget()
        layout = QFormLayout(tab)
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
        self.bridge_edit = QLineEdit()
        self.bridge_edit.textChanged.connect(self._refresh_preview)
        self.auto_resume_check = QCheckBox("Save snapshot and resume on next launch")
        self.auto_resume_check.stateChanged.connect(self._refresh_preview)
        self.resume_snapshot_edit = QLineEdit()
        self.resume_snapshot_edit.textChanged.connect(self._refresh_preview)
        layout.addRow("Network Mode", self.network_combo)
        layout.addRow("Bridge / Interface", self.bridge_edit)
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

    def _load_profile_into_form(self, profile: VMProfile) -> None:
        self.name_edit.setText(profile.name)
        self.arch_combo.setCurrentText(profile.architecture)
        self.qemu_edit.setText(profile.qemu_executable)
        self.disk_edit.setText(profile.disk_path)
        self.firmware_edit.setText(profile.firmware_path)
        self.memory_spin.setValue(profile.memory_mib)
        self.cpu_spin.setValue(profile.cpu_cores)
        self.display_combo.clear()
        self.display_combo.addItems(detect_screens())
        self.display_combo.setCurrentText(profile.target_display_name)
        self.fullscreen_check.setChecked(profile.enable_fullscreen)
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
        self.bridge_edit.setText(profile.bridge_name)
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
        profile.firmware_path = self.firmware_edit.text().strip()
        profile.memory_mib = self.memory_spin.value()
        profile.cpu_cores = self.cpu_spin.value()
        profile.target_display_name = self.display_combo.currentText() or "Primary Display"
        profile.enable_fullscreen = self.fullscreen_check.isChecked()
        profile.display_backend = self.display_backend_combo.currentText()
        profile.graphics_mode = self.graphics_combo.currentText()
        profile.shared_dir_path = self.shared_dir_edit.text().strip()
        profile.sharing_backend = self.sharing_combo.currentText()
        profile.mount_tag = self.mount_tag_edit.text().strip() or "host_share"
        profile.enable_audio = self.audio_check.isChecked()
        profile.enable_microphone = self.mic_check.isChecked()
        profile.enable_usb = self.usb_check.isChecked()
        profile.enable_webcam = self.webcam_check.isChecked()
        profile.usb_devices = [
            chunk.strip() for chunk in self.usb_devices_edit.text().split(";") if chunk.strip()
        ]
        profile.network_mode = self.network_combo.currentText()
        profile.bridge_name = self.bridge_edit.text().strip()
        profile.auto_resume = self.auto_resume_check.isChecked()
        profile.resume_snapshot_name = self.resume_snapshot_edit.text().strip() or "resume"
        profile.extra_args = [
            line.strip()
            for line in self.extra_args_edit.toPlainText().splitlines()
            if line.strip()
        ]
        return profile

    def _save_current_profile(self, silent: bool = False) -> bool:
        try:
            profile = self._profile_from_form()
            self.profile_map[profile.profile_id] = profile
            save_profile(self.paths, profile)
            self.settings.last_used_profile = profile.profile_id
            self.settings.recent_profiles = list(dict.fromkeys([profile.profile_id, *self.settings.recent_profiles]))[:10]
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
        self.current_profile_id = next(iter(self.profile_map.keys()))
        self._rebuild_profile_list()
        self._load_profile_into_form(self.profile_map[self.current_profile_id])

    def _refresh_preview(self) -> None:
        try:
            profile = self._profile_from_form()
            controller = VMController(self.paths, profile)
            preview = shell_join(controller.preview_command())
            caps = controller.capabilities
            self.preview_edit.setPlainText(preview)
            self.status_label.setText(
                f"{caps.version or 'QEMU not found'} | displays={','.join(sorted(caps.displays)) or '-'} | "
                f"audio={','.join(sorted(caps.audio_drivers)) or '-'} | "
                f"net={','.join(sorted(caps.netdev_backends)) or '-'}"
            )
        except Exception as exc:
            self.preview_edit.setPlainText(str(exc))
            self.status_label.setText(str(exc))

    def _launch_profile(self) -> None:
        if not self._save_current_profile():
            return
        profile = self._current_profile()
        controller = VMController(self.paths, profile)
        try:
            controller.launch()
            self.status_label.setText(f"Launched {profile.name}. QMP: {controller.artifacts.qmp_socket}")
        except (ConfigurationError, OSError, RuntimeError) as exc:
            QMessageBox.critical(self, "Launch Failed", str(exc))

    def _on_arch_changed(self, architecture: str) -> None:
        qemu_path = find_default_qemu(architecture)
        if qemu_path and not self.qemu_edit.text().strip():
            self.qemu_edit.setText(qemu_path)
        self._refresh_preview()


def run_ui() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    window = MainWindow(AppPaths())
    window.show()
    return app.exec()
