"""AI Assistant (V4): optional, off by default, every request reviewed first, answers shown as unverified."""

from __future__ import annotations

import html
import logging
from dataclasses import asdict
from typing import Callable, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QHBoxLayout, QListWidget, QListWidgetItem, QMessageBox,
                               QPlainTextEdit, QSplitter, QTabWidget, QTextBrowser, QVBoxLayout, QWidget)

from ... import diagnostics
from ...ai import context_builder as cb
from ...ai.errors import AIError
from ...ai.knowledge import documentation_excerpt
from ...ai.prompts import SUGGESTED_QUESTIONS, TASKS
from ...ai.providers import PROVIDER_IDS
from ...ai.response_validator import parse_suggestions
from .. import theme
from ..ai_consent import ConsentDialog, ReviewResult
from ..widgets import make_button, make_label, page_header
from .ai_settings import NAMES, NetworkTab, PrivacyTab, ProvidersTab, UsageTab

log = logging.getLogger("sentinel.ai.ui")

TASK_ORDER = ("finding", "correlation", "events", "integrity", "project", "tasks", "settings", "features", "chat")
NOTICE = ("AI answers may be wrong. They are unverified explanations, separate from Sentinel's rule-based findings, "
          "and never change a finding. Sentinel never runs commands, opens links or changes files because of an answer.")


