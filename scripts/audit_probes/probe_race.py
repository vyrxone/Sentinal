"""Start N app processes at once; count survivors. python -I probe_race.py <src> <N>"""
import os, sys, subprocess, tempfile, time
src, n = sys.argv[1], int(sys.argv[2])
env = dict(os.environ, QT_QPA_PLATFORM="offscreen", PYTHONPATH=src,
           SENTINEL_DATA_DIR=tempfile.mkdtemp(), SENTINEL_INSTANCE_NAME="race-%d" % os.getpid())
ps = [subprocess.Popen([sys.executable, "-m", "sentinel", "--background", "--panel", "--quit-after", "8"],
      env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE) for _ in range(n)]
time.sleep(5)
alive = sum(p.poll() is None for p in ps)
print(f"started={n} still_running_after_5s={alive}")
for p in ps:
    try: p.wait(timeout=20)
    except Exception: p.kill()
