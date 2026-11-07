"""AI Assistant settings tabs: providers and keys, privacy and limits, usage."""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFrame, QHBoxLayout, QInputDialog, QLineEdit, QListWidget,
                               QListWidgetItem, QMessageBox, QPlainTextEdit, QSpinBox, QVBoxLayout, QWidget)

from ...ai.proxy import ProxyConfigError, validate_host

from ...ai.errors import InvalidKey, SecureStorageUnavailable
from ...ai.models import Health
from ...ai.providers import PROVIDER_CLASSES, PROVIDER_IDS
from ...storage.settings_store import SettingError
from .. import theme
from ..widgets import make_button, make_label

NAMES = {pid: cls.display_name for pid, cls in PROVIDER_CLASSES.items()}
HEALTH_COLOR = {Health.AVAILABLE: theme.GOLD, Health.AUTH_ERROR: theme.CRIMSON_BRIGHT, Health.RATE_LIMITED: theme.CRIMSON_BRIGHT,
                Health.OFFLINE: theme.CRIMSON_BRIGHT}
WARNING = ("API providers process the data you send them under their own policies. Free tiers and rate limits can change "
           "at any time. Sentinel never sends anything without showing you the exact request first.")


def set_setting(ctrl, key: str, value, parent: Optional[QWidget] = None) -> bool:
    try:
        ctrl.settings.set(key, value)
        return True
    except SettingError as exc:
        QMessageBox.warning(parent, "Setting not changed", str(exc))
        return False


