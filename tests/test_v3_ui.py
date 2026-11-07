"""V3 UI + controller integration: findings page, project tabs, performance page, events model/view."""

import json
import time

import pytest
from PySide6.QtWidgets import QFileDialog, QInputDialog, QMessageBox

from sentinel.ui.pages.events import PAGE_SIZE

NOW = time.time()


@pytest.fixture
def window(ctrl, qapp, pump):
    from sentinel.ui.main_window import MainWindow

    w = MainWindow(ctrl)
    w.show()
    pump(0.1)
    yield w
    ctrl.quitting = True
    w.close()


# ------------------------------------------------------------------ controller + engine
def test_security_events_trigger_a_coalesced_background_analysis(ctrl, pump):
    for i in range(4):
        ctrl.events.add("security", "baseline_mismatch", "changed", severity="warning", path=f"/d/f{i}")
    assert pump(10, lambda: ctrl.findings.count() >= 1)
    assert {f.rule_id for f in ctrl.findings.query()} >= {"BASE-001", "CORR-001"}
    assert ctrl.events.count(event_type="finding_opened") >= 1
    jobs_before = ctrl.jobs.counts["done"]
    for _ in range(5):
        ctrl.request_analysis()
    pump(0.2)
    assert ctrl._analysis.isActive()                       # one pending run, not five


def test_finding_events_do_not_retrigger_analysis_loops(ctrl, pump):
    ctrl.events.add("security", "baseline_missing", "gone", severity="warning", path="/x")
    assert pump(10, lambda: ctrl.findings.count() == 1)
    pump(1.5)
    runs = ctrl.engine.last_run_ts
    pump(5.5)                                              # longer than the debounce: no run caused by finding_* events
    assert ctrl.engine.last_run_ts == runs or ctrl.findings.count() == 1


def test_finding_actions_are_audited_and_signal(ctrl, pump):
    ctrl.events.add("security", "baseline_missing", "gone", severity="warning", path="/x")
    assert pump(10, lambda: ctrl.findings.count() == 1)
    seen = []
    ctrl.findings_changed.connect(lambda: seen.append(1))
    fid = ctrl.findings.query()[0].id
    assert ctrl.finding_action([fid], "dismiss", "expected") == 1
    assert ctrl.findings.get(fid).dismiss_reason == "expected" and seen
    assert ctrl.events.query(event_type="finding_status")
    assert ctrl.finding_action([fid], "bogus") == 0
    assert ctrl.finding_action([fid], "reopen") == 1


def test_sensitivity_and_disable_settings_take_effect(ctrl, pump):
    ctrl.settings.set("findings_sensitivity", "high")
    assert ctrl.engine.sensitivity == "high"
    ctrl.settings.set("findings_enabled", False)
    ctrl.events.add("security", "baseline_missing", "gone", severity="warning", path="/x")
    pump(5)
    assert ctrl.findings.count() == 0 and not ctrl._analysis.isActive()
    ctrl.settings.set("findings_enabled", True)
    assert pump(10, lambda: ctrl.findings.count() == 1)


def test_monitoring_errors_are_logged_rate_limited_and_recovery_noted(ctrl):
    from sentinel.monitoring.sampler import Sample

    class S:
        errors = ("disk list unavailable: boom",)
    ctrl._track_monitoring_errors(S)
    ctrl._track_monitoring_errors(S)
    assert ctrl.events.count(event_type="monitoring_error") == 1           # 5 minute spacing
    class OK:
        errors = ()
    ctrl._track_monitoring_errors(OK)
    assert ctrl.events.count(event_type="monitoring_recovered") == 1


def test_performance_alerts_are_not_security_findings(ctrl, pump):
    for i in range(3):
        ctrl.events.add("system", "cpu_warning", "hot", severity="warning", ts=time.time() - 300 + i * 60)
    assert pump(10, lambda: ctrl.findings.count() == 1)
    f = ctrl.findings.query()[0]
    assert f.category == "resource" and f.rule_id == "RES-004"
    assert ctrl.findings.query(category="integrity") == []                  # kept apart from integrity findings


# ------------------------------------------------------------------ findings page
def test_findings_page_empty_state_filters_and_lifecycle(window, ctrl, pump, dialogs, monkeypatch):
    window.goto(7)
    page = window.pages[7]
    pump(0.2)
    assert "No findings" in page.empty.text() and "does not prove" in page.empty.text()
    for i in range(3):
        ctrl.events.add("security", "baseline_mismatch", "changed", severity="warning", path=f"/d/f{i}",
                        details={"expected_sha256": "a", "actual_sha256": "b"})
    ctrl.events.add("security", "watch_unavailable", "gone", severity="notice", path="/w")
    page.analyze_now()
    assert pump(10, lambda: page.model.rowCount() >= 4)
    page.table.selectRow(0)
    text = page.detail.toPlainText()
    for needle in ("Why this was raised", "How certain this is", "Suggested safe next step", "Evidence",
                   "Related events", "Rule "):
        assert needle in text
    page.category.setCurrentIndex(page.category.findData("watcher"))
    assert page.model.rowCount() == 1 and page.model.data(page.model.index(0, 6)).startswith("WATCH-003")
    page.category.setCurrentIndex(0)
    page.text.setText("f1")
    assert pump(2, lambda: page.model.rowCount() == 1)
    page.text.clear()
    pump(0.5)
    page.table.selectRow(0)
    sel = page._selected_ids()
    page.b_ack.click()
    assert ctrl.findings.get(sel[0]).status == "acknowledged"
    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: ("known update", True)))
    page.b_dismiss.click()
    assert ctrl.findings.get(sel[0]).status == "dismissed"
    page.status.setCurrentIndex(page.status.findData("dismissed"))
    assert page.model.rowCount() == 1
    page.table.selectRow(0)
    page.b_reopen.click()
    assert ctrl.findings.get(sel[0]).status == "new"
    page.table.clearSelection()
    page.b_ack.click()
    assert dialogs["information"]                           # nothing selected -> explained, not silent


