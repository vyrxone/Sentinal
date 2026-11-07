"""Application entry point: ``python -m sentinel`` or launch_sentinel.pyw."""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from typing import Optional, Sequence

from . import APP_NAME, RELEASE_LABEL, __version__


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="sentinel", description=f"{APP_NAME} {RELEASE_LABEL}")
    p.add_argument("--version", action="store_true", help="print the version and exit")
    p.add_argument("--stop", action="store_true",
                   help="ask a running Sentinel to exit cleanly (add --force to terminate a hung Sentinel)")
    p.add_argument("--force", action="store_true",
                   help="with --stop: if Sentinel does not exit, terminate it after verifying it is Sentinel")
    p.add_argument("--diagnostics", action="store_true",
                   help="print a short, redacted support report and exit")
    p.add_argument("--show", action="store_true", help="ask a running Sentinel to open its control panel")
    p.add_argument("--background", action="store_true", help="start without opening the control panel")
    p.add_argument("--panel", action="store_true", help="start in Control Panel mode (no wallpaper)")
    p.add_argument("--wallpaper", action="store_true", help="start in Wallpaper mode")
    p.add_argument("--keyring-selftest", action="store_true",
                   help="test the Windows Credential Manager with throw-away entries (no network, no real keys) and exit")
    p.add_argument("--data-dir", help="use this folder for Sentinel's data instead of the default")
    p.add_argument("--self-check", action="store_true",
                   help="verify dependencies and the local database, then exit")
    p.add_argument("--quit-after", type=float, metavar="SECONDS", help=argparse.SUPPRESS)
    return p


def _fatal(title: str, message: str) -> int:
    """Report a startup failure: always on stderr, and in a dialog when a real display exists."""
    if sys.stderr is not None:
        sys.stderr.write(f"{title}: {message}\n")
    try:
        from PySide6.QtGui import QGuiApplication
        from PySide6.QtWidgets import QApplication, QMessageBox

        QApplication.instance() or QApplication([])
        if QGuiApplication.platformName() not in ("offscreen", "minimal"):   # never block a headless run
            QMessageBox.critical(None, title, message)
    except Exception:
        pass
    return 2


def self_check() -> int:
    from .core import paths
    from .storage.database import Database, DatabaseError

    problems = []
    for name in ("PySide6", "psutil", "watchdog"):
        try:
            mod = __import__(name)
            print(f"[ok] {name} {getattr(mod, '__version__', '')}".rstrip())
        except Exception as exc:
            problems.append(name)
            print(f"[missing] {name}: {exc}")
    try:
        db = Database(paths.db_path())
        print(f"[ok] database {paths.db_path()} (schema v{db.user_version})")
        db.close()
    except DatabaseError as exc:
        problems.append("database")
        print(f"[error] {exc}")
    from .projects.git_status import find_git

    from .ai.secrets import SecretStore

    store = SecretStore()
    print("[info] AI key storage: " + ("Windows Credential Manager (keyring) available" if store.storage_available
          else "not available (optional 'keyring' package missing; AI keys would need an environment variable)"))
    print(f"[info] git: {find_git() or 'not found (project Git status will be unavailable)'}")
    print("Self-check passed." if not problems else f"Self-check FAILED: {', '.join(problems)}")
    return 0 if not problems else 1


