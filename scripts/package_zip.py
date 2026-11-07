"""Build and verify the distributable ZIP of the V5 source project.

    python scripts/package_zip.py [--out DIR]

Excludes virtual environments, caches, build output, databases, logs and backups,
then re-opens the archive and verifies its contents. Exits non-zero on any problem.
"""

from __future__ import annotations

import argparse
import fnmatch
import re
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOP = "VYRXONE-Sentinel-V5"
ZIP_NAME = f"{TOP}.zip"

EXCLUDE_DIRS = {".git", ".venv", ".venv-build", ".venv-test", "venv", "__pycache__", ".pytest_cache",
                "build", "dist", "logs", ".idea", ".vscode"}
EXCLUDE_GLOBS = ["*.pyc", "*.pyo", "*.db", "*.db-*", "*.sqlite*", "*.bak", "*.log", "*.spec", "*.zip",
                 "*.egg-info", ".DS_Store", "Thumbs.db", "sentinel-diagnostics.txt"]

REQUIRED = [
    "README.md", "requirements.txt", "requirements-dev.txt", "run_sentinel.bat", "stop_sentinel.bat",
    "build_exe.bat", "run_tests.bat", ".gitignore", "pytest.ini", "launch_sentinel.pyw",
    "config/sentinel.sample.json", "assets/sentinel.ico", "assets/sentinel.png",
    "docs/ARCHITECTURE.md", "docs/WINDOWS_TEST_CHECKLIST.md", "docs/V1_COMPLETION_REPORT.md",
    "scripts/build_exe.py", "scripts/make_assets.py", "scripts/package_zip.py",
    "src/sentinel/__init__.py", "src/sentinel/__main__.py", "src/sentinel/app.py",
    "src/sentinel/controller.py", "src/sentinel/core/ipc.py", "src/sentinel/core/thresholds.py",
    "src/sentinel/storage/database.py", "src/sentinel/storage/migrations.py",
    "src/sentinel/monitoring/sampler.py", "src/sentinel/security/service.py",
    "src/sentinel/security/integrity.py", "src/sentinel/projects/git_status.py",
    "src/sentinel/platform/windows/desktop_layer.py", "src/sentinel/platform/windows/autostart.py",
    "src/sentinel/ui/wallpaper.py", "src/sentinel/ui/wallpaper_host.py", "src/sentinel/ui/main_window.py",
    "src/sentinel/ui/tray.py", "tests/conftest.py", "tests/test_ui.py", "tests/test_security.py",
    # ---- V2 ----
    "diagnose_sentinel.bat", "docs/V2_RELEASE_NOTES.md", "docs/V2_TEST_REPORT.md", "docs/V2_AUDIT.md",
    "scripts/benchmark.py", "scripts/audit_probes/probe_audit.py", "scripts/audit_probes/probe_race.py",
    "src/sentinel/diagnostics.py", "src/sentinel/core/alerts.py", "src/sentinel/core/appearance.py",
    "src/sentinel/core/monitors.py", "src/sentinel/core/recovery.py", "src/sentinel/core/instance.py",
    "src/sentinel/monitoring/selfstats.py", "src/sentinel/platform/windows/shell_events.py",
    "src/sentinel/ui/pages/performance.py", "tests/fakes.py", "tests/test_v2_storage.py",
    "tests/test_v2_alerts_security.py", "tests/test_v2_desktop.py", "tests/test_v2_appearance_perf.py",
    "tests/test_v2_lifecycle.py",
    # ---- V3 ----
    "docs/V3_RELEASE_NOTES.md", "docs/V3_TEST_REPORT.md", "docs/V3_AUDIT.md",
    "scripts/audit_probes/probe_v3_audit.py", "scripts/audit_probes/compare_events_reload.py",
    "src/sentinel/security/findings.py", "src/sentinel/security/rules.py",
    "src/sentinel/projects/tasks.py", "src/sentinel/projects/health.py", "src/sentinel/projects/overview.py",
    "src/sentinel/ui/jobs.py", "src/sentinel/ui/pages/events.py", "src/sentinel/ui/pages/findings.py",
    "src/sentinel/ui/pages/project_tabs.py", "src/sentinel/ui/pages/projects.py",
    "tests/test_v3_storage.py", "tests/test_v3_findings.py", "tests/test_v3_integrity.py",
    "tests/test_v3_projects.py", "tests/test_v3_jobs.py", "tests/test_v3_ui.py", "tests/test_v3_desktop.py",
    # ---- V4 ----
    "docs/V4_AUDIT.md", "docs/V4_RELEASE_NOTES.md", "docs/V4_TEST_REPORT.md", "docs/AI_SETUP.md", "docs/AI_PRIVACY.md",
    "docs/AI_PROVIDER_SUPPORT.md", "docs/V4_KNOWN_LIMITATIONS.md", "scripts/audit_probes/probe_v4_audit.py",
    "src/sentinel/ai/__init__.py", "src/sentinel/ai/models.py", "src/sentinel/ai/errors.py", "src/sentinel/ai/router.py",
    "src/sentinel/ai/config.py", "src/sentinel/ai/privacy.py", "src/sentinel/ai/context_builder.py",
    "src/sentinel/ai/response_validator.py", "src/sentinel/ai/conversation_store.py", "src/sentinel/ai/usage.py",
    "src/sentinel/ai/secrets.py", "src/sentinel/ai/transport.py", "src/sentinel/ai/service.py",
    "src/sentinel/ai/prompts.py", "src/sentinel/ai/knowledge.py", "src/sentinel/ai/providers/__init__.py",
    "src/sentinel/ai/providers/base.py", "src/sentinel/ai/providers/gemini.py", "src/sentinel/ai/providers/groq.py",
    "src/sentinel/ai/providers/openrouter.py", "src/sentinel/ai/providers/openai_compat.py",
    "src/sentinel/ui/pages/ai.py", "src/sentinel/ui/pages/ai_settings.py", "src/sentinel/ui/ai_consent.py",
    "tests/ai_fakes.py", "tests/test_v4_providers.py", "tests/test_v4_router_secrets_privacy.py",
    "tests/test_v4_service_transport.py", "tests/test_v4_ui.py",
    # ---- V5 (final) ----
    "docs/V5_AUDIT.md", "docs/V5_RELEASE_NOTES.md", "docs/V5_TEST_REPORT.md", "docs/V5_SECURITY_REVIEW.md",
    "docs/V5_KNOWN_LIMITATIONS.md", "docs/V5_INSTALLATION.md", "docs/V5_WINDOWS_VALIDATION.md",
    "docs/V5_PERFORMANCE_REPORT.md", "src/sentinel/ai/proxy.py", "src/sentinel/ai/keyring_selftest.py",
    "tests/proxy_fakes.py", "tests/test_v5_regressions.py", "tests/test_v5_proxy.py", "tests/test_v5_ui_db_privacy.py",
    "scripts/benchmark_v5.py", "scripts/audit_probes/probe_v5_audit.py",
]
SECRET_PATTERNS = [re.compile(p, re.I) for p in (
    r"api[_-]?key\s*[:=]\s*['\"][^'\"]{8,}", r"password\s*[:=]\s*['\"][^'\"]{4,}", r"\bsk-[A-Za-z0-9]{20,}",
    r"BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY", r"AKIA[0-9A-Z]{16}")]