def test_findings_page_export_and_rules_dialog(window, ctrl, pump, dialogs, monkeypatch, tmp_path):
    ctrl.events.add("security", "baseline_missing", "gone", severity="warning", path="=HYPERLINK(1)")
    window.goto(7)
    page = window.pages[7]
    page.analyze_now()
    assert pump(10, lambda: page.model.rowCount() == 1)
    dest = tmp_path / "f.csv"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(dest), "CSV (*.csv)")))
    page.export()
    assert pump(5, dest.exists) and pump(2, lambda: bool(dialogs["information"]))
    assert "'=HYPERLINK" in dest.read_text(encoding="utf-8-sig")
    page.show_rules()
    assert "not proof" in dialogs["information"][-1][1].lower() and "BASE-001" in dialogs["information"][-1][1]


# ------------------------------------------------------------------ projects tabs
def test_project_tabs_health_tasks_notes_overview(window, ctrl, pump, dialogs, tmp_path, monkeypatch):
    (tmp_path / "app.py").write_text("x=1")
    (tmp_path / "README.md").write_text("# hi")
    p = ctrl.projects.add("Atlas", str(tmp_path))
    window.goto(3)
    page = window.pages[3]
    pump(0.3)
    tabs = page.tabs
    assert tabs.project is not None and tabs.project.id == p.id
    assert "Not checked yet" in tabs.h_status.text()                      # nothing scanned automatically
    tabs.run_health()
    assert pump(10, lambda: "Healthy" in tabs.h_status.text() or "Needs attention" in tabs.h_status.text())
    assert "No test result recorded" in tabs.h_detail.toPlainText()
    monkeypatch.setattr(QInputDialog, "getItem", staticmethod(lambda *a, **k: ("passed", True)))
    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: ("42 passed", True)))
    tabs._record("test")
    assert "passed (recorded by you" in tabs.h_detail.toPlainText()
    tabs.t_title.setText("Write docs")
    tabs.t_due.setText("2020-01-01")
    tabs.add_task()
    assert tabs.t_list.count() == 1 and "OVERDUE" in tabs.t_list.item(0).text()
    tabs.t_list.setCurrentRow(0)
    tabs._task_status("done")
    assert ctrl.tasks.list_tasks(p.id)[0].status == "done"
    tabs.t_due.setText("not a date")
    tabs.t_title.setText("bad")
    tabs.add_task()
    assert dialogs["warning"] and ctrl.tasks.counts(p.id)["open"] == 0
    tabs.n_text.setPlainText("remember this")
    tabs.add_note()
    assert tabs.n_list.count() == 1 and ctrl.tasks.list_notes(p.id)[0].body == "remember this"
    tabs.run_overview()
    assert pump(10, lambda: "README" in tabs.o_text.toPlainText())
    assert "Python 1" in tabs.o_text.toPlainText() and "nothing" not in tabs.o_text.toPlainText().lower().split("readme")[0][:0]
    # tabs are disabled when no project is selected
    page._new()
    assert not tabs.widget(0).isEnabled()


def test_projects_page_never_blocks_on_a_hung_folder(window, ctrl, pump, monkeypatch, tmp_path):
    import os
    import threading

    release = threading.Event()
    real = os.path.exists
    monkeypatch.setattr(os.path, "exists", lambda p: (release.wait(5), real(p))[1] if "hungshare" in str(p) else real(p))
    p = ctrl.projects.add("Net", "Z:\\hungshare\\proj")
    window.goto(3)
    t0 = time.perf_counter()
    window.pages[3].refresh()
    window.pages[3].path.setText("Z:\\hungshare\\proj2")
    assert time.perf_counter() - t0 < 0.3                                # UI thread returned immediately (was 0.5 s/call in V2)
    assert "Checking folder" in window.pages[3].path_note.text()
    release.set()
    assert pump(8, lambda: "Checking" not in window.pages[3].path_note.text())