class ProviderBox(QFrame):
    def __init__(self, ctrl, pid: str) -> None:
        super().__init__()
        self.ctrl, self.pid = ctrl, pid
        self.setProperty("card", True)
        v = QVBoxLayout(self)
        top = QHBoxLayout()
        self.enabled = QCheckBox(f"Use {NAMES[pid]}")
        self.health = make_label("", "muted")
        top.addWidget(self.enabled); top.addStretch(1); top.addWidget(self.health)
        v.addLayout(top)
        keyrow = QHBoxLayout()
        self.key_state = make_label("", "muted")
        self.b_key = make_button("Set / replace key…")
        self.b_del = make_button("Delete key", danger=True)
        keyrow.addWidget(self.key_state, 1); keyrow.addWidget(self.b_key); keyrow.addWidget(self.b_del)
        v.addLayout(keyrow)
        mrow = QHBoxLayout()
        self.model = QComboBox(); self.model.setEditable(True)
        self.model.lineEdit().setPlaceholderText("Model ID (copy one from the provider's model list)")
        self.b_models = make_button("Fetch model list", tip="Asks the provider for its model list. Sends no personal data.")
        self.timeout = QSpinBox(); self.timeout.setRange(3, 120); self.timeout.setSuffix(" s")
        self.timeout.setToolTip("Request timeout")
        mrow.addWidget(self.model, 1); mrow.addWidget(self.b_models); mrow.addWidget(self.timeout)
        v.addLayout(mrow)
        trow = QHBoxLayout()
        self.b_test = make_button("Test connection", tip="Checks the key with a read-only request. Sends no events, findings or project data.")
        self.test_result = make_label("", "muted", wrap=True)
        link = make_label(f"Get a key directly from the provider: {PROVIDER_CLASSES[pid].help_url}", "muted")
        trow.addWidget(self.b_test); trow.addWidget(self.test_result, 1)
        v.addLayout(trow); v.addWidget(link)
        self.enabled.toggled.connect(lambda on: set_setting(ctrl, f"ai_{pid}_enabled", on, self))
        self.model.lineEdit().editingFinished.connect(self._save_model)
        self.timeout.editingFinished.connect(lambda: set_setting(ctrl, f"ai_{pid}_timeout", self.timeout.value(), self))
        self.b_key.clicked.connect(self._set_key)
        self.b_del.clicked.connect(self._delete_key)
        self.b_test.clicked.connect(self._test)
        self.b_models.clicked.connect(self._models)
        self.refresh()

    def _save_model(self) -> None:
        text = self.model.currentText().strip()
        if text != self.ctrl.settings.get(f"ai_{self.pid}_model"):
            set_setting(self.ctrl, f"ai_{self.pid}_model", text, self)

    def refresh(self) -> None:
        ai = self.ctrl.ai
        s = self.ctrl.settings
        for w in (self.enabled, self.model, self.timeout):
            w.blockSignals(True)
        self.enabled.setChecked(bool(s.get(f"ai_{self.pid}_enabled")))
        if not self.model.hasFocus() and not self.model.lineEdit().hasFocus():
            self.model.setCurrentText(s.get(f"ai_{self.pid}_model"))
        self.timeout.setValue(int(s.get(f"ai_{self.pid}_timeout")))
        for w in (self.enabled, self.model, self.timeout):
            w.blockSignals(False)
        h = ai.health(self.pid)
        self.health.setText(h.value)
        self.health.setStyleSheet(f"color:{HEALTH_COLOR.get(h, theme.MUTED)};")
        st = ai.secrets.status(self.pid)
        self.key_state.setText(f"{st.label}: {ai.secrets.masked(self.pid)}" if st.present else st.label)
        self.b_key.setEnabled(ai.secrets.storage_available)
        self.b_key.setToolTip("" if ai.secrets.storage_available else ai.secrets.storage_note())
        self.b_del.setEnabled(st.source == "credential_manager")
        self.b_test.setEnabled(st.present and ai.enabled)
        self.b_test.setToolTip("" if ai.enabled else "Turn on 'Enable AI Assistant' first; while AI is off Sentinel makes no "
                                                      "network connection.")

    def _set_key(self) -> None:
        text, ok = QInputDialog.getText(self, f"{NAMES[self.pid]} API key", "Paste the API key (it is stored in the Windows "
                                        "Credential Manager, never in Sentinel's files):", QLineEdit.EchoMode.Password)
        if not ok or not text.strip():
            return
        try:
            self.ctrl.ai.set_key(self.pid, text)
        except (InvalidKey, SecureStorageUnavailable) as exc:
            QMessageBox.warning(self, "Key not saved", str(exc))
        self.refresh()

    def _delete_key(self) -> None:
        if QMessageBox.question(self, "Delete key", f"Delete the stored {NAMES[self.pid]} API key?") == QMessageBox.StandardButton.Yes:
            self.ctrl.ai.delete_key(self.pid)
        self.refresh()

    def _test(self) -> None:
        self.test_result.setText("Testing…")
        self.ctrl.ai.test_connection(self.pid, lambda res: (self.test_result.setText(res.message), self.refresh()))

    def _models(self) -> None:
        self.test_result.setText("Asking the provider for its model list…")

        def ok(ids: list) -> None:
            self.model.blockSignals(True)
            cur = self.model.currentText()
            self.model.clear(); self.model.addItems(ids[:300]); self.model.setCurrentText(cur)
            self.model.blockSignals(False)
            self.test_result.setText(f"{len(ids)} model(s) listed by the provider. Pick one, then press Enter.")

        self.ctrl.ai.list_models(self.pid, ok, lambda m: self.test_result.setText(m))


