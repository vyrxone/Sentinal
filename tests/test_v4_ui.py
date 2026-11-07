"""AI Assistant page: consent gate, preview, transcript safety, settings tabs, cross-page buttons."""

import json
import time

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QInputDialog, QMessageBox

from ai_fakes import KEY, FakeKeyring, FakeTransport, gemini_ok, openai_ok, resp
from sentinel.ai.errors import ErrorKind
from sentinel.ui.ai_consent import ConsentDialog, ReviewResult
from sentinel.ui.main_window import MainWindow


@pytest.fixture
def window(ctrl, qapp):
    w = MainWindow(ctrl)
    w.show()
    qapp.processEvents()
    yield w
    ctrl.quitting = True
    w.close()


@pytest.fixture
def page(window, ctrl):
    ctrl.ai.secrets._backend = FakeKeyring()
    ctrl.ai.router.transport = FakeTransport({"generateContent": resp(200, gemini_ok("Plain answer <b>bold</b> https://evil.example")),
                                              "api.groq.com": resp(200, openai_ok("groq answer"))})
    for p in ("gemini", "groq"):
        ctrl.settings.set(f"ai_{p}_enabled", True)
        ctrl.settings.set(f"ai_{p}_model", f"{p}-model")
        ctrl.ai.set_key(p, KEY)
    ctrl.settings.set("ai_fallback_order", "groq")
    return window.pages[8]


def add_finding(ctrl):
    ctrl.events.add("security", "baseline_missing", "gone", severity="warning", path="/x")
    ctrl.run_analysis()
    for _ in range(100):
        from PySide6.QtWidgets import QApplication
        QApplication.processEvents(); time.sleep(0.01)
        if ctrl.findings.count():
            break


def accept_all(**kw):
    return ReviewResult(set(kw["preselected"]), True)


def test_page_is_registered_off_by_default_and_blocks_sending(window, ctrl, page, pump):
    ctrl.settings.set("ai_enabled", False)
    pump(0.1)
    assert not page.b_send.isEnabled() and "off" in page.status.text().lower()
    assert ctrl.settings.get("ai_enabled") is False
    window.goto(8); pump(0.05)
    assert window.stack.currentIndex() == 8


def test_cancelling_the_review_sends_nothing(ctrl, page, pump):
    ctrl.settings.set("ai_enabled", True)
    page.task.setCurrentIndex(page.task.findData("chat"))
    page.input.setPlainText("what does Sentinel do?")
    seen = {}
    page._review = lambda **kw: seen.update(kw) or None
    page.begin()
    pump(0.2)
    assert seen and ctrl.ai.router.transport.count == 0 and "nothing was sent" in page.status.text().lower()
    assert not ctrl.ai.busy


def test_confirmed_request_shows_provider_model_unverified_and_inert_text(ctrl, page, pump):
    ctrl.settings.set("ai_enabled", True)
    page.task.setCurrentIndex(page.task.findData("chat"))
    page.input.setPlainText("hello")
    page._review = accept_all
    page.begin()
    assert pump(5, lambda: page._last_answer)
    html = page.view.toHtml(); plain = page.view.toPlainText()
    assert "unverified" in plain.lower() and "Google Gemini" in plain and "gemini-model" in plain
    assert "<b>bold</b>" in plain                                     # shown literally, not interpreted as markup
    assert "href" not in html                                          # no link was created from the URL in the answer
    page.copy_answer()
    from PySide6.QtWidgets import QApplication
    assert QApplication.clipboard().text().startswith("Plain answer")


def test_fallback_is_visible_in_transcript_and_status(ctrl, page, pump):
    ctrl.settings.set("ai_enabled", True)
    ctrl.ai.router.transport.script["generateContent"] = [resp(503, {})]
    page.task.setCurrentIndex(page.task.findData("chat")); page.input.setPlainText("hi")
    page._review = accept_all
    page.begin(); assert pump(5, lambda: page._last_answer)
    assert "Fallback" in page.view.toPlainText() and "Groq" in page.status.text()


