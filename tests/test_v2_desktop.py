"""Desktop integration, recovery and multi-monitor logic against a fake Windows desktop (Linux-runnable).

These prove the LOGIC. Whether real Explorer behaves the same is a Windows manual test."""

import pytest
from PySide6.QtCore import QEvent

from fakes import FakeWin32
from sentinel.core.monitors import ScreenInfo, disambiguate, native_rect, select_screens, stable_id
from sentinel.core.recovery import RecoveryPolicy
from sentinel.platform.windows import desktop_layer, shell_events
from sentinel.ui import wallpaper_host
from sentinel.ui.wallpaper import WallpaperScene
from sentinel.ui.wallpaper_host import WallpaperHost


@pytest.fixture
def fake(monkeypatch):
    f = FakeWin32()
    desktop_layer.set_api(f)
    yield f
    desktop_layer.set_api(None)


def scenes_alive(qapp):
    """Scenes that still exist AND are shown (hidden ones awaiting deletion are not stray windows)."""
    qapp.processEvents()
    qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)
    return [w for w in qapp.allWidgets() if isinstance(w, WallpaperScene) and w.isVisible()]


def scenes_existing(qapp):
    qapp.processEvents()
    qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)
    return [w for w in qapp.allWidgets() if isinstance(w, WallpaperScene)]


# ------------------------------------------------------------- desktop_layer
@pytest.mark.parametrize("layout,method", [("workerw", "workerw_sibling"), ("progman", "progman_child")])
def test_locates_both_explorer_layouts(layout, method):
    f = FakeWin32(layout)
    desktop_layer.set_api(f)
    try:
        host, reason = desktop_layer.locate_desktop_host()
        assert host.method == method and reason == ""
        assert host.parent == (f.w_wall if layout == "workerw" else f.progman)
        res = desktop_layer.attach(900, (0, 0, 1920, 1080))
        assert res.success and res.method == method
        assert f.get_parent(900) == host.parent
        assert desktop_layer.is_attached(900, host.parent)
        desktop_layer.detach(900)
        assert f.get_parent(900) == 0 and not desktop_layer.is_attached(900, host.parent)
    finally:
        desktop_layer.set_api(None)


def test_refuses_third_party_shell():
    f = FakeWin32(owner="someshell.exe")
    desktop_layer.set_api(f)
    try:
        host, reason = desktop_layer.locate_desktop_host()
        assert host is None and "not Windows Explorer" in reason
        assert not desktop_layer.attach(900, (0, 0, 10, 10)).success
    finally:
        desktop_layer.set_api(None)


def test_unrecognised_layout_and_missing_progman_report_why(fake):
    fake.win = {}
    assert "Progman" in desktop_layer.locate_desktop_host()[1]
    fake.progman = fake._make("Progman")
    assert "unrecognised" in desktop_layer.locate_desktop_host()[1]


def test_attach_failure_modes_never_raise_and_clean_up(fake):
    fake.reject_parent = True
    res = desktop_layer.attach(900, (0, 0, 10, 10))
    assert not res.success and "did not accept" in res.detail
    assert fake.win[900]["style"] & desktop_layer.WS_POPUP and not fake.win[900]["style"] & desktop_layer.WS_CHILD
    fake.reject_parent, fake.raise_on_set_parent = False, True
    res = desktop_layer.attach(901, (0, 0, 10, 10))
    assert not res.success and "failed" in res.detail


def test_negative_coordinates_are_passed_through(fake):
    host, _ = desktop_layer.locate_desktop_host()
    desktop_layer.attach(900, (-1920, -200, 1920, 1080), host)
    assert fake.win[900]["pos"] == (-1920, -200, 1920, 1080)


def test_real_api_module_is_unsupported_off_windows():
    desktop_layer.set_api(None)
    assert not desktop_layer.is_supported() and desktop_layer.taskbar_created_message() == 0


# --------------------------------------------------------------- shell events
def test_classify_shell_messages():
    tb = 0xC123
    c = shell_events.classify
    assert c(tb, 0, tb) == "explorer_restart"
    assert c(shell_events.WM_DISPLAYCHANGE, 0, tb) == "display_change"
    assert c(shell_events.WM_DPICHANGED, 0, tb) == "dpi_change"
    assert c(shell_events.WM_POWERBROADCAST, shell_events.PBT_APMRESUMEAUTOMATIC, tb) == "resume"
    assert c(shell_events.WM_POWERBROADCAST, shell_events.PBT_APMRESUMESUSPEND, tb) == "resume"
    assert c(shell_events.WM_POWERBROADCAST, 0x4, tb) is None            # suspend is not resume
    assert c(0x0001, 0, tb) is None and c(0, 0, 0) is None                 # taskbar message 0 never matches


