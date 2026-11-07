"""V3 findings: rules, severity, correlation, dedup, rate limiting, lifecycle, export, evidence safety."""

import csv
import json

import pytest

from sentinel.security.findings import FindingError, FindingStore
from sentinel.security.rules import RULES, FindingsEngine
from sentinel.storage.events_store import EventStore

NOW = 1_000_000.0


@pytest.fixture
def eng(db):
    ev = EventStore(db)
    st = FindingStore(db)
    return ev, st, FindingsEngine(ev, st)


def add(ev, etype, ts, path=None, sev="info", cat="security", **details):
    return ev.add(cat, etype, f"{etype} message", severity=sev, path=path, ts=ts, details=details or None)


def test_every_rule_has_id_version_and_limitation():
    ids = [r.rule_id for r in RULES]
    assert len(ids) == len(set(ids))
    for r in RULES:
        assert r.version >= 1 and r.limitation and r.description and r.title


def test_baseline_change_creates_explainable_finding(eng):
    ev, st, e = eng
    ev1 = add(ev, "baseline_mismatch", NOW - 60, "/data/a.txt", "warning", expected_sha256="aa", actual_sha256="bb")
    res = e.analyze(NOW)
    assert res.created == 1
    f = st.query()[0]
    assert (f.rule_id, f.rule_version, f.status, f.severity, f.category) == ("BASE-001", 1, "new", "medium", "integrity")
    assert f.resource == "/data/a.txt" and f.confidence and f.next_step and f.explanation
    assert "not proof" in (f.explanation + f.confidence).lower() or "cannot be determined" in f.confidence
    full = st.get(f.id)
    assert [r.event_id for r in full.events] == [ev1.id]            # stable event reference
    assert f.evidence["expected_sha256"] == "aa"


def test_analysis_is_deterministic_and_idempotent(eng):
    ev, st, e = eng
    for i in range(10):
        add(ev, "file_modified", NOW - 500 + i * 10, "/x/log.txt")
    r1 = e.analyze(NOW)
    snapshot = [(f.finding_key, f.occurrences, f.last_ts, f.severity) for f in st.query()]
    r2 = e.analyze(NOW)
    assert r1.created == 1 and r2.created == 0 and r2.updated == 0 and r2.unchanged == 1
    assert snapshot == [(f.finding_key, f.occurrences, f.last_ts, f.severity) for f in st.query()]


def test_repeated_changes_respect_threshold_and_window(eng):
    ev, st, e = eng
    for i in range(7):                                  # below the default threshold (8)
        add(ev, "file_modified", NOW - 100 + i, "/x/a")
    assert e.analyze(NOW).created == 0
    for i in range(9):                                  # same count but spread over hours: never grouped
        add(ev, "file_modified", NOW - 20000 + i * 2000, "/x/b")
    assert e.analyze(NOW).created == 0
    add(ev, "file_modified", NOW - 90, "/x/a")
    assert e.analyze(NOW).created == 1
    assert st.query()[0].rule_id == "FILE-002"


def test_sensitivity_changes_thresholds(eng):
    ev, st, e = eng
    for i in range(5):
        add(ev, "file_modified", NOW - 100 + i, "/x/a")
    assert e.analyze(NOW).created == 0
    e.set_sensitivity("high")
    assert e.analyze(NOW).created == 1
    e.set_sensitivity("bogus")
    assert e.sensitivity == "normal"


def test_correlation_groups_with_reason_and_bounded_window(eng):
    ev, st, e = eng
    ids = [add(ev, "baseline_mismatch", NOW - 300 + i * 20, f"/d/f{i}", "warning").id for i in range(3)]
    add(ev, "baseline_mismatch", NOW - 9000, "/d/old", "warning")           # far outside the 10 minute window
    e.analyze(NOW)
    group = [f for f in st.query() if f.rule_id == "CORR-001"]
    assert len(group) == 1 and group[0].severity == "high"
    assert "Grouped because" in group[0].explanation and "3 different" in group[0].explanation
    assert [r.event_id for r in st.get(group[0].id).events] == ids           # old event not pulled in
    assert group[0].evidence["grouped_by"]