class ProvidersTab(QWidget):
    def __init__(self, ctrl) -> None:
        super().__init__()
        self.ctrl = ctrl
        v = QVBoxLayout(self)
        self.enable = QCheckBox("Enable the AI Assistant (off by default)")
        v.addWidget(self.enable)
        v.addWidget(make_label(WARNING, "banner", wrap=True))
        self.storage = make_label(ctrl.ai.secrets.storage_note(), "muted", wrap=True)
        v.addWidget(self.storage)
        row = QHBoxLayout()
        self.primary = QComboBox()
        self.fb1, self.fb2 = QComboBox(), QComboBox()
        for pid in PROVIDER_IDS:
            self.primary.addItem(NAMES[pid], pid)
        for c in (self.fb1, self.fb2):
            c.addItem("(none)", "")
            for pid in PROVIDER_IDS:
                c.addItem(NAMES[pid], pid)
        for text, w in (("Primary", self.primary), ("Fallback 1", self.fb1), ("Fallback 2", self.fb2)):
            row.addWidget(make_label(text, "muted")); row.addWidget(w)
        row.addStretch(1)
        v.addLayout(row)
        self.boxes = {pid: ProviderBox(ctrl, pid) for pid in PROVIDER_IDS}
        for b in self.boxes.values():
            v.addWidget(b)
        v.addStretch(1)
        self.enable.toggled.connect(lambda on: set_setting(ctrl, "ai_enabled", on, self))
        for c in (self.primary, self.fb1, self.fb2):
            c.currentIndexChanged.connect(self._save_order)
        ctrl.ai.changed.connect(self.refresh)
        self.refresh()

    def _save_order(self) -> None:
        primary = self.primary.currentData()
        order = []
        for c in (self.fb1, self.fb2):
            pid = c.currentData()
            if pid and pid != primary and pid not in order:
                order.append(pid)
        set_setting(self.ctrl, "ai_primary", primary, self)
        set_setting(self.ctrl, "ai_fallback_order", ",".join(order), self)

    def refresh(self) -> None:
        cfg = self.ctrl.ai.config()
        widgets = (self.enable, self.primary, self.fb1, self.fb2)
        for w in widgets:
            w.blockSignals(True)
        self.enable.setChecked(cfg.enabled)
        self.primary.setCurrentIndex(self.primary.findData(cfg.primary))
        for c, pid in zip((self.fb1, self.fb2), list(cfg.fallbacks) + ["", ""]):
            c.setCurrentIndex(max(0, c.findData(pid)))
        for w in widgets:
            w.blockSignals(False)
        for b in self.boxes.values():
            b.refresh()