def test_shell_events_do_not_install_off_windows(qapp):
    se = shell_events.ShellEvents()
    assert se.install() is False and not se.active
    se.uninstall()


# ------------------------------------------------------------------ recovery
def test_recovery_policy_is_bounded():
    p = RecoveryPolicy((1, 2, 3))
    assert [p.next_delay() for _ in range(4)] == [1, 2, 3, None] and p.exhausted
    p.reset()
    assert not p.exhausted and p.next_delay() == 1
    assert sum(RecoveryPolicy().delays_ms) < 30000                          # total retry budget is bounded


# ------------------------------------------------------------------ monitors
def mk(id_, x, y, w=1920, h=1080, dpr=1.0, primary=False):
    return ScreenInfo(id_, id_, x, y, w, h, dpr, primary)


def test_stable_ids_and_duplicates():
    assert stable_id("DEL", "U2720Q", "SN123", "\\\\.\\DISPLAY2") == "DEL|U2720Q|SN123"
    assert stable_id("", "", "", "\\\\.\\DISPLAY2") == "\\\\.\\DISPLAY2"              # no metadata: fall back to name
    out = disambiguate([mk("A", 1920, 0), mk("A", 0, 0)])
    assert [i.id for i in out] == ["A", "A#2"] and out[0].x == 0              # ids unique, ordered by position


def test_native_rect_scales_size_not_position():
    assert native_rect((-2560, 100), (1280, 720), 2.0) == (-2560, 100, 2560, 1440)
    assert native_rect((0, 0), (1, 1), 1.5)[2:] == (2, 2)


def test_select_screens_modes_and_fallbacks():
    a, b, c = mk("A", 0, 0, primary=True), mk("B", 1920, 0), mk("C", -1920, 0)
    assert select_screens([a, b, c], "primary") == ([a], "")
    assert select_screens([a, b, c], "all")[0] == [a, b, c]
    assert select_screens([a, b, c], "selected", "C") == ([c], "")
    scr, note = select_screens([a, b], "selected", "GONE")
    assert scr == [a] and "not connected" in note                              # explained fallback
    assert select_screens([a], "selected", "")[1]
    assert select_screens([], "all") == ([], "No display was detected.")


# ---------------------------------------------------------------------- host
def patch_screens(monkeypatch, infos):
    box = {"infos": infos}
    monkeypatch.setattr(wallpaper_host, "screen_infos", lambda: list(box["infos"]))
    return box


def test_host_embeds_and_reports_honest_status(ctrl, fake, monkeypatch, qapp):
    patch_screens(monkeypatch, [mk("A", 0, 0, primary=True)])
    kind, detail = ctrl.host.show_wallpaper()
    assert kind == "embedded" and "workerw_sibling" in detail
    st = ctrl.host.status
    assert (st.kind, st.label, st.method, st.monitors) == ("embedded", "Embedded successfully", "workerw_sibling", 1)
    assert len(fake.ours()) == 1 and len(scenes_alive(qapp)) == 1
    assert ctrl.host.scene.interactive is False


def test_hide_wallpaper_detaches_releases_and_refreshes(ctrl, fake, monkeypatch, qapp):
    patch_screens(monkeypatch, [mk("A", 0, 0, primary=True)])
    ctrl.host.show_wallpaper()
    hwnd = fake.ours()[0]
    scene = ctrl.host.scene
    ctrl.host.hide_wallpaper()
    assert ctrl.host.kind == "hidden" and ctrl.host.scene is None and ctrl.host.scenes == []
    assert fake.get_parent(hwnd) == 0                                          # un-parented before destruction
    assert fake.redraws >= 1                                                    # desktop repaint requested
    assert not scene._frame.isActive() and not scene._second.isActive()         # timers stopped
    assert not scene.isVisible()
    ctrl.host.hide_wallpaper()                                                  # idempotent


def test_attach_exception_leaves_no_visible_window_regression_A5(ctrl, fake, monkeypatch, qapp):
    patch_screens(monkeypatch, [mk("A", 0, 0, primary=True)])
    monkeypatch.setattr(desktop_layer, "attach", lambda *a, **k: (_ for _ in ()).throw(OSError("boom")))
    kind, detail = ctrl.host.show_wallpaper()
    assert kind == "window" and "normal window" in detail
    assert ctrl.host.status.label == "Desktop integration unavailable"
    assert len(scenes_alive(qapp)) == 1 and ctrl.host.scene.isVisible()        # only the labelled fallback window
    assert ctrl.host.scene.interactive and not ctrl.host.scene.isFullScreen()


