"""Dialogs of the recap panel: an image's summary, batches, Fiji's location, and making a folder
a workspace."""

from __future__ import annotations

from pathlib import Path

from qtpy.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from recap.errors import RecapError
from recap.gui import common as C
from recap.workspace import Workspace


class SummaryDialog(QDialog):
    """One image: file details, program results per run, manual counts, edited masks, and plots."""

    def __init__(self, ws: Workspace, sha: str, parent=None):
        super().__init__(parent)
        info = ws.image(sha)
        im = info["image"]
        self.setWindowTitle(f"recap: {im['name']}")
        lay = QVBoxLayout(self)
        lines = [
            f"<b>{im['name']}</b>  {im['width']} x {im['height']} px, {im['channels']} channel(s), {im['dtype']}"
            + (f", {im['pixel_size']:.3f} um/px" if im["pixel_size"] else ""),
            f"<small>{im['path']}<br>SHA-256 {sha[:16]}...</small>",
        ]
        lay.addWidget(QLabel("<br>".join(lines)))
        lay.addWidget(QLabel("Program results (one row per run)"))
        t = C.table(["run", "method", "nuclei", "range", "VRC+", "QC"])
        C.fill(t, [[r["stamp"], r["method"], r["n_nuclei"], f"{r['n_nuclei_low']}-{r['n_nuclei_high']}",
                   r["n_infected"], r["qc_flags"]] for r in info["results"]])  # fmt: skip
        lay.addWidget(C.fit_height(t))
        lay.addWidget(QLabel("Manual counts"))
        t = C.table(["counted by", "tool", "when", "nuclei", "VRC+"])
        C.fill(t, [[c.counted_by, c.tool, c.counted_at, c.nuclei, c.infected] for c in info["counts"]])
        lay.addWidget(C.fit_height(t))
        lay.addWidget(QLabel(f"Edited masks: {len(info['masks'])}"
                             + (f" (latest: {info['masks'][-1]['n_nuclei']} nuclei by {info['masks'][-1]['edited_by']})"
                                if info["masks"] else "")))  # fmt: skip
        table = ws.abs(info["results"][-1]["table_path"]) if info["results"] else None
        if table is not None and table.is_file():
            lay.addWidget(self._plots(table, info))
        self.resize(720, 600)

    @staticmethod
    def _plots(table: Path, info: dict) -> QWidget:
        import pandas as pd
        from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
        from matplotlib.figure import Figure

        df = pd.read_csv(table)
        fig = Figure(figsize=(7, 2.6), layout="constrained")
        ax1, ax2, ax3 = fig.subplots(1, 3)
        ax1.hist(df["area"], bins=30, color="#2a78d6")
        ax1.set_title("nucleus area (px)", fontsize=9)
        ax2.hist(df["vrc_area_fraction"], bins=30, color="#1baf7a")
        ax2.set_title("VRC+ area fraction per nucleus", fontsize=9)
        labels, values = ["program"], [len(df)]
        for c in info["counts"][-3:]:
            labels.append(c.counted_by or c.tool or "manual")
            values.append(c.nuclei)
        ax3.bar(labels, values, color=["#2a78d6"] + ["#eb6834"] * (len(values) - 1))
        ax3.set_title("nuclei: program vs manual", fontsize=9)
        for ax in (ax1, ax2, ax3):
            ax.tick_params(labelsize=7)
            for side in ("top", "right"):
                ax.spines[side].set_visible(False)
        return FigureCanvasQTAgg(fig)