class AIPage(QWidget):
    title = "AI Assistant"

    def __init__(self, ctrl) -> None:
        super().__init__()
        self.ctrl = ctrl
        self.ai = ctrl.ai
        self._log: list[tuple[str, str, str]] = []        # (kind, text, extra)
        self._last: Optional[dict] = None                 # the last request parameters, for Retry
        self._last_answer = ""
        self._suggestions: list[str] = []
        self._review: Callable = self._show_review       # replaced in tests; the only gate before sending
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(10)
        root.addWidget(page_header("AI Assistant", "Optional help to understand findings, events and project health. "
                                   "Off by default; nothing is sent until you review and confirm the exact request."))
        self.banner = make_label(NOTICE, "banner", wrap=True)
        root.addWidget(self.banner)
        self.tabs = QTabWidget()
        root.addWidget(self.tabs, 1)
        self.tabs.addTab(self._build_chat(), "Assistant")
        self.providers = ProvidersTab(ctrl)
        self.privacy = PrivacyTab(ctrl)
        self.network = NetworkTab(ctrl)
        self.usage = UsageTab(ctrl)
        self.tabs.addTab(self.providers, "Providers")
        self.tabs.addTab(self.privacy, "Privacy && limits")
        self.tabs.addTab(self.network, "Network")
        self.tabs.addTab(self.usage, "Usage")
        ctrl.findings_changed.connect(self.reload_sources)
        ctrl.projects_changed.connect(self.reload_sources)
        self.ai.changed.connect(self._sync)
        self.ai.activity.connect(lambda *_: self._sync())
        self.ai.changed.connect(self._render_if_conversation_changed)
        self._conv_marker = id(self.ai.conversation)
        self.reload_sources()
        self._sync()

    # ------------------------------------------------------------------ layout
    def _build_chat(self) -> QWidget:
        w = QWidget()
        lay = QHBoxLayout(w)
        split = QSplitter(Qt.Orientation.Horizontal)
        lay.addWidget(split)
        left = QWidget(); lv = QVBoxLayout(left)
        lv.addWidget(make_label("ASK ABOUT", "section"))
        self.task = QComboBox()
        for t in TASK_ORDER:
            self.task.addItem(TASKS[t][0], t)
        lv.addWidget(self.task)
        self.source = QComboBox(); lv.addWidget(self.source)
        self.event_list = QListWidget(); self.event_list.setMinimumHeight(160); lv.addWidget(self.event_list, 1)
        self.diag = QCheckBox("Include the (redacted) diagnostics report"); lv.addWidget(self.diag)
        self.b_reload = make_button("Refresh choices"); lv.addWidget(self.b_reload)
        self.source_note = make_label("", "muted", wrap=True); lv.addWidget(self.source_note)
        lv.addWidget(make_label("Nothing is selected automatically: you choose the context, then review it before it is sent.",
                                "muted", wrap=True))
        right = QWidget(); rv = QVBoxLayout(right)
        self.status = make_label("", "muted", wrap=True)
        rv.addWidget(self.status)
        self.view = QTextBrowser()
        self.view.setOpenLinks(False); self.view.setOpenExternalLinks(False)
        rv.addWidget(self.view, 1)
        self.suggest_box = QListWidget(); self.suggest_box.setMaximumHeight(110); self.suggest_box.hide()
        self.b_add_tasks = make_button("Add checked suggestions as project tasks…"); self.b_add_tasks.hide()
        rv.addWidget(self.suggest_box); rv.addWidget(self.b_add_tasks)
        self.suggested = QComboBox(); self.suggested.addItem("Suggested safe questions…", None)
        for t, q in SUGGESTED_QUESTIONS:
            self.suggested.addItem(q, (t, q))
        rv.addWidget(self.suggested)
        self.input = QPlainTextEdit(); self.input.setMaximumHeight(80)
        self.input.setPlaceholderText("Type a question, or leave empty to run the selected action…")
        rv.addWidget(self.input)
        row = QHBoxLayout()
        self.override = QComboBox(); self.override.addItem("Use configured provider order", None)
        for pid in PROVIDER_IDS:
            self.override.addItem(f"Only {NAMES[pid]}", pid)
        self.b_send = make_button("Review && send…", primary=True)
        self.b_stop = make_button("Stop"); self.b_retry = make_button("Retry…"); self.b_copy = make_button("Copy answer")
        self.b_new = make_button("New conversation"); self.b_clear = make_button("Clear view")
        for b in (self.override, self.b_send, self.b_stop, self.b_retry, self.b_copy, self.b_new, self.b_clear):
            row.addWidget(b)
        rv.addLayout(row)
        split.addWidget(left); split.addWidget(right); split.setStretchFactor(1, 3)
        self.task.currentIndexChanged.connect(self._task_changed)
        self.source.currentIndexChanged.connect(lambda *_: self._fill_events())
        self.b_reload.clicked.connect(self.reload_sources)
        self.suggested.currentIndexChanged.connect(self._pick_suggestion)
        self.b_send.clicked.connect(self.begin)
        self.b_stop.clicked.connect(self.ai.cancel)
        self.b_retry.clicked.connect(self.retry)
        self.b_copy.clicked.connect(self.copy_answer)
        self.b_new.clicked.connect(self.new_conversation)
        self.b_clear.clicked.connect(self._clear_view)
        self.b_add_tasks.clicked.connect(self._add_tasks)
        self._task_changed()
        return w

    # ------------------------------------------------------------------ choices
    def task_key(self) -> str:
        return self.task.currentData()

    def reload_sources(self) -> None:
        """Refresh the list of findings/projects/events while keeping the user's current choice."""
        keep = self.source.currentData()
        checked = {self.event_list.item(k).data(Qt.ItemDataRole.UserRole) for k in range(self.event_list.count())
                   if self.event_list.item(k).checkState() == Qt.CheckState.Checked}
        self._task_changed()
        j = self.source.findData(keep)
        if j >= 0:
            self.source.setCurrentIndex(j)
        for k in range(self.event_list.count()):
            if self.event_list.item(k).data(Qt.ItemDataRole.UserRole) in checked:
                self.event_list.item(k).setCheckState(Qt.CheckState.Checked)

    def showEvent(self, e) -> None:  # noqa: N802
        super().showEvent(e)
        self.reload_sources()

    def _task_changed(self) -> None:
        t = self.task_key()
        self.source.blockSignals(True)
        self.source.clear()
        self.event_list.clear()
        self.diag.setVisible(t == "settings")
        show_src = t in ("finding", "correlation", "project", "tasks")
        self.source.setVisible(show_src)
        self.event_list.setVisible(t in ("events", "integrity"))
        if t in ("finding", "correlation"):
            for f in self.ctrl.findings.query(limit=60):
                self.source.addItem(f"[{f.severity}] {f.title}", f.id)
        elif t in ("project", "tasks"):
            for p in self.ctrl.projects.list():
                self.source.addItem(p.name, p.id)
        self.source.blockSignals(False)
        self._fill_events()
        self.source_note.setText("" if self.source.count() or t not in ("finding", "correlation", "project", "tasks")
                                 else "Nothing to choose yet.")

    def _fill_events(self) -> None:
        if self.task_key() not in ("events", "integrity"):
            return
        limit = self.ai.config().context_max_events
        rows = self.ctrl.events.query(limit=200 if self.task_key() == "integrity" else limit)
        if self.task_key() == "integrity":
            rows = [e for e in rows if e.event_type.startswith("baseline")][:limit]
        self.event_list.clear()
        for e in rows:
            it = QListWidgetItem(f"{e.local_time}  {e.event_type}  {e.message[:70]}")
            it.setData(Qt.ItemDataRole.UserRole, e.id)
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            it.setCheckState(Qt.CheckState.Unchecked)
            self.event_list.addItem(it)

    def preselect(self, task: str, ident: Optional[object] = None, event_ids: Optional[list] = None) -> None:
        """Called by other pages ('Ask AI about this'). It only selects context; nothing is sent."""
        i = self.task.findData(task)
        if i >= 0:
            self.task.setCurrentIndex(i)
        if ident is not None:
            j = self.source.findData(ident)
            if j >= 0:
                self.source.setCurrentIndex(j)
        for k in range(self.event_list.count()):
            it = self.event_list.item(k)
            if event_ids and it.data(Qt.ItemDataRole.UserRole) in event_ids:
                it.setCheckState(Qt.CheckState.Checked)
        self.tabs.setCurrentIndex(0)

    def _pick_suggestion(self) -> None:
        data = self.suggested.currentData()
        if data:
            t, q = data
            self.task.setCurrentIndex(self.task.findData(t))
            self.input.setPlainText(q)
        self.suggested.setCurrentIndex(0)

    # ------------------------------------------------------------------ context for the selected task
    def build_items(self) -> list[cb.ContextItem]:
        t, b = self.task_key(), self.ai.builder()
        items: list[cb.ContextItem] = []
        ident = self.source.currentData()
        if t in ("finding", "correlation") and ident is not None:
            f = self.ctrl.findings.get(ident)
            if f:
                items += b.finding(f)
        elif t in ("events", "integrity"):
            ids = [self.event_list.item(k).data(Qt.ItemDataRole.UserRole) for k in range(self.event_list.count())
                   if self.event_list.item(k).checkState() == Qt.CheckState.Checked]
            evs = [e for e in self.ctrl.events.query(limit=500) if e.id in ids]
            items += b.integrity(evs) if t == "integrity" else b.events(evs)
        elif t in ("project", "tasks") and ident is not None:
            proj = self.ctrl.projects.get(ident)
            if proj:
                rep = self.ctrl.health.load(ident)
                items += b.project(proj, asdict(rep) if rep else None, self.ctrl.tasks.counts(ident))
        elif t == "settings":
            items += b.settings(self.ctrl.settings.all())
            if self.diag.isChecked():
                items += b.diagnostics(diagnostics.build_report(self.ctrl))
        if t in ("settings", "features"):
            items.append(b.knowledge(documentation_excerpt()))
        return items

    # ------------------------------------------------------------------ sending
    def begin(self, _checked: bool = False, *, preselected: Optional[set] = None) -> None:
        ai = self.ai
        if not ai.enabled:
            self._notice("AI is off. Turn it on in the Providers tab to use the assistant.", "error")
            self.tabs.setCurrentIndex(1)
            return
        task, question = self.task_key(), self.input.toPlainText().strip()
        if task == "chat" and not question:
            self._notice("Type a question first.", "error")
            return
        items = self.build_items()
        if task not in ("chat", "features", "settings") and not items:
            self._notice("Choose something to ask about first (nothing is selected automatically).", "error")
            return
        cfg = ai.config()
        default_sel = preselected if preselected is not None else {i.item_id for i in items if i.default_on}
        history = ai.history_for_request()
        plan = self._plan(cfg)
        if not plan:
            self._notice("No provider is ready. Add a key and a model ID in the Providers tab.", "error")
            self.tabs.setCurrentIndex(1)
            return

        def compose(sel: set, with_hist: bool):
            return cb.compose_request(task, question, items, sel, history if with_hist else None, cfg.max_tokens)

        override = self.override.currentData()
        if ai.review_required(default_sel):
            result = self._review(plan=plan, items=items, history_count=len(history), compose=compose,
                                  preselected=default_sel, redaction_note=self._redaction_note(cfg))
            if result is None:
                self._notice("Cancelled - nothing was sent.", "info")
                return
        else:
            result = ReviewResult(default_sel, True)
        request = compose(result.selected, result.include_history)
        self._last = dict(task=task, question=question, selected=set(result.selected))
        labels = [i.label for i in items if i.item_id in result.selected]
        self._add("user", question or f"({TASKS[task][0]})", "; ".join(labels))
        sent_text = request.messages[-1].content
        selected = set(result.selected)
        try:
            ai.send(request, lambda r: self._done(r, question or TASKS[task][0], sent_text, selected, task),
                    self._failed, confirmed=True, override=override)
        except AIError as exc:
            self._failed(exc)
            return
        self.input.clear()
        self._notice(f"Waiting for {self._plan_names(plan, override)}…", "info")

    def retry(self) -> None:
        if not self._last or self.ai.busy:
            return
        if self._last["task"] != self.task_key():
            self.task.setCurrentIndex(self.task.findData(self._last["task"]))
        if self._last["question"]:
            self.input.setPlainText(self._last["question"])
        self.begin(preselected=self._last["selected"])        # the review dialog is shown again

    def _plan(self, cfg) -> list[tuple[str, str]]:
        chain, _ = self.ai.router.plan(cfg, self.override.currentData())
        return [(NAMES[p], cfg.provider(p).model) for p in chain]

    @staticmethod
    def _plan_names(plan, override) -> str:
        return plan[0][0] if override or len(plan) == 1 else f"{plan[0][0]} (fallbacks: {', '.join(n for n, _ in plan[1:])})"

    @staticmethod
    def _redaction_note(cfg) -> str:
        off = [n for n, v in (("paths", cfg.redaction.paths), ("names", cfg.redaction.names),
                              ("addresses", cfg.redaction.addresses)) if not v]
        return ("Redaction is ON for paths, user/machine names and IP/MAC addresses." if not off
                else f"Warning: redaction is OFF for {', '.join(off)}.")

    def _show_review(self, *, plan, items, history_count, compose, preselected, redaction_note) -> Optional[ReviewResult]:
        cfg = self.ai.config()
        first = next((p for p in cfg.order if cfg.provider(p).enabled), cfg.primary)

        def describe(req):
            lines = ["SYSTEM INSTRUCTIONS (written by Sentinel):", req.system, ""]
            for m in req.messages:
                lines += [f"--- {m.role.upper()} MESSAGE ---", m.content, ""]
            prov = self.ai.router.provider(first)
            try:
                _, body = prov.build_request(req, cfg.provider(first).model)
                import json
                exact = json.dumps(body, indent=2, ensure_ascii=False)
            except AIError as exc:
                exact = str(exc)
            return "\n".join(lines), exact

        dlg = ConsentDialog(self, plan=plan, items=items, history_count=history_count, compose=compose, describe=describe,
                            preselected=preselected, redaction_note=redaction_note)
        return dlg.result_value() if dlg.exec() else None

    # ------------------------------------------------------------------ results
    def _done(self, result, question: str, sent_text: str, selected: set, task: str) -> None:
        resp = result.response
        self._last_answer = resp.text
        extra = f"{resp.provider_name} · {resp.model}"
        if resp.usage.known and resp.usage.total_tokens is not None:
            extra += f" · {resp.usage.total_tokens} tokens (as reported)"
        if resp.truncated:
            extra += " · cut off at the length limit"
        self._add("ai", resp.text, extra)
        note = result.fallback_note()
        if note:
            self._add("notice", note, "")
        for n in resp.notes:
            self._add("notice", n, "")
        self.ai.record_exchange(question, sent_text, result, selected)
        self._conv_marker = id(self.ai.conversation)
        self._notice(f"Answered by {resp.provider_name} ({resp.model}) in {resp.latency_s:.1f} s." + (f" {note}" if note else ""), "info")
        self._suggestions = parse_suggestions(resp.text) if task == "tasks" else []
        self._show_suggestions()

    def _failed(self, err: AIError) -> None:
        self._add("error", err.message, "")
        self._notice(err.message, "error")

    # ------------------------------------------------------------------ project task suggestions (user-confirmed)
    def _show_suggestions(self) -> None:
        self.suggest_box.clear()
        for s in self._suggestions:
            it = QListWidgetItem(s)
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            it.setCheckState(Qt.CheckState.Unchecked)
            self.suggest_box.addItem(it)
        self.suggest_box.setVisible(bool(self._suggestions)); self.b_add_tasks.setVisible(bool(self._suggestions))

    def _add_tasks(self) -> None:
        pid = self.source.currentData()
        picked = [self.suggest_box.item(i).text() for i in range(self.suggest_box.count())
                  if self.suggest_box.item(i).checkState() == Qt.CheckState.Checked]
        if pid is None or not picked:
            return
        if QMessageBox.question(self, "Add tasks", f"Add {len(picked)} AI-suggested task(s) to this project?\n\n"
                                + "\n".join("• " + p for p in picked)) != QMessageBox.StandardButton.Yes:
            return
        for title in picked:
            self.ctrl.tasks.add_task(pid, title)
        self.ctrl.projects_changed.emit()
        self._suggestions = []
        self._show_suggestions()

    # ------------------------------------------------------------------ transcript
    def _add(self, kind: str, text: str, extra: str) -> None:
        self._log.append((kind, text, extra))
        del self._log[:-80]
        self._render()

    def _notice(self, text: str, level: str) -> None:
        self.status.setText(text)
        self.status.setStyleSheet(f"color:{theme.CRIMSON_BRIGHT};" if level == "error" else f"color:{theme.MUTED};")

    def _render(self) -> None:
        parts = []
        for kind, text, extra in self._log:
            body = html.escape(text).replace("\n", "<br>")      # escaped plain text: no links, no markup from the model
            if kind == "user":
                head, col = "You", theme.GOLD
            elif kind == "ai":
                head, col = "AI answer (unverified)", theme.CRIMSON_BRIGHT
            elif kind == "error":
                head, col = "Error", theme.CRIMSON_BRIGHT
            else:
                head, col = "Note", theme.MUTED
            sub = f" <span style='color:{theme.MUTED}'>· {html.escape(extra)}</span>" if extra else ""
            parts.append(f"<p><b style='color:{col}'>{head}</b>{sub}<br>{body}</p>")
        self.view.setHtml("".join(parts) or f"<p style='color:{theme.MUTED}'>No messages yet.</p>")
        self.view.verticalScrollBar().setValue(self.view.verticalScrollBar().maximum())

    def _render_if_conversation_changed(self) -> None:
        if id(self.ai.conversation) != self._conv_marker:
            self._conv_marker = id(self.ai.conversation)
            self._log = [("user" if e.role == "user" else "ai", e.display, e.provider and f"{e.provider} · {e.model}")
                         for e in self.ai.conversation.entries]
            self._last_answer = next((e.content for e in reversed(self.ai.conversation.entries) if e.role == "assistant"), "")
            self._render()

    def _clear_view(self) -> None:
        self._log.clear(); self._render()

    def new_conversation(self) -> None:
        if self.ai.busy:
            return
        self.ai.new_conversation()
        self._log.clear(); self._last_answer = ""; self._suggestions = []; self._show_suggestions()
        self._conv_marker = id(self.ai.conversation)
        self._render()

    def copy_answer(self) -> None:
        if self._last_answer:
            QApplication.clipboard().setText(self._last_answer)
            self._notice("Answer copied to the clipboard.", "info")

    # ------------------------------------------------------------------ state
    def _sync(self) -> None:
        enabled, busy = self.ai.enabled, self.ai.busy
        self.b_send.setEnabled(enabled and not busy)
        self.b_stop.setEnabled(busy)
        self.b_retry.setEnabled(enabled and not busy and self._last is not None)
        self.b_copy.setEnabled(bool(self._last_answer))
        if not enabled:
            self.status.setText("AI is off (the default). Enable it in the Providers tab; the rest of Sentinel never needs it.")
        elif busy and "Waiting" not in self.status.text():
            self.status.setText("Request running…")
