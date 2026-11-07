from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QComboBox, QFileDialog, QFormLayout, QHBoxLayout, QLineEdit, QListWidget,
                               QListWidgetItem, QMessageBox, QPlainTextEdit, QSplitter, QVBoxLayout, QWidget)

from ...projects.git_status import get_git_status
from ...projects.health import PATH_TEXT, inspect_path
from ...projects.store import STATUSES, Project, ProjectError
from ...storage.database import DatabaseError
from ..widgets import Card, make_button, make_label, page_header, set_state
from .project_tabs import ProjectTabs


class ProjectsPage(QWidget):
    title = "Projects"

    def __init__(self, ctrl) -> None:
        super().__init__()
        self.ctrl = ctrl
        self._current_id: Optional[int] = None
        self._dirty = False
        self._loading = False
        self._creating = False
        self._path_state = "unset"
        self._path_token = 0
        self._path_timer = QTimer(self)
        self._path_timer.setSingleShot(True)
        self._path_timer.setInterval(250)
        self._path_timer.timeout.connect(self._start_path_check)
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(12)
        root.addWidget(page_header("Project command center",
                                   "Projects you add yourself. Sentinel never scans your computer for projects."))
        split = QSplitter(Qt.Orientation.Horizontal)
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 8, 0)
        self.list = QListWidget()
        self.list.currentItemChanged.connect(self._on_select)
        ll.addWidget(self.list, 1)
        new = make_button("New project", primary=True)
        new.clicked.connect(self._new)
        ll.addWidget(new)
        split.addWidget(left)

        right = Card("Details")
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        self.name = QLineEdit()
        self.name.setMaxLength(120)
        self.status = QComboBox()
        self.status.addItems(STATUSES)
        self.status.setCurrentText("Active")
        self.path = QLineEdit()
        self.path.setPlaceholderText("Folder on this PC (optional)")
        browse = make_button("Browse…")
        browse.clicked.connect(self._browse)
        path_row = QHBoxLayout()
        path_row.addWidget(self.path, 1)
        path_row.addWidget(browse)
        self.path_note = make_label("", "muted")
        self.desc = QLineEdit()
        self.desc.setMaxLength(2000)
        self.notes = QPlainTextEdit()
        self.notes.setPlaceholderText("Notes…")
        self.notes.setMinimumHeight(110)
        form.addRow("Name", self.name)
        form.addRow("Status", self.status)
        form.addRow("Folder", path_row)
        form.addRow("", self.path_note)
        form.addRow("Description", self.desc)
        form.addRow("Notes", self.notes)
        right.body.addLayout(form)
        for w in (self.name, self.path, self.desc):
            w.textChanged.connect(self._mark_dirty)
        self.notes.textChanged.connect(self._mark_dirty)
        self.status.currentIndexChanged.connect(self._mark_dirty)
        self.path.textChanged.connect(self._update_path_note)

        row = QHBoxLayout()
        self.save_btn = make_button("Save", primary=True)
        self.save_btn.clicked.connect(self._save)
        self.open_btn = make_button("Open folder")
        self.open_btn.clicked.connect(self._open_folder)
        self.git_btn = make_button("Check Git status", tip="Runs read-only Git commands; nothing is changed.")
        self.git_btn.clicked.connect(self._git)
        self.delete_btn = make_button("Remove project", danger=True,
                                      tip="Removes only Sentinel's record. Your files are never deleted.")
        self.delete_btn.clicked.connect(self._delete)
        self.ai_btn = make_button("Ask AI…", tip="Open the AI Assistant with this project selected. Nothing is sent until you review it.")
        self.ai_btn.clicked.connect(lambda: self._current_id is not None and self.ctrl.ai.request_context("project", self._current_id))
        for b in (self.save_btn, self.open_btn, self.git_btn, self.ai_btn):
            row.addWidget(b)
        row.addStretch(1)
        row.addWidget(self.delete_btn)
        right.body.addLayout(row)
        self.git_label = make_label("", wrap=True)
        right.add(self.git_label)
        side = QWidget()
        sl = QVBoxLayout(side)
        sl.setContentsMargins(0, 0, 0, 0)
        sl.addWidget(right)
        self.tabs = ProjectTabs(ctrl)
        sl.addWidget(self.tabs, 1)
        split.addWidget(side)
        split.setStretchFactor(1, 1)
        split.setSizes([280, 640])
        root.addWidget(split, 1)
        self._set_enabled(False)
        ctrl.projects_changed.connect(self._on_projects_changed)

    def _on_projects_changed(self) -> None:
        """Health/tasks changed: update the list badges in place (no form reload, no selection change)."""
        if not self.isVisible():
            return
        for i in range(self.list.count()):
            item = self.list.item(i)
            p = self.ctrl.projects.get(item.data(Qt.ItemDataRole.UserRole))
            if p is None:
                continue
            rep = self.ctrl.health.load(p.id)
            flag = "   ⚠ folder unavailable" if rep is not None and rep.path_state not in ("ok", "unset") else ""
            item.setText(f"{p.name}   [{p.status}]{flag}")

    def showEvent(self, e) -> None:  # noqa: N802
        super().showEvent(e)
        self.refresh()

    # ----------------------------------------------------------------- list
    def refresh(self, select_id: Optional[int] = None) -> None:
        keep = select_id if select_id is not None else self._current_id
        self.list.blockSignals(True)
        self.list.clear()
        target = None
        for p in self.ctrl.projects.list():
            rep = self.ctrl.health.load(p.id)
            flag = "   ⚠ folder unavailable" if rep is not None and rep.path_state not in ("ok", "unset") else ""
            item = QListWidgetItem(f"{p.name}   [{p.status}]{flag}")
            item.setData(Qt.ItemDataRole.UserRole, p.id)
            self.list.addItem(item)
            if p.id == keep:
                target = item
        self.list.blockSignals(False)
        if target is None and self.list.count() and not self._dirty and not self._creating:
            target = self.list.item(0)
            keep = target.data(Qt.ItemDataRole.UserRole)
        if target is not None:
            self.list.setCurrentItem(target)
            self._load(self.ctrl.projects.get(keep))
        elif self._current_id is not None and keep == self._current_id:
            self._clear()

    def _confirm_discard(self) -> bool:
        if not self._dirty:
            return True
        answer = QMessageBox.question(self, "Unsaved changes", "Discard your unsaved changes to this project?")
        return answer == QMessageBox.StandardButton.Yes

    def _on_select(self, current: Optional[QListWidgetItem], previous: Optional[QListWidgetItem]) -> None:
        if self._loading:
            return
        if current is None:
            return
        pid = current.data(Qt.ItemDataRole.UserRole)
        if pid == self._current_id:
            return
        if not self._confirm_discard():
            self.list.blockSignals(True)
            if previous is not None:
                self.list.setCurrentItem(previous)
            self.list.blockSignals(False)
            return
        self._load(self.ctrl.projects.get(pid))

    def _set_enabled(self, enabled: bool) -> None:
        for w in (self.name, self.status, self.path, self.desc, self.notes, self.save_btn):
            w.setEnabled(enabled)
        has = enabled and self._current_id is not None
        for w in (self.open_btn, self.git_btn, self.delete_btn):
            w.setEnabled(has)

    def _load(self, p: Optional[Project]) -> None:
        if p is None:
            self._clear()
            return
        self._loading = True
        self._current_id = p.id
        self._creating = False
        self.name.setText(p.name)
        self.status.setCurrentText(p.status)
        self.path.setText(p.path)
        self.desc.setText(p.description)
        self.notes.setPlainText(p.notes)
        self.git_label.setText("")
        self._loading = False
        self._dirty = False
        self._set_enabled(True)
        self._update_path_note()
        self.tabs.set_project(p)

    def _clear(self) -> None:
        self._loading = True
        self._current_id = None
        for w in (self.name, self.path, self.desc):
            w.clear()
        self.notes.clear()
        self.status.setCurrentText("Active")
        self.git_label.setText("")
        self._loading = False
        self._dirty = False
        self._set_enabled(False)
        self._update_path_note()
        self.tabs.set_project(None)

    def _new(self) -> None:
        if not self._confirm_discard():
            return
        self.list.blockSignals(True)
        self.list.clearSelection()
        self.list.setCurrentItem(None)
        self.list.blockSignals(False)
        self._clear()
        self._creating = True
        self._set_enabled(True)
        for w in (self.open_btn, self.git_btn, self.delete_btn):
            w.setEnabled(False)
        self.name.setFocus()

    def _mark_dirty(self, *_a) -> None:
        if not self._loading:
            self._dirty = True

    def _update_path_note(self, *_a) -> None:
        """Never touches the file system on the UI thread: shows 'checking' and verifies in the background."""
        text = self.path.text().strip()
        self._path_token += 1
        if not text:
            self._apply_path_state("unset")
            self._path_timer.stop()
            return
        self.path_note.setText("Checking folder…")
        set_state(self.path_note, "off")
        self.open_btn.setEnabled(False)
        self.git_btn.setEnabled(False)
        self._path_timer.start()

    def _start_path_check(self) -> None:
        text, token = self.path.text().strip(), self._path_token

        def done(state: str) -> None:
            if token == self._path_token:                     # ignore answers for text the user already changed
                self._apply_path_state(state)

        def failed(_msg: str) -> None:
            if token == self._path_token:
                self._apply_path_state("unreachable")

        self.ctrl.jobs.submit(lambda _c, _p: inspect_path(text), done, failed, name="Folder check", timeout_s=15.0)

    def _apply_path_state(self, state: str) -> None:
        self._path_state = state
        self.path_note.setText(PATH_TEXT[state])
        set_state(self.path_note, "ok" if state == "ok" else ("off" if state == "unset" else "warn"))
        usable = state == "ok" and self._current_id is not None
        self.open_btn.setEnabled(usable)
        self.git_btn.setEnabled(usable)

    # --------------------------------------------------------------- actions
    def _browse(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Choose the project folder", self.path.text() or "")
        if folder:
            self.path.setText(folder)

    def _save(self) -> None:
        store = self.ctrl.projects
        args = (self.name.text(), self.path.text(), self.desc.text(), self.status.currentText(),
                self.notes.toPlainText())
        try:
            if self._current_id is None:
                project = store.add(*args)
                self.ctrl.log("project", "project_added", f"Project added: {project.name}")
            else:
                project = store.update(self._current_id, *args)
                self.ctrl.log("project", "project_updated", f"Project updated: {project.name}")
        except ProjectError as exc:
            QMessageBox.warning(self, "Cannot save project", str(exc))
            return
        except DatabaseError as exc:
            QMessageBox.critical(self, "Database problem", str(exc))
            return
        self._dirty = False
        self.refresh(select_id=project.id)

    def _delete(self) -> None:
        if self._current_id is None:
            return
        name = self.name.text()
        if QMessageBox.question(self, "Remove project",
                                f"Remove “{name}” from Sentinel?\n\nOnly this record is removed. "
                                "Your project folder and files are not touched.") != QMessageBox.StandardButton.Yes:
            return
        self.ctrl.projects.remove(self._current_id)
        self.ctrl.log("project", "project_removed", f"Project removed: {name}")
        self._current_id = None
        self._dirty = False
        self.refresh()
        self._clear()

    def _open_folder(self) -> None:
        text = self.path.text().strip()
        state = self._path_state
        if state != "ok":
            QMessageBox.information(self, "Cannot open folder", PATH_TEXT[state])
            return
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(text)):
            QMessageBox.warning(self, "Cannot open folder", "Windows could not open this folder.")

    def _git(self) -> None:
        text = self.path.text().strip()
        self.git_label.setText("Checking Git status…")
        set_state(self.git_label, "off")
        self.git_btn.setEnabled(False)

        def work(_cancel, _progress):
            return get_git_status(text)

        def done(status) -> None:
            lines = [status.summary()]
            if status.last_commit:
                lines.append(f"Last commit: {status.last_commit}")
            self.git_label.setText("\n".join(lines))
            set_state(self.git_label, "ok" if status.is_repo and status.clean else
                      ("warn" if status.error or (status.is_repo and not status.clean) else "off"))
            self._update_path_note()

        def failed(message: str) -> None:
            self.git_label.setText(f"Git check failed: {message}")
            set_state(self.git_label, "warn")
            self._update_path_note()

        self.ctrl.jobs.submit(work, done, failed, name="Git status", timeout_s=30.0)
