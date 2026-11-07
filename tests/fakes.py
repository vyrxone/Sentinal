"""A fake Windows desktop for Linux tests (mimics Explorer's window layouts and restarts)."""

from __future__ import annotations


class FakeWin32:
    def __init__(self, layout: str = "workerw", owner: str = "explorer.exe") -> None:
        self.layout, self.owner = layout, owner
        self._next = 100
        self.win: dict[int, dict] = {}
        self.calls: list[tuple] = []
        self.reject_parent = False
        self.raise_on_set_parent = False
        self.redraws = 0
        self.build()

    # ---- topology ------------------------------------------------------------
    def _make(self, cls, parent=0):
        self._next += 1
        self.win[self._next] = dict(cls=cls, parent=parent, style=0x10000000, ex=0, pos=None)
        return self._next

    def build(self):
        self.win = {}
        self.progman = self._make("Progman")
        if self.layout == "workerw":
            self.w_icons = self._make("WorkerW")
            self.defview = self._make("SHELLDLL_DefView", self.w_icons)
            self.w_wall = self._make("WorkerW")
        else:                                   # progman (24H2 style)
            self.defview = self._make("SHELLDLL_DefView", self.progman)
            self.w_wall = self._make("WorkerW", self.progman)

    def restart(self):
        """Explorer restarts: every shell window is replaced by new ones; ours lose their parent."""
        ours = {h: w for h, w in self.win.items() if w["cls"] == "_ours"}
        self.build()
        for h, w in ours.items():
            w["parent"] = 0                    # parent destroyed
            self.win[h] = w

    # ---- API used by desktop_layer ---------------------------------------------
    def _ensure(self, h):
        if h and h not in self.win:
            self.win[h] = dict(cls="_ours", parent=0, style=0x80000000, ex=0, pos=None)

    def find_window(self, cls):
        return next((h for h, w in self.win.items() if w["cls"] == cls and not w["parent"]), 0)

    def find_window_ex(self, parent, after, cls):
        hs = [h for h, w in self.win.items() if w["parent"] == (parent or 0)]
        if after:
            hs = [h for h in hs if h > after]
        return next((h for h in hs if self.win[h]["cls"] == cls), 0)

    def spawn_workerw(self, progman):
        self.calls.append(("spawn", progman))

    def top_windows(self):
        return [h for h, w in self.win.items() if not w["parent"]]

    def set_parent(self, h, p):
        self._ensure(h)
        if self.raise_on_set_parent:
            raise OSError("SetParent exploded")
        if not self.reject_parent:
            self.win[h]["parent"] = p or 0
        return 0

    def get_parent(self, h):
        return self.win.get(h, {}).get("parent", 0)

    def is_window(self, h):
        return h in self.win

    def set_window_pos(self, h, after, x, y, w, hh, flags):
        self._ensure(h)
        self.win[h]["pos"] = (x, y, w, hh)
        return True

    def screen_to_client(self, parent, x, y):
        return x, y                                     # parent origin at 0,0 in the fake

    def get_style(self, h, ex=False):
        self._ensure(h)
        return self.win[h]["ex" if ex else "style"]

    def set_style(self, h, v, ex=False):
        self.win[h]["ex" if ex else "style"] = v

    def redraw_all(self):
        self.redraws += 1

    def process_image(self, hwnd):
        return self.owner

    def taskbar_created_message(self):
        return 0xC123

    def ours(self):
        return [h for h, w in self.win.items() if w["cls"] == "_ours"]
