"""One running recap app per user.

A lock file decides who runs: the first app holds it until it exits; Qt clears it automatically if
that app crashed (it checks whether the process in the lock still exists). The running app also
listens on a local socket (a named pipe on Windows), so a second launch, e.g. a double-click on the
desktop icon, asks it to come to the front and then exits.

Only QtCore is used: this works before napari (slow to import) is loaded.
"""

from __future__ import annotations

import getpass
import hashlib
from collections.abc import Callable
from pathlib import Path

from qtpy.QtCore import QDir, QLockFile
from qtpy.QtNetwork import QLocalServer, QLocalSocket

ACTIVATE = b"activate"


def default_key() -> str:
    """Per-user name for the lock and socket (safe characters only)."""
    user = hashlib.sha1(getpass.getuser().encode()).hexdigest()[:10]
    return f"recap-gui-{user}"


class SingleInstance:
    def __init__(self, key: str | None = None, on_activate: Callable[[], None] | None = None):
        self.key = key or default_key()
        self.on_activate = on_activate
        self._lock = QLockFile(str(Path(QDir.tempPath()) / f"{self.key}.lock"))
        self._lock.setStaleLockTime(0)  # never "stale" by age: the app may run for days
        self._server: QLocalServer | None = None

    def acquire(self) -> bool:
        """True: this process is the app. False: one is already running (it was asked to come forward).

        Needs no Qt application. Call listen() once one exists, so later launches can reach this one."""
        if not self._lock.tryLock(200):
            self._notify_running()
            return False
        return True

    def listen(self) -> None:
        """Accept "come to the front" requests from later launches (needs a running Qt application)."""
        QLocalServer.removeServer(self.key)  # socket left behind by a crashed app (Linux/macOS)
        self._server = QLocalServer()
        self._server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        self._server.newConnection.connect(self._handle_connection)
        self._server.listen(self.key)  # if this fails the app still runs; only activation is lost

    def release(self) -> None:
        if self._server is not None:
            self._server.close()
            self._server = None
        self._lock.unlock()

    def _notify_running(self) -> None:
        sock = QLocalSocket()
        sock.connectToServer(self.key)
        if sock.waitForConnected(1000):
            sock.write(ACTIVATE)
            sock.flush()
            sock.waitForBytesWritten(1000)
            # let the running app read it before hanging up (Windows named pipes drop unread data)
            sock.waitForDisconnected(1000)
            sock.disconnectFromServer()

    def _handle_connection(self) -> None:
        """New connection: read it when its data arrives (asynchronously; blocking reads here lose
        the message on Windows)."""
        while self._server is not None and self._server.hasPendingConnections():
            conn = self._server.nextPendingConnection()
            conn.readyRead.connect(lambda c=conn: self._read(c))
            if conn.bytesAvailable():
                self._read(conn)

    def _read(self, conn) -> None:
        if ACTIVATE in conn.readAll().data() and self.on_activate:
            self.on_activate()
        conn.disconnectFromServer()  # tells the second launch it was heard