class PrivacyTab(QWidget):
    def __init__(self, ctrl) -> None:
        super().__init__()
        self.ctrl = ctrl
        v = QVBoxLayout(self)
        v.addWidget(make_label("PRIVACY, REDACTION AND LIMITS", "section"))
        self.consent = QComboBox()
        self.consent.addItem("Review every request (recommended)", "every_request")
        self.consent.addItem("Review only when new context is added to a conversation", "new_context_only")
        v.addWidget(self.consent)
        v.addWidget(make_label("The first request of a conversation, and any request that adds new context, is always "
                               "reviewed. A one-time approval never covers future events.", "muted", wrap=True))
        self.paths = QCheckBox("Remove folder paths (keep only the file name)")
        self.names = QCheckBox("Remove user names, machine names, e-mail addresses and Windows SIDs")
        self.addrs = QCheckBox("Remove IP and MAC addresses")
        for c in (self.paths, self.names, self.addrs):
            v.addWidget(c)
        self.redact_warn = make_label("", "banner", wrap=True)
        v.addWidget(self.redact_warn)
        self.fallback_perm = QCheckBox("Also try the next provider after key, permission or model errors")
        v.addWidget(self.fallback_perm)
        self.spins: dict[str, QSpinBox] = {}
        for key, text, lo, hi in (("ai_max_response", "Maximum response length (tokens)", 64, 4096),
                                  ("ai_max_requests_hour", "Maximum requests per hour", 1, 200),
                                  ("ai_max_requests_session", "Maximum requests per session", 1, 1000),
                                  ("ai_history_messages", "Earlier messages offered with a new question", 0, 20),
                                  ("ai_context_max_events", "Maximum events offered as context", 1, 50)):
            row = QHBoxLayout()
            sp = QSpinBox(); sp.setRange(lo, hi)
            row.addWidget(make_label(text)); row.addStretch(1); row.addWidget(sp)
            v.addLayout(row)
            self.spins[key] = sp
            sp.editingFinished.connect(lambda k=None, key=key, sp=sp: set_setting(ctrl, key, sp.value(), self))
        self.history = QCheckBox("Keep conversations on this PC (off by default; stored locally, never uploaded)")
        v.addWidget(self.history)
        self.convs = QListWidget(); self.convs.setMaximumHeight(110)
        v.addWidget(self.convs)
        row = QHBoxLayout()
        self.b_open = make_button("Open"); self.b_delete = make_button("Delete selected"); self.b_clear = make_button("Delete all history", danger=True)
        self.b_reset = make_button("Reset AI configuration…", danger=True)
        for b in (self.b_open, self.b_delete, self.b_clear, self.b_reset):
            row.addWidget(b)
        row.addStretch(1)
        v.addLayout(row)
        v.addStretch(1)
        self.consent.currentIndexChanged.connect(lambda *_: set_setting(ctrl, "ai_consent_mode", self.consent.currentData(), self))
        for c, key in ((self.paths, "ai_redact_paths"), (self.names, "ai_redact_names"), (self.addrs, "ai_redact_addresses"),
                       (self.fallback_perm, "ai_fallback_on_permanent"), (self.history, "ai_history_enabled")):
            c.toggled.connect(lambda on, key=key: set_setting(ctrl, key, on, self))
        self.b_open.clicked.connect(self._open)
        self.b_delete.clicked.connect(self._delete)
        self.b_clear.clicked.connect(self._clear)
        self.b_reset.clicked.connect(self._reset)
        ctrl.ai.changed.connect(self.refresh)
        self.refresh()

    def refresh(self) -> None:
        s = self.ctrl.settings
        blocked = [self.consent, self.paths, self.names, self.addrs, self.fallback_perm, self.history, *self.spins.values()]
        for w in blocked:
            w.blockSignals(True)
        self.consent.setCurrentIndex(max(0, self.consent.findData(s.get("ai_consent_mode"))))
        for c, key in ((self.paths, "ai_redact_paths"), (self.names, "ai_redact_names"), (self.addrs, "ai_redact_addresses"),
                       (self.fallback_perm, "ai_fallback_on_permanent"), (self.history, "ai_history_enabled")):
            c.setChecked(bool(s.get(key)))
        for key, sp in self.spins.items():
            sp.setValue(int(s.get(key)))
        for w in blocked:
            w.blockSignals(False)
        off = [n for n, c in (("paths", self.paths), ("names", self.names), ("addresses", self.addrs)) if not c.isChecked()]
        self.redact_warn.setText("Warning: " + ", ".join(off) + " will be sent to the provider unredacted." if off else "")
        self.redact_warn.setVisible(bool(off))
        self.convs.clear()
        for c in self.ctrl.ai.store.list():
            it = QListWidgetItem(f"{c.title}  ({c.messages} messages)")
            it.setData(Qt.ItemDataRole.UserRole, c.id)
            self.convs.addItem(it)

    def _current(self) -> Optional[int]:
        it = self.convs.currentItem()
        return it.data(Qt.ItemDataRole.UserRole) if it else None

    def _open(self) -> None:
        cid = self._current()
        if cid is not None:
            self.ctrl.ai.load_conversation(cid)

    def _delete(self) -> None:
        cid = self._current()
        if cid is not None:
            self.ctrl.ai.delete_conversation(cid)

    def _clear(self) -> None:
        if QMessageBox.question(self, "Delete all history", "Delete every stored AI conversation?") == QMessageBox.StandardButton.Yes:
            self.ctrl.ai.clear_history()

    def _reset(self) -> None:
        if QMessageBox.question(self, "Reset AI configuration",
                                "Reset every AI setting to its default (AI off, no providers enabled)?") != QMessageBox.StandardButton.Yes:
            return
        delete = QMessageBox.question(self, "Stored keys", "Also delete the API keys stored in the Windows Credential "
                                      "Manager?") == QMessageBox.StandardButton.Yes
        self.ctrl.ai.reset_configuration(delete_keys=delete)