def test_error_is_shown_clearly_and_retry_reopens_the_review(ctrl, page, pump):
    ctrl.settings.set("ai_enabled", True)
    ctrl.ai.router.transport.script["generateContent"] = [resp(401, {"error": {"message": "bad"}})]
    page.task.setCurrentIndex(page.task.findData("chat")); page.input.setPlainText("hi")
    calls = []
    page._review = lambda **kw: calls.append(1) or accept_all(**kw)
    page.begin(); pump(2, lambda: "Error" in page.view.toPlainText())
    assert "rejected the API key" in page.status.text() and ctrl.ai.router.transport.count == 1
    assert page.b_retry.isEnabled()
    ctrl.ai.router.transport.script["generateContent"] = [resp(200, gemini_ok("fixed"))]
    page.retry(); assert pump(5, lambda: page._last_answer == "fixed") and len(calls) == 2


def test_stop_button_cancels(ctrl, page, pump):
    ctrl.settings.set("ai_enabled", True)
    ctrl.ai.router.transport.delay = 5
    page.task.setCurrentIndex(page.task.findData("chat")); page.input.setPlainText("hi")
    page._review = accept_all
    page.begin(); pump(0.3)
    assert page.b_stop.isEnabled() and not page.b_send.isEnabled()
    page.b_stop.click()
    assert pump(4, lambda: not ctrl.ai.busy) and "cancelled" in page.status.text().lower()


def test_finding_context_flow_preview_matches_payload(ctrl, page, pump):
    add_finding(ctrl)
    ctrl.settings.set("ai_enabled", True)
    page.task.setCurrentIndex(page.task.findData("finding")); page.reload_sources()
    assert page.source.count() >= 1
    captured = {}

    def review(**kw):
        dlg = ConsentDialog(page, plan=kw["plan"], items=kw["items"], history_count=0, compose=kw["compose"],
                            describe=lambda req: ("\n".join(m.content for m in req.messages), "{}"),
                            preselected=kw["preselected"])
        captured["preview"] = dlg.readable.toPlainText() if False else None
        dlg2 = dlg
        captured["items"] = [dlg.checks.item(i).text() for i in range(dlg.checks.count())]
        dlg2.selected()
        # uncheck the related events item: it must disappear from the request
        for i in range(dlg.checks.count()):
            if dlg.checks.item(i).data(Qt.ItemDataRole.UserRole).startswith("finding_events"):
                dlg.checks.item(i).setCheckState(Qt.CheckState.Unchecked)
        captured["readable"] = dlg.readable.toPlainText()
        return dlg.result_value()

    page._review = review
    page.begin(); assert pump(5, lambda: page._last_answer)
    sent = ctrl.ai.router.transport.calls[-1]["json"]
    sent_text = sent["contents"][-1]["parts"][0]["text"]
    assert "UNTRUSTED DATA BEGIN" in sent_text and "BASE-001" in sent_text or "baseline" in sent_text.lower()
    assert sent_text in captured["readable"] and "related event" not in sent_text.lower()
    assert any("Finding:" in t for t in captured["items"])


def test_dialog_defaults_cancel_and_lists_fallback_providers(qapp, ctrl):
    from sentinel.ai import context_builder as cb
    items = cb.ContextBuilder().events([])
    d = ConsentDialog(None, plan=[("Google Gemini", "m1"), ("Groq", "m2")], items=items, history_count=2,
                      compose=lambda s, h: cb.compose_request("chat", "q", items, s), describe=lambda r: ("r", "e"))
    assert d.cancel.isDefault() and not d.send.isDefault()
    assert "Google Gemini" in d.banner.text() and "Groq" in d.banner.text() and "SAME request" in d.banner.text()
    assert "own terms" in d.banner.text() and d.history.isChecked()


