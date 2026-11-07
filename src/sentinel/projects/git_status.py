"""Read-only Git status using argument lists (never a shell).

Safety measures:
* ``--no-optional-locks`` / GIT_OPTIONAL_LOCKS=0 so Git does not write to the repo.
* ``core.fsmonitor=false`` so a repository's config cannot launch a program.
* No Git command that changes anything is ever issued.
* Git's own safe.directory protection is respected, never overridden.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from typing import Optional

log = logging.getLogger("sentinel.git")

GIT_TIMEOUT = 8.0


@dataclass(frozen=True)
class GitStatus:
    git_available: bool
    is_repo: bool = False
    branch: Optional[str] = None
    ahead: int = 0
    behind: int = 0
    staged: int = 0
    modified: int = 0
    untracked: int = 0
    conflicted: int = 0
    last_commit: Optional[str] = None
    error: Optional[str] = None
    changed_files: tuple = ()        # first few changed paths (V3), for the health report
    timed_out: bool = False

    @property
    def clean(self) -> bool:
        return self.is_repo and not (self.staged or self.modified or self.untracked or self.conflicted)

    def summary(self) -> str:
        if not self.git_available:
            return "Git is not installed or not on PATH."
        if self.error:
            return self.error
        if not self.is_repo:
            return "Not a Git repository."
        state = "clean" if self.clean else (
            f"{self.staged} staged, {self.modified} modified, {self.untracked} untracked"
            + (f", {self.conflicted} conflicted" if self.conflicted else ""))
        sync = ""
        if self.ahead or self.behind:
            sync = f" (ahead {self.ahead}, behind {self.behind})"
        return f"Branch {self.branch or '(unknown)'}{sync} - {state}"


def find_git() -> Optional[str]:
    return shutil.which("git")


def _run(git: str, path: str, args: list[str], timeout: float) -> subprocess.CompletedProcess:
    env = dict(os.environ, GIT_OPTIONAL_LOCKS="0", GIT_TERMINAL_PROMPT="0", LC_ALL="C")
    cmd = [git, "--no-optional-locks", "-c", "core.fsmonitor=false", "-C", path, *args]
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env,
                          creationflags=flags, shell=False, encoding="utf-8", errors="replace")


_BRANCH_RE = re.compile(r"^## (?P<branch>.+?)(?:\.\.\.(?P<upstream>\S+))?(?: \[(?P<track>[^\]]+)\])?$")


def parse_porcelain(text: str) -> dict:
    """Parse ``git status --porcelain=v1 --branch`` output."""
    out = dict(branch=None, ahead=0, behind=0, staged=0, modified=0, untracked=0, conflicted=0)
    for line in text.splitlines():
        if line.startswith("## "):
            if line.startswith("## No commits yet on "):
                out["branch"] = line[len("## No commits yet on "):].strip() + " (no commits yet)"
                continue
            if line.startswith("## HEAD (no branch)"):
                out["branch"] = "(detached HEAD)"
                continue
            m = _BRANCH_RE.match(line)
            if m:
                out["branch"] = m.group("branch")
                track = m.group("track") or ""
                a = re.search(r"ahead (\d+)", track)
                b = re.search(r"behind (\d+)", track)
                out["ahead"] = int(a.group(1)) if a else 0
                out["behind"] = int(b.group(1)) if b else 0
            continue
        if len(line) < 3:
            continue
        x, y = line[0], line[1]
        if x == "?" and y == "?":
            out["untracked"] += 1
        elif x == "!" and y == "!":
            continue
        elif x == "U" or y == "U" or (x == "A" and y == "A") or (x == "D" and y == "D"):
            out["conflicted"] += 1
        else:
            if x not in (" ", "?"):
                out["staged"] += 1
            if y not in (" ", "?"):
                out["modified"] += 1
    return out


def parse_changed_files(text: str, limit: int = 20) -> tuple:
    """First *limit* changed paths from porcelain output (status code + path); malformed lines are skipped."""
    out = []
    for line in text.splitlines():
        if line.startswith("## ") or len(line) < 4 or line[:2] == "!!":
            continue
        name = line[3:].strip().strip('"')
        if " -> " in name:
            name = name.split(" -> ", 1)[1]
        if name:
            out.append(f"{line[:2].strip() or '?'} {name}"[:200])
        if len(out) >= limit:
            break
    return tuple(out)


def get_git_status(path: str, timeout: float = GIT_TIMEOUT,
                   git_exe: Optional[str] = None) -> GitStatus:
    """Never raises: every failure is reported in the returned GitStatus."""
    git = git_exe or find_git()
    if not git:
        return GitStatus(git_available=False)
    if not path or not os.path.isdir(path):
        return GitStatus(git_available=True, error="The project folder is missing or unavailable.")
    try:
        proc = _run(git, path, ["status", "--porcelain=v1", "--branch"], timeout)
    except subprocess.TimeoutExpired:
        return GitStatus(git_available=True, error=f"Git did not answer within {timeout:.0f}s.", timed_out=True)
    except OSError as exc:
        return GitStatus(git_available=True, error=f"Could not run Git: {exc}")
    if proc.returncode != 0:
        err = (proc.stderr or "").strip()
        low = err.lower()
        if "not a git repository" in low:
            return GitStatus(git_available=True, is_repo=False)
        if "dubious ownership" in low or "safe.directory" in low:
            return GitStatus(git_available=True, error=(
                "Git refused to read this repository because of its ownership safety check. "
                "Sentinel does not override Git's safety settings."))
        first = err.splitlines()[0] if err else f"exit code {proc.returncode}"
        return GitStatus(git_available=True, error=f"Git reported: {first}")
    parsed = parse_porcelain(proc.stdout)
    last = None
    try:
        log_proc = _run(git, path, ["log", "-1", "--format=%h %s"], timeout)
        if log_proc.returncode == 0 and log_proc.stdout.strip():
            last = log_proc.stdout.strip()[:200]
    except (subprocess.TimeoutExpired, OSError):
        pass
    return GitStatus(git_available=True, is_repo=True, last_commit=last,
                     changed_files=parse_changed_files(proc.stdout), **parsed)
