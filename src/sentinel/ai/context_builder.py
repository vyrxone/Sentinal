"""Build the minimal, redacted, user-selected context for a request.

Nothing is gathered automatically: callers pass the objects the user selected (one finding, some events, one
project). Every text that originates from recorded data is redacted and quoted as untrusted data.
Never included: file contents, project notes, log files, the event database, environment variables, keys."""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

from .models import AIRequest, ChatMessage
from .privacy import Redactor, quote_untrusted
from .prompts import TASKS, system_prompt

MAX_ITEM_CHARS = 1800
MAX_QUESTION_CHARS = 2000
HASH_PREFIX = 16
_SAFE_DETAIL_KEYS = {"status", "size", "old_size", "new_size", "expected_size", "actual_size", "sha256",
                     "expected_sha256", "actual_sha256", "old_sha256", "new_sha256", "changed", "missing", "unchanged",
                     "inaccessible", "unstable", "checked", "total", "count", "suppressed", "cancelled", "window_s",
                     "value", "threshold", "errors"}
_SAFE_GIT_KEYS = {"branch", "ahead", "behind", "staged", "modified", "untracked", "conflicts", "dirty", "clean",
                   "detached", "available", "state", "error"}


@dataclass
class ContextItem:
    item_id: str
    kind: str                         # finding | finding_events | event | integrity | project | git | settings | knowledge
    label: str                        # shown in the preview checklist
    text: str                         # exactly what is sent for this item (already redacted and quoted)
    fields: tuple[str, ...] = ()      # names of the data fields included, shown to the user
    redactions: dict = field(default_factory=dict)
    default_on: bool = True
    trusted: bool = False

    @property
    def size(self) -> int:
        return len(self.text)


