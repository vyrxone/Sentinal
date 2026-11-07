"""Place a native window in the Windows desktop layer (behind the desktop icons).

HOW IT WORKS (and why it is isolated here)
------------------------------------------
Explorer draws the desktop with these windows::

    Windows 10 / Windows 11 up to 23H2          Windows 11 24H2 and newer
    Progman                                       Progman
    WorkerW  -> SHELLDLL_DefView (icons)           |- SHELLDLL_DefView (icons)
    WorkerW  <- wallpaper layer (our parent)       |- WorkerW (wallpaper layer)

Sending Progman the *undocumented* message 0x052C makes Explorer create the
wallpaper WorkerW. We then re-parent our window into the wallpaper layer, so the
desktop icons stay above it and remain clickable.

LIMITATIONS (honest list)
-------------------------
* 0x052C and the window layout are undocumented. A Windows update can change
  them; every step is verified and failure returns a result so the caller can
  fall back to a normal window. Nothing here is guaranteed on future builds.
* The 24H2 branch (``progman_child``) is written from the observed window
  layout and has NOT been verified on real hardware by the author - see README.
* The wallpaper layer sits below the icon layer, so it receives no mouse input.
  Interaction happens through the tray icon and the control panel.
* V2 can cover several monitors (one child window per monitor, same parent). That path is
  UNVERIFIED on real multi-monitor hardware; Settings lets the user fall back to one monitor.
* If Explorer restarts, the parent window is destroyed. V2 reacts to the TaskbarCreated
  broadcast (see shell_events.py) and also checks :func:`is_attached` slowly as a safety net.
* V2 refuses to touch a desktop whose Progman is not owned by explorer.exe (third-party shells).

Only ctypes + user32 are used (no extra dependency). Nothing is injected into
other processes and nothing is modified outside our own window.
"""

from __future__ import annotations

import ctypes
import logging
import sys
from ctypes import wintypes
from dataclasses import dataclass
from typing import Optional

log = logging.getLogger("sentinel.desktop")

WS_CHILD = 0x40000000
WS_POPUP = 0x80000000
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TOOLWINDOW = 0x00000080
GWL_STYLE = -16
GWL_EXSTYLE = -20
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040
SWP_FRAMECHANGED = 0x0020
SMTO_NORMAL = 0x0000
RDW_INVALIDATE, RDW_ERASE, RDW_ALLCHILDREN, RDW_UPDATENOW = 0x0001, 0x0004, 0x0080, 0x0100
MSG_SPAWN_WORKERW = 0x052C  # undocumented
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
SHELL_IMAGE = "explorer.exe"