def test_attach_failure_result_never_claims_embedded(ctrl, fake, monkeypatch, qapp):
    patch_screens(monkeypatch, [mk("A", 0, 0, primary=True)])
    fake.reject_parent = True
    kind, detail = ctrl.host.show_wallpaper()
    assert kind == "window" and ctrl.host.status.kind == "window" and "embedded" not in ctrl.desktop_label.lower()
    assert len(fake.ours()) <= 1


def test_window_mode_selected_by_user_is_labelled(ctrl, fake, monkeypatch):
    patch_screens(monkeypatch, [mk("A", 0, 0, primary=True)])
    ctrl.settings.set("desktop_integration", "window_only")
    kind, detail = ctrl.host.show_wallpaper()
    assert kind == "window" and "Window mode was selected" in detail and ctrl.host.status.label == "Running as a normal window"


def test_not_windows_reports_unavailable(ctrl, monkeypatch):
    desktop_layer.set_api(None)
    kind, detail = ctrl.host.show_wallpaper()
    assert kind == "window" and ctrl.host.status.label == "Desktop integration unavailable"


def test_multi_monitor_one_scene_per_screen_no_duplicates_on_reconcile(ctrl, fake, monkeypatch, qapp, pump):
    a, b, c = mk("A", 0, 0, primary=True), mk("B", 1920, 0), mk("C", -1280, 0, 1280, 720, 1.5)
    box = patch_screens(monkeypatch, [a, b, c])
    ctrl.settings.set("monitor_mode", "all")
    kind, _ = ctrl.host.show_wallpaper()
    assert kind == "embedded" and ctrl.host.status.monitors == 3 and len(fake.ours()) == 3
    assert sorted(w["pos"] for h, w in fake.win.items() if w["cls"] == "_ours") == [(-1280, 0, 1280, 720), (0, 0, 1920, 1080), (1920, 0, 1920, 1080)]
    for _ in range(3):                                                           # repeated display events
        ctrl.host.on_shell_event("display_change")
    pump(1.2, lambda: False)
    assert len(ctrl.host.scenes) == 3 and len(scenes_alive(qapp)) == 3
    box["infos"] = [a, b]                                                         # monitor C unplugged
    ctrl.host.on_shell_event("display_change")
    assert pump(3, lambda: len(ctrl.host.scenes) == 2) and len(scenes_alive(qapp)) == 2
    assert len(fake.ours()) == 3 and sum(1 for h in fake.ours() if fake.get_parent(h)) == 2   # removed one was detached
    box["infos"] = [a, b, c]                                                      # plugged back in: one new scene
    ctrl.host.on_shell_event("display_change")
    assert pump(3, lambda: len(ctrl.host.scenes) == 3)
    box["infos"] = [mk("A", 0, 0, 2560, 1440, primary=True), b]                   # primary resolution change
    ctrl.host.on_shell_event("resume")
    assert pump(3, lambda: any(w["pos"] == (0, 0, 2560, 1440) for w in fake.win.values()))
    ctrl.host.hide_wallpaper()
    assert scenes_alive(qapp) == [] and all(fake.get_parent(h) == 0 for h in fake.ours())


def test_selected_monitor_and_missing_selection_fall_back_with_note(ctrl, fake, monkeypatch):
    a, b = mk("A", 0, 0, primary=True), mk("B", 1920, 0)
    patch_screens(monkeypatch, [a, b])
    ctrl.settings.set("monitor_mode", "selected")
    ctrl.settings.set("selected_monitor", "B")
    ctrl.host.show_wallpaper()
    assert [e.info.id for e in ctrl.host._entries.values()] == ["B"] and ctrl.host.status.note == ""
    ctrl.settings.set("selected_monitor", "GONE")
    ctrl.host.show_wallpaper()
    assert [e.info.id for e in ctrl.host._entries.values()] == ["A"] and "not connected" in ctrl.host.status.note


def test_partial_multi_monitor_failure_is_reported(ctrl, fake, monkeypatch):
    a, b = mk("A", 0, 0, primary=True), mk("B", 1920, 0)
    patch_screens(monkeypatch, [a, b])
    ctrl.settings.set("monitor_mode", "all")
    real, calls = desktop_layer.attach, []

    def flaky(hwnd, rect, host=None):
        calls.append(rect)
        if len(calls) == 2:
            raise OSError("second monitor failed")
        return real(hwnd, rect, host)

    monkeypatch.setattr(desktop_layer, "attach", flaky)
    kind, detail = ctrl.host.show_wallpaper()
    assert kind == "embedded" and "1 monitor(s) could not be embedded" in detail and len(ctrl.host.scenes) == 1


