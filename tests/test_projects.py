import shutil
import subprocess

import pytest

from sentinel.projects import git_status
from sentinel.projects.git_status import GitStatus, get_git_status, parse_porcelain
from sentinel.projects.store import STATUSES, ProjectError, ProjectStore, path_state


@pytest.fixture
def store(db):
    return ProjectStore(db)


def test_add_edit_remove_project(store, tmp_path):
    p = store.add("  Alpha  ", str(tmp_path), "desc", "Active", "notes here")
    assert p.name == "Alpha" and p.path == str(tmp_path) and p.path_state == "ok"
    store.add("beta", "", status="Planned")
    assert [x.name for x in store.list()] == ["Alpha", "beta"]       # case-insensitive order

    upd = store.update(p.id, "Alpha 2", str(tmp_path), "new desc", "Paused", "n2")
    assert (upd.name, upd.status, upd.description, upd.notes) == ("Alpha 2", "Paused", "new desc", "n2")
    assert upd.updated_ts >= p.updated_ts

    assert store.remove(p.id) is True and store.remove(p.id) is False
    assert tmp_path.exists()                                          # removing a record never deletes files
    assert [x.name for x in store.list()] == ["beta"]


def test_projects_persist_across_restart(tmp_path):
    from sentinel.storage.database import Database

    d = Database(tmp_path / "p.db")
    ProjectStore(d).add("Persisted", "", "d")
    d.close()
    d2 = Database(tmp_path / "p.db")
    assert ProjectStore(d2).list()[0].name == "Persisted"
    d2.close()


@pytest.mark.parametrize("kwargs,msg", [
    (dict(name="   "), "name"),
    (dict(name="x" * 500), "too long"),
    (dict(name="ok", status="Bogus"), "Status"),
    (dict(name="ok", path="bad\0path"), "path"),
    (dict(name="ok", notes="n" * 30000), "notes"),
])
def test_project_validation(store, kwargs, msg):
    with pytest.raises(ProjectError, match=msg):
        store.add(**kwargs)


def test_update_missing_project_raises(store):
    with pytest.raises(ProjectError, match="no longer exists"):
        store.update(999, "x")


def test_path_states(tmp_path):
    f = tmp_path / "file.txt"
    f.write_text("x")
    assert path_state("") == "unset"
    assert path_state(str(tmp_path)) == "ok"
    assert path_state(str(tmp_path / "nope")) == "missing"
    assert path_state(str(f)) == "not_a_directory"


def test_invalid_path_project_can_still_be_stored(store, tmp_path):
    p = store.add("Offline drive", str(tmp_path / "E_drive" / "proj"))
    assert p.path_state == "missing"                                  # flagged, not crashed
    assert set(STATUSES) >= {"Active", "Done"}


# ------------------------------------------------------------------- git
def test_parse_porcelain_branch_and_counts():
    out = parse_porcelain(
        "## main...origin/main [ahead 2, behind 1]\n"
        "M  staged.py\n"
        " M modified.py\n"
        "MM both.py\n"
        "?? new.txt\n"
        "UU conflict.txt\n"
        "!! ignored.log\n")
    assert out == dict(branch="main", ahead=2, behind=1, staged=2, modified=2, untracked=1, conflicted=1)
    assert parse_porcelain("## No commits yet on dev\n")["branch"].startswith("dev")
    assert parse_porcelain("## HEAD (no branch)\n")["branch"] == "(detached HEAD)"
    assert parse_porcelain("## feature\n")["branch"] == "feature"


def test_git_unavailable_is_reported_not_raised(monkeypatch, tmp_path):
    monkeypatch.setattr(git_status, "find_git", lambda: None)
    st = get_git_status(str(tmp_path))
    assert st.git_available is False and "not installed" in st.summary()


def test_git_missing_or_inaccessible_folder(tmp_path):
    st = get_git_status(str(tmp_path / "nope"), git_exe="git")
    assert st.error and not st.is_repo
    assert get_git_status("", git_exe="git").error


def test_git_timeout_and_oserror_are_handled(monkeypatch, tmp_path):
    def slow(*a, **k):
        raise subprocess.TimeoutExpired("git", 1)

    monkeypatch.setattr(git_status, "_run", slow)
    assert "did not answer" in get_git_status(str(tmp_path), git_exe="git").error

    def broken(*a, **k):
        raise OSError("exec format error")

    monkeypatch.setattr(git_status, "_run", broken)
    assert "Could not run Git" in get_git_status(str(tmp_path), git_exe="git").error


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
def test_real_repository_status_and_read_only(tmp_path):
    def git(*args):
        subprocess.run(["git", "-C", str(tmp_path), *args], check=True, capture_output=True)

    plain = get_git_status(str(tmp_path))
    assert plain.git_available and not plain.is_repo and plain.summary() == "Not a Git repository."

    git("init", "-q", "-b", "main")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "Tester")
    (tmp_path / "a.txt").write_text("1")
    git("add", "a.txt")
    git("commit", "-q", "-m", "first commit")
    clean = get_git_status(str(tmp_path))
    assert clean.is_repo and clean.clean and clean.branch == "main"
    assert clean.last_commit and "first commit" in clean.last_commit

    (tmp_path / "a.txt").write_text("2")
    (tmp_path / "new.txt").write_text("n")
    before = sorted(p.name for p in (tmp_path / ".git").iterdir())
    dirty = get_git_status(str(tmp_path))
    assert (dirty.modified, dirty.untracked, dirty.staged) == (1, 1, 0)
    assert "1 modified" in dirty.summary()
    assert sorted(p.name for p in (tmp_path / ".git").iterdir()) == before   # repo untouched
    assert (tmp_path / "a.txt").read_text() == "2"


def test_git_commands_are_argument_lists_without_shell(monkeypatch, tmp_path):
    captured = {}

    def fake_run(cmd, **kw):
        captured["cmd"], captured["kw"] = cmd, kw
        return subprocess.CompletedProcess(cmd, 0, stdout="## main\n", stderr="")

    monkeypatch.setattr(git_status.subprocess, "run", fake_run)
    st = get_git_status(str(tmp_path), git_exe="/usr/bin/git")
    assert isinstance(captured["cmd"], list) and captured["kw"]["shell"] is False
    assert "--no-optional-locks" in captured["cmd"] and "core.fsmonitor=false" in captured["cmd"]
    assert captured["kw"]["env"]["GIT_OPTIONAL_LOCKS"] == "0"
    assert isinstance(st, GitStatus) and st.is_repo
