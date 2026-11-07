"""System-tray icon and menu (the always-available way to reach Sentinel)."""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QObject
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from .. import APP_NAME
from .icons import make_icon


class TrayController(QObject):
    def __init__(self, ctrl, window) -> None:
        super().__init__(window)
        self.ctrl = ctrl
        self.window = window
        self.icon: Optional[QSystemTrayIcon] = None
        if not QSystemTrayIcon.isSystemTrayAvailable():
            ctrl.tray_available = False
            ctrl.tray_message = lambda *_a, **_k: None   # type: ignore[method-assign]
            return
        self.icon = QSystemTrayIcon(make_icon(), self)
        self.icon.setToolTip(APP_NAME)
        menu = QMenu()
        self.m_panel = menu.addAction("Open control panel", window.show_panel)
        self.m_mode = menu.addAction("", ctrl.toggle_mode)      # show / hide wallpaper
        self.m_anim = menu.addAction("", lambda: ctrl.set_animation_paused(not ctrl.animation_paused))
        self.m_mon = menu.addAction("", lambda: ctrl.set_monitoring(not ctrl.monitoring_active))
        menu.addSeparator()
        self.m_restore = menu.addAction("Restore normal desktop", ctrl.restore_desktop)
        menu.addAction("Exit Sentinel", window.force_exit)
        self.menu = menu
        self.icon.setContextMenu(menu)
        self.icon.activated.connect(self._activated)
        menu.aboutToShow.connect(self._sync)
        ctrl.tray_available = True
        ctrl.tray_message = self.message           # type: ignore[method-assign]
        self._sync()
        self.icon.show()

    def _sync(self) -> None:
        c = self.ctrl
        self.m_mode.setText("Hide wallpaper (Control Panel mode)" if c.mode == "wallpaper" else "Show wallpaper")
        self.m_mode.setToolTip("Show or hide the wallpaper")
        self.m_anim.setText("Resume animation" if c.animation_paused else "Pause animation")
        self.m_mon.setText("Stop monitoring" if c.monitoring_active else "Start monitoring")
        self.m_restore.setEnabled(c.mode == "wallpaper")

    def _activated(self, reason) -> None:
        if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick):
            self.window.show_panel()

    def message(self, title: str, text: str) -> None:
        if self.icon is not None:
            self.icon.showMessage(title, text, QSystemTrayIcon.MessageIcon.Information, 4000)

    def hide(self) -> None:
        if self.icon is not None:
            self.icon.hide()
