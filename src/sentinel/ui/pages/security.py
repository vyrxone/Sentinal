from __future__ import annotations

import os
import threading
from datetime import datetime
from typing import Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QFileDialog, QHBoxLayout, QHeaderView, QInputDialog,
                               QMessageBox, QProgressBar, QTableWidget, QTableWidgetItem, QVBoxLayout,
                               QWidget)

from ...core.formatting import human_bytes, short_hash
from ...security.info import DISCLAIMER, LIMITATIONS
from ...security.integrity import plan_folder_baseline
from ...security.service import SecurityError
from ...storage.database import DatabaseError
from ..widgets import Card, make_button, make_label, page_header, set_state


def _ts(value: Optional[float]) -> str:
    return datetime.fromtimestamp(value).strftime("%Y-%m-%d %H:%M") if value else "never"


class SecurityPage(QWidget):
    title = "Security"

    def __init__(self, ctrl) -> None:
        super().__init__()
        self.ctrl = ctrl
        self._cancel: Optional[threading.Event] = None
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(12)
        root.addWidget(page_header("Defensive security center"))
        banner = make_label(DISCLAIMER, "banner", wrap=True)
        root.addWidget(banner)

        # --- watched folders -------------------------------------------------
        folders = Card("Watched folders")
        top = QHBoxLayout()
        self.watch_state = make_label("", wrap=True)
        top.addWidget(self.watch_state, 1)
        self.toggle_btn = make_button("Stop watching")
        self.toggle_btn.clicked.connect(self._toggle_watching)
        top.addWidget(self.toggle_btn)
        folders.body.addLayout(top)
        self.folder_table = self._table(["Folder", "Scope", "Status"])
        self.folder_table.setMinimumHeight(120)
        self.folder_table.setMaximumHeight(170)
        folders.add(self.folder_table)
        row = QHBoxLayout()
        self.recursive = QCheckBox("Include subfolders")
        self.recursive.setChecked(True)
        add = make_button("Add folder…", primary=True)
        add.clicked.connect(self._add_folder)
        self.remove_folder_btn = make_button("Stop watching selected folder")
        self.remove_folder_btn.clicked.connect(self._remove_folder)
        self.excl_btn = make_button("Exclusions…", tip="Names or patterns (for example node_modules or *.tmp) that "
                                    "Sentinel should not report for the selected folder.")
        self.excl_btn.clicked.connect(self._edit_excludes)
        row.addWidget(add)
        row.addWidget(self.recursive)
        row.addWidget(self.excl_btn)
        row.addStretch(1)
        row.addWidget(self.remove_folder_btn)
        folders.body.addLayout(row)
        self.stats = make_label("", "muted")
        folders.add(self.stats)
        root.addWidget(folders)

        # --- integrity baseline --------------------------------------------
        base = Card("File integrity baseline (SHA-256)")
        base.add(make_label("Choose the files you care about. Sentinel records their SHA-256 hashes and can later "
                            "tell you if the content differs. Nothing is hashed until you ask.", "muted", wrap=True))
        self.base_table = self._table(["File", "SHA-256", "Size", "Baseline taken", "Last verified"])
        self.base_table.setMinimumHeight(170)
        base.add(self.base_table, 1)
        buttons = QHBoxLayout()
        self.add_files_btn = make_button("Add files…", primary=True)
        self.add_files_btn.clicked.connect(self._add_files)
        self.add_dir_btn = make_button("Add folder…", tip="Hashes the files in a folder (with safety limits).")
        self.add_dir_btn.clicked.connect(self._add_dir)
        self.verify_btn = make_button("Verify baseline now")
        self.verify_btn.clicked.connect(self._verify)
        self.accept_btn = make_button("Accept current state of selected")
        self.accept_btn.clicked.connect(self._accept)
        self.remove_btn = make_button("Remove selected")
        self.remove_btn.clicked.connect(self._remove)
        for b in (self.add_files_btn, self.add_dir_btn, self.verify_btn, self.accept_btn, self.remove_btn):
            buttons.addWidget(b)
        buttons.addStretch(1)
        base.body.addLayout(buttons)
        prow = QHBoxLayout()
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        self.cancel_btn = make_button("Cancel")
        self.cancel_btn.setVisible(False)
        self.cancel_btn.clicked.connect(self._cancel_job)
        self.progress_label = make_label("", "muted")
        prow.addWidget(self.progress, 1)
        prow.addWidget(self.cancel_btn)
        base.body.addLayout(prow)
        base.add(self.progress_label)
        root.addWidget(base, 1)

        lim = Card("What Sentinel can and cannot see")
        lim.add(make_label("• " + "\n• ".join(LIMITATIONS), "muted", wrap=True))
        root.addWidget(lim)

        self._timer = QTimer(self)
        self._timer.setInterval(2000)
        self._timer.timeout.connect(self._refresh_stats)
        ctrl.state_changed.connect(self.refresh)

    @staticmethod
    def _table(headers: list[str]) -> QTableWidget:
        t = QTableWidget(0, len(headers))
        t.setHorizontalHeaderLabels(headers)
        t.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        t.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        t.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        t.verticalHeader().setVisible(False)
        t.setAlternatingRowColors(True)
        t.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for i in range(1, len(headers)):
            t.horizontalHeader().setSectionResizeMode(i, QHeaderView.ResizeMode.ResizeToContents)
        return t

    def showEvent(self, e) -> None:  # noqa: N802
        super().showEvent(e)
        self._timer.start()
        self.refresh()

    def hideEvent(self, e) -> None:  # noqa: N802
        super().hideEvent(e)
        self._timer.stop()

    # ---------------------------------------------------------------- refresh
    def refresh(self) -> None:
        if not self.isVisible():
            return
        c = self.ctrl
        folders = c.security.list_folders()
        if c.security_active:
            self.watch_state.setText(f"Watching {len([f for f in folders if f.state == 'watching'])} of "
                                     f"{len(folders)} folder(s). Observation only - files are never changed.")
            set_state(self.watch_state, "ok")
            self.toggle_btn.setText("Stop watching")
        else:
            self.watch_state.setText("Folder watching is stopped. No events are being recorded.")
            set_state(self.watch_state, "off")
            self.toggle_btn.setText("Start watching")
        t = self.folder_table
        t.setRowCount(len(folders))
        names = {"watching": "Watching", "stopped": "Not watching", "unavailable": "Folder unavailable"}
        for r, f in enumerate(folders):
            t.setItem(r, 0, QTableWidgetItem(f.path))
            t.setItem(r, 1, QTableWidgetItem("With subfolders" if f.recursive else "Top level only"))
            t.setItem(r, 2, QTableWidgetItem(names.get(f.state, f.state)))
        self._fill_baseline()
        self._refresh_stats()

    def _refresh_stats(self) -> None:
        s = self.ctrl.security.stats()
        self.stats.setText(f"Events processed: {s['processed']} · queued: {s['queued']} · "
                           f"dropped (overflow): {s['dropped']} · rate-limited: {s['suppressed']} · excluded: {s['excluded']}")

    def _fill_baseline(self) -> None:
        entries = self.ctrl.baseline.list()
        t = self.base_table
        t.setRowCount(len(entries))
        for r, e in enumerate(entries):
            item = QTableWidgetItem(e.path)
            item.setData(Qt.ItemDataRole.UserRole, e.path)
            item.setToolTip(e.path)
            t.setItem(r, 0, item)
            h = QTableWidgetItem(short_hash(e.sha256))
            h.setToolTip(e.sha256)
            t.setItem(r, 1, h)
            t.setItem(r, 2, QTableWidgetItem(human_bytes(e.size)))
            t.setItem(r, 3, QTableWidgetItem(_ts(e.added_ts)))
            t.setItem(r, 4, QTableWidgetItem(_ts(e.verified_ts)))

    def _selected_paths(self) -> list[str]:
        rows = sorted({i.row() for i in self.base_table.selectedIndexes()})
        return [self.base_table.item(r, 0).data(Qt.ItemDataRole.UserRole) for r in rows]

    # ---------------------------------------------------------------- folders
    def _toggle_watching(self) -> None:
        self.ctrl.set_security(not self.ctrl.security_active)

    def _add_folder(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Choose a folder to watch")
        if not path:
            return
        try:
            self.ctrl.security.add_folder(path, self.recursive.isChecked())
        except SecurityError as exc:
            QMessageBox.warning(self, "Cannot watch this folder", str(exc))
        except DatabaseError as exc:
            QMessageBox.critical(self, "Database problem", str(exc))
        self.refresh()

    def _edit_excludes(self) -> None:
        row = self.folder_table.currentRow()
        if row < 0:
            QMessageBox.information(self, "Exclusions", "Select a watched folder in the list first.")
            return
        path = self.folder_table.item(row, 0).text()
        current = next((f.excludes for f in self.ctrl.security.list_folders() if f.path == path), ())
        text, ok = QInputDialog.getMultiLineText(
            self, "Exclusions",
            f"One name or pattern per line for:\n{path}\n\nMatching files and folders are not reported "
            "(they are still checked by 'Verify baseline now'). Patterns like *.tmp and names like node_modules work.",
            "\n".join(current))
        if ok:
            clean = self.ctrl.security.set_excludes(path, text.splitlines())
            QMessageBox.information(self, "Exclusions saved", f"{len(clean)} exclusion pattern(s) saved.")
            self.refresh()

    def _remove_folder(self) -> None:
        row = self.folder_table.currentRow()
        if row < 0:
            QMessageBox.information(self, "Stop watching", "Select a folder in the list first.")
            return
        path = self.folder_table.item(row, 0).text()
        if QMessageBox.question(self, "Stop watching folder",
                                f"Stop watching this folder?\n\n{path}\n\nYour files are not affected.") \
                == QMessageBox.StandardButton.Yes:
            self.ctrl.security.remove_folder(path)
            self.refresh()

    # --------------------------------------------------------------- baseline
    def _busy(self, label: str, indeterminate: bool = False) -> None:
        for b in (self.add_files_btn, self.add_dir_btn, self.verify_btn, self.accept_btn, self.remove_btn):
            b.setEnabled(False)
        self.progress.setVisible(True)
        self.cancel_btn.setVisible(True)
        self.progress.setRange(0, 0 if indeterminate else 100)
        self.progress.setValue(0)
        self.progress_label.setText(label)

    def _idle(self, message: str = "") -> None:
        for b in (self.add_files_btn, self.add_dir_btn, self.verify_btn, self.accept_btn, self.remove_btn):
            b.setEnabled(True)
        self.progress.setVisible(False)
        self.cancel_btn.setVisible(False)
        self.progress_label.setText(message)
        self._cancel = None
        self.refresh()

    def _on_progress(self, done: int, total: int, label: str) -> None:
        self.progress.setRange(0, max(1, total))
        self.progress.setValue(done)
        if label:
            self.progress_label.setText(f"{done}/{total}  {os.path.basename(label)}")

    def _cancel_job(self) -> None:
        if self._cancel is not None:
            self._cancel.set()
            self.progress_label.setText("Cancelling…")

    def _hash_paths(self, paths: list[str], what: str, replace: bool = False) -> None:
        self._busy(f"Hashing {len(paths)} file(s)…")
        store = self.ctrl.baseline

        def work(cancel, progress):
            return store.add_files(paths, cancel, progress, replace=replace)

        def done(summary) -> None:
            note = f"Baseline updated: {summary.added} added, {summary.updated} refreshed"
            if summary.failed:
                note += f", {len(summary.failed)} could not be read"
            if summary.cancelled:
                note += " (cancelled - the remaining files were not added)"
            if summary.conflicts:
                note += f", {len(summary.conflicts)} already in the baseline (not replaced)"
            self.ctrl.log("security", "baseline_added", f"{what}: {note}.",
                          details={"failed": [f"{os.path.basename(p)}: {e}" for p, e in summary.failed[:20]]})
            self._idle(note + ".")
            if summary.conflicts and not summary.cancelled:
                if QMessageBox.question(
                        self, "Replace existing baseline entries?",
                        f"{len(summary.conflicts)} file(s) already have a baseline hash. Replacing it records "
                        "their CURRENT content as the new trusted state. Replace them?"
                ) == QMessageBox.StandardButton.Yes:
                    self._hash_paths(summary.conflicts, "Baseline replaced (confirmed)", replace=True)
                    return
            if summary.failed:
                QMessageBox.warning(self, "Some files were skipped", "\n".join(
                    f"{os.path.basename(p)}: {e}" for p, e in summary.failed[:15]))

        self._cancel = self.ctrl.jobs.submit(work, done, self._failed, self._on_progress)

    def _failed(self, message: str) -> None:
        self._idle("")
        QMessageBox.critical(self, "Operation failed", message)

    def _add_files(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(self, "Choose files to add to the integrity baseline")
        if files:
            self._hash_paths(files, "Files added")

    def _add_dir(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Choose a folder whose files should be baselined")
        if not folder:
            return
        self._busy("Counting files (nothing is hashed yet)…", indeterminate=True)

        def work(cancel, _progress):
            return plan_folder_baseline(folder, cancel=cancel)

        def planned(plan) -> None:
            self._idle("")
            if not plan.files:
                QMessageBox.information(self, "Nothing to baseline", "No readable files were found in that folder.")
                return
            text = (f"Sentinel will read and hash {len(plan.files)} file(s) ({human_bytes(plan.total_bytes)}) "
                    f"in:\n{folder}\n\nSubfolders are included; symbolic links are skipped.")
            if plan.truncated:
                text += ("\n\nThe folder is large, so only the first files up to the safety limit are included. "
                         "Choose a smaller folder or add specific files for full coverage.")
            if QMessageBox.question(self, "Hash these files?", text) == QMessageBox.StandardButton.Yes:
                self._hash_paths(plan.files, f"Folder added ({os.path.basename(folder) or folder})")

        self._cancel = self.ctrl.jobs.submit(work, planned, self._failed)

    def _verify(self) -> None:
        if self.ctrl.baseline.count() == 0:
            QMessageBox.information(self, "Nothing to verify", "Add files to the baseline first.")
            return
        self._busy("Verifying…")
        service = self.ctrl.security

        def work(cancel, progress):
            return service.verify_baseline(cancel, progress)

        def done(results) -> None:
            bad = [r for r in results if r.status != "ok"]
            report = getattr(service, "last_report", None)
            if report is not None and not report.complete:
                self._idle(f"Verification stopped early: {len(results)} of {report.total} file(s) checked. "
                           "This is NOT a complete verification.")
            else:
                self._idle(f"Verified {len(results)} file(s): {len(results) - len(bad)} unchanged, "
                           f"{len(bad)} need review.")
            if bad:
                lines = [f"{os.path.basename(r.path)}: {r.status}" for r in bad[:15]]
                QMessageBox.warning(
                    self, "Differences found",
                    "These files differ from your baseline:\n\n" + "\n".join(lines) +
                    "\n\nA difference is not proof of malware - software updates and normal edits change files. "
                    "Details are in the Events page.")

        self._cancel = self.ctrl.jobs.submit(work, done, self._failed, self._on_progress)

    def _accept(self) -> None:
        paths = self._selected_paths()
        if not paths:
            QMessageBox.information(self, "Accept current state", "Select one or more baseline files first.")
            return
        if QMessageBox.question(
                self, "Accept current state?",
                f"This replaces the recorded hash of {len(paths)} file(s) with their current content. "
                "Do this only if you know why they changed.") != QMessageBox.StandardButton.Yes:
            return
        self._hash_paths(paths, "Baseline refreshed", replace=True)

    def _remove(self) -> None:
        paths = self._selected_paths()
        if not paths:
            QMessageBox.information(self, "Remove", "Select one or more baseline files first.")
            return
        if QMessageBox.question(self, "Remove from baseline",
                                f"Remove {len(paths)} file(s) from the baseline? The files themselves are not touched.") \
                != QMessageBox.StandardButton.Yes:
            return
        for p in paths:
            self.ctrl.baseline.remove(p)
        self.ctrl.log("security", "baseline_removed", f"{len(paths)} file(s) removed from the baseline.")
        self.refresh()