class UsageTab(QWidget):
    def __init__(self, ctrl) -> None:
        super().__init__()
        self.ctrl = ctrl
        v = QVBoxLayout(self)
        self.text = QPlainTextEdit(); self.text.setReadOnly(True)
        v.addWidget(self.text, 1)
        b = make_button("Refresh")
        b.clicked.connect(self.refresh)
        v.addWidget(b)
        ctrl.ai.changed.connect(self.refresh)
        self.refresh()

    def refresh(self) -> None:
        snap = self.ctrl.ai.status_snapshot()
        lines = [f"AI assistant: {'enabled' if snap['enabled'] else 'off'}",
                 f"Requests this session: {snap['session_requests']}   last hour: {snap['last_hour']}",
                 f"Worker threads: {snap['workers']}   running: {snap['active']}   queued: {snap['queue_depth']}",
                 f"Last answer latency: " + (f"{snap['last_latency_s']:.2f} s" if snap["last_latency_s"] is not None else "unavailable"),
                 f"Secure key storage: {'available' if snap['secure_storage'] else 'not available'}", ""]
        for pid in PROVIDER_IDS:
            pu = snap["providers"].get(pid)
            lines.append(f"{NAMES[pid]} - {snap['health'][pid]}")
            if not pu:
                lines.append("   no requests this session")
                continue
            avg = pu["latency_total_s"] / pu["requests"] if pu["requests"] else None
            tokens = (f"{pu['total_tokens']} (reported for {pu['requests_with_usage']} of {pu['requests']} requests)"
                      if pu["requests_with_usage"] else "not reported by the provider")
            lines.append(f"   requests {pu['requests']}, ok {pu['succeeded']}, failed {pu['failed']}, "
                         f"average time {avg:.2f} s" if avg is not None else "")
            lines.append(f"   tokens: {tokens}")
        lines += ["", "Prices and costs are not estimated: Sentinel only shows values a provider reports. Check your "
                      "provider account for billing and quota."]
        self.text.setPlainText("\n".join(l for l in lines if l is not None))