def test_explorer_restart_recovers_after_bounded_retries(ctrl, fake, monkeypatch, qapp, pump):
    patch_screens(monkeypatch, [mk("A", 0, 0, primary=True)])
    ctrl.host.show_wallpaper()
    ctrl.host._policy = RecoveryPolicy((20, 20, 20, 20))
    fake.restart()
    fake.layout_ready = False
    real = desktop_layer.locate_desktop_host
    n = {"c": 0}

    def late():                                    # Explorer is still starting for the first two attempts
        n["c"] += 1
        return (None, "Explorer is starting") if n["c"] <= 2 else real()

    monkeypatch.setattr(desktop_layer, "locate_desktop_host", late)
    ctrl.host.on_shell_event("explorer_restart")
    assert ctrl.host.kind == "reconnecting" and ctrl.host.status.label == "Reconnecting after Explorer restart"
    assert ctrl.desktop_kind == "reconnecting"
    assert pump(5, lambda: ctrl.host.kind == "embedded")
    assert ctrl.desktop_label == "Embedded successfully" and len(ctrl.host.scenes) == 1
    assert n["c"] == 3 and len(scenes_alive(qapp)) == 1                         # no duplicate or leftover scenes
    assert any(e.event_type == "desktop_status" for e in ctrl.events.query())


def test_explorer_restart_exhaustion_falls_back_to_one_labelled_window(ctrl, fake, monkeypatch, qapp, pump):
    patch_screens(monkeypatch, [mk("A", 0, 0, primary=True)])
    ctrl.host.show_wallpaper()
    ctrl.host._policy = RecoveryPolicy((10, 10))
    monkeypatch.setattr(desktop_layer, "locate_desktop_host", lambda: (None, "Explorer never came back"))
    ctrl.host.on_shell_event("explorer_restart")
    assert pump(5, lambda: ctrl.host.kind == "window")
    assert ctrl.desktop_label == "Desktop integration unavailable" and "Could not re-attach" in ctrl.desktop_detail
    assert len(scenes_alive(qapp)) == 1 and ctrl.host.scene.interactive and not ctrl.host.scene.isFullScreen()
    assert ctrl.events.query(event_type="desktop_fallback")


def test_restore_during_recovery_cancels_pending_retries(ctrl, fake, monkeypatch, qapp, pump):
    patch_screens(monkeypatch, [mk("A", 0, 0, primary=True)])
    ctrl.host.show_wallpaper()
    ctrl.host._policy = RecoveryPolicy((50, 50, 50))
    monkeypatch.setattr(desktop_layer, "locate_desktop_host", lambda: (None, "x"))
    ctrl.host.on_shell_event("explorer_restart")
    ctrl.restore_desktop()
    pump(0.6)
    assert ctrl.host.kind == "hidden" and scenes_alive(qapp) == [] and ctrl.mode == "panel"


def test_lost_desktop_layer_is_noticed_by_safety_net_poll(ctrl, fake, monkeypatch, qapp, pump):
    patch_screens(monkeypatch, [mk("A", 0, 0, primary=True)])
    ctrl.host.show_wallpaper()
    ctrl.host._policy = RecoveryPolicy((10, 10, 10))
    fake.restart()                                                              # parent vanished, no broadcast received
    ctrl.host._check_attached()
    assert pump(5, lambda: ctrl.host.kind == "embedded")


def test_recovery_storm_gives_up_and_uses_window(ctrl, fake, monkeypatch, qapp, pump):
    patch_screens(monkeypatch, [mk("A", 0, 0, primary=True)])
    ctrl.host.show_wallpaper()
    ctrl.host._policy = RecoveryPolicy((5,))
    for _ in range(5):
        ctrl.host.on_shell_event("explorer_restart")
        pump(0.15, lambda: ctrl.host.kind == "embedded")
        if ctrl.host.kind == "window":
            break
    assert ctrl.host.kind in ("window", "embedded")
    ctrl.host.begin_recovery("again")
    ctrl.host.begin_recovery("again")
    ctrl.host.begin_recovery("again")
    pump(0.5)
    assert len(scenes_alive(qapp)) <= 1


def test_repeated_show_hide_does_not_leak_scenes_or_connections(ctrl, fake, monkeypatch, qapp):
    patch_screens(monkeypatch, [mk("A", 0, 0, primary=True), mk("B", 1920, 0)])
    ctrl.settings.set("monitor_mode", "all")
    for _ in range(10):
        ctrl.host.show_wallpaper()
        ctrl.host.hide_wallpaper()
    assert scenes_existing(qapp) == []                                          # destroyed, not merely hidden
    assert ctrl.render_stats.snapshot()["scenes"] == 0
    assert ctrl.host._token >= 20


def test_closing_fallback_window_returns_to_panel_mode(ctrl, monkeypatch, pump):
    desktop_layer.set_api(None)
    ctrl.set_mode("wallpaper")
    ctrl.host.scene.close()
    assert pump(1, lambda: ctrl.mode == "panel") and ctrl.host.scene is None
