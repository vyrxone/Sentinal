"""The control panel: navigation, toolbar actions, status bar and keyboard shortcuts."""

from __future__ import annotations


from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QCloseEvent, QKeySequence
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QListWidget, QMainWindow, QMessageBox, QScrollArea,
                               QSizePolicy, QStackedWidget, QToolBar, QVBoxLayout, QWidget)

from .. import APP_NAME, RELEASE_LABEL, __version__
from ..core.formatting import freshness
from .icons import make_icon
from .pages.ai import AIPage
from .pages.events import EventsPage
from .pages.findings import FindingsPage
from .pages.monitor import MonitorPage
from .pages.overview import OverviewPage
from .pages.performance import PerformancePage
from .pages.projects import ProjectsPage
from .pages.security import SecurityPage
from .pages.settings import SettingsPage


class MainWindow(QMainWindow):
    def __init__(self, ctrl) -> None:
        super().__init__()
        self.ctrl = ctrl
        self.setWindowTitle(f"{APP_NAME} {RELEASE_LABEL}")
        self.setWindowIcon(make_icon())
        self.resize(1120, 760)
        self.setMinimumSize(900, 620)
        self._exiting = False
        self._tray_hint_shown = False

        central = QWidget()
        h = QHBoxLayout(central)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)
        side = QWidget()
        side.setFixedWidth(200)
        sl = QVBoxLayout(side)
        sl.setContentsMargins(0, 0, 0, 0)
        brand = QLabel(f"VYRXONE\nSENTINEL")
        brand.setContentsMargins(20, 18, 16, 12)
        brand.setStyleSheet("font-size:15px; font-weight:600; letter-spacing:4px; color:#c9a24b;")
        sl.addWidget(brand)
        self.nav = QListWidget()
        self.nav.setObjectName("nav")
        sl.addWidget(self.nav, 1)
        ver = QLabel(f"{RELEASE_LABEL} · v{__version__}")
        ver.setProperty("role", "muted")
        ver.setContentsMargins(20, 6, 0, 12)
        sl.addWidget(ver)
        h.addWidget(side)

        self.stack = QStackedWidget()
        h.addWidget(self.stack, 1)
        self.setCentralWidget(central)

        self.pages = [OverviewPage(ctrl), MonitorPage(ctrl), SecurityPage(ctrl), ProjectsPage(ctrl),
                      EventsPage(ctrl), SettingsPage(ctrl), PerformancePage(ctrl), FindingsPage(ctrl), AIPage(ctrl)]
        for i, page in enumerate(self.pages):
            self.nav.addItem(page.title.upper())
            self.stack.addWidget(page if getattr(page, "self_scrolling", False) else self._scrollable(page))
            act = QAction(page.title, self)
            act.setShortcut(QKeySequence(f"Ctrl+{i + 1}"))
            act.triggered.connect(lambda _c=False, idx=i: self.goto(idx))
            self.addAction(act)
        self._ai_index = len(self.pages) - 1
        ctrl.ai.context_requested.connect(self._open_ai)
        self.nav.currentRowChanged.connect(self.stack.setCurrentIndex)
        self.nav.setCurrentRow(0)

        self._build_actions()
        self._build_status_bar()
        ctrl.state_changed.connect(self._sync_actions)
        ctrl.desktop_status_changed.connect(lambda *_: self._sync_actions())
        self._sync_actions()
        self._clock = QTimer(self)
        self._clock.setInterval(1000)
        self._clock.timeout.connect(self._update_status)
        self._clock.start()

    def _open_ai(self, task: str, ident, event_ids) -> None:
        self.goto(self._ai_index)
        self.pages[self._ai_index].preselect(task, ident, event_ids)

    @staticmethod
    def _scrollable(page: QWidget) -> QScrollArea:
        """Pages keep their natural minimum size and scroll when the window is smaller."""
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setFrameShape(QFrame.Shape.NoFrame)
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        area.setWidget(page)
        return area

    # ---------------------------------------------------------------- actions
    def _action(self, text: str, shortcut: str, slot, checkable: bool = False, tip: str = "") -> QAction:
        act = QAction(text, self)
        act.setShortcut(QKeySequence(shortcut))
        act.setCheckable(checkable)
        act.setToolTip(f"{tip or text} ({shortcut})")
        act.setShortcutContext(Qt.ShortcutContext.WindowShortcut)
        act.triggered.connect(slot)
        self.addAction(act)
        return act

    def _build_actions(self) -> None:
        self.act_monitor = self._action("Monitoring", "Ctrl+M", self._toggle_monitoring, True,
                                        "Start or stop system monitoring")
        self.act_anim = self._action("Pause animation", "Ctrl+P", self._toggle_animation, True,
                                     "Pause or resume the wallpaper animation")
        self.act_mode = self._action("Switch mode", "F9", lambda: self.ctrl.toggle_mode(), False,
                                     "Switch between Wallpaper mode and Control panel mode")
        self.act_export = self._action("Export logs…", "Ctrl+E", self._export_logs, False, "Export the event log")
        self.act_restore = self._action("Restore normal desktop", "Ctrl+Shift+R", lambda: self.ctrl.restore_desktop(),
                                        False, "Remove the wallpaper and show the normal desktop immediately")
        self.act_refresh = self._action("Refresh", "F5", self._refresh_page)
        self.act_exit = self._action("Exit Sentinel", "Ctrl+Q", self.confirm_exit, False,
                                     "Exit Sentinel and restore the normal desktop")

        bar = QToolBar("Main")
        bar.setMovable(False)
        bar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.addToolBar(bar)
        for act in (self.act_monitor, self.act_anim, self.act_mode):
            bar.addAction(act)
        bar.addSeparator()
        bar.addAction(self.act_export)
        bar.addAction(self.act_restore)
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        spacer.setStyleSheet("background: transparent;")
        bar.addWidget(spacer)
        bar.addAction(self.act_exit)

        menu = self.menuBar().addMenu("&Sentinel")
        for act in (self.act_monitor, self.act_anim, self.act_mode, self.act_export, self.act_restore, self.act_refresh):
            menu.addAction(act)
        menu.addSeparator()
        menu.addAction(self.act_exit)
        nav_menu = self.menuBar().addMenu("&Go")
        for i, page in enumerate(self.pages):
            a = QAction(f"{page.title}\tCtrl+{i + 1}", self)
            a.triggered.connect(lambda _c=False, idx=i: self.goto(idx))
            nav_menu.addAction(a)

    def _sync_actions(self) -> None:
        c = self.ctrl
        self.act_monitor.setChecked(c.monitoring_active)
        self.act_monitor.setText("Monitoring: ON" if c.monitoring_active else "Monitoring: OFF")
        self.act_anim.setChecked(c.animation_paused)
        self.act_anim.setText("Resume animation" if c.animation_paused else "Pause animation")
        self.act_mode.setText("Switch to Control Panel mode" if c.mode == "wallpaper" else "Switch to Wallpaper mode")
        self.act_restore.setEnabled(c.mode == "wallpaper")
        self._update_status()

    def _toggle_monitoring(self) -> None:
        self.ctrl.set_monitoring(not self.ctrl.monitoring_active)

    def _toggle_animation(self) -> None:
        self.ctrl.set_animation_paused(not self.ctrl.animation_paused)

    def _refresh_page(self) -> None:
        page = self.pages[self.stack.currentIndex()]
        for name in ("reload", "refresh", "_refresh_all"):
            fn = getattr(page, name, None)
            if callable(fn):
                fn()
                return

    def _export_logs(self) -> None:
        self.goto(4)
        self.pages[4].export()

    def goto(self, index: int) -> None:
        self.nav.setCurrentRow(index)

    # -------------------------------------------------------------- status bar
    def _build_status_bar(self) -> None:
        self.status_left = QLabel("")
        self.status_right = QLabel("")
        self.statusBar().addWidget(self.status_left, 1)
        self.statusBar().addPermanentWidget(self.status_right)

    def _update_status(self) -> None:
        c = self.ctrl
        self.status_left.setText(("Wallpaper mode — " + c.desktop_label + ". " if c.mode == "wallpaper"
                                  else "Control panel mode — ") + c.desktop_detail
                                 + ("" if c.tray_available else "  [No tray icon: use this window or stop_sentinel.bat to exit]"))
        if c.monitoring_active:
            state, text = freshness(c.data_age(), c.settings.get("sample_interval_s"))
            self.status_right.setText(f"Monitoring {text}")
        else:
            self.status_right.setText("Monitoring stopped")

    # --------------------------------------------------------------- show / close
    def show_panel(self) -> None:
        if self.isMinimized():
            self.showNormal()
        else:
            self.show()
        self.raise_()
        self.activateWindow()

    def confirm_exit(self) -> None:
        answer = QMessageBox.question(
            self, "Exit Sentinel",
            "Exit Sentinel?\n\nMonitoring and folder watching stop, and your normal desktop is restored.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        if answer == QMessageBox.StandardButton.Yes:
            self.force_exit()

    def force_exit(self) -> None:
        self._exiting = True
        self.ctrl.quit()

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        if self._exiting or self.ctrl.quitting:
            event.accept()
            return
        if self.ctrl.tray_available and self.ctrl.settings.get("close_to_tray"):
            event.ignore()
            self.hide()
            if not self._tray_hint_shown:
                self._tray_hint_shown = True
                self.ctrl.tray_message("Sentinel is still running",
                                       "Use the tray icon to reopen the panel or exit.")
            return
        # No tray icon (or the user turned the option off): closing means exit, so ask first.
        event.ignore()
        self.confirm_exit()