def test_ask_ai_buttons_open_the_page_and_select_without_sending(ctrl, window, page, pump):
    add_finding(ctrl)
    fp = window.pages[7]
    window.goto(7); pump(0.3); fp.reload(); pump(0.3)
    assert fp.model.rowCount() >= 1
    fp.table.selectRow(0); pump(0.1)
    fp.b_ai.click(); pump(0.1)
    assert window.stack.currentIndex() == 8 and page.task_key() == "finding" and page.source.count() >= 1
    assert ctrl.ai.router.transport.count == 0
    ep = window.pages[4]
    ctrl.events.add("security", "baseline_changed", "file changed", severity="warning", path="/f")
    window.goto(4); pump(0.3); ep.reload(); pump(0.2)
    ep.table.selectRow(0); ep.b_ai.click(); pump(0.2)
    assert page.task_key() in ("integrity", "events") and ctrl.ai.router.transport.count == 0


def test_provider_tab_key_set_masked_delete_and_no_plaintext_fallback(ctrl, page, dialogs, monkeypatch):
    box = page.providers.boxes["openrouter"]
    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: (KEY2, True)))
    box._set_key()
    assert ctrl.ai.secrets.has_key("openrouter") and KEY2 not in box.key_state.text() and "Credential Manager" in box.key_state.text()
    box._delete_key()
    assert not ctrl.ai.secrets.has_key("openrouter")
    ctrl.ai.secrets._backend = None
    page.providers.refresh()
    assert not box.b_key.isEnabled() and "plain text" in page.providers.storage.text()
    ctrl.ai.secrets._backend = FakeKeyring()
    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: ("bad key with spaces", True)))
    box._set_key()
    assert dialogs["warning"] and not ctrl.ai.secrets.has_key("openrouter")


KEY2 = "TESTKEY-zzzzzzzz-yyyyyyyy-xxxxxxxx-9876"


def test_test_connection_button_sends_no_personal_data(ctrl, page, pump):
    ctrl.settings.set("ai_enabled", True)      # V5: connection tests make no request while AI is off
    t = ctrl.ai.router.transport
    t.script["/v1beta/models"] = [resp(200, {"models": [{"name": "models/a", "supportedGenerationMethods": ["generateContent"]}]})]
    box = page.providers.boxes["gemini"]
    box.b_test.click()
    assert pump(5, lambda: "Connection" in box.test_result.text())
    assert t.calls[-1]["body"] is None and t.calls[-1]["method"] == "GET"


def test_model_list_fetch_fills_the_editable_combo(ctrl, page, pump):
    ctrl.settings.set("ai_enabled", True)      # V5: the model list is only fetched while AI is on
    ctrl.ai.router.transport.script["api.groq.com"] = [resp(200, {"data": [{"id": "m-a"}, {"id": "m-b"}]})]
    box = page.providers.boxes["groq"]
    box.b_models.click()
    assert pump(5, lambda: box.model.count() == 2) and box.model.itemText(0) == "m-a"


def test_settings_tabs_persist_validate_and_warn_about_redaction(ctrl, page, pump):
    pt = page.privacy
    pt.paths.setChecked(False)
    assert ctrl.settings.get("ai_redact_paths") is False and "paths" in pt.redact_warn.text() and not pt.redact_warn.isHidden()
    pt.paths.setChecked(True)
    assert pt.redact_warn.isHidden()
    pt.history.setChecked(True); assert ctrl.settings.get("ai_history_enabled") is True
    pt.spins["ai_max_response"].setValue(1234); pt.spins["ai_max_response"].editingFinished.emit()
    assert ctrl.settings.get("ai_max_response") == 1234
    page.providers.primary.setCurrentIndex(page.providers.primary.findData("groq"))
    assert ctrl.settings.get("ai_primary") == "groq"
    page.providers.fb1.setCurrentIndex(page.providers.fb1.findData("gemini"))
    assert ctrl.settings.get("ai_fallback_order") == "gemini"
    page.providers.enable.setChecked(True); assert ctrl.settings.get("ai_enabled") is True


def test_usage_tab_says_unavailable_instead_of_inventing_numbers(ctrl, page, pump):
    ctrl.settings.set("ai_enabled", True)
    page.usage.refresh()
    assert "unavailable" in page.usage.text.toPlainText() and "not estimated" in page.usage.text.toPlainText()
    ctrl.ai.router.transport.script["generateContent"] = [resp(200, gemini_ok(usage=False))]
    page.task.setCurrentIndex(page.task.findData("chat")); page.input.setPlainText("hi"); page._review = accept_all
    page.begin(); assert pump(5, lambda: page._last_answer)
    page.usage.refresh()
    assert "not reported by the provider" in page.usage.text.toPlainText()