class BatchesDialog(QDialog):
    """Staining batches: their images and settings; rename and delete."""

    def __init__(self, ws: Workspace, parent=None):
        super().__init__(parent)
        self.ws = ws
        self.setWindowTitle("recap: staining batches")
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Images stained together share settings. Put images in a batch with "
                             "Set batch (select several rows first)."))  # fmt: skip
        self.t = C.table(["batch", "images", "settings", "note"])
        lay.addWidget(self.t)
        row = QHBoxLayout()
        for label, fn in (("Rename...", self.rename), ("Delete", self.delete), ("Close", self.accept)):
            b = QPushButton(label)
            b.clicked.connect(fn)
            row.addWidget(b)
        lay.addLayout(row)
        self.refresh()
        self.resize(640, 320)

    def refresh(self) -> None:
        rows = self.ws.batches()

        def settings(name: str) -> str:
            return ", ".join(f"{k.split('.', 1)[1]}={v}" for k, v in self.ws.level_settings("batch", name).items())

        C.fill(self.t, [[b["name"], b["n_images"], settings(b["name"]), b["note"]] for b in rows],
               [b["name"] for b in rows])  # fmt: skip

    def selected(self) -> str | None:
        items = self.t.selectedItems()
        return self.t.item(items[0].row(), 0).text() if items else None

    def rename(self) -> None:
        old = self.selected()
        if old is None:
            return
        new, ok = QInputDialog.getText(self, "Rename batch", "New name:", text=old)
        if ok and new.strip() and new.strip() != old:
            try:
                self.ws.rename_batch(old, new.strip())
            except RecapError as e:
                QMessageBox.warning(self, "recap", str(e))
            self.refresh()
            C.STATE.changed()

    def delete(self) -> None:
        name = self.selected()
        if (
            name
            and QMessageBox.question(
                self, "recap", f"Delete batch {name!r} and its settings? Its images stay, in no batch."
            )
            == QMessageBox.StandardButton.Yes
        ):
            self.ws.delete_batch(name)
            self.refresh()
            C.STATE.changed()


class FijiDialog(QDialog):
    """Which Fiji counting opens, and choosing another (the same check as `recap fiji set`)."""

    def __init__(self, ws: Workspace | None, parent=None):
        super().__init__(parent)
        self.ws = ws
        self.setWindowTitle("recap: Fiji location")
        lay = QVBoxLayout(self)
        self.info = QLabel()
        self.info.setWordWrap(True)
        lay.addWidget(self.info)
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        lay.addWidget(self.log)
        row = QHBoxLayout()
        choose = QPushButton("Choose Fiji folder...")
        choose.clicked.connect(self.choose)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        row.addWidget(choose)
        row.addWidget(close)
        lay.addLayout(row)
        self.show_current()
        self.resize(560, 320)

    def show_current(self) -> None:
        from recap.fiji import NOT_FOUND_HINT, resolve_fiji

        try:
            loc = self.ws.fiji() if self.ws is not None else resolve_fiji(None)
        except RecapError as e:
            self.info.setText(f"<b>Problem:</b> {e}")
            return
        if loc.folder is None:
            self.info.setText("<b>Fiji not found.</b> " + NOT_FOUND_HINT)
        else:
            self.info.setText(f"<b>Fiji:</b> {loc.folder}<br><small>found by: {loc.source}</small>")

    def choose(self) -> None:
        from recap.fiji import set_default

        folder = QFileDialog.getExistingDirectory(self, "Fiji folder (Fiji.app)")
        if not folder:
            return
        try:
            d, results, saved = set_default(folder)
        except ValueError as e:
            return self.log.setText(str(e))
        self.log.setText("\n".join(f"[{st.upper()}] {msg}" for st, msg in results))
        if not saved and QMessageBox.question(self, "recap", "Some checks failed. Save this Fiji anyway?") \
                == QMessageBox.StandardButton.Yes:  # fmt: skip
            set_default(folder, force=True)
        self.show_current()


def make_workspace(parent, folder: Path) -> Workspace | None:
    """Open a folder as a workspace; if it has no recap.yaml yet, ask how to make one."""
    from recap.workspace import SETTINGS_NAME, Workspace, init_workspace

    folder = Path(folder)
    if (folder / SETTINGS_NAME).is_file():
        return Workspace(folder)
    m = QMessageBox(parent)
    m.setWindowTitle("recap: new workspace")
    m.setText(f"{folder.name} has no recap settings (recap.yaml) yet. Make it a workspace:")
    m.setInformativeText(
        "Copy a settings file you already use (recommended), or start from recap's defaults and check "
        "the channel numbers in Guided mode or recap.yaml."
    )
    copy = m.addButton("Copy a settings file...", QMessageBox.ButtonRole.AcceptRole)
    defaults = m.addButton("Start from defaults", QMessageBox.ButtonRole.ActionRole)
    m.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
    m.exec()
    try:
        if m.clickedButton() is copy:
            path, _ = QFileDialog.getOpenFileName(
                parent, "Settings file to copy", str(folder), "Settings (*.yaml *.yml)"
            )
            return init_workspace(folder, Path(path)) if path else None
        if m.clickedButton() is defaults:
            return init_workspace(folder)
    except (RecapError, ValueError) as e:
        QMessageBox.warning(parent, "recap", f"Could not make the workspace:\n{e}")
    return None


