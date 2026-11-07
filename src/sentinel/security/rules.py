"""Small, explainable, read-only rule engine over the local audit log.

Every rule states what it looks at, how it groups events and what it cannot tell you. Rules
only *read* events; findings never trigger any action on files, processes or system settings.

Deliberately absent: process-behaviour or network-anomaly rules. Sentinel does not inspect
processes' behaviour or traffic, and nothing here claims to detect malware, ransomware or
persistence. A future rule of that kind must carry its own limitation text (see ``LIMITATIONS``).
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from ..storage.events_store import Event, EventStore
from .findings import Candidate, EventRef, FindingStore

log = logging.getLogger("sentinel.rules")

LOOKBACK_S = 24 * 3600
MAX_EVENTS_PER_RUN = 5000
CLUSTER_WINDOW_S = 600           # 10 minutes: events further apart are never grouped together
RESOURCE_WINDOW_S = 1800
BURST_WINDOW_S = 60

LIMITATIONS = (
    "Findings are rule-based observations from Sentinel's own local audit log. They are not proof "
    "of malicious activity and Sentinel does not detect malware, ransomware or persistence.",
    "A changed file is not necessarily malicious: updates, editors and sync tools change files.",
    "Sentinel does not identify which program changed a file or used resources, and has no "
    "process-behaviour or network-anomaly rules.",
    "Changes that happen while Sentinel is not running, or while a watched folder is "
    "unavailable, are not observed.",
)

# sensitivity -> thresholds (all deterministic and visible to the user)
SENSITIVITY: dict[str, dict[str, int]] = {
    "low":    dict(repeat=15, group=4, burst=300, resource=5, service=5),
    "normal": dict(repeat=8,  group=3, burst=150, resource=3, service=3),
    "high":   dict(repeat=5,  group=2, burst=80,  resource=2, service=2),
}

FILE_TYPES = ("file_created", "file_modified", "file_deleted", "file_moved")
BASE_TYPES = ("baseline_mismatch", "baseline_missing")
ALL_TYPES = (FILE_TYPES + BASE_TYPES + ("events_suppressed", "watch_unavailable", "watch_restored",
             "cpu_warning", "ram_warning", "disk_warning", "monitoring_error", "monitoring_recovered",
             "project_path_unavailable", "project_path_restored"))


def ref(e: Event) -> EventRef:
    return EventRef(e.id, e.ts, e.event_type, e.message[:200])


def _key(path: Optional[str]) -> str:
    return os.path.normcase(os.path.abspath(path)) if path else "(unknown)"


@dataclass(frozen=True)
class Rule:
    rule_id: str
    version: int
    title: str
    category: str
    description: str
    limitation: str
    fn: Callable[["Context"], list[Candidate]]


@dataclass
class Context:
    now: float
    events: list[Event]            # oldest first, already filtered to relevant types
    cfg: dict[str, int]

    def of(self, *types: str) -> list[Event]:
        return [e for e in self.events if e.event_type in types]


def _clusters(events: list[Event], window: float = CLUSTER_WINDOW_S) -> list[list[Event]]:
    """Greedy, deterministic grouping: a cluster spans at most *window* seconds from its first event."""
    out: list[list[Event]] = []
    for e in sorted(events, key=lambda x: (x.ts, x.id)):
        if out and e.ts - out[-1][0].ts <= window:
            out[-1].append(e)
        else:
            out.append([e])
    return out


# --------------------------------------------------------------------------- rules
def rule_baseline_changed(ctx: Context) -> list[Candidate]:
    by_path: dict[str, list[Event]] = {}
    for e in ctx.of(*BASE_TYPES):
        by_path.setdefault(_key(e.path), []).append(e)
    out = []
    for key, evs in by_path.items():
        mism = [e for e in evs if e.event_type == "baseline_mismatch"]
        title = "Baseline file changed" if mism and len(mism) == len(evs) else (
            "Baseline file missing" if not mism else "Baseline file changed or missing")
        out.append(Candidate(
            key=f"BASE-001:{key}", rule_id="BASE-001", rule_version=1, severity="medium", category="integrity",
            title=title,
            explanation=("A file you added to the integrity baseline no longer matches what you recorded "
                         "(different SHA-256, or not at its recorded path). Checked against the baseline, "
                         "not against any list of known-bad files."),
            resource=evs[-1].path or "", first_ts=evs[0].ts, last_ts=evs[-1].ts,
            confidence="Certain that the content or location differs from the baseline; whether the change "
                       "is expected or harmful cannot be determined from this data.",
            next_step="If you made or expected this change (an update, an edit), use 'Accept current state' "
                      "in Security. If not, compare the file with a known-good copy or backup.",
            evidence={"events": len(evs), "mismatches": len(mism), "missing": len(evs) - len(mism),
                      "latest_actual_sha256": (evs[-1].details or {}).get("actual_sha256"),
                      "expected_sha256": (evs[-1].details or {}).get("expected_sha256")},
            events=[ref(e) for e in evs]))
    return out


def rule_baseline_group(ctx: Context) -> list[Candidate]:
    out = []
    for cl in _clusters(ctx.of(*BASE_TYPES)):
        paths = {_key(e.path) for e in cl}
        if len(paths) < ctx.cfg["group"]:
            continue
        out.append(Candidate(
            key=f"CORR-001:e{cl[0].id}", rule_id="CORR-001", rule_version=1, severity="high",
            category="integrity", title=f"{len(paths)} baseline files changed within minutes of each other",
            explanation=(f"Grouped because {len(paths)} different baseline files changed or went missing "
                         f"within {CLUSTER_WINDOW_S // 60} minutes of the first one (events "
                         f"#{cl[0].id}-#{cl[-1].id}). Several files changing together is more notable than one, "
                         "but software installs and updates look the same."),
            resource=f"{len(paths)} files", first_ts=cl[0].ts, last_ts=cl[-1].ts,
            confidence="Certain these events happened together; the cause is unknown.",
            next_step="Think about what you ran in this period (installer, update, build). If nothing "
                      "explains it, verify against backups before trusting these files.",
            evidence={"distinct_files": len(paths), "window_s": CLUSTER_WINDOW_S,
                      "grouped_by": "time proximity (<=10 min from first event)"},
            events=[ref(e) for e in cl]))
    return out


def rule_repeated_changes(ctx: Context) -> list[Candidate]:
    need = ctx.cfg["repeat"]
    by_path: dict[str, list[Event]] = {}
    for e in ctx.of(*FILE_TYPES):
        by_path.setdefault(_key(e.path), []).append(e)
    out = []
    for key, evs in by_path.items():
        best: list[Event] = []
        lo = 0
        for hi in range(len(evs)):
            while evs[hi].ts - evs[lo].ts > CLUSTER_WINDOW_S:
                lo += 1
            if hi - lo + 1 > len(best):
                best = evs[lo:hi + 1]
        if len(best) < need:
            continue
        out.append(Candidate(
            key=f"FILE-002:{key}", rule_id="FILE-002", rule_version=1,
            severity="medium" if len(best) >= need * 3 else "low", category="activity",
            title="The same file changed repeatedly",
            explanation=(f"{len(best)} file-system events for this one path were recorded within "
                         f"{CLUSTER_WINDOW_S // 60} minutes (threshold {need}). Applications that save often, "
                         "logs and sync tools do this routinely."),
            resource=best[-1].path or "", first_ts=best[0].ts, last_ts=best[-1].ts,
            confidence="Counts come straight from Windows change notifications; one save can raise "
                       "several notifications, so the number is approximate.",
            next_step="If this file should not change so often, check which application writes to it "
                      "(for example with Windows Resource Monitor). Sentinel does not identify the program.",
            evidence={"events_in_window": len(best), "threshold": need, "window_s": CLUSTER_WINDOW_S},
            events=[ref(e) for e in best]))
    return out


def rule_watch_interrupted(ctx: Context) -> list[Candidate]:
    by_path: dict[str, list[Event]] = {}
    for e in ctx.of("watch_unavailable", "watch_restored"):
        by_path.setdefault(_key(e.path), []).append(e)
    out = []
    for key, evs in by_path.items():
        if not any(e.event_type == "watch_unavailable" for e in evs):
            continue
        down = evs[-1].event_type == "watch_unavailable"
        out.append(Candidate(
            key=f"WATCH-003:{key}", rule_id="WATCH-003", rule_version=1,
            severity="medium" if down else "low", category="watcher",
            title="Watched folder is unavailable" if down else "Watched folder was unavailable for a while",
            explanation=("Sentinel could not watch this folder (removed, renamed or the drive disconnected). "
                         + ("It will resume automatically if the folder returns. " if down else
                            "It is available again, but changes made in the gap were not observed. ")
                         + "Monitoring is incomplete for this folder during that time."),
            resource=evs[-1].path or "", first_ts=evs[0].ts, last_ts=evs[-1].ts,
            confidence="Certain the folder could not be accessed when checked; the reason is not known.",
            next_step="Reconnect the drive or check the folder path. For a gap that already ended, run "
                      "'Verify baseline' to compare file contents with your recorded hashes.",
            evidence={"currently_unavailable": down, "transitions": len(evs)},
            events=[ref(e) for e in evs]))
    return out


def rule_sustained_resource(ctx: Context) -> list[Candidate]:
    out = []
    need = ctx.cfg["resource"]
    for metric in ("cpu", "ram", "disk"):
        evs = ctx.of(f"{metric}_warning")
        best: list[Event] = []
        lo = 0
        for hi in range(len(evs)):
            while evs[hi].ts - evs[lo].ts > RESOURCE_WINDOW_S:
                lo += 1
            if hi - lo + 1 > len(best):
                best = evs[lo:hi + 1]
        if len(best) < need:
            continue
        out.append(Candidate(
            key=f"RES-004:{metric}", rule_id="RES-004", rule_version=1, severity="low", category="resource",
            title=f"Repeated {metric.upper() if metric != 'ram' else 'memory'} warnings",
            explanation=(f"{len(best)} {metric} threshold warnings within {RESOURCE_WINDOW_S // 60} minutes. "
                         "This is a performance observation, kept separate from file-integrity findings. "
                         "Heavy use is normal during games, builds, updates and indexing."),
            resource=metric, first_ts=best[0].ts, last_ts=best[-1].ts,
            confidence="Certain that readings crossed your thresholds repeatedly; the cause is unknown "
                       "and high usage alone does not indicate a security problem.",
            next_step="Open Monitor to see the busiest programs. Raise the thresholds in Settings if "
                      "this is your normal workload.",
            evidence={"warnings_in_window": len(best), "threshold": need, "window_s": RESOURCE_WINDOW_S},
            events=[ref(e) for e in best]))
    return out


def rule_monitoring_failures(ctx: Context) -> list[Candidate]:
    evs = ctx.of("monitoring_error")
    need = ctx.cfg["service"]
    best: list[Event] = []
    lo = 0
    for hi in range(len(evs)):
        while evs[hi].ts - evs[lo].ts > RESOURCE_WINDOW_S:
            lo += 1
        if hi - lo + 1 > len(best):
            best = evs[lo:hi + 1]
    if len(best) < need:
        return []
    return [Candidate(
        key="SVC-005:monitoring", rule_id="SVC-005", rule_version=1, severity="medium", category="service",
        title="Monitoring keeps reporting errors",
        explanation=(f"{len(best)} monitoring failures within {RESOURCE_WINDOW_S // 60} minutes. While "
                     "monitoring is failing, resource data shown by Sentinel may be missing or stale."),
        resource="system monitoring", first_ts=best[0].ts, last_ts=best[-1].ts,
        confidence="Certain that sampling reported errors; the cause (permissions, a driver, a "
                   "restricted counter) is not known.",
        next_step="Open Performance to see the last sample time, and the diagnostics page for the error "
                  "text. Restarting Sentinel usually clears transient failures.",
        evidence={"failures_in_window": len(best), "threshold": need},
        events=[ref(e) for e in best])]


def rule_event_burst(ctx: Context) -> list[Candidate]:
    sec = [e for e in ctx.events if e.event_type in FILE_TYPES]
    out = []
    need = ctx.cfg["burst"]
    # every window of BURST_WINDOW_S holding >= need events is a burst; overlapping windows merge
    clusters: list[list[Event]] = []
    lo = 0
    for hi in range(len(sec)):
        while sec[hi].ts - sec[lo].ts > BURST_WINDOW_S:
            lo += 1
        if hi - lo + 1 < need:
            continue
        if clusters and sec[lo].id <= clusters[-1][-1].id:
            clusters[-1].extend(sec[hi:hi + 1])
        else:
            clusters.append(sec[lo:hi + 1])
    suppressed = ctx.of("events_suppressed")
    for cl in clusters[:5]:
        out.append(Candidate(
            key=f"BURST-006:e{cl[0].id}", rule_id="BURST-006", rule_version=1, severity="low",
            category="activity", title="Unusual burst of file activity",
            explanation=(f"{len(cl)} file events within {BURST_WINDOW_S} seconds (threshold {need}). "
                         "Installers, backups, builds and sync clients often cause bursts. Sentinel limits "
                         "how many events it records per minute, so the real count may be higher."),
            resource="watched folders", first_ts=cl[0].ts, last_ts=cl[-1].ts,
            confidence="Certain that activity was unusually dense; whether it was wanted is not known.",
            next_step="If you were not installing, copying or building something at this time, review the "
                      "listed events for the affected folders.",
            evidence={"events": len(cl), "threshold": need, "window_s": BURST_WINDOW_S},
            events=[ref(e) for e in cl]))
    if suppressed and not out:
        total = sum(int((e.details or {}).get("suppressed", 0)) for e in suppressed)
        out.append(Candidate(
            key=f"BURST-006:e{suppressed[0].id}", rule_id="BURST-006", rule_version=1, severity="low",
            category="activity", title="File activity exceeded the recording limit",
            explanation=(f"Sentinel skipped about {total} file events because they arrived faster than its "
                         "per-minute recording limit. Individual files in that period are not listed."),
            resource="watched folders", first_ts=suppressed[0].ts, last_ts=suppressed[-1].ts,
            confidence="Certain events were skipped; what caused the volume is not known.",
            next_step="Consider excluding noisy folders (caches, build output) in Security, or run a "
                      "baseline verification for the files that matter to you.",
            evidence={"suppressed_total": total}, events=[ref(e) for e in suppressed]))
    return out


def rule_project_unavailable(ctx: Context) -> list[Candidate]:
    by_path: dict[str, list[Event]] = {}
    for e in ctx.of("project_path_unavailable", "project_path_restored"):
        by_path.setdefault(_key(e.path), []).append(e)
    out = []
    for key, evs in by_path.items():
        if evs[-1].event_type != "project_path_unavailable":
            continue
        out.append(Candidate(
            key=f"PROJ-007:{key}", rule_id="PROJ-007", rule_version=1, severity="low", category="project",
            title="Project folder is not available",
            explanation=("A project folder you registered cannot be reached (moved, renamed, drive "
                         "disconnected or a network share offline). Git and health information for it "
                         "is unavailable until it returns."),
            resource=evs[-1].path or "", first_ts=evs[0].ts, last_ts=evs[-1].ts,
            confidence="Certain the path could not be reached at the last check.",
            next_step="Reconnect the drive, or edit the project's folder in Projects.",
            evidence={"checks": len(evs)}, events=[ref(e) for e in evs]))
    return out


RULES: tuple[Rule, ...] = (
    Rule("BASE-001", 1, "Baseline file changed or missing", "integrity",
         "Reports files in your integrity baseline whose hash differs or which are missing.",
         "A difference is not proof of malware.", rule_baseline_changed),
    Rule("CORR-001", 1, "Several baseline files changed together", "integrity",
         "Groups baseline changes that happen within 10 minutes of each other.",
         "Installers and updates look identical.", rule_baseline_group),
    Rule("FILE-002", 1, "Same file changed repeatedly", "activity",
         "Counts change notifications per file in a 10-minute window.",
         "Cannot tell which program wrote the file.", rule_repeated_changes),
    Rule("WATCH-003", 1, "Watched folder unavailable", "watcher",
         "Reports watched folders that could not be accessed.",
         "Changes during the gap were not observed.", rule_watch_interrupted),
    Rule("RES-004", 1, "Repeated resource warnings", "resource",
         "Counts CPU, memory or disk warnings in a 30-minute window.",
         "Resource use alone is not a security signal.", rule_sustained_resource),
    Rule("SVC-005", 1, "Monitoring failures", "service",
         "Counts monitoring sampling errors in a 30-minute window.",
         "Cause of the failure is unknown.", rule_monitoring_failures),
    Rule("BURST-006", 1, "Burst of file activity", "activity",
         "Detects dense file-event bursts and recording-limit overflows.",
         "Cannot tell whether the burst was wanted.", rule_event_burst),
    Rule("PROJ-007", 1, "Project folder unavailable", "project",
         "Reports registered project folders that cannot be reached.",
         "Says nothing about the project's contents.", rule_project_unavailable),
)


@dataclass
class AnalysisResult:
    created: int = 0
    updated: int = 0
    reopened: int = 0
    unchanged: int = 0
    rate_limited: int = 0
    events_examined: int = 0
    duration_ms: float = 0.0
    rule_errors: int = 0
    opened: list = field(default_factory=list)     # findings newly created or reopened by this run

    @property
    def changed(self) -> bool:
        return bool(self.created or self.updated or self.reopened)


class FindingsEngine:
    """Runs the rules over a bounded lookback window and merges the results into the store.

    Deterministic for a given event set and ``now``; idempotent (a second run changes nothing);
    new findings are rate-limited per hour so a noisy system cannot flood the list.
    """

    def __init__(self, events: EventStore, store: FindingStore, sensitivity: str = "normal",
                 max_new_per_hour: int = 20) -> None:
        self._events = events
        self.store = store
        self.max_new_per_hour = max_new_per_hour
        self.set_sensitivity(sensitivity)
        self.last_result: Optional[AnalysisResult] = None
        self.last_run_ts: Optional[float] = None

    def set_sensitivity(self, sensitivity: str) -> None:
        self.sensitivity = sensitivity if sensitivity in SENSITIVITY else "normal"
        self.cfg = dict(SENSITIVITY[self.sensitivity])

    def candidates(self, now: float) -> tuple[list[Candidate], int, int]:
        events = self._events.query_types(ALL_TYPES, now - LOOKBACK_S, MAX_EVENTS_PER_RUN)
        events = [e for e in events if e.ts <= now]
        ctx = Context(now, events, self.cfg)
        found: list[Candidate] = []
        errors = 0
        for rule in RULES:
            try:
                found.extend(rule.fn(ctx))
            except Exception:                       # one broken rule must not stop the others
                errors += 1
                log.exception("rule %s failed", rule.rule_id)
        found.sort(key=lambda c: (c.rule_id, c.key))
        return found, len(events), errors

    def analyze(self, now: Optional[float] = None, cancel=None) -> AnalysisResult:
        t0 = time.perf_counter()
        now = time.time() if now is None else now
        cands, examined, errors = self.candidates(now)
        res = AnalysisResult(events_examined=examined, rule_errors=errors)
        budget = self.max_new_per_hour - self.store.created_since(now - 3600)
        for cand in cands:
            if cancel is not None and cancel.is_set():
                break
            if self.store.get_by_key(cand.key) is None:
                if budget <= 0:
                    res.rate_limited += 1
                    continue
                budget -= 1
            _f, action = self.store.record(cand, now)
            if action in ("created", "reopened") and _f is not None:
                res.opened.append(_f)
            setattr(res, action, getattr(res, action) + 1)
        res.duration_ms = (time.perf_counter() - t0) * 1000
        self.last_result, self.last_run_ts = res, now
        return res