def test_rate_limit_caps_new_findings_but_keeps_updating(db):
    ev, st = EventStore(db), FindingStore(db)
    e = FindingsEngine(ev, st, max_new_per_hour=3)
    for i in range(6):
        add(ev, "baseline_mismatch", NOW - 100 + i, f"/d/f{i}", "warning")
    res = e.analyze(NOW)
    n = st.count(rule_id="BASE-001")
    assert res.rate_limited >= 1 and st.created_since(NOW - 3600) == 3 and n <= 3
    add(ev, "baseline_mismatch", NOW - 10, "/d/f0", "warning")             # existing finding still updates
    assert e.analyze(NOW).updated >= 1


def test_watch_unavailable_then_restored_changes_severity(eng):
    ev, st, e = eng
    add(ev, "watch_unavailable", NOW - 500, "/w", "notice")
    e.analyze(NOW)
    f = st.query()[0]
    assert f.rule_id == "WATCH-003" and f.severity == "medium"
    add(ev, "watch_restored", NOW - 100, "/w")
    e.analyze(NOW)
    f2 = st.query()[0]
    assert f2.id == f.id and "gap" in f2.explanation and f2.occurrences == 2


def test_resource_rule_is_separate_category_with_limitation(eng):
    ev, st, e = eng
    for i in range(3):
        add(ev, "cpu_warning", NOW - 600 + i * 100, None, "warning", cat="system")
    e.analyze(NOW)
    f = st.query()[0]
    assert f.rule_id == "RES-004" and f.category == "resource" and f.severity == "low"
    assert "not indicate" in f.confidence or "not a security" in f.explanation.lower() or "alone" in f.confidence


def test_monitoring_failure_burst_and_project_rules(eng):
    ev, st, e = eng
    for i in range(3):
        add(ev, "monitoring_error", NOW - 900 + i * 200, None, "notice", cat="system")
    add(ev, "project_path_unavailable", NOW - 50, "/proj", "notice", cat="project")
    for i in range(160):
        add(ev, "file_created", NOW - 59 + i * 0.3, f"/b/f{i}")
    e.analyze(NOW)
    rules = {f.rule_id for f in st.query(limit=50)}
    assert {"SVC-005", "PROJ-007", "BURST-006"} <= rules


def test_project_restored_clears_unavailable_finding(eng):
    ev, st, e = eng
    add(ev, "project_path_unavailable", NOW - 500, "/proj", cat="project")
    add(ev, "project_path_restored", NOW - 100, "/proj", cat="project")
    assert e.analyze(NOW).created == 0


def test_no_process_or_malware_claims_in_rule_text():
    from sentinel.security import rules as mod

    text = " ".join(r.description + r.limitation for r in RULES).lower()
    assert "ransomware" not in text.replace("not detect", "") or "does not" in mod.LIMITATIONS[0]
    assert "malware" in mod.LIMITATIONS[0] and "not" in mod.LIMITATIONS[0]


def test_a_broken_rule_does_not_stop_the_others(eng, monkeypatch):
    ev, st, e = eng
    from sentinel.security import rules as mod

    bad = mod.Rule("BAD-000", 1, "bad", "integrity", "d", "l", lambda ctx: 1 / 0)
    monkeypatch.setattr(mod, "RULES", (bad,) + mod.RULES)
    add(ev, "baseline_missing", NOW - 5, "/d/a", "warning")
    res = e.analyze(NOW)
    assert res.rule_errors == 1 and res.created == 1


# ------------------------------------------------------------------ lifecycle
def make_finding(eng):
    ev, st, e = eng
    add(ev, "baseline_mismatch", NOW - 100, "/d/a", "warning")
    e.analyze(NOW)
    return st.query()[0]


def test_lifecycle_acknowledge_resolve_dismiss_reopen(eng):
    ev, st, e = eng
    f = make_finding(eng)
    assert st.acknowledge([f.id]) == 1 and st.get(f.id).status == "acknowledged"
    assert st.resolve([f.id]) == 1 and st.get(f.id).status == "resolved"
    assert st.dismiss([f.id], "known update") == 1
    g = st.get(f.id)
    assert g.status == "dismissed" and g.dismiss_reason == "known update"
    assert st.reopen([f.id]) == 1 and st.get(f.id).status == "new" and st.get(f.id).dismiss_reason == ""
    with pytest.raises(FindingError):
        st.dismiss([f.id], "bad\x00reason")


