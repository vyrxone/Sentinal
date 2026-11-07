"""User-triggered lightweight repository overview (V3). Local only: no uploads, no AI.

Walks one project folder with hard limits (files, time, depth), never follows symlinks and skips
virtual environments, dependency folders, build output and caches.
"""

from __future__ import annotations

import json
import os
import threading
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Callable, Optional

EXCLUDED_DIRS = frozenset({
    ".git", ".hg", ".svn", ".venv", "venv", "env", ".env", "node_modules", "__pycache__", ".tox", ".nox",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", ".cache", "build", "dist", "target", ".next", ".nuxt",
    "site-packages", ".idea", ".vscode", "bin", "obj", ".gradle", "vendor", ".terraform", "coverage",
    ".eggs", "htmlcov", ".parcel-cache", ".turbo"})
MAX_FILES = 20000
MAX_SECONDS = 15.0
MAX_DEPTH = 12
README_NAMES = ("readme.md", "readme.rst", "readme.txt", "readme")
TEST_FILES = ("pytest.ini", "tox.ini", "conftest.py", "jest.config.js", "jest.config.ts", "vitest.config.ts",
              "vitest.config.js", "karma.conf.js", "phpunit.xml", "noxfile.py", ".mocharc.json")
LANGUAGES = {
    ".py": "Python", ".pyw": "Python", ".js": "JavaScript", ".mjs": "JavaScript", ".jsx": "JavaScript",
    ".ts": "TypeScript", ".tsx": "TypeScript", ".java": "Java", ".kt": "Kotlin", ".c": "C", ".h": "C/C++ header",
    ".cpp": "C++", ".cc": "C++", ".cs": "C#", ".go": "Go", ".rs": "Rust", ".rb": "Ruby", ".php": "PHP",
    ".swift": "Swift", ".sh": "Shell", ".bat": "Batch", ".ps1": "PowerShell", ".html": "HTML", ".css": "CSS",
    ".scss": "CSS", ".sql": "SQL", ".md": "Markdown", ".json": "JSON", ".yml": "YAML", ".yaml": "YAML",
    ".toml": "TOML", ".lua": "Lua", ".dart": "Dart", ".r": "R", ".vue": "Vue", ".svelte": "Svelte"}


@dataclass
class Overview:
    root: str
    files: int = 0
    dirs: int = 0
    total_bytes: int = 0
    languages: dict = field(default_factory=dict)         # language -> file count
    top_dirs: list = field(default_factory=list)         # [(name, files)]
    readme: Optional[str] = None
    readme_excerpt: str = ""
    test_config: list = field(default_factory=list)
    has_tests_dir: bool = False
    skipped_dirs: int = 0
    truncated: bool = False
    cancelled: bool = False
    reason: str = ""
    seconds: float = 0.0

    def summary_lines(self) -> list[str]:
        out = [f"{self.files} file(s) in {self.dirs} folder(s) (generated/dependency folders skipped: {self.skipped_dirs})."]
        if self.languages:
            out.append("Languages by file count: " + ", ".join(f"{k} {v}" for k, v in
                                                               sorted(self.languages.items(), key=lambda kv: -kv[1])[:6]))
        out.append("README: " + (self.readme or "none found"))
        tests = list(self.test_config) + (["tests/ folder"] if self.has_tests_dir else [])
        out.append("Test configuration found: " + (", ".join(tests) if tests else "none detected"))
        if self.truncated or self.cancelled:
            out.append("Partial result: " + (self.reason or "stopped early") + ".")
        return out


def scan_overview(root: str, cancel: Optional[threading.Event] = None,
                  progress: Optional[Callable[[int, int, str], None]] = None, *,
                  max_files: int = MAX_FILES, max_seconds: float = MAX_SECONDS) -> Overview:
    ov = Overview(root=root)
    t0 = time.monotonic()
    top: Counter = Counter()
    langs: Counter = Counter()
    root = os.path.abspath(root)
    root_depth = root.rstrip(os.sep).count(os.sep)
    last_progress = 0
    for cur, dirs, names in os.walk(root, followlinks=False):
        if cancel is not None and cancel.is_set():
            ov.cancelled, ov.reason = True, "cancelled by you"
            break
        if time.monotonic() - t0 > max_seconds:
            ov.truncated, ov.reason = True, f"stopped after {max_seconds:g} seconds"
            break
        depth = cur.rstrip(os.sep).count(os.sep) - root_depth
        keep = []
        for d in dirs:
            if d.lower() in EXCLUDED_DIRS or d.endswith(".egg-info") or os.path.islink(os.path.join(cur, d)):
                ov.skipped_dirs += 1
            elif depth >= MAX_DEPTH:
                ov.truncated, ov.reason = True, "depth limit reached"
            else:
                keep.append(d)
        dirs[:] = keep
        ov.dirs += 1
        rel_top = os.path.relpath(cur, root).split(os.sep)[0] if cur != root else "(top level)"
        for name in names:
            full = os.path.join(cur, name)
            if os.path.islink(full):
                continue
            if ov.files >= max_files:
                ov.truncated, ov.reason = True, f"file limit ({max_files}) reached"
                break
            ov.files += 1
            top[rel_top] += 1
            ext = os.path.splitext(name)[1].lower()
            if ext in LANGUAGES:
                langs[LANGUAGES[ext]] += 1
            try:
                ov.total_bytes += os.path.getsize(full)
            except OSError:
                pass
            if cur == root:
                low = name.lower()
                if ov.readme is None and low in README_NAMES:
                    ov.readme = name
                    try:
                        with open(full, "r", encoding="utf-8", errors="replace") as fh:
                            ov.readme_excerpt = fh.read(600)
                    except OSError:
                        pass
                if low in TEST_FILES and name not in ov.test_config:
                    ov.test_config.append(name)
                if low == "package.json" or low == "pyproject.toml" or low == "setup.cfg":
                    _probe_manifest(full, low, ov)
        if ov.truncated and ov.reason.startswith("file limit"):
            break
        if progress and ov.files - last_progress >= 500:
            last_progress = ov.files
            progress(ov.files, max_files, rel_top)
    if os.path.isdir(os.path.join(root, "tests")) or os.path.isdir(os.path.join(root, "test")):
        ov.has_tests_dir = True
    ov.languages = dict(langs)
    ov.top_dirs = top.most_common(30)
    ov.seconds = time.monotonic() - t0
    if progress:
        progress(ov.files, ov.files, "")
    return ov


def _probe_manifest(path: str, low: str, ov: Overview) -> None:
    try:
        if os.path.getsize(path) > 256 * 1024:
            return
        text = open(path, "r", encoding="utf-8", errors="replace").read()
    except OSError:
        return
    if low == "package.json":
        try:
            if "test" in (json.loads(text).get("scripts") or {}):
                ov.test_config.append("package.json (test script)")
        except (ValueError, AttributeError):
            pass
    elif low == "pyproject.toml" and ("[tool.pytest" in text):
        ov.test_config.append("pyproject.toml ([tool.pytest])")
    elif low == "setup.cfg" and "[tool:pytest]" in text:
        ov.test_config.append("setup.cfg ([tool:pytest])")
