"""Trusted local documentation excerpt for the settings/feature assistants (static text, no user data)."""

from __future__ import annotations

from ..storage.settings_store import DEFINITIONS

FEATURES = """\
VYRXONE Sentinel is a Windows live wallpaper, system monitor and defensive dashboard. Everything runs locally;
there is no telemetry. Pages: Overview, Monitor (CPU, memory, disk, network, processes), Security (watched folders and
a SHA-256 integrity baseline), Projects (health, tasks, notes, read-only Git status), Events (the local audit log with
paging, filters, acknowledge/archive and export), Settings, Performance (measured values only; 'unavailable' when
nothing was measured), Findings (rule-based observations with evidence, certainty and a safe next step; statuses
new/acknowledged/resolved/dismissed) and AI Assistant (optional, off by default).
Findings come from a small local rule engine (rules BASE-001, CORR-001, FILE-002, WATCH-003, RES-004, SVC-005,
BURST-006, PROJ-007). A finding is an observation, not proof of malicious activity. Sentinel never deletes,
quarantines, terminates, disables or changes anything on its own, and the AI Assistant cannot either.
The integrity baseline records SHA-256 hashes of files the user chose; verification reports unchanged, changed,
missing, inaccessible or unstable files. A cancelled verification is reported as incomplete.
Restore normal desktop: Ctrl+Shift+R. Exit: Ctrl+Q or the tray menu. Page shortcuts: Ctrl+1..9.
The AI Assistant sends data to the chosen provider (Gemini, Groq or OpenRouter) only after the user reviews the exact
request and confirms. API keys are kept in the Windows Credential Manager and never in settings, exports or logs.
"""


def settings_reference() -> str:
    lines = ["Sentinel settings (key: meaning, default):"]
    for d in DEFINITIONS:
        if d.key == "selected_monitor":
            continue
        rng = ""
        if d.minimum is not None:
            rng = f", range {d.minimum:g}-{d.maximum:g}"
        elif d.choices:
            rng = f", choices {list(d.choices)}"
        lines.append(f"- {d.key}: {d.label} (default {d.default!r}{rng})")
    return "\n".join(lines)


def documentation_excerpt() -> str:
    return FEATURES + "\n" + settings_reference()
