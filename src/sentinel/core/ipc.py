"""Single-instance guard and the control channel used by stop_sentinel.bat.

A local-only named pipe / Unix socket accepts three whitelisted commands:
``ping``, ``show`` and ``quit``. Stopping Sentinel therefore never requires
killing a process.
"""

from __future__ import annotations

import getpass
import logging
import os
import re
from typing import Optional

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

log = logging.getLogger("sentinel.ipc")

COMMANDS = ("ping", "show", "quit")


def default_name() -> str:
    override = os.environ.get("SENTINEL_INSTANCE_NAME")   # used by automated tests only
    if override:
        return re.sub(r"[^A-Za-z0-9_.-]", "", override)[:64] or "vyrxone-sentinel"
    try:
        user = re.sub(r"[^A-Za-z0-9]", "", getpass.getuser())[:24] or "user"
    except Exception:
        user = "user"
    return f"vyrxone-sentinel-{user}"


def send_command(command: str, name: Optional[str] = None, timeout_ms: int = 1500) -> Optional[str]:
    """Send a command to a running instance. Returns its reply or None if none is running."""
    if command not in COMMANDS:
        raise ValueError(f"unknown command {command!r}")
    sock = QLocalSocket()
    sock.connectToServer(name or default_name())
    if not sock.waitForConnected(timeout_ms):
        return None
    try:
        sock.write((command + "\n").encode("ascii"))
        sock.waitForBytesWritten(timeout_ms)
        if sock.waitForReadyRead(timeout_ms):
            return bytes(sock.readAll()).decode("ascii", "replace").strip()
        return None
    finally:
        sock.disconnectFromServer()
        sock.close()


class InstanceServer(QObject):
    command_received = Signal(str)

    def __init__(self, name: Optional[str] = None, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self.name = name or default_name()
        self._server = QLocalServer(self)
        self._server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        self._server.newConnection.connect(self._on_connection)

    def listen(self) -> bool:
        """Call only after send_command('ping') found no live instance."""
        QLocalServer.removeServer(self.name)  # clears a stale socket file after a crash
        ok = self._server.listen(self.name)
        if not ok:
            log.warning("single-instance server could not listen: %s", self._server.errorString())
        return ok

    def close(self) -> None:
        self._server.close()

    def _on_connection(self) -> None:
        while self._server.hasPendingConnections():
            sock = self._server.nextPendingConnection()
            if sock is None:
                return
            sock.readyRead.connect(self._make_reader(sock))
            sock.disconnected.connect(sock.deleteLater)
            if sock.bytesAvailable():  # data may already be buffered
                self._on_ready(sock)

    def _make_reader(self, sock: QLocalSocket):
        # A plain closure (no default arguments) is what PySide6 invokes reliably
        # for a zero-argument signal such as readyRead.
        def reader() -> None:
            self._on_ready(sock)

        return reader

    def _on_ready(self, sock: QLocalSocket) -> None:
        if not sock.bytesAvailable():
            return
        raw = bytes(sock.read(64)).decode("ascii", "ignore").strip().lower()
        if raw == "ping":
            reply = "pong"
        elif raw in ("show", "quit"):
            reply = "ok"
        else:
            reply = "error"
        sock.write(reply.encode("ascii"))
        sock.flush()
        sock.disconnectFromServer()
        if reply == "ok":
            self.command_received.emit(raw)
