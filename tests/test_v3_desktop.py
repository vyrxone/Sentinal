"""V3 wallpaper lifecycle regressions against the fake Windows desktop (logic only; real Explorer is a manual test)."""

import threading

from test_v2_desktop import fake, mk, patch_screens, scenes_alive, scenes_existing  # noqa: F401  (fixtures)
from sentinel.core.recovery import RecoveryPolicy
from sentinel.platform.windows import desktop_layer


def test_shutdown_during_pending_recovery_leaves_nothing_behind(ctrl, fake, monkeypatch, qapp, pump):
    patch_screens(monkeypatch, [mk("A", 0, 0, primary=True)])
    ctrl.host.show_wallpaper()
    ctrl.host._policy = RecoveryPolicy((40, 40, 40))
    attempts = []
    monkeypatch.setattr(desktop_layer, "locate_desktop_host", lambda: attempts.append(1) or (None, "starting"))
    ctrl.host.on_shell_event("explorer_restart")
    assert ctrl.host.kind == "reconnecting"
    ctrl.host.shutdown()                                   # app exits while a retry is pending
    n = len(attempts)
    pump(0.5)
    assert len(attempts) == n                              # the pending retry never fired
    assert ctrl.host.kind == "hidden" and scenes_existing(qapp) == []
    assert not ctrl.host._watch.isActive() and not ctrl.host._refit_timer.isActive()


def test_late_shell_events_after_shutdown_are_harmless(ctrl, fake, monkeypatch, qapp, pump):
    patch_screens(monkeypatch, [mk("A", 0, 0, primary=True)])
    ctrl.host.show_wallpaper()
    ctrl.host.shutdown()
    for kind in ("explorer_restart", "display_change", "dpi_change", "resume"):
        ctrl.host.on_shell_event(kind)
    pump(0.4)
    assert ctrl.host.kind == "hidden" and scenes_existing(qapp) == []


def test_retry_storm_is_bounded_in_attempts(ctrl, fake, monkeypatch, qapp, pump):
    patch_screens(monkeypatch, [mk("A", 0, 0, primary=True)])
    ctrl.host.show_wallpaper()
    ctrl.host._policy = RecoveryPolicy((5, 5, 5))
    attempts = []
    monkeypatch.setattr(desktop_layer, "locate_desktop_host", lambda: attempts.append(1) or (None, "down"))
    for _ in range(20):                                    # a flood of restart notifications
        ctrl.host.on_shell_event("explorer_restart")
    assert pump(3, lambda: ctrl.host.kind == "window")
    assert len(attempts) <= 3 + 3                          # at most the policy's tries (+ the rate limiter's fallback)
    assert len(scenes_alive(qapp)) == 1


def test_dpi_and_display_bursts_coalesce_into_one_reconcile(ctrl, fake, monkeypatch, qapp, pump):
    patch_screens(monkeypatch, [mk("A", 0, 0, primary=True)])
    ctrl.host.show_wallpaper()
    calls = []
    real = ctrl.host._do_reconcile
    monkeypatch.setattr(ctrl.host, "_do_reconcile", lambda: (calls.append(1), real())[1])
    ctrl.host._refit_timer.timeout.disconnect()
    ctrl.host._refit_timer.timeout.connect(ctrl.host._do_reconcile)
    for _ in range(30):
        ctrl.host.on_shell_event("dpi_change")
        ctrl.host.on_shell_event("display_change")
    pump(0.8)
    assert len(calls) == 1 and len(scenes_alive(qapp)) == 1


def test_sleep_resume_checks_the_layer_once_and_recovers(ctrl, fake, monkeypatch, qapp, pump):
    patch_screens(monkeypatch, [mk("A", 0, 0, primary=True)])
    ctrl.host.show_wallpaper()
    ctrl.host._policy = RecoveryPolicy((10, 10, 10))
    fake.restart()                                         # Explorer was restarted while the PC slept
    ctrl.host.on_shell_event("resume")
    assert pump(5, lambda: ctrl.host.kind == "embedded" and ctrl.host.status.label == "Embedded successfully")
    assert len(scenes_alive(qapp)) == 1


def test_repeated_start_stop_does_not_leak_threads_timers_or_scenes(tmp_path, fake, monkeypatch, qapp):
    from sentinel.controller import AppController
    from sentinel.storage.database import Database
    from sentinel.ui.wallpaper_host import WallpaperHost

    patch_screens(monkeypatch, [mk("A", 0, 0, primary=True)])
    before = threading.active_count()
    for i in range(5):
        c = AppController(Database(tmp_path / f"s{i}.db"))
        c.host = WallpaperHost(c)
        c.start()
        c.set_mode("wallpaper")
        c.request_analysis()
        c.check_project_paths()
        qapp.processEvents()
        c.shutdown()
        assert not c._analysis.isActive() and not c._analysis_periodic.isActive()
        assert not c._path_timer.isActive() and not c._health.isActive()
        assert c.jobs.active_count == 0
        c.deleteLater()
    qapp.processEvents()
    assert scenes_existing(qapp) == []
    assert threading.active_count() <= before + 1
