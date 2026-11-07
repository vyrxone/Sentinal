"""V3 project intelligence: tasks, notes, health, disconnected paths, Git failure modes, overview."""

import os
import shutil
import subprocess
import threading
import time

import pytest

from sentinel.projects import git_status, health
from sentinel.projects.git_status import GitStatus, parse_changed_files
from sentinel.projects.health import (HealthStore, bounded_call, compute_health, inspect_path, path_transition)
from sentinel.projects.overview import scan_overview
from sentinel.projects.store import ProjectError, ProjectStore
from sentinel.projects.tasks import TaskStore, parse_due


@pytest.fixture
def pstore(db):
    return ProjectStore(db)


@pytest.fixture
def tstore(db):
    return TaskStore(db)


@pytest.fixture
def proj(pstore, tmp_path):
    d = tmp_path / "atlas"
    d.mkdir()
    return pstore.add("Atlas", str(d))


# ------------------------------------------------------------------ tasks and notes
def test_task_lifecycle_priority_due_and_activity(tstore, proj):
    t = tstore.add_task(proj.id, "  Write   docs ", "high", parse_due("2020-01-01"))
    assert t.title == "Write docs" and t.overdue and t.due_text == "2020-01-01"
    t2 = tstore.update_task(t.id, status="in_progress")
    assert t2.status == "in_progress" and t2.done_ts is None
    t3 = tstore.update_task(t.id, status="done")
    assert t3.done_ts and not t3.overdue                      # done tasks are never overdue
    t4 = tstore.update_task(t.id, status="open", clear_due=True)
    assert t4.due_ts is None and t4.done_ts is None
    assert tstore.delete_task(t.id) and not tstore.delete_task(t.id)
    kinds = [k for _, k, _ in tstore.recent_activity(proj.id)]
    assert "task_added" in kinds and "task_done" in kinds and "task_deleted" in kinds


def test_task_validation(tstore, proj):
    for bad in ("", "   ", "x" * 201):
        with pytest.raises(ProjectError):
            tstore.add_task(proj.id, bad)
    with pytest.raises(ProjectError):
        tstore.add_task(proj.id, "ok", "urgent")
    with pytest.raises(ProjectError):
        tstore.add_task(9999, "orphan")
    with pytest.raises(ProjectError):
        tstore.update_task(9999, status="done")
    with pytest.raises(ProjectError):
        parse_due("31/12/2026")
    assert parse_due("") is None


def test_task_filter_search_sort_and_counts(tstore, proj):
    a = tstore.add_task(proj.id, "Fix login bug", "high")
    b = tstore.add_task(proj.id, "Update 100% README_v2", "low", parse_due("2020-05-05"))
    c = tstore.add_task(proj.id, "Ship", "normal")
    tstore.update_task(c.id, status="done")
    assert [t.id for t in tstore.list_tasks(proj.id, text="login")] == [a.id]
    assert [t.id for t in tstore.list_tasks(proj.id, text="100%")] == [b.id]     # LIKE wildcards escaped
    assert tstore.list_tasks(proj.id, text="%") == [] or all("%" in t.title for t in tstore.list_tasks(proj.id, text="%"))
    assert {t.id for t in tstore.list_tasks(proj.id, hide_done=True)} == {a.id, b.id}
    assert [t.id for t in tstore.list_tasks(proj.id, priority="high")] == [a.id]
    assert tstore.list_tasks(proj.id)[0].id == b.id                              # dated first, done last
    assert tstore.list_tasks(proj.id)[-1].id == c.id
    assert tstore.counts(proj.id) == {"open": 2, "in_progress": 0, "done": 1, "overdue": 1}


def test_notes_and_tasks_persist_across_restart_and_cascade(tmp_path):
    from sentinel.storage.database import Database

    path = tmp_path / "p.db"
    db = Database(path)
    p = ProjectStore(db).add("Persist", "")
    ts = TaskStore(db)
    ts.add_task(p.id, "remember me")
    ts.add_note(p.id, "first line\nmore")
    db.close()
    db = Database(path)
    ts = TaskStore(db)
    assert ts.list_tasks(p.id)[0].title == "remember me"
    assert ts.list_notes(p.id)[0].body.startswith("first line")
    assert ts.last_note_ts(p.id) is not None
    ProjectStore(db).remove(p.id)
    assert ts.list_tasks() == [] and ts.recent_activity(p.id) == []
    db.close()


def test_note_validation_and_activity_is_bounded(tstore, proj):
    with pytest.raises(ProjectError):
        tstore.add_note(proj.id, "   ")
    with pytest.raises(ProjectError):
        tstore.add_note(proj.id, "x" * 5001)
    for i in range(230):
        tstore.log(proj.id, "x", str(i))
    assert len(tstore.recent_activity(proj.id, limit=1000)) == 200


def test_recorded_test_results_are_never_invented(tstore, proj):
    assert tstore.last_check_result(proj.id, "test") is None
    tstore.record_check_result(proj.id, "test", "passed", "42 passed")
    r = tstore.last_check_result(proj.id, "test")
    assert r["status"] == "passed" and r["summary"] == "42 passed"
    assert tstore.last_check_result(proj.id, "build") is None
    with pytest.raises(ProjectError):
        tstore.record_check_result(proj.id, "deploy", "passed")