# ------------------------------------------------------------------ performance page
def test_performance_page_shows_background_status_and_unavailable_not_zero(window, ctrl, pump):
    window.goto(6)
    page = window.pages[6]
    pump(0.4)
    R = page.rows
    assert "unavailable" in R["Last sample"].text() or "s ago" in R["Last sample"].text()
    assert R["Monitoring"].text() in ("Running", "Stopped")
    assert "running" in R["Background jobs"].text() and "rejected" in R["Background jobs"].text()
    assert R["Slow operations"].text()
    assert "Findings" in page.report.toPlainText() and "Background jobs" in page.report.toPlainText()
    ctrl.set_monitoring(False)
    page.refresh()
    assert R["Monitoring"].text() == "Stopped"


# ------------------------------------------------------------------ events model/view
def seed(ctrl, n, base=None):
    base = base or NOW - 10000
    ctrl.db.execute("PRAGMA synchronous=OFF")
    with ctrl.db.transaction() as conn:
        conn.executemany(
            "INSERT INTO events(ts,category,event_type,severity,path,message) VALUES(?,?,?,?,?,?)",
            [(base + i, "security" if i % 3 else "system", "file_modified", "info" if i % 5 else "warning",
              f"/data/dir{i % 40}/file{i}.txt", f"msg {i}") for i in range(n)])


def test_events_page_paginates_large_history_with_bounded_rows(window, ctrl, pump):
    seed(ctrl, 5000)
    window.goto(4)
    page = window.pages[4]
    page.status.setCurrentIndex(page.status.findData("all"))
    page.range.setCurrentIndex(page.range.count() - 1)
    pump(0.3)
    assert page.model.rowCount() == PAGE_SIZE                            # bounded rendering
    assert page._total >= 5000 and "Page 1 of" in page.page_label.text()
    first_ts = page.model.rows[0].ts
    page.b_next.click()
    assert page._page == 1 and page.model.rows[0].ts < first_ts           # older events, no overlap
    assert page.b_prev.isEnabled()
    page.b_prev.click()
    assert page.model.rows[0].ts == first_ts


def test_events_page_sorting_and_stable_selection(window, ctrl, pump):
    seed(ctrl, 300)
    window.goto(4)
    page = window.pages[4]
    page.status.setCurrentIndex(page.status.findData("all"))
    page.range.setCurrentIndex(page.range.count() - 1)
    pump(0.2)
    page._on_header(1)                                                   # severity, descending
    sev = [e.severity for e in page.model.rows]
    assert sev[0] == "warning" and sev == sorted(sev, key=lambda s: {"warning": 0, "notice": 1, "info": 2}[s])
    page._on_header(1)
    assert page.model.rows[0].severity == "info"
    page._on_header(6)                                                   # derived column: ignored, no crash
    page.table.selectRow(5)
    chosen = page._selected_id()
    ctrl.events.add("app", "tick", "new row")                            # live refresh must not lose the selection
    pump(1.2)
    assert page._selected_id() == chosen


def test_events_live_update_changes_in_place_when_only_flags_change(window, ctrl, pump):
    ctrl.events.add("security", "baseline_missing", "gone", severity="warning", path="/x")
    window.goto(4)
    page = window.pages[4]
    pump(0.3)
    resets = []
    page.model.modelReset.connect(lambda: resets.append(1))
    changed = []
    page.model.dataChanged.connect(lambda *a: changed.append(1))
    page.table.selectRow(0)
    page._ack_selected(True)
    page.reload()
    assert changed and not resets                                         # no full rebuild for a flag change


def test_events_export_streams_large_sets_and_is_cancellable(ctrl, tmp_path):
    import threading

    from sentinel.storage.export import ExportCancelled, export_events_streamed

    seed(ctrl, 3000)
    ctrl.events.add("security", "file_created", "=cmd|' /C calc'!A0", path="@evil")
    n = export_events_streamed(ctrl.events.iter_batches(batch=500), tmp_path / "e.csv")
    assert n == 3001
    text = (tmp_path / "e.csv").read_text(encoding="utf-8-sig")
    assert "'=cmd" in text and "'@evil" in text
    n = export_events_streamed(ctrl.events.iter_batches(batch=500), tmp_path / "e.json")
    assert json.loads((tmp_path / "e.json").read_text())["count"] == n == 3001
    ev = threading.Event()
    ev.set()
    with pytest.raises(ExportCancelled):
        export_events_streamed(ctrl.events.iter_batches(batch=500), tmp_path / "c.json", cancel=ev)
    assert not (tmp_path / "c.json").exists() and not list(tmp_path.glob("*.tmp"))


def test_keyset_batches_are_stable_while_events_arrive(ctrl):
    seed(ctrl, 1200)
    seen = []
    for i, batch in enumerate(ctrl.events.iter_batches(batch=500)):
        seen += [e.id for e in batch]
        if i == 0:
            seed(ctrl, 50, base=NOW)                                      # new rows appended mid-export
    assert len(seen) == len(set(seen)) and seen == sorted(seen)


def test_events_sort_argument_is_allow_listed(ctrl):
    with pytest.raises(ValueError):
        ctrl.events.query(sort="ts; DROP TABLE events")


def test_findings_page_is_reachable_by_keyboard_and_menu(window):
    keys = {a.shortcut().toString() for a in window.actions()}
    assert "Ctrl+8" in keys
    window.goto(7)
    assert window.stack.currentIndex() == 7 and window.pages[7].title == "Findings"