def main(argv: Optional[Sequence[str]] = None) -> int:
    args, qt_args = build_parser().parse_known_args(list(sys.argv[1:] if argv is None else argv))
    if args.version:
        print(f"{APP_NAME} {RELEASE_LABEL} ({__version__})")
        return 0
    if args.data_dir:
        os.environ["SENTINEL_DATA_DIR"] = os.path.abspath(args.data_dir)

    if args.self_check:
        return self_check()
    if args.keyring_selftest:
        from .ai.keyring_selftest import run as keyring_selftest

        return 0 if keyring_selftest() else 1
    if args.diagnostics:
        from .diagnostics import build_report

        print(build_report())
        return 0

    from PySide6.QtCore import QCoreApplication

    # ---- control commands talk to a running instance and never start the UI ----
    if args.stop or args.show:
        from .core import ipc

        from .core import instance

        app = QCoreApplication.instance() or QCoreApplication([sys.argv[0]])
        reply = ipc.send_command("quit" if args.stop else "show")
        if reply is None:
            _lock, pid = instance.acquire(100)          # is a hung instance still holding the lock?
            if _lock is not None:
                _lock.unlock()
                print("Sentinel is not running.")
                return 0
            if not (args.stop and args.force):
                print("Sentinel does not answer but seems to be running"
                      + (f" (process {pid})" if pid else "") + ". Use stop_sentinel.bat /force to stop it.")
                return 1
            print(instance.force_stop(pid))
            return 0
        if args.show:
            print("Sentinel was asked to open its panel.")
            return 0
        print("Sentinel was asked to exit...")
        if instance.wait_until_gone(lambda: ipc.send_command("ping", timeout_ms=400) == "pong"):
            print("Sentinel has exited.")
            return 0
        if args.force:
            _lock, pid = instance.acquire(100)
            print(instance.force_stop(pid))
            return 0
        print("Sentinel did not exit within 8 seconds. Run stop_sentinel.bat /force to stop it.")
        return 1

    from .core.logging_setup import setup_logging

    setup_logging()
    log = logging.getLogger("sentinel")

    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    from .core import ipc, paths
    from .storage.database import Database, DatabaseError

    app = QApplication([sys.argv[0], *qt_args])
    app.setApplicationName(APP_NAME)
    app.setOrganizationName("VYRXONE")
    app.setQuitOnLastWindowClosed(False)

    # ---- single instance -------------------------------------------------------
    if ipc.send_command("ping") == "pong":
        if not args.background:
            ipc.send_command("show")
        print("Sentinel is already running; its control panel was brought to the front.")
        return 0
    from .core import instance as instance_guard

    try:
        lock, other_pid = instance_guard.acquire(300)   # closes the simultaneous-start race
    except OSError as exc:
        return _fatal(f"{APP_NAME} cannot start",
                      f"The data folder is not usable: {exc}\n\nData folder: {paths.data_dir()}")
    if lock is None:
        for _ in range(15):                              # the other copy may still be starting up
            if ipc.send_command("ping") == "pong":
                if not args.background:
                    ipc.send_command("show")
                print("Sentinel is already running; its control panel was brought to the front.")
                return 0
            time.sleep(0.2)
        return _fatal(f"{APP_NAME} is already running",
                      "Another Sentinel is running or starting"
                      + (f" (process {other_pid})" if other_pid else "") + ".")
    instance = ipc.InstanceServer()
    if not instance.listen():
        log.warning("control channel unavailable; stop_sentinel will not be able to reach this instance")

    from .controller import AppController
    from .ui import theme
    from .ui.icons import make_icon
    from .ui.main_window import MainWindow
    from .ui.tray import TrayController
    from .ui.wallpaper_host import WallpaperHost

    app.setStyleSheet(theme.STYLESHEET)
    app.setWindowIcon(make_icon())

    try:
        db = Database(paths.db_path())
    except DatabaseError as exc:
        instance.close()
        lock.unlock()
        return _fatal(f"{APP_NAME} cannot start", f"{exc}\n\nData folder: {paths.data_dir()}")

    ctrl = AppController(db)
    ctrl.host = WallpaperHost(ctrl)
    window = MainWindow(ctrl)
    tray = TrayController(ctrl, window)

    shutting_down = {"done": False}

    def shutdown() -> None:
        if shutting_down["done"]:
            return
        shutting_down["done"] = True
        log.info("shutting down")
        try:
            tray.hide()
            instance.close()
            shell.uninstall()
        finally:
            ctrl.shutdown()
            lock.unlock()

    from .platform.windows.shell_events import ShellEvents

    shell = ShellEvents(app)
    if shell.install():                                  # Windows only; event-driven Explorer/display recovery
        shell.event.connect(ctrl.host.on_shell_event)
    ctrl.quit_requested.connect(app.quit)
    try:    # Windows sign-out / shutdown must never be blocked by Sentinel's close handling
        app.commitDataRequest.connect(lambda _manager: setattr(ctrl, "quitting", True))
    except (AttributeError, TypeError):
        pass
    app.aboutToQuit.connect(shutdown)
    instance.command_received.connect(lambda cmd: window.show_panel() if cmd == "show" else ctrl.quit())

    ctrl.start()
    start_mode = "wallpaper" if args.wallpaper else "panel" if args.panel else ctrl.settings.get("start_mode")
    ctrl.set_mode(start_mode)
    open_panel = (start_mode == "panel" or ctrl.settings.get("open_panel_on_start")) and not args.background
    if args.background and start_mode == "panel" and not ctrl.tray_available:
        open_panel = True   # never leave the user with no visible way to reach or exit Sentinel
    if open_panel:
        window.show_panel()

    if args.quit_after is not None:                      # used by the automated smoke test
        QTimer.singleShot(int(args.quit_after * 1000), ctrl.quit)

    log.info("started (%s mode, desktop: %s)", ctrl.mode, ctrl.desktop_kind)
    code = app.exec()
    shutdown()
    return code


if __name__ == "__main__":
    sys.exit(main())