# ------------------------------------------------------------------ path and health
def test_path_states(tmp_path):
    f = tmp_path / "file.txt"
    f.write_text("x")
    assert inspect_path("") == "unset"
    assert inspect_path(str(tmp_path)) == "ok"
    assert inspect_path(str(tmp_path / "gone")) == "missing"
    assert inspect_path(str(f)) == "not_a_folder"


def test_disconnected_drive_does_not_block_and_hung_calls_are_capped(monkeypatch, tmp_path):
    release = threading.Event()
    real_exists = os.path.exists
    monkeypatch.setattr(os.path, "exists", lambda p: (release.wait(10), real_exists(p))[1] if "dead" in str(p) else real_exists(p))
    t0 = time.perf_counter()
    assert inspect_path("Z:\\dead\\share", timeout=0.3) == "unreachable"
    assert time.perf_counter() - t0 < 1.5                         # bounded by the timeout, not the drive
    assert health.stuck_calls() == 1
    for _ in range(health.MAX_STUCK + 2):
        inspect_path("Z:\\dead\\share", timeout=0.2)
    assert health.stuck_calls() <= health.MAX_STUCK                # no unbounded thread growth
    t0 = time.perf_counter()
    assert inspect_path("Z:\\dead\\share", timeout=5) == "unreachable"
    assert time.perf_counter() - t0 < 0.5                          # fast-fail while the cap is reached
    release.set()
    deadline = time.time() + 3
    while health.stuck_calls() and time.time() < deadline:
        time.sleep(0.05)
    assert health.stuck_calls() == 0                               # recovered when the drive answered
    assert inspect_path(str(tmp_path)) == "ok"


def test_bounded_call_reports_errors_and_values():
    assert bounded_call(lambda: 7, 1) == ("ok", 7)
    kind, exc = bounded_call(lambda: 1 / 0, 1)
    assert kind == "error" and isinstance(exc, ZeroDivisionError)


def test_path_transition_reports_each_change_once():
    assert path_transition(None, "missing") == "unavailable"
    assert path_transition("ok", "unreachable") == "unavailable"
    assert path_transition("missing", "missing") is None
    assert path_transition("missing", "unreachable") is None
    assert path_transition("missing", "ok") == "restored"
    assert path_transition(None, "ok") is None and path_transition("unset", "ok") is None


def test_health_report_missing_folder(pstore, tstore, tmp_path):
    p = pstore.add("Gone", str(tmp_path / "nope"))
    rep = compute_health(p, tstore)
    assert rep.path_state == "missing" and rep.overall == "unavailable" and rep.git is None
    assert any("not found" in m for m in rep.messages)


def test_health_without_git_installed(proj, tstore, monkeypatch):
    monkeypatch.setattr(git_status, "find_git", lambda: None)
    rep = compute_health(proj, tstore)
    assert rep.git["git_available"] is False and "not installed" in rep.git["summary"] and rep.overall == "ok"
    assert any("No test result recorded" in m for m in rep.messages)


def test_health_not_a_repo_timeout_and_malformed_output(proj, tstore, monkeypatch):
    monkeypatch.setattr(git_status, "find_git", lambda: "git")

    def fake_run(git, path, args, timeout):
        raise subprocess.TimeoutExpired("git", timeout)

    monkeypatch.setattr(git_status, "_run", fake_run)
    rep = compute_health(proj, tstore, git_timeout=0.2)
    assert rep.git["error"] and "did not answer" in rep.git["error"] and rep.overall == "attention"

    class Done:
        returncode, stderr = 128, "fatal: not a git repository (or any of the parent directories): .git"
        stdout = ""

    monkeypatch.setattr(git_status, "_run", lambda *a, **k: Done())
    assert compute_health(proj, tstore).git["is_repo"] is False

    class Garbage:
        returncode, stderr, stdout = 0, "", "\x00\x01 not porcelain\n##\n\n  \n"

    monkeypatch.setattr(git_status, "_run", lambda *a, **k: Garbage())
    rep = compute_health(proj, tstore)                      # must not raise
    assert rep.git["is_repo"] is True


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed in this environment")
def test_health_with_real_git_repository(proj, tstore):
    def git(*a):
        subprocess.run(["git", "-C", proj.path, "-c", "user.email=t@t", "-c", "user.name=t", *a], check=True,
                       capture_output=True)

    git("init", "-q")
    (os_path := os.path.join(proj.path, "a.txt"))
    open(os_path, "w").write("1")
    git("add", "a.txt")
    git("commit", "-qm", "first")
    assert compute_health(proj, tstore).overall == "ok"
    open(os_path, "w").write("2")
    open(os.path.join(proj.path, "new.txt"), "w").write("n")
    rep = compute_health(proj, tstore)
    assert rep.overall == "attention" and rep.git["modified"] == 1 and rep.git["untracked"] == 1
    assert any(f.endswith("a.txt") for f in rep.git["changed_files"])
    # read-only: nothing was committed/staged/cleaned by the check
    assert subprocess.run(["git", "-C", proj.path, "log", "--oneline"], capture_output=True, text=True).stdout.count("\n") == 1
    assert os.path.exists(os.path.join(proj.path, "new.txt"))