class NetworkTab(QWidget):
    """Optional HTTP proxy for AI requests. Direct connection is the default."""

    def __init__(self, ctrl) -> None:
        super().__init__()
        self.ctrl = ctrl
        v = QVBoxLayout(self)
        v.addWidget(make_label("NETWORK AND PROXY", "section"))
        v.addWidget(make_label(
            "By default Sentinel connects to the AI providers directly. If your network requires an HTTP proxy, enable it "
            "here. Requests then travel through a CONNECT tunnel: the provider's certificate is still verified by Sentinel, "
            "the proxy cannot read your request or API key, and if the proxy fails Sentinel does not fall back to a "
            "direct connection. Proxies that must be reached over TLS (https://), SOCKS and automatic proxy scripts "
            "are not supported. The Windows system proxy is not used automatically.", "muted", wrap=True))
        self.enabled = QCheckBox("Send AI requests through an HTTP proxy")
        v.addWidget(self.enabled)
        row = QHBoxLayout()
        self.host = QLineEdit(); self.host.setPlaceholderText("proxy host name or IP address")
        self.host.setAccessibleName("Proxy host")
        self.port = QSpinBox(); self.port.setRange(1, 65535); self.port.setAccessibleName("Proxy port")
        row.addWidget(make_label("Host")); row.addWidget(self.host, 1); row.addWidget(make_label("Port")); row.addWidget(self.port)
        v.addLayout(row)
        self.auth = QCheckBox("The proxy requires a user name and password")
        v.addWidget(self.auth)
        row = QHBoxLayout()
        self.user = QLineEdit(); self.user.setPlaceholderText("proxy user name"); self.user.setAccessibleName("Proxy user name")
        self.password = QLineEdit(); self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.password.setPlaceholderText("proxy password"); self.password.setAccessibleName("Proxy password")
        self.b_save = make_button("Save sign-in"); self.b_del = make_button("Remove sign-in", danger=True)
        for w in (self.user, self.password):
            row.addWidget(w, 1)
        row.addWidget(self.b_save); row.addWidget(self.b_del)
        v.addLayout(row)
        self.cred_state = make_label("", "muted", wrap=True)
        v.addWidget(self.cred_state)
        row = QHBoxLayout()
        self.b_test = make_button("Test proxy")
        self.result = make_label("", "muted", wrap=True)
        row.addWidget(self.b_test); row.addWidget(self.result, 1)
        v.addLayout(row)
        v.addStretch(1)
        self.enabled.toggled.connect(self._toggle)
        self.host.editingFinished.connect(self._host_done)
        self.port.editingFinished.connect(lambda: set_setting(ctrl, "ai_proxy_port", self.port.value(), self))
        self.auth.toggled.connect(lambda on: set_setting(ctrl, "ai_proxy_auth", on, self))
        self.b_save.clicked.connect(self._save)
        self.b_del.clicked.connect(self._delete)
        self.b_test.clicked.connect(self._test)
        ctrl.ai.changed.connect(self.refresh)
        self.refresh()

    def _toggle(self, on: bool) -> None:
        if on:
            try:
                validate_host(self.host.text())
            except ProxyConfigError as exc:
                QMessageBox.warning(self, "Proxy not enabled", str(exc))
                self.refresh()
                return
            if not set_setting(self.ctrl, "ai_proxy_host", self.host.text(), self):
                self.refresh()
                return
        set_setting(self.ctrl, "ai_proxy_enabled", on, self)

    def _host_done(self) -> None:
        if self.host.text().strip() != self.ctrl.settings.get("ai_proxy_host"):
            set_setting(self.ctrl, "ai_proxy_host", self.host.text().strip(), self)
            self.refresh()

    def _save(self) -> None:
        try:
            self.ctrl.ai.secrets.set_proxy_credentials(self.user.text(), self.password.text())
        except (InvalidKey, SecureStorageUnavailable) as exc:
            QMessageBox.warning(self, "Sign-in not saved", str(exc))
            return
        self.password.clear()
        self.ctrl.ai.changed.emit()

    def _delete(self) -> None:
        if QMessageBox.question(self, "Remove sign-in", "Remove the stored proxy user name and password?") \
                == QMessageBox.StandardButton.Yes:
            self.ctrl.ai.secrets.delete_proxy_credentials()
            self.ctrl.ai.changed.emit()

    def _test(self) -> None:
        self.result.setText("Testing the proxy…")
        self.ctrl.ai.test_proxy(lambda res: self.result.setText(res.message))

    def refresh(self) -> None:
        s, ai = self.ctrl.settings, self.ctrl.ai
        widgets = (self.enabled, self.host, self.port, self.auth)
        for w in widgets:
            w.blockSignals(True)
        self.enabled.setChecked(bool(s.get("ai_proxy_enabled")))
        if not self.host.hasFocus():
            self.host.setText(str(s.get("ai_proxy_host")))
        self.port.setValue(int(s.get("ai_proxy_port")))
        self.auth.setChecked(bool(s.get("ai_proxy_auth")))
        for w in widgets:
            w.blockSignals(False)
        src = ai.secrets.proxy_source()
        self.cred_state.setText({"credential_manager": "Proxy sign-in stored in the Windows Credential Manager "
                                 "(the password is never shown).",
                                 "environment": "Proxy sign-in read from VYRXONE_PROXY_USERNAME / VYRXONE_PROXY_PASSWORD.",
                                 "none": "No proxy sign-in stored."}[src])
        self.b_save.setEnabled(ai.secrets.storage_available)
        self.b_save.setToolTip("" if ai.secrets.storage_available else ai.secrets.storage_note())
        self.b_del.setEnabled(src == "credential_manager")
        self.b_test.setEnabled(ai.enabled and self.enabled.isChecked())
        self.b_test.setToolTip("" if ai.enabled else "Turn on 'Enable AI Assistant' first; while AI is off Sentinel "
                                                     "makes no network connection.")