def test_history_clear_and_reset_from_the_privacy_tab(ctrl, page, dialogs, pump):
    ctrl.settings.set("ai_enabled", True); ctrl.settings.set("ai_history_enabled", True)
    page.task.setCurrentIndex(page.task.findData("chat")); page.input.setPlainText("hi"); page._review = accept_all
    page.begin(); assert pump(5, lambda: page._last_answer)
    assert ctrl.ai.store.count() == 1 and page.privacy.convs.count() == 1
    page.privacy._clear(); pump(0.1)
    assert ctrl.ai.store.count() == 0 and page.privacy.convs.count() == 0
    page.privacy._reset()
    assert ctrl.settings.get("ai_enabled") is False and not ctrl.ai.secrets.has_key("gemini")   # dialogs answer Yes to both


def test_task_suggestions_require_explicit_confirmation(ctrl, page, dialogs, pump):
    ctrl.settings.set("ai_enabled", True)
    p = ctrl.projects.add("Atlas", "")
    page.reload_sources(); page.task.setCurrentIndex(page.task.findData("tasks")); pump(0.1)
    ctrl.ai.router.transport.script["generateContent"] = [resp(200, gemini_ok("1. Write tests\n2. Update docs\n3. Tag release"))]
    page._review = accept_all
    page.begin(); assert pump(5, lambda: page._suggestions)
    assert ctrl.tasks.list_tasks(p.id) == []                              # nothing is added automatically
    page.suggest_box.item(0).setCheckState(Qt.CheckState.Checked)
    page._add_tasks()
    assert [t.title for t in ctrl.tasks.list_tasks(p.id)] == ["Write tests"]


def test_new_context_only_mode_skips_review_for_followups_only(ctrl, page, pump):
    ctrl.settings.set("ai_enabled", True); ctrl.settings.set("ai_consent_mode", "new_context_only")
    page.task.setCurrentIndex(page.task.findData("chat"))
    reviews = []
    page._review = lambda **kw: reviews.append(1) or accept_all(**kw)
    page.input.setPlainText("first"); page.begin(); assert pump(5, lambda: page._last_answer)
    page.input.setPlainText("second"); page.begin(); assert pump(5, lambda: ctrl.ai.router.transport.count == 2 and not ctrl.ai.busy)
    assert len(reviews) == 1                                              # the follow-up carried no new context
    page.new_conversation()
    page.input.setPlainText("third"); page.begin(); pump(1)
    assert len(reviews) == 2


def test_performance_page_unaffected_and_ai_idle_has_zero_workers_running(ctrl, page):
    snap = ctrl.ai.status_snapshot()
    assert snap["active"] == 0 and snap["queue_depth"] == 0 and snap["workers"] == 1 and snap["session_requests"] == 0


def test_events_page_live_refresh_on_alert_change_regression(ctrl, window, pump, capfd):
    """V3 defect: alerts_changed (no argument) was connected to a slot that required one -> TypeError, no live refresh."""
    ep = window.pages[4]
    window.goto(4); ep.show(); pump(0.2)
    ep.live.setChecked(True)
    ep._live_timer.stop()
    ctrl.alerts_changed.emit()
    assert ep._live_timer.isActive()
    assert "TypeError" not in capfd.readouterr().err


def test_performance_page_and_diagnostics_report_ai_state_without_secrets(ctrl, window, pump):
    from sentinel.diagnostics import build_report
    ctrl.ai.secrets._backend = FakeKeyring(); ctrl.ai.set_key("groq", KEY)
    rep = build_report(ctrl)
    assert "AI assistant: off (default)" in rep and KEY not in rep
    ctrl.settings.set("ai_enabled", True)
    assert "1 worker, 0 running, 0 queued" in build_report(ctrl) and "unavailable" in build_report(ctrl)
    window.goto(6); pump(0.3)
    assert "worker" in window.pages[6].rows["AI assistant"].text()