# ------------------------------------------------------------------ OS abstraction
class RealApi:
    """The only code that calls user32/kernel32. Every function has explicit 64-bit-safe signatures."""

    def __init__(self) -> None:
        u = ctypes.WinDLL("user32", use_last_error=True)  # type: ignore[attr-defined]
        k = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
        H, B, U, I = wintypes.HWND, wintypes.BOOL, wintypes.UINT, ctypes.c_int
        u.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
        u.FindWindowW.restype = H
        u.FindWindowExW.argtypes = [H, H, wintypes.LPCWSTR, wintypes.LPCWSTR]
        u.FindWindowExW.restype = H
        u.SendMessageTimeoutW.argtypes = [H, U, wintypes.WPARAM, wintypes.LPARAM, U, U,
                                          ctypes.POINTER(ctypes.c_size_t)]
        u.SendMessageTimeoutW.restype = ctypes.c_ssize_t
        self._enum_proc = ctypes.WINFUNCTYPE(B, H, wintypes.LPARAM)  # type: ignore[attr-defined]
        u.EnumWindows.argtypes = [self._enum_proc, wintypes.LPARAM]
        u.EnumWindows.restype = B
        u.SetParent.argtypes = [H, H]
        u.SetParent.restype = H
        u.GetParent.argtypes = [H]
        u.GetParent.restype = H
        u.IsWindow.argtypes = [H]
        u.IsWindow.restype = B
        u.SetWindowPos.argtypes = [H, H, I, I, I, I, U]
        u.SetWindowPos.restype = B
        u.ScreenToClient.argtypes = [H, ctypes.POINTER(wintypes.POINT)]
        u.ScreenToClient.restype = B
        u.RedrawWindow.argtypes = [H, ctypes.c_void_p, ctypes.c_void_p, U]
        u.RedrawWindow.restype = B
        u.GetWindowThreadProcessId.argtypes = [H, ctypes.POINTER(wintypes.DWORD)]
        u.GetWindowThreadProcessId.restype = wintypes.DWORD
        u.RegisterWindowMessageW.argtypes = [wintypes.LPCWSTR]
        u.RegisterWindowMessageW.restype = U
        try:
            get_long, set_long = u.GetWindowLongPtrW, u.SetWindowLongPtrW
        except AttributeError:  # 32-bit Python
            get_long, set_long = u.GetWindowLongW, u.SetWindowLongW
        get_long.argtypes = [H, I]
        get_long.restype = ctypes.c_ssize_t
        set_long.argtypes = [H, I, ctypes.c_ssize_t]
        set_long.restype = ctypes.c_ssize_t
        k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k.OpenProcess.restype = wintypes.HANDLE
        k.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                                 ctypes.POINTER(wintypes.DWORD)]
        k.QueryFullProcessImageNameW.restype = wintypes.BOOL
        k.CloseHandle.argtypes = [wintypes.HANDLE]
        k.CloseHandle.restype = wintypes.BOOL
        self.u, self.k, self._get_long, self._set_long = u, k, get_long, set_long

    def find_window(self, cls): return int(self.u.FindWindowW(cls, None) or 0)

    def find_window_ex(self, parent, after, cls):
        return int(self.u.FindWindowExW(parent or None, after or None, cls, None) or 0)

    def spawn_workerw(self, progman):
        result = ctypes.c_size_t(0)
        for wparam, lparam in ((0xD, 0), (0xD, 1)):  # both forms; harmless if ignored
            self.u.SendMessageTimeoutW(progman, MSG_SPAWN_WORKERW, wparam, lparam, SMTO_NORMAL, 1000,
                                       ctypes.byref(result))

    def top_windows(self):
        found: list[int] = []

        def _each(hwnd, _lparam):
            found.append(int(hwnd))
            return True

        self.u.EnumWindows(self._enum_proc(_each), 0)
        return found

    def set_parent(self, h, p): return int(self.u.SetParent(h, p or None) or 0)
    def get_parent(self, h): return int(self.u.GetParent(h) or 0)
    def is_window(self, h): return bool(self.u.IsWindow(h))

    def set_window_pos(self, h, after, x, y, w, hgt, flags):
        return bool(self.u.SetWindowPos(h, after or 0, x, y, w, hgt, flags))

    def screen_to_client(self, parent, x, y):
        pt = wintypes.POINT(x, y)
        self.u.ScreenToClient(parent, ctypes.byref(pt))
        return pt.x, pt.y

    def get_style(self, h, ex=False): return int(self._get_long(h, GWL_EXSTYLE if ex else GWL_STYLE)) & 0xFFFFFFFF
    def set_style(self, h, value, ex=False): self._set_long(h, GWL_EXSTYLE if ex else GWL_STYLE, value)

    def redraw_all(self):
        self.u.RedrawWindow(None, None, None, RDW_INVALIDATE | RDW_ERASE | RDW_ALLCHILDREN | RDW_UPDATENOW)

    def process_image(self, hwnd) -> Optional[str]:
        """Lower-case file name of the process that owns *hwnd*, or None if it cannot be determined."""
        pid = wintypes.DWORD(0)
        self.u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if not pid.value:
            return None
        handle = self.k.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
        if not handle:
            return None
        try:
            size = wintypes.DWORD(1024)
            buf = ctypes.create_unicode_buffer(1024)
            if not self.k.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                return None
            return buf.value.replace("/", "\\").rsplit("\\", 1)[-1].lower()
        finally:
            self.k.CloseHandle(handle)

    def taskbar_created_message(self) -> int:
        return int(self.u.RegisterWindowMessageW("TaskbarCreated"))


_api_cache = None
_injected = None


def set_api(api) -> None:
    """Tests inject a fake OS here (None restores the real one)."""
    global _injected, _api_cache
    _injected = api
    _api_cache = None


def is_supported() -> bool:
    return sys.platform == "win32" or _injected is not None


def _api():
    global _api_cache
    if _injected is not None:
        return _injected
    if _api_cache is None:
        _api_cache = RealApi()
    return _api_cache


@dataclass(frozen=True)
class DesktopHost:
    parent: int          # window to SetParent() into
    insert_after: int    # z-order anchor (0 = top of parent's children)
    method: str          # "workerw_sibling" | "progman_child"


@dataclass(frozen=True)
class AttachResult:
    success: bool
    method: str
    detail: str
    parent: int = 0
    insert_after: int = 0


