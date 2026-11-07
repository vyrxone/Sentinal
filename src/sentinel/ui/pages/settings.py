from __future__ import annotations

from datetime import datetime
from typing import Any, Callable

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout, QGroupBox,
                               QHBoxLayout, QMessageBox, QScrollArea, QSpinBox, QVBoxLayout, QWidget)

from ...core import paths
from ...platform.windows import autostart
from ...storage.database import DatabaseError
from ...storage.settings_store import DEFS_BY_KEY, PRESET_KEYS, PRESET_LABELS, SettingError
from ..widgets import make_button, make_label, page_header


class SettingsPage(QWidget):
    title = "Settings"
    self_scrolling = True

    def __init__(self, ctrl) -> None:
        super().__init__()
        self.ctrl = ctrl
        self._syncers: list[Callable[[], None]] = []
        self._syncing = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 20, 0, 0)
        outer.addWidget(page_header("Settings", "Changes apply immediately and are saved locally."))
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        host = QWidget()
        lay = QVBoxLayout(host)
        lay.setContentsMargins(0, 0, 24, 20)
        lay.setSpacing(14)
        scroll.setWidget(host)
        outer.addWidget(scroll, 1)

        # ---------------------------------------------------------- animation
        g = self._group(lay, "Animation")
        self._choice(g, "animation_fps", "Frame-rate cap", [(f"{v} FPS", v) for v in (15, 24, 30)])
        self._choice(g, "animation_intensity", "Effect intensity",
                     [("Low (fewest effects)", "low"), ("Medium", "medium"), ("High", "high")])
        self._check(g, "reduce_under_load", "Automatically reduce effects during sustained high CPU load")
        self._check(g, "animation_paused", "Pause the animation (clock and data still update)")
        self._check(g, "reduced_motion", "Reduced motion: static emblem, no continuous animation")
        self._check(g, "lightweight_mode", "Lightweight mode for low-memory PCs: 15 fps cap, fewest effects, system "
                                           "sampled at most every 4 seconds (your own settings are kept)")

        # --------------------------------------------------------- appearance
        g = self._group(lay, "Wallpaper appearance")
        self.preset = QComboBox()
        for key in ("sentinel_core", "crimson_watch", "silent_guardian", "custom"):
            self.preset.addItem(PRESET_LABELS[key], key)
        self.preset.setToolTip("Presets change the settings below. Changing any of them yourself switches to Custom.")
        self.preset.activated.connect(self._on_preset)
        g.addRow("Preset", self.preset)
        self._syncers.append(lambda: self.preset.setCurrentIndex(
            max(0, self.preset.findData(self.ctrl.settings.get("appearance_preset")))))
        self._check(g, "show_overlay", "Show the monitoring overlay on the wallpaper")
        self._choice(g, "overlay_mode", "Overlay size", [("Compact", "compact"), ("Expanded", "expanded")])
        self._int(g, "emblem_size", "Emblem size", " %", 50, 150)
        self._choice(g, "particle_intensity", "Particles",
                     [("Off", "off"), ("Low", "low"), ("Medium", "medium"), ("High", "high")])
        self._choice(g, "pulse_intensity", "Rings and pulses",
                     [("Off", "off"), ("Low", "low"), ("Medium", "medium"), ("High", "high")])
        self._int(g, "dimmer_percent", "Dimmer (darkens the wallpaper)", " %", 0, 70)
        self._check(g, "show_clock", "Show clock and date")

        # --------------------------------------------------------------- mode
        g = self._group(lay, "Wallpaper and window")
        self._choice(g, "start_mode", "Mode at launch",
                     [("Wallpaper mode", "wallpaper"), ("Control panel only", "panel")])
        self._choice(g, "desktop_integration", "Wallpaper placement",
                     [("Automatic: embed behind desktop icons (Windows)", "auto"),
                      ("Always use a normal window (safe fallback)", "window_only")])
        g.addRow("", make_label("Takes effect the next time wallpaper mode is entered. If embedding is not "
                                "possible Sentinel uses a normal window and says so.", "muted", wrap=True))
        self._choice(g, "monitor_mode", "Monitors",
                     [("Primary monitor only", "primary"), ("One selected monitor", "selected"),
                      ("All monitors", "all")])
        self.monitor_combo = QComboBox()
        self.monitor_combo.activated.connect(self._on_monitor_selected)
        g.addRow("Selected monitor", self.monitor_combo)
        self.monitor_note = make_label("", "muted", wrap=True)
        g.addRow("", self.monitor_note)
        self._check(g, "open_panel_on_start", "Open the control panel at launch")
        self._check(g, "close_to_tray", "Closing the panel keeps Sentinel running in the tray")

        # --------------------------------------------------------- monitoring
        g = self._group(lay, "Monitoring and warnings")
        self._float(g, "sample_interval_s", "Sampling interval", " s", 1.0, 10.0, 0.5)
        self._int(g, "cpu_warn_percent", "CPU warning threshold", " %", 10, 100)
        self._int(g, "ram_warn_percent", "Memory warning threshold", " %", 10, 100)
        self._int(g, "disk_warn_percent", "System disk warning threshold", " %", 10, 100)
        self._int(g, "sustained_samples", "Samples above the limit before warning", "", 1, 30)
        self._int(g, "max_events", "Maximum audit events kept", "", 1000, 500000)
        self._int(g, "process_interval_s", "Process list refresh interval", " s", 2, 60)
        self._check(g, "notifications_enabled", "Show a tray notification for warnings")
        self._int(g, "notify_min_interval_s", "Minimum time between notifications", " s", 5, 3600)

        # ------------------------------------------------------------ startup
        g = self._group(lay, "Startup")
        self.autostart = QCheckBox("Start Sentinel when I sign in to Windows")
        self.autostart.setEnabled(autostart.is_supported())
        g.addRow(self.autostart)
        self.autostart_note = make_label(
            "Off by default. Adds one entry to your per-user Run list (no administrator rights); "
            "turn it off here at any time." if autostart.is_supported()
            else "Available on Windows only.", "muted", wrap=True)
        g.addRow(self.autostart_note)
        self._sync_autostart()
        self.autostart.toggled.connect(self._on_autostart)

        # --------------------------------------------------------------- data
        g = self._group(lay, "Data and privacy")
        g.addRow(make_label("Sentinel makes no network connections, sends no telemetry and stores no passwords "
                            "or API keys.", "muted", wrap=True))
        self.location = make_label(str(paths.data_dir()), wrap=True)
        g.addRow("Data folder", self.location)
        row = QHBoxLayout()
        for text, fn in (("Open data folder", self._open_data), ("Back up database…", self._backup),
                         ("Export settings…", self._export_settings), ("Import settings…", self._import_settings)):
            b = make_button(text)
            b.clicked.connect(fn)
            row.addWidget(b)
        row.addStretch(1)
        g.addRow(row)
        row2 = QHBoxLayout()
        reset = make_button("Reset settings to defaults", danger=True)
        reset.clicked.connect(self._reset)
        clear = make_button("Clear event log…", danger=True)
        clear.clicked.connect(self._clear_events)
        row2.addWidget(reset)
        row2.addWidget(clear)
        row2.addStretch(1)
        g.addRow(row2)
        lay.addStretch(1)
        ctrl.setting_changed.connect(lambda *_: self._sync())

    # ------------------------------------------------------------ builders
    def _group(self, parent: QVBoxLayout, title: str) -> QFormLayout:
        box = QGroupBox(title.upper())
        form = QFormLayout(box)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        form.setHorizontalSpacing(18)
        form.setVerticalSpacing(10)
        parent.addWidget(box)
        return form

    def _apply(self, key: str, value: Any) -> None:
        if self._syncing:
            return
        try:
            old = self.ctrl.settings.get(key)
            if key in PRESET_KEYS:
                self.ctrl.settings.set_appearance(key, value)     # a manual change leaves the named preset
            else:
                self.ctrl.settings.set(key, value)
            if old != value:
                self.ctrl.log("settings", "setting_changed", f"{DEFS_BY_KEY[key].label}: {old} → {value}",
                              details={"key": key, "old": old, "new": value})
        except (SettingError, DatabaseError) as exc:
            QMessageBox.warning(self, "Setting not saved", str(exc))
            self._sync()
            return
        if key == "animation_paused":
            self.ctrl.state_changed.emit()

    def _check(self, form: QFormLayout, key: str, label: str) -> None:
        box = QCheckBox(label)
        box.toggled.connect(lambda checked, k=key: self._apply(k, bool(checked)))
        form.addRow(box)
        self._syncers.append(lambda: box.setChecked(bool(self.ctrl.settings.get(key))))

    def _choice(self, form: QFormLayout, key: str, label: str, options: list) -> None:
        combo = QComboBox()
        for text, value in options:
            combo.addItem(text, value)
        combo.currentIndexChanged.connect(lambda _i, k=key, c=combo: self._apply(k, c.currentData()))
        form.addRow(label, combo)
        self._syncers.append(lambda: combo.setCurrentIndex(max(0, combo.findData(self.ctrl.settings.get(key)))))

    def _int(self, form: QFormLayout, key: str, label: str, suffix: str, lo: int, hi: int) -> None:
        spin = QSpinBox()
        spin.setRange(lo, hi)
        spin.setSuffix(suffix)
        spin.setKeyboardTracking(False)
        spin.valueChanged.connect(lambda v, k=key: self._apply(k, int(v)))
        form.addRow(label, spin)
        self._syncers.append(lambda: spin.setValue(int(self.ctrl.settings.get(key))))

    def _float(self, form: QFormLayout, key: str, label: str, suffix: str, lo: float, hi: float, step: float) -> None:
        spin = QDoubleSpinBox()
        spin.setRange(lo, hi)
        spin.setSingleStep(step)
        spin.setDecimals(1)
        spin.setSuffix(suffix)
        spin.setKeyboardTracking(False)
        spin.valueChanged.connect(lambda v, k=key: self._apply(k, float(v)))
        form.addRow(label, spin)
        self._syncers.append(lambda: spin.setValue(float(self.ctrl.settings.get(key))))

    def _on_preset(self, _index: int) -> None:
        key = self.preset.currentData()
        if key == "custom":
            return
        try:
            self.ctrl.settings.apply_preset(key)
            self.ctrl.log("settings", "preset_applied", f"Appearance preset: {PRESET_LABELS[key]}.")
        except (SettingError, DatabaseError) as exc:
            QMessageBox.warning(self, "Preset not applied", str(exc))
        self._sync()

    def _fill_monitors(self) -> None:
        from ..wallpaper_host import screen_infos
        from ...core.monitors import select_screens

        infos = screen_infos()
        self.monitor_combo.blockSignals(True)
        self.monitor_combo.clear()
        for i in infos:
            self.monitor_combo.addItem(i.label(), i.id)
        want = self.ctrl.settings.get("selected_monitor")
        idx = self.monitor_combo.findData(want)
        self.monitor_combo.setCurrentIndex(max(0, idx))
        self.monitor_combo.blockSignals(False)
        self.monitor_combo.setEnabled(self.ctrl.settings.get("monitor_mode") == "selected")
        _, note = select_screens(infos, self.ctrl.settings.get("monitor_mode"), want)
        extra = ("Multi-monitor embedding is new in V2 and has not been verified on real multi-monitor hardware. "
                 "If it misbehaves, choose 'Primary monitor only'. Window mode always uses one window.")
        self.monitor_note.setText((note + " " if note else "") + extra)

    def _on_monitor_selected(self, _index: int) -> None:
        self._apply("selected_monitor", self.monitor_combo.currentData() or "")

    def showEvent(self, e) -> None:  # noqa: N802
        super().showEvent(e)
        self._sync()
        self._fill_monitors()

    def _sync(self) -> None:
        self._syncing = True
        try:
            for fn in self._syncers:
                fn()
        finally:
            self._syncing = False

    # ----------------------------------------------------------- start with Windows
    def _sync_autostart(self) -> None:
        try:
            state = autostart.status() if autostart.is_supported() else "off"
        except autostart.AutostartError:
            state = "off"
        self.autostart.blockSignals(True)
        self.autostart.setChecked(state != "off")
        self.autostart.blockSignals(False)
        if state == "stale":
            self.autostart_note.setText("Start with Windows is on, but it points to a different or moved copy of "
                                        "Sentinel. Turn it off and on again to repair it.")

    def _on_autostart(self, checked: bool) -> None:
        question = ("Add Sentinel to the programs that start when you sign in to Windows?\n\n"
                    "You can turn this off here at any time." if checked
                    else "Stop Sentinel from starting when you sign in to Windows?")
        if QMessageBox.question(self, "Start with Windows", question) != QMessageBox.StandardButton.Yes:
            self._sync_autostart()
            return
        try:
            if checked:
                command = autostart.enable()
                self.ctrl.log("settings", "autostart_enabled", "Start with Windows enabled.", details={"command": command})
            else:
                autostart.disable()
                self.ctrl.log("settings", "autostart_disabled", "Start with Windows disabled.")
        except autostart.AutostartError as exc:
            QMessageBox.warning(self, "Start with Windows", str(exc))
        self._sync_autostart()

    # ------------------------------------------------------------------- data
    def _open_data(self) -> None:
        folder = paths.ensure_dir(paths.data_dir())
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def _backup(self) -> None:
        default = paths.backup_dir() / f"sentinel-{datetime.now().strftime('%Y%m%d-%H%M%S')}.db"
        path, _ = QFileDialog.getSaveFileName(self, "Back up database", str(default), "SQLite database (*.db)")
        if not path:
            return
        try:
            self.ctrl.db.backup_to(path)
        except DatabaseError as exc:
            QMessageBox.critical(self, "Backup failed", str(exc))
            return
        self.ctrl.log("app", "backup_created", "Database backup created.", details={"file": path})
        QMessageBox.information(self, "Backup complete", f"A consistent copy of your Sentinel data was saved to:\n{path}")

    def _export_settings(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Export settings", "sentinel-settings.json", "JSON (*.json)")
        if path:
            try:
                self.ctrl.settings.export_json(path)
            except OSError as exc:
                QMessageBox.critical(self, "Export failed", str(exc))

    def _import_settings(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Import settings", "", "JSON (*.json)")
        if not path:
            return
        try:
            applied, rejected = self.ctrl.settings.import_json(path)
        except SettingError as exc:
            QMessageBox.warning(self, "Import failed", str(exc))
            return
        self.ctrl.log("settings", "settings_imported", f"Imported {len(applied)} setting(s).")
        text = f"Applied {len(applied)} setting(s)."
        if rejected:
            text += "\n\nIgnored:\n" + "\n".join(f"{k}: {v}" for k, v in list(rejected.items())[:10])
        QMessageBox.information(self, "Import complete", text)
        self._sync()

    def _reset(self) -> None:
        if QMessageBox.question(self, "Reset settings", "Reset every setting to its default value?\n\n"
                                "Projects, watched folders and events are kept.") != QMessageBox.StandardButton.Yes:
            return
        self.ctrl.settings.reset()
        self.ctrl.log("settings", "settings_reset", "All settings reset to defaults.")
        self._sync()
        self.ctrl.state_changed.emit()

    def _clear_events(self) -> None:
        count = self.ctrl.events.count()
        if QMessageBox.question(self, "Clear event log",
                                f"Permanently delete all {count} recorded event(s)?\n\n"
                                "Consider exporting them first. This cannot be undone.") != QMessageBox.StandardButton.Yes:
            return
        try:
            self.ctrl.db.execute("DELETE FROM events")
        except DatabaseError as exc:
            QMessageBox.critical(self, "Could not clear events", str(exc))
            return
        self.ctrl.log("app", "events_cleared", f"Event log cleared by the user ({count} events removed).")
