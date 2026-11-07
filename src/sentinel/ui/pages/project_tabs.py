"""Project intelligence tabs (V3): health, tasks, notes and repository overview.

Everything that touches the file system or Git runs through ``ctrl.jobs`` (background, bounded,
with timeouts and cancel). The tabs only ever read cached data on the UI thread.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QCheckBox, QComboBox, QHBoxLayout, QInputDialog, QLineEdit, QListWidget,
                               QListWidgetItem, QMessageBox, QPlainTextEdit, QProgressBar, QTabWidget,
                               QVBoxLayout, QWidget)

from ...projects.store import Project, ProjectError
from ...projects.tasks import TASK_PRIORITIES, TASK_STATUSES, parse_due
from ...storage.database import DatabaseError
from ..widgets import make_button, make_label, set_state

OVERALL_TEXT = {"ok": "Healthy", "attention": "Needs attention", "unavailable": "Folder unavailable",
                "unknown": "Not checked / no folder"}
OVERALL_STATE = {"ok": "ok", "attention": "warn", "unavailable": "warn", "unknown": "off"}


def ts_text(ts: Optional[float]) -> str:
    return "never" if not ts else datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


class ProjectTabs(QTabWidget):
    def __init__(self, ctrl) -> None:
        super().__init__()
        self.ctrl = ctrl
        self.project: Optional[Project] = None
        self._cancel = None
        self._overview_cancel = None
        self.setMinimumHeight(260)
        self._build_health()
        self._build_tasks()
        self._build_notes()
        self._build_overview()
        self.set_project(None)
        ctrl.projects_changed.connect(self._on_changed)
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(300)
        self._debounce.timeout.connect(self.refresh)

    # ------------------------------------------------------------------ health
    def _build_health(self) -> None:
        w = QWidget()
        lay = QVBoxLayout(w)
        row = QHBoxLayout()
        self.h_run = make_button("Run health check", primary=True,
                                 tip="Checks the folder and reads Git status (read-only) in the background")
        self.h_cancel = make_button("Cancel")
        self.h_test = make_button("Record test result…", tip="Sentinel never runs your tests; record the outcome yourself")
        self.h_build = make_button("Record build result…")
        for b in (self.h_run, self.h_cancel, self.h_test, self.h_build):
            row.addWidget(b)
        row.addStretch(1)
        lay.addLayout(row)
        self.h_progress = QProgressBar()
        self.h_progress.setTextVisible(False)
        self.h_progress.setMaximumHeight(6)
        self.h_progress.hide()
        lay.addWidget(self.h_progress)
        self.h_status = make_label("", wrap=True)
        lay.addWidget(self.h_status)
        self.h_detail = QPlainTextEdit()
        self.h_detail.setReadOnly(True)
        self.h_detail.setMaximumHeight(170)
        lay.addWidget(self.h_detail)
        lay.addWidget(make_label("RECENT ACTIVITY", "section"))
        self.h_activity = QListWidget()
        self.h_activity.setMaximumHeight(90)
        lay.addWidget(self.h_activity)
        self.addTab(w, "Health")
        self.h_run.clicked.connect(self.run_health)
        self.h_cancel.clicked.connect(self._cancel_health)
        self.h_test.clicked.connect(lambda: self._record("test"))
        self.h_build.clicked.connect(lambda: self._record("build"))
        self.h_cancel.setEnabled(False)

    def run_health(self) -> None:
        if self.project is None:
            return
        self.h_run.setEnabled(False)
        self.h_cancel.setEnabled(True)
        self.h_progress.setRange(0, 0)
        self.h_progress.show()
        self.h_status.setText("Checking… (folder and Git are read in the background; the app stays usable)")
        set_state(self.h_status, "off")
        self._cancel = self.ctrl.check_project(self.project.id, on_done=self._health_done,
                                               on_fail=self._health_failed)
        if self._cancel is None:
            self._health_failed("This project no longer exists.")

    def _cancel_health(self) -> None:
        if self._cancel is not None:
            self._cancel.set()
            self.h_status.setText("Cancelling…")

    def _health_done(self, rep) -> None:
        self._health_idle()
        if not rep.complete:
            self.h_status.setText("The check was cancelled; this report is incomplete.")
            set_state(self.h_status, "warn")
        self.refresh()

    def _health_failed(self, message: str) -> None:
        self._health_idle()
        self.h_status.setText(f"Health check did not finish: {message}")
        set_state(self.h_status, "warn")

    def _health_idle(self) -> None:
        self.h_run.setEnabled(self.project is not None)
        self.h_cancel.setEnabled(False)
        self.h_progress.hide()
        self._cancel = None

    def _record(self, kind: str) -> None:
        if self.project is None:
            return
        items = ["passed", "failed", "unknown"]
        status, ok = QInputDialog.getItem(self, f"Record {kind} result", f"Outcome of your last {kind} run:",
                                          items, 0, False)
        if not ok:
            return
        note, ok = QInputDialog.getText(self, f"Record {kind} result", "Short note (optional):")
        if not ok:
            return
        try:
            self.ctrl.tasks.record_check_result(self.project.id, kind, status, note)
        except (ProjectError, DatabaseError) as exc:
            QMessageBox.warning(self, "Cannot record", str(exc))
            return
        self.refresh()

    def _render_health(self) -> None:
        p = self.project
        if p is None:
            self.h_status.setText("Select or save a project to see its health.")
            self.h_detail.clear()
            self.h_activity.clear()
            return
        rep = self.ctrl.health.load(p.id)
        if rep is None:
            self.h_status.setText("Not checked yet. Press “Run health check”.")
            set_state(self.h_status, "off")
            self.h_detail.setPlainText("Nothing is scanned automatically; health is checked only when you ask "
                                       "(the folder's availability is also re-checked every two minutes).")
        else:
            self.h_status.setText(f"{OVERALL_TEXT.get(rep.overall, rep.overall)} - checked {ts_text(rep.checked_ts)}"
                                  f" · last fully healthy: {ts_text(rep.last_ok_ts)}")
            set_state(self.h_status, OVERALL_STATE.get(rep.overall, "off"))
            lines = list(rep.messages)
            g = rep.git
            if g and g.get("is_repo"):
                lines.append(f"Git: branch {g.get('branch')}; {g.get('staged', 0)} staged, {g.get('modified', 0)} "
                             f"modified, {g.get('untracked', 0)} untracked, {g.get('conflicted', 0)} conflicted.")
                if g.get("last_commit"):
                    lines.append(f"Last commit: {g['last_commit']}")
                for f in g.get("changed_files", [])[:20]:
                    lines.append(f"    {f}")
            t = self.ctrl.tasks.counts(p.id)                 # tasks and recorded results are always read live
            rep.test = self.ctrl.tasks.last_check_result(p.id, "test")
            rep.build = self.ctrl.tasks.last_check_result(p.id, "build")
            rep.last_note_ts = self.ctrl.tasks.last_note_ts(p.id)
            lines.append(f"Tasks: {t.get('open', 0)} open, {t.get('in_progress', 0)} in progress, "
                         f"{t.get('done', 0)} done, {t.get('overdue', 0)} overdue.")
            lines.append(f"Last note: {ts_text(rep.last_note_ts)}")
            for label, res in (("Tests", rep.test), ("Build", rep.build)):
                lines.append(f"{label}: " + (f"{res['status']} (recorded by you {ts_text(res['ts'])})"
                                             + (f" - {res['summary']}" if res.get("summary") else "")
                                             if res else "no result recorded"))
            if not rep.complete:
                lines.append("(Incomplete: the check was cancelled.)")
            self.h_detail.setPlainText("\n".join(lines))
        self.h_activity.clear()
        for ts, kind, text in self.ctrl.tasks.recent_activity(p.id, 10):
            self.h_activity.addItem(f"{ts_text(ts)}  {kind.replace('_', ' ')}  {text.split('|')[0][:80]}")

    # ------------------------------------------------------------------- tasks
    def _build_tasks(self) -> None:
        w = QWidget()
        lay = QVBoxLayout(w)
        f = QHBoxLayout()
        self.t_status = QComboBox()
        self.t_status.addItem("All statuses", "all")
        for s in TASK_STATUSES:
            self.t_status.addItem(s.replace("_", " ").capitalize(), s)
        self.t_hide = QCheckBox("Hide done")
        self.t_search = QLineEdit()
        self.t_search.setPlaceholderText("Search tasks…")
        self.t_search.setClearButtonEnabled(True)
        f.addWidget(self.t_status)
        f.addWidget(self.t_hide)
        f.addWidget(self.t_search, 1)
        lay.addLayout(f)
        self.t_list = QListWidget()
        self.t_list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        lay.addWidget(self.t_list, 1)
        self.t_empty = make_label("", "muted", wrap=True)
        lay.addWidget(self.t_empty)
        add = QHBoxLayout()
        self.t_title = QLineEdit()
        self.t_title.setPlaceholderText("New task…")
        self.t_title.setMaxLength(200)
        self.t_prio = QComboBox()
        for p in TASK_PRIORITIES:
            self.t_prio.addItem(p.capitalize(), p)
        self.t_prio.setCurrentIndex(1)
        self.t_due = QLineEdit()
        self.t_due.setPlaceholderText("Due YYYY-MM-DD")
        self.t_due.setMaximumWidth(130)
        self.t_add = make_button("Add task", primary=True)
        for x in (self.t_title, self.t_prio, self.t_due, self.t_add):
            add.addWidget(x) if x is not self.t_title else add.addWidget(x, 1)
        lay.addLayout(add)
        acts = QHBoxLayout()
        self.t_start = make_button("Start")
        self.t_done = make_button("Mark done")
        self.t_reopen = make_button("Reopen")
        self.t_delete = make_button("Delete…", danger=True)
        for b in (self.t_start, self.t_done, self.t_reopen, self.t_delete):
            acts.addWidget(b)
        acts.addStretch(1)
        lay.addLayout(acts)
        self.addTab(w, "Tasks")
        self.t_add.clicked.connect(self.add_task)
        self.t_title.returnPressed.connect(self.add_task)
        self.t_start.clicked.connect(lambda: self._task_status("in_progress"))
        self.t_done.clicked.connect(lambda: self._task_status("done"))
        self.t_reopen.clicked.connect(lambda: self._task_status("open"))
        self.t_delete.clicked.connect(self.delete_task)
        self.t_status.currentIndexChanged.connect(self._render_tasks)
        self.t_hide.toggled.connect(self._render_tasks)
        self.t_search.textChanged.connect(lambda _t: self._render_tasks())

    def add_task(self) -> None:
        if self.project is None:
            return
        try:
            self.ctrl.tasks.add_task(self.project.id, self.t_title.text(), self.t_prio.currentData(),
                                     parse_due(self.t_due.text()))
        except (ProjectError, DatabaseError) as exc:
            QMessageBox.warning(self, "Cannot add task", str(exc))
            return
        self.t_title.clear()
        self.t_due.clear()
        self.refresh()
        self.ctrl.projects_changed.emit()

    def _selected_task_id(self) -> Optional[int]:
        item = self.t_list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _task_status(self, status: str) -> None:
        tid = self._selected_task_id()
        if tid is None:
            QMessageBox.information(self, "Tasks", "Select a task first.")
            return
        try:
            self.ctrl.tasks.update_task(tid, status=status)
        except (ProjectError, DatabaseError) as exc:
            QMessageBox.warning(self, "Cannot update task", str(exc))
            return
        self.refresh()
        self.ctrl.projects_changed.emit()

    def delete_task(self) -> None:
        tid = self._selected_task_id()
        if tid is None:
            QMessageBox.information(self, "Tasks", "Select a task first.")
            return
        if QMessageBox.question(self, "Delete task", "Delete this task? This cannot be undone.") \
                != QMessageBox.StandardButton.Yes:
            return
        self.ctrl.tasks.delete_task(tid)
        self.refresh()
        self.ctrl.projects_changed.emit()

    def _render_tasks(self, *_a) -> None:
        self.t_list.clear()
        if self.project is None:
            return
        tasks = self.ctrl.tasks.list_tasks(self.project.id, status=self.t_status.currentData(),
                                           hide_done=self.t_hide.isChecked(), text=self.t_search.text().strip() or None)
        for t in tasks:
            mark = {"open": "○", "in_progress": "◐", "done": "●"}[t.status]
            due = f"  · due {t.due_text}" + (" (OVERDUE)" if t.overdue else "") if t.due_ts else ""
            item = QListWidgetItem(f"{mark} [{t.priority}] {t.title}{due}")
            item.setData(Qt.ItemDataRole.UserRole, t.id)
            self.t_list.addItem(item)
        self.t_empty.setText("" if tasks else (
            "No tasks match." if self.ctrl.tasks.list_tasks(self.project.id, limit=1)
            else "No tasks yet. Add one below; tasks stay on this PC."))

    # ------------------------------------------------------------------- notes
    def _build_notes(self) -> None:
        w = QWidget()
        lay = QVBoxLayout(w)
        self.n_list = QListWidget()
        lay.addWidget(self.n_list, 1)
        self.n_empty = make_label("", "muted")
        lay.addWidget(self.n_empty)
        self.n_text = QPlainTextEdit()
        self.n_text.setPlaceholderText("Write a note (kept locally with a timestamp)…")
        self.n_text.setMaximumHeight(80)
        lay.addWidget(self.n_text)
        row = QHBoxLayout()
        self.n_add = make_button("Add note", primary=True)
        self.n_del = make_button("Delete selected…", danger=True)
        row.addWidget(self.n_add)
        row.addWidget(self.n_del)
        row.addStretch(1)
        lay.addLayout(row)
        self.addTab(w, "Notes")
        self.n_add.clicked.connect(self.add_note)
        self.n_del.clicked.connect(self.delete_note)

    def add_note(self) -> None:
        if self.project is None:
            return
        try:
            self.ctrl.tasks.add_note(self.project.id, self.n_text.toPlainText())
        except (ProjectError, DatabaseError) as exc:
            QMessageBox.warning(self, "Cannot add note", str(exc))
            return
        self.n_text.clear()
        self.refresh()
        self.ctrl.projects_changed.emit()

    def delete_note(self) -> None:
        item = self.n_list.currentItem()
        if item is None:
            QMessageBox.information(self, "Notes", "Select a note first.")
            return
        if QMessageBox.question(self, "Delete note", "Delete this note? This cannot be undone.") \
                != QMessageBox.StandardButton.Yes:
            return
        self.ctrl.tasks.delete_note(item.data(Qt.ItemDataRole.UserRole))
        self.refresh()

    def _render_notes(self) -> None:
        self.n_list.clear()
        if self.project is None:
            return
        notes = self.ctrl.tasks.list_notes(self.project.id)
        for n in notes:
            first = n.body.splitlines()[0]
            item = QListWidgetItem(f"{ts_text(n.created_ts)}   {first[:120]}")
            item.setToolTip(n.body)
            item.setData(Qt.ItemDataRole.UserRole, n.id)
            self.n_list.addItem(item)
        self.n_empty.setText("" if notes else "No notes yet.")

    # ---------------------------------------------------------------- overview
    def _build_overview(self) -> None:
        w = QWidget()
        lay = QVBoxLayout(w)
        row = QHBoxLayout()
        self.o_run = make_button("Scan repository overview", primary=True,
                                 tip="Counts files and languages in this project folder. Local only; skips "
                                     "virtual environments, node_modules, build output and caches.")
        self.o_cancel = make_button("Cancel")
        self.o_cancel.setEnabled(False)
        row.addWidget(self.o_run)
        row.addWidget(self.o_cancel)
        row.addStretch(1)
        lay.addLayout(row)
        self.o_progress = QProgressBar()
        self.o_progress.setMaximumHeight(6)
        self.o_progress.setTextVisible(False)
        self.o_progress.hide()
        lay.addWidget(self.o_progress)
        self.o_text = QPlainTextEdit()
        self.o_text.setReadOnly(True)
        self.o_text.setPlaceholderText("Press “Scan repository overview”. Nothing is scanned automatically, "
                                       "and nothing leaves your PC.")
        lay.addWidget(self.o_text, 1)
        self.addTab(w, "Overview")
        self.o_run.clicked.connect(self.run_overview)
        self.o_cancel.clicked.connect(lambda: self._overview_cancel and self._overview_cancel.set())

    def run_overview(self) -> None:
        if self.project is None or not self.project.path:
            QMessageBox.information(self, "Overview", "Set a project folder and save first.")
            return
        self.o_run.setEnabled(False)
        self.o_cancel.setEnabled(True)
        self.o_progress.setRange(0, 0)
        self.o_progress.show()
        self.o_text.setPlainText("Scanning…")

        def done(ov) -> None:
            self._overview_idle()
            lines = ov.summary_lines()
            if ov.top_dirs:
                lines += ["", "Largest top-level folders (by file count):"] + [f"  {n}: {c}" for n, c in ov.top_dirs[:12]]
            if ov.readme_excerpt:
                lines += ["", "README excerpt:", ov.readme_excerpt.strip()[:500]]
            lines += ["", f"Took {ov.seconds:.1f} s. Language counts are by file extension, not lines of code."]
            self.o_text.setPlainText("\n".join(lines))

        def failed(msg: str) -> None:
            self._overview_idle()
            self.o_text.setPlainText(f"The overview did not finish: {msg}")

        def progress(done_n: int, total: int, label: str) -> None:
            self.o_text.setPlainText(f"Scanning… {done_n} file(s) so far ({label})")

        self._overview_cancel = self.ctrl.project_overview(self.project.id, done, failed, progress)
        if self._overview_cancel is None:
            failed("This project has no folder.")

    def _overview_idle(self) -> None:
        self.o_run.setEnabled(self.project is not None)
        self.o_cancel.setEnabled(False)
        self.o_progress.hide()
        self._overview_cancel = None

    # ------------------------------------------------------------------ common
    def set_project(self, project: Optional[Project]) -> None:
        same = project is not None and self.project is not None and project.id == self.project.id
        self.project = project
        enabled = project is not None
        for i in range(self.count()):
            self.widget(i).setEnabled(enabled)
        if not same:
            self.o_text.clear()
        self.refresh()

    def refresh(self) -> None:
        self._render_health()
        self._render_tasks()
        self._render_notes()
        if self._cancel is None:
            self.h_run.setEnabled(self.project is not None)

    def _on_changed(self) -> None:
        if self.isVisible():
            self._debounce.start()