PLACEHOLDER = re.compile(r"\b(TODO|FIXME|NotImplementedError|add your code here)\b", re.I)
TEXT_SUFFIXES = {".py", ".pyw", ".md", ".txt", ".bat", ".json", ".ini", ".gitignore", ""}


def excluded(rel: Path) -> bool:
    if any(part in EXCLUDE_DIRS or part.endswith(".egg-info") for part in rel.parts[:-1]):
        return True
    return any(fnmatch.fnmatch(rel.name, g) for g in EXCLUDE_GLOBS)


def collect() -> list[Path]:
    return sorted(p for p in ROOT.rglob("*") if p.is_file() and not excluded(p.relative_to(ROOT)))


def build(out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / ZIP_NAME
    if target.exists():
        target.unlink()
    files = collect()
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for path in files:
            rel = path.relative_to(ROOT).as_posix()
            info = zipfile.ZipInfo.from_file(path, f"{TOP}/{rel}")
            info.compress_type = zipfile.ZIP_DEFLATED
            if rel.endswith(".bat"):                       # Windows batch files need CRLF to be safe
                data = path.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
            else:
                data = path.read_bytes()
            zf.writestr(info, data)
    return target


def verify(target: Path) -> list[str]:
    problems: list[str] = []
    with zipfile.ZipFile(target) as zf:
        bad = zf.testzip()
        if bad:
            problems.append(f"corrupt member: {bad}")
        names = [n for n in zf.namelist() if not n.endswith("/")]
        stripped = {n.split("/", 1)[1] for n in names if "/" in n}
        if any(not n.startswith(TOP + "/") for n in names):
            problems.append("archive has entries outside the top-level folder")
        for req in REQUIRED:
            if req not in stripped:
                problems.append(f"missing required file: {req}")
        for n in sorted(stripped):
            rel = Path(n)
            if excluded(rel):
                problems.append(f"forbidden file included: {n}")
            if "/home/" in n or ":\\" in n:
                problems.append(f"machine-specific path in name: {n}")
        for n in names:
            rel = n.split("/", 1)[1]
            suffix = Path(rel).suffix.lower()
            if suffix not in TEXT_SUFFIXES and not rel.endswith(".gitignore"):
                continue
            text = zf.read(n).decode("utf-8", "replace")
            for pat in SECRET_PATTERNS:
                if pat.search(text):
                    problems.append(f"possible secret in {rel}: /{pat.pattern[:30]}/")
            if rel.startswith(("src/", "scripts/")) and PLACEHOLDER.search(text) and rel != "scripts/package_zip.py":
                problems.append(f"placeholder text in {rel}")
            if rel in ("README.md",) or rel.startswith("docs/"):
                if "TESTCOUNT" in text or "TBD" in text:
                    problems.append(f"unfilled placeholder in {rel}")
            if rel.endswith(".bat") and "\r\n" not in text:
                problems.append(f"{rel} is not CRLF")
    return problems


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "dist"))
    args = ap.parse_args()
    target = build(Path(args.out))
    problems = verify(target)
    with zipfile.ZipFile(target) as zf:
        infos = [i for i in zf.infolist() if not i.is_dir()]
    print(f"{target}  ({target.stat().st_size / 1024:.0f} KB, {len(infos)} files)")
    if problems:
        print("VERIFICATION FAILED:")
        for p in problems:
            print("  -", p)
        return 1
    print("Verification passed: required files present; no caches, venvs, databases, logs, secrets or placeholders.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
