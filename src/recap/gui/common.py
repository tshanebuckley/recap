"""What recap's panels share: the open workspace and image, background work, questions from a
background thread, small table helpers, and the names remembered for this user.

Every panel reads the state as `common.STATE` (never a copy), so tests can swap it.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from qtpy.QtCore import QObject, Qt, QThread, Signal
from qtpy.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QLineEdit,
    QMessageBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from recap.workspace import Workspace

MAX_RECENT = 8


@dataclass
class SharedState:
    workspace: Workspace | None = None
    sha: str | None = None  # the image open in the viewer
    on_workspace: list[Callable] = field(default_factory=list)  # fn(workspace) when one is opened
    on_open: list[Callable] = field(default_factory=list)  # fn(sha) when an image is opened
    on_changed: list[Callable] = field(default_factory=list)  # fn() when the workspace's data changed
    asked_name: bool = False  # the counter's name was confirmed in this session

    def changed(self) -> None:
        for fn in self.on_changed:
            fn()


STATE = SharedState()


class Bridge(QObject):
    """Carries messages from a background thread to the window (Qt queues them safely)."""

    message = Signal(str)
    progress = Signal(int, int)


def run_in_background(fn, on_done, on_error, bridge: Bridge | None = None):
    """Run fn() in a thread (napari's thread_worker); on_done(result) / on_error(exc) run in the window."""
    from napari.qt.threading import create_worker

    worker = create_worker(fn)
    worker.returned.connect(on_done)
    worker.errored.connect(on_error)
    worker.start()
    return worker


class Asker(QObject):
    """Lets work in a background thread ask the person something and wait for the answer
    (e.g. the spot check's decision, or whether to keep points from a closed Fiji)."""

    _ask = Signal(object)

    def __init__(self, parent_widget):
        super().__init__()
        self.parent_widget = parent_widget
        self._ask.connect(self._answer, Qt.ConnectionType.BlockingQueuedConnection)

    def _answer(self, req) -> None:
        kind, text, box = req
        w = self.parent_widget
        if kind == "yesno":
            box.append(QMessageBox.question(w, "recap", text) == QMessageBox.StandardButton.Yes)
        else:  # the spot check's decision after its overview
            m = QMessageBox(w)
            m.setWindowTitle("recap: spot check")
            m.setText("Spot check done. Run every image, keep only the counts, or cancel?")
            m.setDetailedText(text)
            run = m.addButton("Run all images", QMessageBox.ButtonRole.AcceptRole)
            save = m.addButton("Save counts only", QMessageBox.ButtonRole.ActionRole)
            m.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
            m.exec()
            clicked = m.clickedButton()
            box.append("run_all" if clicked is run else "save" if clicked is save else "cancel")

    def __call__(self, kind: str, text: str):
        box: list = []
        if QThread.currentThread() is self.thread():  # already in the window's thread
            self._answer((kind, text, box))
        else:
            self._ask.emit((kind, text, box))
        return box[0]

    def yesno(self, text: str) -> bool:
        return self("yesno", text)

    def decide(self, text: str) -> str:
        return self("decide", text)


# ---- tables ---------------------------------------------------------------------------------------
def table(columns: list[str], multi: bool = False) -> QTableWidget:
    t = QTableWidget(0, len(columns))
    t.setHorizontalHeaderLabels(columns)
    t.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    t.setSelectionMode(
        QAbstractItemView.SelectionMode.ExtendedSelection if multi else QAbstractItemView.SelectionMode.SingleSelection
    )
    t.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    t.verticalHeader().setVisible(False)
    return t


def fill(t: QTableWidget, rows: list[list], keys: list | None = None) -> None:
    t.setSortingEnabled(False)
    t.setRowCount(len(rows))
    for i, row in enumerate(rows):
        for j, v in enumerate(row):
            item = QTableWidgetItem("" if v is None else str(v))
            if keys is not None and j == 0:
                item.setData(Qt.ItemDataRole.UserRole, keys[i])
            t.setItem(i, j, item)
    t.resizeColumnsToContents()
    t.setColumnWidth(0, min(t.columnWidth(0), 240))  # long file names: the other columns stay visible
    t.setSortingEnabled(True)


def fit_height(t: QTableWidget, max_rows: int = 8) -> QTableWidget:
    rows = min(max(t.rowCount(), 1), max_rows)
    t.setMaximumHeight(t.horizontalHeader().height() + rows * t.verticalHeader().defaultSectionSize() + 6)
    return t


# ---- remembered for this user (recap's user settings file) ----------------------------------------
def counter_name(parent, ask: bool = False) -> str | None:
    """The name counts and corrected masks are saved under. Asked once per session (or when ask),
    with the option to remember it on this computer. None if the person cancels."""
    from recap.counts import default_counter_name, remember_counter_name
    from recap.fiji import load_settings

    name = default_counter_name()
    if not ask and (STATE.asked_name or load_settings().get("counter_name")):  # remembered: change in More
        return name
    d = QDialog(parent)
    d.setWindowTitle("recap: who is working?")
    lay = QVBoxLayout(d)
    lay.addWidget(QLabel("Your name, saved with counts and corrected masks:"))
    edit = QLineEdit(name)
    lay.addWidget(edit)
    keep = QCheckBox("Remember on this computer")
    keep.setChecked(True)
    lay.addWidget(keep)
    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
    buttons.accepted.connect(d.accept)
    buttons.rejected.connect(d.reject)
    lay.addWidget(buttons)
    if d.exec() != QDialog.DialogCode.Accepted or not edit.text().strip():
        return None
    if keep.isChecked():
        remember_counter_name(edit.text())
    STATE.asked_name = True
    return edit.text().strip()


def recent_workspaces() -> list[Path]:
    from recap.fiji import load_settings

    return [Path(p) for p in load_settings().get("recent_workspaces", []) if Path(p).is_dir()]


def remember_workspace(root: Path) -> None:
    from recap.fiji import load_settings, save_settings

    d = load_settings()
    paths = [str(root)] + [p for p in d.get("recent_workspaces", []) if p != str(root)]
    d["recent_workspaces"] = paths[:MAX_RECENT]
    save_settings(d)
