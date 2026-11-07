import os

# Must be set before any Qt module is imported so UI tests run headless.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from sentinel.storage.database import Database
from sentinel.storage.events_store import EventStore
from sentinel.storage.settings_store import SettingsStore


@pytest.fixture(autouse=True)
def isolated_data_dir(tmp_path, monkeypatch):
    """Never touch the real user data folder during tests."""
    data = tmp_path / "appdata"
    monkeypatch.setenv("SENTINEL_DATA_DIR", str(data))
    return data


@pytest.fixture
def db(tmp_path):
    database = Database(tmp_path / "test.db")
    yield database
    database.close()


@pytest.fixture
def events(db):
    return EventStore(db)


@pytest.fixture
def settings(db):
    return SettingsStore(db)


# --------------------------------------------------------------------------- Qt
@pytest.fixture(scope="session", autouse=True)
def qapp():
    """One QApplication for the whole run (a QCoreApplication would block creating it later)."""
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    from sentinel.ui import theme

    app.setStyleSheet(theme.STYLESHEET)
    return app


@pytest.fixture
def pump(qapp):
    import time

    def _pump(seconds: float = 0.2, until=None) -> bool:
        end = time.time() + seconds
        while time.time() < end:
            qapp.processEvents()
            if until is not None and until():
                return True
            time.sleep(0.01)
        qapp.processEvents()
        return bool(until()) if until is not None else True

    return _pump


@pytest.fixture
def ctrl(db, qapp):
    from sentinel.controller import AppController
    from sentinel.ui.wallpaper_host import WallpaperHost

    c = AppController(db)
    c.host = WallpaperHost(c)
    yield c
    c.shutdown()


@pytest.fixture
def dialogs(monkeypatch):
    """Replace modal dialogs so UI tests never block; records what was shown."""
    from PySide6.QtWidgets import QMessageBox

    log = {"question": [], "warning": [], "information": [], "critical": [], "answer": QMessageBox.StandardButton.Yes}

    def make(kind):
        def fake(*args, **kwargs):
            log[kind].append(args[1:3] if len(args) >= 3 else args)
            return log["answer"] if kind == "question" else QMessageBox.StandardButton.Ok
        return staticmethod(fake)

    for kind in ("question", "warning", "information", "critical"):
        monkeypatch.setattr(QMessageBox, kind, make(kind))
    return log