def test_parse_changed_files_is_tolerant():
    out = parse_changed_files('## main\n M a.py\nR  old.py -> new.py\n?? "sp ace.txt"\n!! ig.log\nxx\n')
    assert out == ("M a.py", "R new.py", "?? sp ace.txt")
    assert len(parse_changed_files("\n".join(f" M f{i}" for i in range(100)))) == 20


def test_health_cancelled_is_marked_incomplete(proj, tstore, monkeypatch):
    ev = threading.Event()
    ev.set()
    rep = compute_health(proj, tstore, cancel=ev)
    assert rep.complete is False and rep.git is None


def test_health_store_remembers_last_ok(db, proj, tstore):
    hs = HealthStore(db)
    rep = hs.save(compute_health(proj, tstore, with_git=False))
    assert rep.overall == "ok" and hs.load(proj.id).last_ok_ts == rep.checked_ts
    shutil.rmtree(proj.path)
    bad = hs.save(compute_health(proj, tstore, with_git=False))
    assert bad.overall == "unavailable" and bad.last_ok_ts == rep.checked_ts      # last success is kept
    assert hs.previous_path_state(proj.id) == "missing"
    assert hs.load(99999) is None


# ------------------------------------------------------------------ overview
def make_tree(root):
    for rel, text in {"README.md": "# Hello\nbody", "app/main.py": "print(1)", "app/util.py": "x=1",
                      "web/index.ts": "export {}", "tests/test_a.py": "def test(): pass", "pytest.ini": "[pytest]",
                      ".venv/lib/x.py": "venv", "node_modules/pkg/i.js": "nm", "build/out.js": "o",
                      "app/__pycache__/m.pyc": "c", "package.json": '{"scripts": {"test": "jest"}}'}.items():
        p = os.path.join(root, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        open(p, "w").write(text)


def test_overview_counts_and_excludes_generated_folders(tmp_path):
    make_tree(tmp_path)
    ov = scan_overview(str(tmp_path))
    assert ov.readme == "README.md" and ov.readme_excerpt.startswith("# Hello")
    assert ov.languages["Python"] == 3 and ov.languages["TypeScript"] == 1
    assert "JavaScript" not in ov.languages                       # node_modules / build skipped
    assert ov.skipped_dirs >= 4 and ov.has_tests_dir
    assert "pytest.ini" in ov.test_config and "package.json (test script)" in ov.test_config
    assert not ov.truncated and any("README" in line for line in ov.summary_lines())


def test_overview_limits_cancel_and_symlinks(tmp_path):
    make_tree(tmp_path)
    for i in range(30):
        (tmp_path / f"extra{i}.py").write_text("x")
    assert scan_overview(str(tmp_path), max_files=10).truncated
    ev = threading.Event()
    ev.set()
    assert scan_overview(str(tmp_path), ev).cancelled
    outside = tmp_path.parent / "outside_tree"
    outside.mkdir(exist_ok=True)
    (outside / "secret.py").write_text("s")
    try:
        os.symlink(outside, tmp_path / "linked")
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")
    ov = scan_overview(str(tmp_path))
    assert all(name != "linked" for name, _ in ov.top_dirs)       # not followed


# ------------------------------------------------------------------ controller integration
def test_controller_records_project_unavailable_once_then_restored(ctrl, pump, tmp_path):
    d = tmp_path / "work"
    d.mkdir()
    p = ctrl.projects.add("Work", str(d))
    done = []
    ctrl.check_project(p.id, on_done=done.append, with_git=False)
    assert pump(5, lambda: done)
    assert ctrl.events.count(event_type="project_path_unavailable") == 0
    shutil.rmtree(d)
    for _ in range(3):                                           # repeated checks must not repeat the event
        done.clear()
        ctrl.check_project(p.id, on_done=done.append, with_git=False)
        assert pump(5, lambda: done)
    assert ctrl.events.count(event_type="project_path_unavailable") == 1
    d.mkdir()
    done.clear()
    ctrl.check_project(p.id, on_done=done.append, with_git=False)
    assert pump(5, lambda: done)
    assert ctrl.events.count(event_type="project_path_restored") == 1
    assert ctrl.health.load(p.id).overall == "ok"


def test_periodic_path_check_is_cheap_and_event_driven(ctrl, pump, tmp_path):
    p = ctrl.projects.add("Net", str(tmp_path / "missing"))
    ctrl.check_project_paths()
    assert pump(5, lambda: ctrl.events.count(event_type="project_path_unavailable") == 1)
    ctrl.check_project_paths()
    pump(1)
    assert ctrl.events.count(event_type="project_path_unavailable") == 1     # no duplicate
    assert ctrl.health.load(p.id).git is None                                 # never ran Git
