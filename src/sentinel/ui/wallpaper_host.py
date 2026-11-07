"""Owns the wallpaper scene(s) and decides where they live.

* "embedded"     - real Windows desktop-layer integration (behind the icons), one scene per monitor.
* "window"       - an ordinary, resizable window. This is the clearly labelled fallback; it is NEVER
                   described as embedded in the desktop. Only one window is ever created.
* "reconnecting" - Explorer restarted or the desktop layer changed; bounded retries are in progress.
* "hidden"       - Control Panel mode: nothing is drawn, the normal desktop shows.

V2 additions: multi-monitor scenes keyed by a stable monitor id, event-driven recovery
(TaskbarCreated / display change / DPI change / resume) with a bounded RecoveryPolicy,
exception-safe embedding, and honest status labels.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Optional

from PySide6.QtCore import QObject, Qt, QTimer
from PySide6.QtGui import QGuiApplication, QScreen

from ..core.monitors import ScreenInfo, disambiguate, native_rect, select_screens, stable_id
from ..core.recovery import RecoveryPolicy
from ..platform.windows import desktop_layer
from .wallpaper import WallpaperScene

log = logging.getLogger("sentinel.host")

WATCH_INTERVAL_MS = 10000       # safety net only; Explorer restarts are normally event driven
MAX_RECOVERIES_PER_MINUTE = 3
MAX_REATTACH_PER_MINUTE = MAX_RECOVERIES_PER_MINUTE   # V1 name
REFIT_DEBOUNCE_MS = 500

LABEL_EMBEDDED = "Embedded successfully"
LABEL_WINDOW = "Running as a normal window"
LABEL_UNAVAILABLE = "Desktop integration unavailable"
LABEL_RECONNECTING = "Reconnecting after Explorer restart"
LABEL_HIDDEN = "Wallpaper hidden"


@dataclass
class DesktopStatus:
    kind: str = "hidden"          # embedded | window | reconnecting | hidden
    label: str = LABEL_HIDDEN
    method: str = ""              # workerw_sibling | progman_child | window | ""
    detail: str = ""
    monitors: int = 0
    note: str = ""                # why a monitor request could not be honoured


@dataclass
class _Entry:
    scene: WallpaperScene
    info: ScreenInfo
    hwnd: int = 0
    parent: int = 0
    insert_after: int = 0
    rect: tuple = (0, 0, 0, 0)


def screen_infos() -> list[ScreenInfo]:
    """Current displays with stable ids and native-pixel rectangles."""
    app = QGuiApplication.instance()
    if app is None:
        return []
    primary = QGuiApplication.primaryScreen()
    out = []
    for sc in QGuiApplication.screens():
        g = sc.geometry()
        dpr = float(sc.devicePixelRatio())
        x, y, w, h = native_rect((g.x(), g.y()), (g.width(), g.height()), dpr)
        out.append(ScreenInfo(stable_id(sc.manufacturer(), sc.model(), sc.serialNumber(), sc.name()),
                              sc.name(), x, y, w, h, dpr, sc is primary))
    return disambiguate(out)


class WallpaperHost(QObject):
    def __init__(self, controller) -> None:
        super().__init__(controller)
        self.ctrl = controller
        self.kind = "hidden"
        self.status = DesktopStatus()
        self._entries: dict[str, _Entry] = {}
        self._policy = RecoveryPolicy()
        self._token = 0                       # invalidates pending recovery timers
        self._recoveries: list[float] = []
        self._embed_wanted = False
        self._watch = QTimer(self)
        self._watch.setInterval(WATCH_INTERVAL_MS)
        self._watch.timeout.connect(self._check_attached)
        self._refit_timer = QTimer(self)
        self._refit_timer.setSingleShot(True)
        self._refit_timer.setInterval(REFIT_DEBOUNCE_MS)
        self._refit_timer.timeout.connect(self._do_reconcile)
        self._bound: list[QScreen] = []
        app = QGuiApplication.instance()
        if app is not None:
            app.screenAdded.connect(self._on_screens_changed)
            app.screenRemoved.connect(self._on_screens_changed)
            app.primaryScreenChanged.connect(self._on_screens_changed)
        self._bind_screens()

    # ------------------------------------------------------------ compatibility
    @property
    def scene(self) -> Optional[WallpaperScene]:
        """The primary scene (V1 API)."""
        if not self._entries:
            return None
        for e in self._entries.values():
            if e.info.primary:
                return e.scene
        return next(iter(self._entries.values())).scene

    @property
    def scenes(self) -> list[WallpaperScene]:
        return [e.scene for e in self._entries.values()]

    # --------------------------------------------------------------------- API
    def show_wallpaper(self) -> tuple[str, str]:
        """Show the wallpaper. Returns (kind, human-readable detail)."""
        self.hide_wallpaper()
        self._policy.reset()
        screens, note = select_screens(screen_infos(), self.ctrl.settings.get("monitor_mode"),
                                       self.ctrl.settings.get("selected_monitor"))
        if not screens:
            screens = [ScreenInfo("primary", "primary", 0, 0, 1280, 720, 1.0, True)]
        self.status.note = note
        user_window = self.ctrl.settings.get("desktop_integration") != "auto"
        self._embed_wanted = not user_window and desktop_layer.is_supported()
        if not self._embed_wanted:
            if user_window:
                return self._show_window("Showing as a normal window (not embedded in the desktop). "
                                         "Window mode was selected in Settings.", LABEL_WINDOW, screens[0])
            return self._show_window("Showing as a normal window (not embedded in the desktop). "
                                     "Desktop-layer embedding is only available on Windows.",
                                     LABEL_UNAVAILABLE, screens[0])
        ok, detail, method = self._embed_all(screens)
        if ok:
            self._set_status("embedded", LABEL_EMBEDDED, method, detail, len(self._entries))
            self._watch.start()
            return "embedded", detail
        return self._show_window(f"Could not embed in the desktop ({detail}). Showing as a normal window instead.",
                                 LABEL_UNAVAILABLE, screens[0])

    def hide_wallpaper(self) -> None:
        """Remove every scene and give the desktop back to Windows. Safe to call repeatedly."""
        self._token += 1                      # cancel pending recovery attempts
        self._watch.stop()
        self._refit_timer.stop()
        entries, self._entries = list(self._entries.values()), {}
        was_embedded = self.kind == "embedded" or self.kind == "reconnecting"
        self.kind = "hidden"
        for e in entries:
            self._dispose(e, detach=e.hwnd != 0)
        if entries and (was_embedded or any(e.hwnd for e in entries)):
            try:
                desktop_layer.refresh_desktop()     # no black rectangle left behind
            except Exception:
                log.exception("refresh after hide failed")
        self._set_status("hidden", LABEL_HIDDEN, "", "Wallpaper is not shown.", 0, emit=False)

    def shutdown(self) -> None:
        self.hide_wallpaper()

    # --------------------------------------------------------------- internals
    def _set_status(self, kind: str, label: str, method: str, detail: str, monitors: int, emit: bool = True) -> None:
        self.kind = kind
        self.status = DesktopStatus(kind, label, method, detail, monitors, self.status.note)
        if emit and hasattr(self.ctrl, "report_desktop_state") and kind in ("embedded", "reconnecting"):
            self.ctrl.report_desktop_state(kind, label, detail, method)

    def _new_scene(self, info: ScreenInfo) -> WallpaperScene:
        scene = WallpaperScene(self.ctrl, screen_id=info.id, screen_name=info.name)
        scene.closed.connect(self._on_window_closed)
        return scene

    def _dispose(self, e: _Entry, detach: bool) -> None:
        scene = e.scene
        scene.programmatic_close = True
        if detach and e.hwnd:
            try:
                desktop_layer.detach(e.hwnd)    # un-parent BEFORE the window is destroyed
            except Exception:
                log.exception("detach failed")
        try:
            scene.shutdown()
            scene.close()
            scene.deleteLater()
        except RuntimeError:
            pass

    def _show_window(self, detail: str, label: str, info: ScreenInfo) -> tuple[str, str]:
        scene = self._new_scene(info)
        scene.interactive = True
        scene.resize(1280, 720)
        self._entries = {info.id: _Entry(scene, info)}
        scene.show()
        self._set_status("window", label, "window", detail, 1, emit=False)
        return "window", detail

    def _embed_all(self, screens: list[ScreenInfo]) -> tuple[bool, str, str]:
        """Embed one scene per screen. Returns (ok, detail, method). Never raises; cleans up on failure."""
        try:
            host, reason = desktop_layer.locate_desktop_host()
        except Exception as exc:
            log.exception("locating desktop layer failed")
            host, reason = None, f"Desktop integration failed: {exc}"
        if host is None:
            return False, reason, ""
        failures = []
        for info in screens:
            err = self._embed_one(info, host)
            if err:
                failures.append(f"{info.name}: {err}")
        if not self._entries:
            return False, "; ".join(failures) or "no monitor could be embedded", ""
        n, total = len(self._entries), len(screens)
        detail = (f"Embedded in the Windows desktop layer, behind your icons ({host.method}, "
                  f"{n} monitor{'s' if n != 1 else ''}).")
        if failures:
            detail += f" {total - n} monitor(s) could not be embedded: " + "; ".join(failures)
        return True, detail, host.method

    def _embed_one(self, info: ScreenInfo, host) -> Optional[str]:
        """Returns None on success, else the failure reason. Leaves no visible stray window."""
        scene = self._new_scene(info)
        try:
            scene.interactive = False
            scene.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool
                                 | Qt.WindowType.WindowDoesNotAcceptFocus)
            scene.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
            sc = next((s for s in QGuiApplication.screens() if s.name() == info.name), None)
            if sc is not None:
                scene.setGeometry(sc.geometry())
            scene.show()
            hwnd = int(scene.winId())
            result = desktop_layer.attach(hwnd, info.rect_px, host)
        except Exception as exc:                  # V1 defect A5: an exception must not leave a window behind
            log.exception("embedding failed")
            self._discard_scene(scene)
            return f"{exc}"
        if not result.success:
            self._discard_scene(scene)
            return result.detail
        self._entries[info.id] = _Entry(scene, info, hwnd, result.parent, result.insert_after, info.rect_px)
        return None

    def _discard_scene(self, scene: WallpaperScene) -> None:
        try:
            scene.hide()
            scene.programmatic_close = True
            scene.shutdown()
            scene.close()
            scene.deleteLater()
        except RuntimeError:
            pass

    # ----------------------------------------------- Explorer restarts / displays
    def on_shell_event(self, kind: str) -> None:
        """Called (UI thread) by ShellEvents. Bursts are coalesced."""
        log.info("shell event: %s", kind)
        if kind == "explorer_restart":
            self.begin_recovery("Explorer restarted")
        elif kind in ("display_change", "dpi_change", "resume"):
            self._refit_timer.start()
            if kind == "resume" and self.kind == "embedded":
                QTimer.singleShot(1500, self._check_attached)

    def begin_recovery(self, reason: str) -> None:
        if self.kind not in ("embedded", "reconnecting"):
            return
        now = time.monotonic()
        self._recoveries = [t for t in self._recoveries if now - t < 60]
        if len(self._recoveries) >= MAX_RECOVERIES_PER_MINUTE:
            self._fall_back("The desktop layer keeps changing; wallpaper moved to a normal window.")
            return
        if self.kind == "reconnecting":
            return
        self._recoveries.append(now)
        self._token += 1
        self._watch.stop()
        entries, self._entries = list(self._entries.values()), {}
        for e in entries:                           # Explorer's windows are gone/changed: drop our children
            self._dispose(e, detach=True)
        self._policy.reset()
        self._set_status("reconnecting", LABEL_RECONNECTING, "", f"{reason}; trying to re-attach.", 0)
        self._schedule_attempt()

    def _schedule_attempt(self) -> None:
        delay = self._policy.next_delay()
        if delay is None:
            self._fall_back("Could not re-attach to the desktop after several tries "
                            "(Explorer may still be starting). Showing a normal window instead.")
            return
        token = self._token
        QTimer.singleShot(delay, lambda: self._attempt(token))

    def _attempt(self, token: int) -> None:
        if token != self._token or self.kind != "reconnecting":
            return                                  # cancelled (user hid the wallpaper, or already recovered)
        screens, note = select_screens(screen_infos(), self.ctrl.settings.get("monitor_mode"),
                                       self.ctrl.settings.get("selected_monitor"))
        self.status.note = note
        ok, detail, method = self._embed_all(screens)
        if ok:
            self._policy.reset()
            self._set_status("embedded", LABEL_EMBEDDED, method, detail, len(self._entries))
            self._watch.start()
            return
        self._schedule_attempt()

    def _check_attached(self) -> None:
        if self.kind != "embedded":
            self._watch.stop()
            return
        for e in self._entries.values():
            if not desktop_layer.is_attached(e.hwnd, e.parent):
                log.warning("desktop layer lost - recovering")
                self.begin_recovery("The desktop layer was lost")
                return

    def _fall_back(self, detail: str) -> None:
        screens, _ = select_screens(screen_infos(), "primary")
        info = screens[0] if screens else ScreenInfo("primary", "primary", 0, 0, 1280, 720, 1.0, True)
        self.hide_wallpaper()
        self._show_window(detail, LABEL_UNAVAILABLE, info)
        self.ctrl.report_desktop_lost(detail)

    # ------------------------------------------------------------- screen changes
    def _bind_screens(self) -> None:
        for sc in self._bound:
            for sig in (sc.geometryChanged, sc.logicalDotsPerInchChanged):
                try:
                    sig.disconnect(self._on_screens_changed)
                except (RuntimeError, TypeError):
                    pass
        self._bound = list(QGuiApplication.screens())
        for sc in self._bound:
            sc.geometryChanged.connect(self._on_screens_changed)
            sc.logicalDotsPerInchChanged.connect(self._on_screens_changed)

    def _on_screens_changed(self, *_args) -> None:
        self._bind_screens()
        self._refit_timer.start()    # let Windows finish re-laying out the displays; coalesces bursts

    def reconcile(self, reason: str = "") -> None:
        """Public: re-evaluate which monitors should show a scene (settings or display change)."""
        self._refit_timer.start()

    _refit = reconcile      # V1 name

    def _do_reconcile(self) -> None:
        if self.kind != "embedded":
            if self.kind == "window" and self._entries:
                self._reconcile_window()
            return
        screens, note = select_screens(screen_infos(), self.ctrl.settings.get("monitor_mode"),
                                       self.ctrl.settings.get("selected_monitor"))
        self.status.note = note
        wanted = {s.id: s for s in screens}
        for sid in [k for k in self._entries if k not in wanted]:       # removed / deselected monitors
            self._dispose(self._entries.pop(sid), detach=True)
        try:
            host, _reason = desktop_layer.locate_desktop_host()
        except Exception:
            host = None
        for sid, info in wanted.items():
            e = self._entries.get(sid)
            if e is None:                                              # new monitor: exactly one new scene
                if host is not None:
                    self._embed_one(info, host)
            elif e.rect != info.rect_px:                               # moved / resized / DPI changed
                desktop_layer.reposition(e.hwnd, e.parent, info.rect_px, e.insert_after)
                e.rect, e.info = info.rect_px, info
        if not self._entries:
            self._fall_back("No monitor could be embedded after a display change; showing a normal window.")
            return
        self.status.monitors = len(self._entries)
        self.status.detail = (f"Embedded in the Windows desktop layer, behind your icons "
                              f"({self.status.method}, {len(self._entries)} monitor(s)).")
        if hasattr(self.ctrl, "report_desktop_state"):
            self.ctrl.report_desktop_state("embedded", LABEL_EMBEDDED, self.status.detail, self.status.method)

    def _reconcile_window(self) -> None:
        """The fallback window is never moved or re-created by display changes; just refresh the note."""
        _screens, note = select_screens(screen_infos(), self.ctrl.settings.get("monitor_mode"),
                                        self.ctrl.settings.get("selected_monitor"))
        self.status.note = note

    def _on_window_closed(self) -> None:
        """The user closed the fallback window -> go to Control Panel mode."""
        if self.ctrl.quitting:
            return
        if self.kind == "window":
            self._entries = {}
            self.kind = "hidden"
            self.ctrl.set_mode("panel")