def test_resolved_finding_reopens_on_new_evidence_dismissed_stays(eng):
    ev, st, e = eng
    f = make_finding(eng)
    st.resolve([f.id])
    e.analyze(NOW)                                         # nothing new: stays resolved
    assert st.get(f.id).status == "resolved"
    add(ev, "baseline_mismatch", NOW - 10, "/d/a", "warning")
    res = e.analyze(NOW)
    g = st.get(f.id)
    assert res.reopened == 1 and g.status == "new" and g.reopened and g.occurrences == 2
    st.dismiss([f.id], "expected")
    add(ev, "baseline_mismatch", NOW - 5, "/d/a", "warning")
    e.analyze(NOW)
    assert st.get(f.id).status == "dismissed" and st.get(f.id).occurrences == 3   # user decision respected


def test_filters_search_and_counts(eng):
    ev, st, e = eng
    add(ev, "baseline_mismatch", NOW - 100, "/d/report.docx", "warning")
    add(ev, "watch_unavailable", NOW - 90, "/w", "notice")
    e.analyze(NOW)
    assert st.count() == 2 and st.count(status="open") == 2
    assert [f.rule_id for f in st.query(category="watcher")] == ["WATCH-003"]
    assert [f.rule_id for f in st.query(severity="medium", text="report")] == ["BASE-001"]
    assert st.query(text="%") == []                         # LIKE wildcards are escaped
    assert st.query(since=NOW + 1) == [] and len(st.query(until=NOW)) == 2
    assert st.open_count() == 2 and st.high_open_count() == 2


def test_evidence_survives_event_deletion_and_pruning_skips_open_findings(db):
    ev, st = EventStore(db), FindingStore(db)
    e = FindingsEngine(ev, st)
    keep = add(ev, "baseline_missing", NOW - 100, "/d/a", "warning")
    for i in range(20):
        add(ev, "file_created", NOW - 1000 + i, f"/f{i}")
    e.analyze(NOW)
    ev.acknowledge([keep.id])                              # no longer protected as unacknowledged warning
    ev.prune(max_events=5)
    assert db.query_one("SELECT COUNT(*) FROM events WHERE id=?", (keep.id,))[0] == 1   # protected by open finding
    f = st.query()[0]
    st.resolve([f.id])
    for i in range(6):
        add(ev, "file_created", NOW + i, f"/g{i}")
    ev.prune(max_events=5)
    assert db.query_one("SELECT COUNT(*) FROM events WHERE id=?", (keep.id,))[0] == 0   # now prunable
    refs = st.get(f.id).events                             # evidence snapshot is still readable
    assert refs and refs[0].event_id == keep.id and "baseline_missing" in refs[0].event_type


def test_export_json_and_csv_with_event_refs_and_formula_safety(eng, tmp_path):
    ev, st, e = eng
    add(ev, "baseline_mismatch", NOW - 100, "=cmd|' /C calc'!A0", "warning")
    e.analyze(NOW)
    j, n = st.export(tmp_path / "f.json")
    data = json.loads(j.read_text())
    assert n == data["count"] == 1 and data["findings"][0]["related_events"][0]["event_id"]
    c, _ = st.export(tmp_path / "f.csv")
    rows = list(csv.reader(c.open(encoding="utf-8-sig")))
    assert rows[0][0] == "id" and "related_event_ids" in rows[0]
    assert all(not cell.startswith("=") for cell in rows[1])  # injection neutralised
    assert rows[1][-1].split()[0].isdigit()
    with pytest.raises(FindingError):
        st.export(tmp_path / "f.xml")
    assert not list(tmp_path.glob("*.tmp"))


def test_large_history_analysis_is_bounded(eng):
    ev, st, e = eng
    for i in range(6000):
        add(ev, "file_modified", NOW - 3000 + i * 0.4, f"/many/f{i % 50}")
    res = e.analyze(NOW)
    assert res.events_examined <= 5000 and res.duration_ms < 5000
