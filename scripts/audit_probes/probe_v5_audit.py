"""Re-runs the V5 audit reproductions (A11-A14 in docs/V5_AUDIT.md) against this source tree.
Against the V4 tree these tests FAIL; against V5 they pass.  python scripts/audit_probes/probe_v5_audit.py"""
import os, subprocess, sys, pathlib
root = pathlib.Path(__file__).resolve().parents[2]
env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
sys.exit(subprocess.call([sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-o", "addopts=", "-q", "-k", "a11 or a12 or a13 or a14",
                          str(root / "tests" / "test_v5_regressions.py")], cwd=root, env=env))
