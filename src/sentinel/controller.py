"""AppController: owns the services and is the single place the UI talks to.

Background threads (sampler, security worker) never touch widgets. They emit
private signals that are connected to bound methods of this main-thread QObject,
which Qt delivers on the UI thread.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Optional

from PySide6.QtCore import QObject, QTimer, Signal, Slot

from .core.alerts import AlertState, NotifyLimiter
from .core.appearance import Appearance, appearance_from
from .core.performance import Quality, effective_quality
from .core.thresholds import SustainedLoad, ThresholdEngine
from .monitoring.history import MetricHistory
from .monitoring.sampler import Sample, Sampler
from .monitoring.selfstats import RenderStats, SelfMetrics
from .projects.health import HealthReport, HealthStore, compute_health, inspect_path, path_transition
from .projects.overview import scan_overview
from .projects.store import ProjectStore
from .projects.tasks import TaskStore
from .security.findings import FindingError, FindingStore, SEVERITY_RANK
from .security.integrity import BaselineStore
from .security.rules import ALL_TYPES, FindingsEngine
from .security.service import SecurityService
from .storage.database import Database, DatabaseError
from .storage.events_store import Event, EventStore
from .storage.settings_store import SettingsStore
from .ai.service import AIService
from .ui.jobs import JobRunner

log = logging.getLogger("sentinel.controller")

APPEARANCE_KEYS = {"appearance_preset", "overlay_mode", "emblem_size", "particle_intensity", "pulse_intensity",
                   "reduced_motion", "dimmer_percent", "show_clock", "show_overlay"}
QUALITY_KEYS = {"animation_fps", "animation_intensity", "reduce_under_load", "lightweight_mode"} | APPEARANCE_KEYS
LIGHTWEIGHT_MIN_INTERVAL_S = 4.0     # lightweight mode samples the system at most this often
MONITOR_KEYS = {"monitor_mode", "selected_monitor"}
HEALTH_INTERVAL_MS = 30000
PROJECT_PATH_CHECK_MS = 2 * 60 * 1000   # cheap path-availability check only; never a Git or full scan
ANALYSIS_DEBOUNCE_MS = 4000       # coalesce bursts of events into one analysis run
ANALYSIS_PERIODIC_MS = 5 * 60 * 1000
PAST = {"acknowledge": "acknowledged", "resolve": "resolved", "dismiss": "dismissed", "reopen": "reopened"}
MONITOR_ERROR_SPACING_S = 300     # at most one 'monitoring_error' event per 5 minutes


class AppController(QObject):
    sample_ready = Signal(object)        # Sample
    event_added = Signal(object)         # Event
    alert_raised = Signal(object)        # Alert
    setting_changed = Signal(str, object)
    state_changed = Signal()             # monitoring / pause / load / security / mode changed
    quality_changed = Signal()
    mode_changed = Signal(str)           # "wallpaper" | "panel"
    desktop_status_changed = Signal(str, str)   # kind, detail
    quit_requested = Signal()
    alerts_changed = Signal()            # active alert set changed (opened / recovered / acknowledged)
    projects_changed = Signal()          # V3: project health / tasks / notes changed
    findings_changed = Signal()          # V3: a finding was created, updated or its status changed
    _health_in = Signal(object)

    _sample_in = Signal(object)
    _event_in = Signal(object)

    def __init__(self, db: Database, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self.db = db
        self.settings = SettingsStore(db)
        self.events = EventStore(db, max_events=self.settings.get("max_events"))
        self.baseline = BaselineStore(db)
        self.security = SecurityService(db, self.events, self.baseline)
        self.projects = ProjectStore(db)
        self.tasks = TaskStore(db)
        self.health = HealthStore(db)
        self.jobs = JobRunner(self)
        self.ai = AIService(self.settings, db, self)     # V4: optional, off by default, never auto-sends
        self.findings = FindingStore(db)
        self.engine = FindingsEngine(self.events, self.findings, self.settings.get("findings_sensitivity"))
        self.history = MetricHistory()
        self.thresholds = ThresholdEngine()
        self.load = SustainedLoad()
        self.sampler = Sampler(self._sampler_callback, self._sample_interval())
        self.sampler.set_process_interval(self.settings.get("process_interval_s"))
        self.appearance: Appearance = appearance_from(self.settings)
        self.render_stats = RenderStats()
        self.self_metrics = SelfMetrics()
        self.alert_state = AlertState(db)
        self.limiter = NotifyLimiter(self.settings.get("notify_min_interval_s"))
        self.last_process_sample: Optional[Sample] = None
        self.desktop_method = ""
        self.desktop_label = "Wallpaper is not shown."
        self._health = QTimer(self)
        self._health.setInterval(HEALTH_INTERVAL_MS)
        self._health.timeout.connect(self._run_health_check)
        self._analysis = QTimer(self)
        self._analysis.setSingleShot(True)
        self._analysis.setInterval(ANALYSIS_DEBOUNCE_MS)
        self._analysis.timeout.connect(self.run_analysis)
        self._analysis_periodic = QTimer(self)
        self._analysis_periodic.setInterval(ANALYSIS_PERIODIC_MS)
        self._analysis_periodic.timeout.connect(self.request_analysis)
        self._monitor_fault = False
        self._last_monitor_error = float("-inf")   # monotonic() can be small right after boot; 0.0 would suppress the first report
        self._path_timer = QTimer(self)
        self._path_timer.setInterval(PROJECT_PATH_CHECK_MS)
        self._path_timer.timeout.connect(self.check_project_paths)
        self.last_sample: Optional[Sample] = None
        self.host: Any = None            # WallpaperHost, set by app.py
        self.mode = "panel"
        self.desktop_kind = "hidden"
        self.desktop_detail = "Wallpaper is not shown."
        self.tray_available = False
        self._shut_down = False
        self.quitting = False            # True once an exit was requested: windows must not veto it

        self._sample_in.connect(self._handle_sample)
        self._event_in.connect(self._handle_event)
        self.events.subscribe(self._event_in.emit)
        self.settings.subscribe(self._on_setting)
        self._apply_thresholds()

    # ------------------------------------------------------------- lifecycle
    def start(self) -> None:
        self.log("app", "app_started", "VYRXONE Sentinel started.")
        self._report_settings_load()
        self._recover_interrupted_alerts()
        self._health.start()
        self._analysis_periodic.start()
        self._path_timer.start()
        self.request_analysis()
        QTimer.singleShot(1500, self.check_project_paths)
        if self.settings.get("monitoring_enabled"):
            self.sampler.start()
        if self.settings.get("security_enabled"):
            self.security.start()
        self.state_changed.emit()

    def _report_settings_load(self) -> None:
        r = self.settings.load_report
        if r["invalid"]:
            self.log("settings", "settings_invalid",
                     "Some saved settings were invalid and were replaced by defaults: "
                     + ", ".join(sorted(r["invalid"])) + ".", severity="notice")
        if r["unknown"]:
            self.log("settings", "settings_unknown",
                     "Ignored unknown saved settings: " + ", ".join(sorted(r["unknown"])) + ".")
        if r["missing_v2"] and len(r["missing_v2"]) == len(
                [k for k in self.settings.all()][17:30]):
            self.log("settings", "settings_upgraded",
                     "Settings from an earlier version were upgraded; new V2 options use their defaults.")

    def _recover_interrupted_alerts(self) -> None:
        for row in self.alert_state.interrupt_stale():
            self.log("system", "alert_interrupted",
                     f"{row['message']} - Sentinel was not running when this alert ended, so how it ended "
                     "is unknown. It is marked as interrupted, not recovered.", severity="notice",
                     details={"alert": row["key"]})

    def _run_health_check(self) -> None:
        """Folder-watch health runs off the UI thread (a dead network drive can block isdir)."""
        if self._shut_down or not self.security.is_running:
            return

        def work(_cancel, _progress):
            return self.security.check_health()

        self.jobs.submit(work, lambda _r: None, lambda m: log.warning("health check failed: %s", m))

    def shutdown(self) -> None:
        """Idempotent, ordered shutdown: threads, watchers, timers, then the database."""
        if self._shut_down:
            return
        self._shut_down = True
        self._health.stop()
        self._analysis.stop()
        self._analysis_periodic.stop()
        self._path_timer.stop()
        try:
            if self.host is not None:
                self.host.shutdown()
        except Exception:
            log.exception("host shutdown failed")
        self.sampler.stop()
        self.security.stop()
        self.ai.shutdown()
        self.jobs.shutdown()
        try:
            self.log("app", "app_stopped", "VYRXONE Sentinel stopped.")
        except DatabaseError:
            pass
        self.db.close()

    def quit(self) -> None:
        self.quitting = True
        self.quit_requested.emit()

    def tray_message(self, title: str, text: str) -> None:
        """Replaced by the tray controller when a tray icon exists."""

    # --------------------------------------------------------------- logging
    def log(self, category: str, event_type: str, message: str, **kw: Any) -> Optional[Event]:
        """Audit log that never raises (a full disk must not crash the UI)."""
        try:
            return self.events.add(category, event_type, message, **kw)
        except (DatabaseError, ValueError):
            log.exception("could not write audit event %s", event_type)
            return None

    @Slot(object)
    def _handle_event(self, event: Event) -> None:
        self.event_added.emit(event)
        if event.event_type in ALL_TYPES:
            self.request_analysis()
        if event.category == "security" and event.severity == "warning":
            self._notify(f"security:{event.event_type}", "Sentinel: file integrity", event.message)
            self.alerts_changed.emit()

    def _notify(self, key: str, title: str, text: str) -> None:
        """Optional tray notification, rate-limited per key. The audit log is always written."""
        if not self.settings.get("notifications_enabled") or not self.limiter.allow(key):
            return
        try:
            self.tray_message(title, text)
        except Exception:
            log.exception("notification failed")

    # ------------------------------------------------------------- monitoring
    def _sampler_callback(self, sample: Sample) -> None:  # sampler thread
        self._sample_in.emit(sample)

    @Slot(object)
    def _handle_sample(self, sample: Sample) -> None:  # UI thread
        if self._shut_down:
            return
        self.last_sample = sample
        self.history.append(sample)
        values = {"cpu": sample.cpu_percent, "ram": sample.memory_percent,
                  "disk": sample.system_disk_percent}
        if sample.processes is not None:
            self.last_process_sample = sample
        self.self_metrics.poll()
        self._track_monitoring_errors(sample)
        for alert in self.thresholds.evaluate(values):
            warn = alert.kind == "warn"
            self.log("system", f"{alert.metric}_{'warning' if warn else 'recovered'}",
                     alert.message, severity="warning" if warn else "info",
                     details={"value": round(alert.value, 1), "threshold": alert.threshold})
            try:
                if warn:
                    self.alert_state.open(alert.metric, "warning", alert.message)
                else:
                    self.alert_state.close(alert.metric)
            except DatabaseError:
                log.exception("alert state not saved")
            self._notify(f"threshold:{alert.metric}:{alert.kind}",
                         "Sentinel warning" if warn else "Sentinel: recovered", alert.message)
            self.alert_raised.emit(alert)
            self.alerts_changed.emit()
        change = self.load.update(sample.cpu_percent)
        if change == "entered":
            reduced = self.settings.get("reduce_under_load")
            self.log("system", "sustained_load",
                     "Sustained high CPU load detected. "
                     + ("Animation effects were reduced automatically. " if reduced else "")
                     + "Consider a lower animation intensity or frame rate in Settings.",
                     severity="notice")
            self.quality_changed.emit()
            self.state_changed.emit()
        elif change == "exited":
            self.log("system", "load_normal", "CPU load returned to normal; effects restored.")
            self.quality_changed.emit()
            self.state_changed.emit()
        self.sample_ready.emit(sample)

    def _track_monitoring_errors(self, sample: Sample) -> None:
        errors = getattr(sample, "errors", ()) or ()
        now = time.monotonic()
        if errors:
            self._monitor_fault = True
            if now - self._last_monitor_error >= MONITOR_ERROR_SPACING_S:
                self._last_monitor_error = now
                self.log("system", "monitoring_error",
                         "System monitoring reported a problem: " + "; ".join(str(e) for e in errors[:3]),
                         severity="notice", details={"errors": [str(e) for e in errors[:5]]})
        elif self._monitor_fault:
            self._monitor_fault = False
            self.log("system", "monitoring_recovered", "System monitoring is working normally again.")

    # ------------------------------------------------------------ project intelligence (V3)
    def _apply_health(self, rep: HealthReport, name: str) -> HealthReport:
        prev = self.health.previous_path_state(rep.project_id)
        change = path_transition(prev, rep.path_state)
        if change == "unavailable":
            self.log("project", "project_path_unavailable",
                     f"Project folder for '{name}' is not available: {rep.messages[0]}", severity="notice",
                     path=rep.path or None, details={"project_id": rep.project_id, "state": rep.path_state})
        elif change == "restored":
            self.log("project", "project_path_restored", f"Project folder for '{name}' is available again.",
                     path=rep.path or None, details={"project_id": rep.project_id})
        try:
            self.health.save(rep)
        except DatabaseError:
            log.exception("project health not saved")
        self.projects_changed.emit()
        return rep

    def check_project(self, project_id: int, on_done=None, on_fail=None, on_progress=None, with_git: bool = True):
        """Health check for one project, off the UI thread (timeout 45 s, coalesced per project)."""
        project = self.projects.get(project_id)
        if project is None or self._shut_down:
            return None
        tasks = self.tasks

        def work(cancel, progress):
            return compute_health(project, tasks, cancel=cancel, progress=progress, with_git=with_git)

        def done(rep):
            rep = self._apply_health(rep, project.name)
            if on_done:
                on_done(rep)

        return self.jobs.submit(work, done, on_fail or (lambda m: log.warning("project health failed: %s", m)),
                                on_progress, name="Project health", timeout_s=45.0, key=f"health-{project_id}")

    def check_project_paths(self) -> None:
        """Periodic, cheap: is each registered project folder reachable? (no Git, no scanning)"""
        if self._shut_down:
            return
        projects = [p for p in self.projects.list() if p.path]
        if not projects:
            return

        def work(cancel, _progress):
            out = []
            for p in projects:
                if cancel.is_set():
                    break
                out.append((p, inspect_path(p.path)))
            return out

        def done(results):
            from .projects.health import PATH_TEXT
            for p, state in results:
                old = self.health.load(p.id)
                if old is not None and old.path_state == state and old.path == p.path:
                    continue                          # nothing changed: no write, no event, no refresh
                rep = old or HealthReport(p.id, time.time(), p.path, state)
                rep.path, rep.checked_ts, rep.path_state = p.path, time.time(), state
                rep.messages = [PATH_TEXT[state]] + (rep.messages[1:] if old else [])
                rep.overall = "unavailable" if state != "ok" else ("ok" if not rep.git else rep.overall
                                                                    if rep.overall != "unavailable" else "ok")
                self._apply_health(rep, p.name)       # records unavailable/restored once, then saves

        self.jobs.submit(work, done, lambda m: log.warning("project path check failed: %s", m),
                         name="Project path check", timeout_s=60.0, key="project-paths")

    def project_overview(self, project_id: int, on_done, on_fail=None, on_progress=None):
        """User-triggered repo overview (bounded walk, cancellable, 60 s timeout)."""
        project = self.projects.get(project_id)
        if project is None or not project.path:
            return None

        def work(cancel, progress):
            return scan_overview(project.path, cancel, progress)

        return self.jobs.submit(work, on_done, on_fail, on_progress, name="Repository overview",
                                timeout_s=60.0, key=f"overview-{project_id}")

    # ------------------------------------------------------------------ findings (V3)
    @property
    def findings_enabled(self) -> bool:
        return bool(self.settings.get("findings_enabled"))

    def request_analysis(self) -> None:
        """Schedule an analysis; bursts of events collapse into a single run."""
        if self._shut_down or not self.findings_enabled or self._analysis.isActive():
            return
        self._analysis.start()

    def run_analysis(self) -> None:
        """Run the rules off the UI thread (coalesced by key, with a timeout)."""
        if self._shut_down or not self.findings_enabled:
            return
        engine = self.engine

        def work(cancel, _progress):
            return engine.analyze(cancel=cancel)

        self.jobs.submit(work, self._analysis_done, lambda m: log.warning("findings analysis failed: %s", m),
                         name="Findings analysis", timeout_s=30.0, key="findings-analysis")

    def _analysis_done(self, result) -> None:
        if self._shut_down:
            return
        for f in result.opened[:5]:
            self.log("security", "finding_opened" if not f.reopened else "finding_reopened",
                     f"Finding {f.rule_id}: {f.title}", severity="notice", path=f.resource or None,
                     details={"finding_id": f.id, "severity": f.severity})
            if SEVERITY_RANK.get(f.severity, 0) >= SEVERITY_RANK["medium"]:
                self._notify(f"finding:{f.rule_id}", "Sentinel: new finding", f.title)
        if result.rate_limited:
            self.log("security", "findings_rate_limited",
                     f"{result.rate_limited} additional finding(s) were not created because the hourly "
                     "limit was reached. Existing findings are still being updated.", severity="notice")
        if result.changed or result.rate_limited:
            self.findings_changed.emit()
            self.state_changed.emit()

    def finding_action(self, ids: list[int], action: str, reason: str = "") -> int:
        """Acknowledge / resolve / dismiss / reopen findings (audited, never touches files)."""
        fn = {"acknowledge": self.findings.acknowledge, "resolve": self.findings.resolve,
              "reopen": self.findings.reopen}.get(action)
        try:
            n = self.findings.dismiss(ids, reason) if action == "dismiss" else (fn(ids) if fn else 0)
        except (FindingError, DatabaseError) as exc:
            log.warning("finding action failed: %s", exc)
            return 0
        if n:
            self.log("security", "finding_status", f"{n} finding(s) {PAST.get(action, action)} by the user.",
                     details={"ids": ids[:20], "reason": reason[:200] if action == "dismiss" else ""})
            self.findings_changed.emit()
            self.state_changed.emit()
        return n

    @property
    def monitoring_active(self) -> bool:
        return self.sampler.is_running

    def set_monitoring(self, enabled: bool) -> None:
        self.settings.set("monitoring_enabled", enabled)
        if enabled:
            self.sampler.start()
            self.log("system", "monitoring_started", "System monitoring started.")
        else:
            self.sampler.stop()
            self.log("system", "monitoring_stopped", "System monitoring stopped by the user.")
        self.state_changed.emit()

    def set_collect_processes(self, enabled: bool) -> None:
        self.sampler.set_collect_processes(enabled)

    # ---------------------------------------------------------------- security
    @property
    def security_active(self) -> bool:
        return self.security.is_running

    def set_security(self, enabled: bool) -> None:
        self.settings.set("security_enabled", enabled)
        if enabled:
            self.security.start()
            self.log("security", "watching_started", "Folder watching started.")
        else:
            self.security.stop()
            self.log("security", "watching_stopped", "Folder watching stopped by the user.")
        self.state_changed.emit()

    # --------------------------------------------------------------- animation
    @property
    def animation_paused(self) -> bool:
        return bool(self.settings.get("animation_paused"))

    def set_animation_paused(self, paused: bool) -> None:
        self.settings.set("animation_paused", paused)

    @property
    def quality(self) -> Quality:
        a = self.appearance
        if self.settings.get("lightweight_mode"):      # V5: 4 GB / integrated-graphics profile
            return effective_quality(min(15, self.settings.get("animation_fps")), "low", self.load.active, True,
                                     particle_count=min(6, a.particle_count), pulse_strength=0.0,
                                     reduced_motion=a.reduced_motion)
        return effective_quality(self.settings.get("animation_fps"),
                                 self.settings.get("animation_intensity"),
                                 self.load.active, self.settings.get("reduce_under_load"),
                                 particle_count=a.particle_count, pulse_strength=a.pulse_strength,
                                 reduced_motion=a.reduced_motion)

    def _sample_interval(self) -> float:
        base = float(self.settings.get("sample_interval_s"))
        return max(base, LIGHTWEIGHT_MIN_INTERVAL_S) if self.settings.get("lightweight_mode") else base

    # -------------------------------------------------------------------- mode
    def set_mode(self, mode: str) -> None:
        if mode not in ("wallpaper", "panel"):
            raise ValueError(mode)
        if self.host is None:
            self.mode = "panel"
            return
        if mode == "wallpaper":
            kind, detail = self.host.show_wallpaper()
        else:
            self.host.hide_wallpaper()
            kind, detail = "hidden", "Wallpaper is not shown. Your normal desktop is visible."
        changed = mode != self.mode
        st = getattr(self.host, "status", None)
        self.desktop_label = st.label if st else self.desktop_label
        self.desktop_method = st.method if st else ""
        self.mode, self.desktop_kind, self.desktop_detail = mode, kind, detail
        if changed or mode == "wallpaper":
            self.log("app", "mode_changed", f"Mode: {mode}. {detail}")
        self.mode_changed.emit(mode)
        self.desktop_status_changed.emit(kind, detail)
        self.state_changed.emit()

    def toggle_mode(self) -> None:
        self.set_mode("panel" if self.mode == "wallpaper" else "wallpaper")

    def restore_desktop(self) -> None:
        """Immediately return to the normal desktop (wallpaper removed)."""
        self.set_mode("panel")
        try:
            from .platform.windows import desktop_layer

            desktop_layer.refresh_desktop()
        except Exception:
            log.exception("desktop refresh failed")
        self.log("app", "desktop_restored", "Normal desktop restored by the user.")

    def report_desktop_lost(self, detail: str) -> None:
        """Called by the host when it fell back on its own (e.g. Explorer restarted)."""
        self.mode, self.desktop_kind, self.desktop_detail = "wallpaper", "window", detail
        st = getattr(self.host, "status", None)
        self.desktop_label = st.label if st is not None and st.kind == "window" else "Running as a normal window"
        self.desktop_method = "window"
        self.log("app", "desktop_fallback", detail, severity="notice")
        self.desktop_status_changed.emit("window", detail)
        self.state_changed.emit()

    def report_desktop_state(self, kind: str, label: str, detail: str, method: str = "") -> None:
        """Host -> controller status updates (embedded / window / reconnecting / unavailable)."""
        changed = (kind, label) != (self.desktop_kind, self.desktop_label)
        self.desktop_kind, self.desktop_label, self.desktop_detail, self.desktop_method = kind, label, detail, method
        if changed:
            self.log("app", "desktop_status", f"{label}. {detail}",
                     severity="notice" if kind in ("reconnecting", "unavailable") else "info",
                     details={"method": method})
        self.desktop_status_changed.emit(kind, detail)
        self.state_changed.emit()

    # ------------------------------------------------------------------ settings
    def _apply_thresholds(self) -> None:
        s = self.settings
        self.thresholds.configure(cpu=s.get("cpu_warn_percent"), ram=s.get("ram_warn_percent"),
                                  disk=s.get("disk_warn_percent"), sustained=s.get("sustained_samples"))

    def _on_setting(self, key: str, value: Any) -> None:
        if key in ("cpu_warn_percent", "ram_warn_percent", "disk_warn_percent", "sustained_samples"):
            self._apply_thresholds()
        elif key in ("sample_interval_s", "lightweight_mode"):
            self.sampler.set_interval(self._sample_interval())
        elif key == "max_events":
            self.events.max_events = int(value)
        elif key == "process_interval_s":
            self.sampler.set_process_interval(value)
        elif key == "findings_sensitivity":
            self.engine.set_sensitivity(value)
            self.request_analysis()
        elif key == "findings_enabled" and value:
            self.request_analysis()
        elif key == "notify_min_interval_s":
            self.limiter.min_interval_s = float(value)
        if key in APPEARANCE_KEYS:
            self.appearance = appearance_from(self.settings)
        if key in MONITOR_KEYS and self.host is not None and hasattr(self.host, "reconcile"):
            self.host.reconcile("settings")
        self.setting_changed.emit(key, value)
        if key in QUALITY_KEYS:
            self.quality_changed.emit()
        if key in QUALITY_KEYS | {"animation_paused", "show_overlay", "monitoring_enabled",
                                  "security_enabled"}:
            self.state_changed.emit()

    def data_age(self) -> Optional[float]:
        return None if self.last_sample is None else max(0.0, time.time() - self.last_sample.timestamp)