def utc(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")


def _flatten(value: Any, limit: int = 12) -> str:
    """Short 'key=value' evidence summary; nested data is shortened rather than dumped."""
    if isinstance(value, dict):
        parts = []
        for k, v in list(value.items())[:limit]:
            if isinstance(v, (list, tuple)):
                v = ", ".join(str(x) for x in v[:5]) + (" …" if len(v) > 5 else "")
            elif isinstance(v, dict):
                v = json.dumps(v, default=str)[:120]
            parts.append(f"{k}={v}")
        return "; ".join(parts)
    return str(value)


def _short_hash(value: Any) -> str:
    s = str(value)
    return s[:HASH_PREFIX] + "…" if len(s) > HASH_PREFIX + 2 and all(c in "0123456789abcdefABCDEF" for c in s) else s


def safe_details(details: Optional[dict], allowed: Iterable[str] = _SAFE_DETAIL_KEYS) -> dict:
    out = {}
    for k, v in (details or {}).items():
        if k not in allowed:
            continue
        out[k] = _short_hash(v) if "sha256" in k else v
    return out


class ContextBuilder:
    def __init__(self, redactor: Optional[Redactor] = None, max_events: int = 20) -> None:
        self.redactor = redactor or Redactor()
        self.max_events = max_events

    def _item(self, item_id: str, kind: str, label: str, raw: str, fields: tuple[str, ...], *, untrusted_label: str,
              default_on: bool = True) -> ContextItem:
        red, counts = self.redactor.redact(raw)
        return ContextItem(item_id, kind, label, quote_untrusted(untrusted_label, red, MAX_ITEM_CHARS), fields,
                           dict(counts), default_on)

    # ------------------------------------------------------------------ findings and events
    def finding(self, f: Any) -> list[ContextItem]:
        raw = (f"Finding rule {f.rule_id} v{f.rule_version}; severity {f.severity}; category {f.category}; "
               f"status {f.status}; certainty: {f.confidence}\n"
               f"Title: {f.title}\nExplanation: {f.explanation}\nResource: {f.resource}\n"
               f"Evidence: {_flatten(f.evidence)}\nSuggested safe next step (rule text): {f.next_step}\n"
               f"First observed {utc(f.first_ts)}, last observed {utc(f.last_ts)}, seen {f.occurrences} time(s)")
        items = [self._item(f"finding:{f.id}", "finding", f"Finding: {f.title}", raw,
                            ("rule id/version", "severity", "category", "status", "certainty", "title", "explanation",
                             "resource (redacted)", "evidence summary", "next step", "timestamps"),
                            untrusted_label=f"finding {f.id}")]
        refs = list(getattr(f, "events", []) or [])[: self.max_events]
        if refs:
            lines = [f"{utc(r.ts)} | {r.event_type} | {r.summary}" for r in refs]
            items.append(self._item(f"finding_events:{f.id}", "finding_events",
                                    f"{len(refs)} related event(s) of this finding", "\n".join(lines),
                                    ("timestamp", "event type", "event summary (redacted)"),
                                    untrusted_label="related events"))
        return items

    def events(self, events: Iterable[Any]) -> list[ContextItem]:
        items = []
        for e in list(events)[: self.max_events]:
            raw = (f"{utc(e.ts)} | type {e.event_type} | severity {e.severity} | category {e.category} | "
                   f"{e.message}" + (f" | path {e.path}" if e.path else ""))
            items.append(self._item(f"event:{e.id}", "event", f"Event {e.id}: {e.event_type} at {utc(e.ts)}", raw,
                                    ("timestamp", "event type", "severity", "category", "message (redacted)",
                                     "path (redacted)"), untrusted_label=f"event {e.id}"))
        return items

    def integrity(self, events: Iterable[Any]) -> list[ContextItem]:
        items = []
        for e in list(events)[: self.max_events]:
            det = safe_details(e.details)
            raw = (f"{utc(e.ts)} | type {e.event_type} | severity {e.severity} | {e.message}"
                   + (f" | file {e.path}" if e.path else "") + (f" | {_flatten(det)}" if det else ""))
            items.append(self._item(f"integrity:{e.id}", "integrity", f"Baseline event {e.id}: {e.event_type}", raw,
                                    ("timestamp", "event type", "message", "file path (redacted)",
                                     f"hash prefixes ({HASH_PREFIX} chars) and sizes only; no file contents"),
                                    untrusted_label=f"baseline event {e.id}"))
        return items

    # ------------------------------------------------------------------ projects
    def project(self, project: Any, report: Optional[dict], task_counts: Optional[dict]) -> list[ContextItem]:
        lines = [f"Project name: {project.name}", f"Status label: {project.status}"]
        fields = ["project name", "status label"]
        if report:
            lines.append(f"Health: {report.get('overall', 'unknown')}; folder state: {report.get('path_state', '?')}")
            for m in list(report.get("messages") or [])[:8]:
                lines.append(f"Health note: {m}")
            fields += ["health result", "folder state", "health notes (redacted)"]
        if task_counts:
            lines.append("Tasks: " + _flatten({k: v for k, v in task_counts.items() if isinstance(v, (int, str))}))
            fields.append("task counts (titles and notes are NOT sent)")
        items = [self._item(f"project:{project.id}", "project", f"Project health: {project.name}", "\n".join(lines),
                            tuple(fields), untrusted_label=f"project {project.id}")]
        git = (report or {}).get("git") if report else None
        if isinstance(git, dict):
            g = safe_details(git, _SAFE_GIT_KEYS)
            if g:
                items.append(self._item(f"git:{project.id}", "git", "Git summary (branch, ahead/behind, change counts)",
                                        _flatten(g), ("branch", "ahead/behind", "change counts"),
                                        untrusted_label="git summary"))
            files = [str(x) for x in (git.get("changed_files") or [])][:15]
            if files:
                items.append(self._item(f"gitfiles:{project.id}", "git", f"{len(files)} changed file names (optional)",
                                        "\n".join(files), ("changed file names (redacted)",),
                                        untrusted_label="changed files", default_on=False))
        return items

    # ------------------------------------------------------------------ settings / docs
    def settings(self, values: dict) -> list[ContextItem]:
        shown = {k: v for k, v in values.items() if not k.startswith("ai_") and k != "selected_monitor"}
        return [self._item("settings", "settings", "Current Sentinel settings (no secrets)",
                           "\n".join(f"{k}={v}" for k, v in sorted(shown.items())), ("setting names and values",),
                           untrusted_label="settings")]

    def diagnostics(self, report_text: str) -> list[ContextItem]:
        return [self._item("diagnostics", "settings", "Diagnostics report (redacted)", report_text[:6000],
                           ("app/Python/OS versions, monitoring and database status",),
                           untrusted_label="diagnostics", default_on=False)]

    def knowledge(self, text: str) -> ContextItem:
        return ContextItem("knowledge", "knowledge", "Sentinel documentation excerpt (static text shipped with the app)",
                           "[SENTINEL DOCUMENTATION - trusted]\n" + text, ("static documentation text",), {}, True, True)


def compose_request(task: str, question: str, items: list[ContextItem], selected: Optional[set[str]] = None,
                    history: Optional[list[ChatMessage]] = None, max_tokens: int = 700) -> AIRequest:
    """The exact request that would be sent. Items not in *selected* are left out. Pure function."""
    question = (question or "").replace("\x00", "").strip()[:MAX_QUESTION_CHARS]
    chosen = [i for i in items if selected is None or i.item_id in selected]
    label = TASKS.get(task, TASKS["chat"])[0]
    parts = [f"Task: {label}"]
    if chosen:
        parts.append("Context selected by the user:")
        parts += [i.text for i in chosen]
    else:
        parts.append("No context was selected.")
    parts.append("User question: " + (question or "(none - perform the task above)"))
    msgs = tuple(history or ()) + (ChatMessage("user", "\n\n".join(parts)),)
    return AIRequest(system=system_prompt(task), messages=msgs, max_tokens=max_tokens, task=task)


def summarize_fields(items: list[ContextItem], selected: Optional[set[str]] = None) -> list[str]:
    seen: list[str] = []
    for i in items:
        if selected is not None and i.item_id not in selected:
            continue
        for f in i.fields:
            if f not in seen:
                seen.append(f)
    return seen


def redaction_summary(items: list[ContextItem], selected: Optional[set[str]] = None) -> Counter:
    total: Counter = Counter()
    for i in items:
        if selected is None or i.item_id in selected:
            total.update(i.redactions)
    return total