def text_dialog(parent, title: str, text: str) -> None:
    d = QDialog(parent)
    d.setWindowTitle(title)
    lay = QVBoxLayout(d)
    t = QTextEdit()
    t.setReadOnly(True)
    t.setPlainText(text)
    lay.addWidget(t)
    d.resize(560, 360)
    d.exec()


NEW_BATCH, NO_BATCH = "\0new", "\0none"


class AddImagesDialog(QDialog):
    """Which staining batch new images belong to: an existing one, a new one, or none."""

    def __init__(self, ws: Workspace, files: list[Path], parent=None):
        super().__init__(parent)
        self.setWindowTitle("recap: add images")
        lay = QVBoxLayout(self)
        names = ", ".join(p.name for p in files[:3]) + (f" and {len(files) - 3} more" if len(files) > 3 else "")
        head = QLabel(f"<b>{len(files)} image(s)</b>: {names}<br>Which staining batch are they from?")
        head.setWordWrap(True)
        lay.addWidget(head)
        form = QFormLayout()
        self.choice = QComboBox()
        for b in ws.batches():
            self.choice.addItem(f"{b['name']}  ({b['n_images']} images)", b["name"])
        self.choice.addItem("New batch...", NEW_BATCH)
        self.choice.addItem("No batch", NO_BATCH)
        self.choice.setCurrentIndex(self.choice.findData(NEW_BATCH))
        form.addRow("Batch", self.choice)
        self.name = QLineEdit()
        self.name.setPlaceholderText("e.g. stain 2026-11-24")
        form.addRow("New batch name", self.name)
        self.note = QLineEdit()
        self.note.setPlaceholderText("optional")
        form.addRow("Note", self.note)
        lay.addLayout(form)
        self.where = QLabel()
        self.where.setWordWrap(True)
        self.where.setStyleSheet("color: gray")
        lay.addWidget(self.where)
        self.inside = all(Path(p).resolve().is_relative_to(ws.root) for p in files)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Add")
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)
        self.choice.currentIndexChanged.connect(self._update)
        self.name.textChanged.connect(self._update)
        self._update()

    def batch(self) -> str | None:
        data = self.choice.currentData()
        if data == NO_BATCH:
            return None
        if data == NEW_BATCH:
            return self.name.text().strip() or None
        return data

    def _update(self, *_) -> None:
        new = self.choice.currentData() == NEW_BATCH
        self.name.setEnabled(new)
        self.note.setEnabled(new)
        from recap.workspace import safe_folder_name

        folder = safe_folder_name(self.batch() or "images")
        self.where.setText(
            "They are in the workspace folder already: they stay where they are." if self.inside
            else f"Copies go to the workspace's folder {folder}/; your originals are not changed."
        )  # fmt: skip

    def _accept(self) -> None:
        if self.choice.currentData() == NEW_BATCH and not self.name.text().strip():
            self.name.setFocus()
            self.name.setPlaceholderText("type a name for the new batch")
            return
        self.accept()


def add_images(parent, ws: Workspace, folder: bool = False):
    """Ask for files (or a folder), then for their batch, and add them. Returns the AddResult or None."""
    from recap.workspace import image_files_in

    if folder:
        d = QFileDialog.getExistingDirectory(parent, "Folder with images (subfolders included)")
        sources = [Path(d)] if d else []
    else:
        paths, _ = QFileDialog.getOpenFileNames(parent, "Images to add", "", "TIFF images (*.tif *.tiff)")
        sources = [Path(p) for p in paths]
    if not sources:
        return None
    files = image_files_in(sources)
    if not files:
        QMessageBox.information(parent, "recap", "No .tif / .tiff images there.")
        return None
    d = AddImagesDialog(ws, files, parent)
    if d.exec() != QDialog.DialogCode.Accepted:
        return None
    try:
        return ws.add_images(files, d.batch(), note=d.note.text().strip() or None)
    except RecapError as e:
        QMessageBox.warning(parent, "recap", str(e))
        return None
