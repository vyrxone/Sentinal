"""Windows shell notifications that matter to the wallpaper (event driven, no polling).

* ``TaskbarCreated``  - broadcast when Explorer (re)creates the taskbar, i.e. after an Explorer restart.
* ``WM_DISPLAYCHANGE`` - resolution / monitor layout changed.
* ``WM_DPICHANGED``    - DPI of a window's monitor changed.
* ``WM_POWERBROADCAST`` with resume codes - the PC woke from sleep or hibernate.

A hidden top-level window (never shown) receives the broadcasts; a QAbstractNativeEventFilter reads
them. On other platforms nothing is installed. ``classify`` is pure so it is tested on Linux;
the real delivery of these messages is a Windows-only manual test.
"""

from __future__ import annotations

import logging
import sys
from ctypes import wintypes
from typing import Optional

from PySide6.QtCore import QAbstractNativeEventFilter, QObject, Qt, Signal
from PySide6.QtWidgets import QWidget

from . import desktop_layer

log = logging.getLogger("sentinel.shell")

WM_DISPLAYCHANGE = 0x007E
WM_DPICHANGED = 0x02E0
WM_POWERBROADCAST = 0x0218
PBT_APMRESUMESUSPEND = 0x0007
PBT_APMRESUMEAUTOMATIC = 0x0012


def classify(message: int, wparam: int, taskbar_created: int) -> Optional[str]:
    """Map a window message to 'explorer_restart' | 'display_change' | 'dpi_change' | 'resume' | None."""
    if taskbar_created and message == taskbar_created:
        return "explorer_restart"
    if message == WM_DISPLAYCHANGE:
        return "display_change"
    if message == WM_DPICHANGED:
        return "dpi_change"
    if message == WM_POWERBROADCAST and wparam in (PBT_APMRESUMESUSPEND, PBT_APMRESUMEAUTOMATIC):
        return "resume"
    return None


class _Filter(QAbstractNativeEventFilter):
    def __init__(self, owner: "ShellEvents") -> None:
        super().__init__()
        self._owner = owner

    def nativeEventFilter(self, event_type, message):  # noqa: N802
        try:
            if bytes(event_type) not in (b"windows_generic_MSG", b"windows_dispatcher_MSG"):
                return False, 0
            msg = wintypes.MSG.from_address(int(message))
            kind = classify(int(msg.message), int(msg.wParam), self._owner.taskbar_message)
            if kind:
                self._owner.deliver(kind)
        except Exception:      # never let a native-event hiccup break the message pump
            log.exception("shell event filter failed")
        return False, 0        # never swallow a message


class ShellEvents(QObject):
    """Emits ``event(kind)`` on the UI thread. Bursts are coalesced by the receiver."""

    event = Signal(str)

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self.taskbar_message = 0
        self.active = False
        self._filter: Optional[_Filter] = None
        self._window: Optional[QWidget] = None

    def install(self) -> bool:
        if sys.platform != "win32" or self.active:
            return self.active
        from PySide6.QtCore import QCoreApplication

        app = QCoreApplication.instance()
        if app is None:
            return False
        self.taskbar_message = desktop_layer.taskbar_created_message()
        # A hidden top-level window exists only so Windows has somewhere to deliver broadcasts.
        self._window = QWidget(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint)
        self._window.setAttribute(Qt.WidgetAttribute.WA_NativeWindow, True)
        self._window.winId()
        self._filter = _Filter(self)
        app.installNativeEventFilter(self._filter)
        self.active = True
        return True

    def uninstall(self) -> None:
        from PySide6.QtCore import QCoreApplication

        app = QCoreApplication.instance()
        if self._filter is not None and app is not None:
            app.removeNativeEventFilter(self._filter)
        self._filter = None
        if self._window is not None:
            self._window.deleteLater()
            self._window = None
        self.active = False

    def deliver(self, kind: str) -> None:
        self.event.emit(kind)