def locate_desktop_host() -> tuple[Optional[DesktopHost], str]:
    """Locate the wallpaper layer. Returns (host, "") or (None, human-readable reason)."""
    if not is_supported():
        return None, "Desktop-layer embedding is only available on Windows."
    api = _api()
    progman = api.find_window("Progman")
    if not progman:
        return None, "Explorer's desktop window (Progman) was not found; Explorer may be restarting."
    owner = api.process_image(progman)
    if owner is not None and owner != SHELL_IMAGE:
        return None, (f"The desktop is drawn by '{owner}', not Windows Explorer. "
                      "Sentinel does not attach to third-party shells.")
    api.spawn_workerw(progman)

    # Windows 11 24H2+: icons (SHELLDLL_DefView) are a child of Progman, and the
    # wallpaper WorkerW is a sibling child behind it.
    defview_child = api.find_window_ex(progman, 0, "SHELLDLL_DefView")
    if defview_child:
        return DesktopHost(parent=progman, insert_after=defview_child, method="progman_child"), ""

    # Windows 10 / earlier Windows 11: the wallpaper WorkerW is the top-level
    # sibling that follows the WorkerW containing SHELLDLL_DefView.
    for hwnd in api.top_windows():
        if api.find_window_ex(hwnd, 0, "SHELLDLL_DefView"):
            nxt = api.find_window_ex(0, hwnd, "WorkerW")
            if nxt:
                return DesktopHost(parent=nxt, insert_after=0, method="workerw_sibling"), ""
    return None, "Could not find Explorer's wallpaper layer (unrecognised desktop layout)."


def find_desktop_host() -> Optional[DesktopHost]:
    return locate_desktop_host()[0]


def _set_style(hwnd: int, child: bool) -> None:
    api = _api()
    style = api.get_style(hwnd)
    style = (style & ~WS_POPUP) | WS_CHILD if child else (style & ~WS_CHILD) | WS_POPUP
    api.set_style(hwnd, style)
    ex = api.get_style(hwnd, ex=True)
    ex = ex | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW if child else ex & ~(WS_EX_NOACTIVATE)
    api.set_style(hwnd, ex, ex=True)


def place(hwnd: int, parent: int, insert_after: int, rect: tuple[int, int, int, int]) -> bool:
    """Position *hwnd* so that it covers screen rectangle *rect* (physical pixels, may be negative)."""
    api = _api()
    x, y, w, h = rect
    cx, cy = api.screen_to_client(parent, x, y)
    return api.set_window_pos(hwnd, insert_after, cx, cy, w, h,
                              SWP_NOACTIVATE | SWP_SHOWWINDOW | SWP_FRAMECHANGED)


def attach(hwnd: int, rect: tuple[int, int, int, int], host: Optional[DesktopHost] = None) -> AttachResult:
    """Embed *hwnd* behind the desktop icons, covering *rect* (x, y, w, h in screen pixels).

    Pass *host* to attach several windows (several monitors) to one already-located layer."""
    if not is_supported():
        return AttachResult(False, "unsupported", "Desktop-layer embedding is only available on Windows.")
    try:
        reason = ""
        if host is None:
            host, reason = locate_desktop_host()
        if host is None:
            return AttachResult(False, "not_found", reason)
        api = _api()
        _set_style(hwnd, child=True)
        api.set_parent(hwnd, host.parent)  # returns NULL for a former top-level window; verify below
        place(hwnd, host.parent, host.insert_after, rect)
        if api.get_parent(hwnd) != host.parent:
            detach(hwnd)
            return AttachResult(False, host.method, "Windows did not accept the new parent window.")
        return AttachResult(True, host.method, f"Embedded in the desktop layer ({host.method}).",
                            host.parent, host.insert_after)
    except Exception as exc:  # never let an OS quirk crash the app
        log.exception("desktop attach failed")
        try:
            detach(hwnd)
        except Exception:
            pass
        return AttachResult(False, "error", f"Desktop integration failed: {exc}")


def detach(hwnd: int) -> None:
    """Return *hwnd* to a normal top-level window (call before closing it)."""
    if not is_supported():
        return
    api = _api()
    if not api.is_window(hwnd):
        return
    api.set_parent(hwnd, 0)
    _set_style(hwnd, child=False)


def reposition(hwnd: int, parent: int, rect: tuple[int, int, int, int], insert_after: int = 0) -> None:
    if is_supported() and _api().is_window(hwnd):
        place(hwnd, parent, insert_after, rect)


def is_attached(hwnd: int, parent: int) -> bool:
    """False if our window or its desktop parent vanished (e.g. Explorer restarted)."""
    if not is_supported() or not hwnd or not parent:
        return False
    api = _api()
    return bool(api.is_window(hwnd) and api.is_window(parent) and api.get_parent(hwnd) == parent)


def refresh_desktop() -> None:
    """Ask Windows to repaint the desktop (used after leaving wallpaper mode)."""
    if not is_supported():
        return
    try:
        _api().redraw_all()
    except Exception:
        log.exception("desktop refresh failed")


def taskbar_created_message() -> int:
    """Window-message id Explorer broadcasts after it (re)creates the taskbar; 0 when unavailable."""
    if not is_supported():
        return 0
    try:
        return int(_api().taskbar_created_message())
    except Exception:
        log.exception("RegisterWindowMessage failed")
        return 0
