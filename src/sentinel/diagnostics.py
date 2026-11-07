"""Small support report: no personal data, no file contents, no paths under the user's profile."""

from __future__ import annotations

import os
import platform
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import APP_NAME, RELEASE_LABEL, __version__


def redact(text: str) -> str:
    """Remove the user name and home directory from any text before it is shown or saved."""
    out = str(text)
    home = str(Path.home())
    for needle in {home, home.replace("\\", "/"), os.environ.get("USERNAME", ""), os.environ.get("USER", "")}:
        if needle and len(needle) > 2:
            out = re.sub(re.escape(needle), "<user>", out, flags=re.IGNORECASE)
    return out


def build_report(controller=None, screens=None) -> str:
    lines = [f"{APP_NAME} {RELEASE_LABEL} ({__version__}) diagnostic report",
             f"Generated (UTC): {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
             f"Python: {platform.python_version()} on {platform.platform()}"]
    try:
        import PySide6
        import psutil
        import watchdog  # noqa: F401

        lines.append(f"PySide6 {PySide6.__version__}, psutil {psutil.__version__}")
    except Exception as exc:
        lines.append(f"Dependency check failed: {exc}")
    if sys.platform == "win32":
        lines.append(f"Windows build: {platform.version()}")
    try:
        from PySide6.QtGui import QGuiApplication

        if QGuiApplication.instance() is not None:
            lines.append(f"Qt platform: {QGuiApplication.platformName()}")
        if screens is None:
            from .ui.wallpaper_host import screen_infos
            screens = screen_infos()
        lines.append(f"Screens: {len(screens)}")
        for s in screens:
            lines.append(f"  - {s.label()} at ({s.x},{s.y}) dpr={s.dpr:g}")
    except Exception as exc:
        lines.append(f"Screens: unavailable ({exc})")
    if controller is not None:
        c = controller
        st = getattr(c.host, "status", None)
        lines += [f"Wallpaper mode: {c.mode}",
                  f"Desktop status: {c.desktop_label}" + (f" (method: {c.desktop_method})" if c.desktop_method else ""),
                  f"Desktop detail: {c.desktop_detail}"]
        if st is not None and st.note:
            lines.append(f"Monitor note: {st.note}")
        s = c.settings
        lines += [f"Animation: {s.get('animation_fps')} fps cap, intensity {s.get('animation_intensity')}, "
                  f"reduced motion {s.get('reduced_motion')}, preset {s.get('appearance_preset')}",
                  f"Monitoring interval: {s.get('sample_interval_s')} s, process refresh {s.get('process_interval_s')} s",
                  f"Reduced effects under load active: {c.load.active}",
                  f"Database schema: v{c.db.user_version}, journal {getattr(c.db, 'journal_mode', '?')}",
                  f"Events pruned this session: {c.events.pruned_total}"]
        rs = c.render_stats.snapshot()
        lines.append("Render (measured): " + ("no scene drawing" if not rs["scenes"] else
                     f"{rs['fps']:.1f} fps total over {rs['scenes']} scene(s), worst paint {rs['paint_ms']:.1f} ms"))
        cpu, rss = (c.self_metrics.cpu[-1] if c.self_metrics.cpu else None,
                    c.self_metrics.rss[-1] if c.self_metrics.rss else None)
        lines.append("Sentinel process: " + ("unavailable" if cpu is None else f"CPU {cpu:.1f}%, memory {rss / 2**20:.0f} MiB"))
        stats = c.security.stats()
        lines.append(f"Security queue: {stats}")
        age = c.data_age()
        lines.append("Last monitoring sample: " + ("unavailable" if age is None else f"{age:.1f} s ago"))
        js = c.jobs.status()
        lines.append(f"Background jobs: {js['running']} running, {js['pending']} waiting; done {js['done']}, "
                     f"failed {js['failed']}, timed out {js['timeout']}, rejected {js['rejected']}")
        if js["slow"]:
            lines.append("Slow operations: " + "; ".join(f"{n} {sec:.2f}s" for n, sec, _o in js["slow"]))
        res = c.engine.last_result
        lines.append(f"Findings: {'on' if c.findings_enabled else 'off'}, sensitivity {c.engine.sensitivity}, "
                     f"{c.findings.open_count()} open; last analysis "
                     + ("not run" if res is None else f"{res.events_examined} events in {res.duration_ms:.0f} ms"))
        from .ui.pages.performance import ai_summary
        lines.append("AI assistant: " + ai_summary(c) + "; secure key storage "
                     + ("available" if c.ai.secrets.storage_available else "not available"))
        lines.append(f"Tray available: {c.tray_available}")
    return redact("\n".join(lines))
