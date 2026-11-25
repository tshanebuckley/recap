"""The recap panel: the workspace, its images, running recap, and the two ways to work on an image.

    Workspace: <folder>   [Open...] [New...]
    table of images: batch, recap's count, up to date?, manual counts, corrected mask, own settings
    [Run selected] [Run out-of-date] [Run all]      [Open image] [Set batch...] [Summary] [More]
    (banner: this image is out of date  [Run this image])
    ( Manual | Guided )
      Manual: count (in Fiji or here) and correct outlines, then save
      Guided: the image's settings step by step with a live preview (gui/guided.py)

Every action calls the same library function as the matching command (see docs/gui.md).
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from qtpy.QtCore import Qt, QUrl
from qtpy.QtGui import QColor, QDesktopServices
from qtpy.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QFileDialog,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from recap.errors import RecapError
from recap.gui import common as C
from recap.gui.guided import PREVIEW_LAYERS, GuidedPage

COUNT_LAYERS = {0: ("count: VRC-", "deepskyblue"), 1: ("count: VRC+", "orange")}
GUIDED_ONLY = (*PREVIEW_LAYERS, "recovered (DAPI + ICP8)")
FILTERS = {
    "all images": lambda r: True,
    "out of date or not run": lambda r: r["state"] != "up to date",
    "not counted by hand": lambda r: not r["manual_counts"],
    "no corrected mask": lambda r: not r["edited_masks"],
}


def _is_mask(name: str) -> bool:
    return name.startswith("nuclei")


class RecapPanel(QWidget):
    COLUMNS = ["image", "batch", "recap (VRC+)", "state", "manual (by)", "corrected", "own settings", "QC"]

    def __init__(self, napari_viewer):
        super().__init__()
        self.viewer = napari_viewer
        self.asker = C.Asker(self)
        self.bridge = C.Bridge()
        self.bridge.progress.connect(lambda i, n: (self.bar.setMaximum(n), self.bar.setValue(i)))
        self._fiji = None  # one Fiji for all counting in this session
        self._cancel = False
        self.mode = "manual"
        lay = QVBoxLayout(self)

        # workspace
        row = QHBoxLayout()
        row.addWidget(QLabel("Workspace:"))
        self.ws_combo = QComboBox()
        self.ws_combo.setToolTip("Recently used workspaces")
        self.ws_combo.activated.connect(self._recent_chosen)
        row.addWidget(self.ws_combo, 1)
        for label, fn, tip in (
            ("Open...", self.open_folder, "Open a folder of images (made a workspace if it is not one yet)"),
            ("New...", self.open_folder, "Make a folder of images a workspace: its settings file and database"),
        ):
            b = QPushButton(label)
            b.setToolTip(tip)
            b.clicked.connect(fn)
            row.addWidget(b)
        lay.addLayout(row)

        # images
        self.filter = QComboBox()
        self.filter.addItems(list(FILTERS))
        self.filter.currentIndexChanged.connect(self.refresh)
        lay.addWidget(self.filter)
        self.table = C.table(self.COLUMNS, multi=True)
        self.table.doubleClicked.connect(lambda *_: self.open_selected())
        self.table.setToolTip("Double-click an image to open it. Ctrl/Shift-click to select several.")
        lay.addWidget(self.table)
        row = QHBoxLayout()
        self.run_buttons = []
        for label, which, tip in (
            ("Run selected", "selected", "Run recap on the selected images with their settings"),
            ("Run out-of-date", "stale", "Images not run yet, or whose settings changed since they were run"),
            ("Run all", "all", "Every image (with the spot check, if it is on in recap.yaml)"),
        ):
            b = QPushButton(label)
            b.setToolTip(tip)
            b.clicked.connect(lambda _=False, w=which: self.run_images(w))
            row.addWidget(b)
            self.run_buttons.append(b)
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self._cancel_run)
        self.cancel_btn.hide()
        row.addWidget(self.cancel_btn)
        lay.addLayout(row)
        self.bar = QProgressBar()
        self.bar.hide()
        lay.addWidget(self.bar)
        row = QHBoxLayout()
        for label, fn, tip in (
            ("Open image", self.open_selected, "Show the image with recap's outlines"),
            ("Set batch...", self.set_batch, "Put the selected images in a staining batch (they share settings)"),
            ("Summary", self.show_summary, "Everything recorded for the selected image"),
        ):
            b = QPushButton(label)
            b.setToolTip(tip)
            b.clicked.connect(fn)
            row.addWidget(b)
        more = QToolButton()
        more.setText("More")
        more.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(more)
        for label, fn in (
            ("Batches...", self.show_batches),
            ("Import counts (Cell Counter XML / CSV)...", self.import_counts),
            ("Report: recap vs manual counts", self.make_report),
            ("Fiji location...", self.fiji_location),
            ("Your name for counts...", lambda: self._set_name(ask=True)),
            ("Edit settings file (recap.yaml)", self.edit_settings),
            ("Rescan folder", self.rescan),
        ):
            menu.addAction(label, fn)
        more.setMenu(menu)
        row.addWidget(more)
        lay.addLayout(row)
        self.msg = QLabel("")
        self.msg.setWordWrap(True)
        lay.addWidget(self.msg)

        # out-of-date banner
        self.banner = QFrame()
        self.banner.setStyleSheet("QFrame { background: #5a4a00; border-radius: 4px; }")
        b_lay = QHBoxLayout(self.banner)
        self.banner_text = QLabel()
        self.banner_text.setWordWrap(True)
        b_lay.addWidget(self.banner_text, 1)
        rerun = QPushButton("Run this image")
        rerun.clicked.connect(lambda: self.run_images("current"))
        b_lay.addWidget(rerun)
        self.banner.hide()
        lay.addWidget(self.banner)

        # modes
        row = QHBoxLayout()
        self.mode_group = QButtonGroup(self)
        self.mode_buttons = {}
        for k, (label, tip) in enumerate((
            ("Manual", "Count nuclei and correct recap's outlines by hand"),
            ("Guided", "Adjust this image's settings step by step and watch the result"),
        )):  # fmt: skip
            b = QPushButton(label)
            b.setCheckable(True)
            b.setToolTip(tip)
            self.mode_group.addButton(b, k)
            self.mode_buttons[label.lower()] = b
            row.addWidget(b)
        self.mode_buttons["manual"].setChecked(True)
        self.mode_group.idClicked.connect(lambda k: self.set_mode(("manual", "guided")[k]))
        lay.addLayout(row)
        self.stack = QStackedWidget()
        self.manual = ManualPage(self)
        self.guided = GuidedPage(napari_viewer)
        self.guided.saved.connect(self._settings_saved)
        self.stack.addWidget(self.manual)
        self.stack.addWidget(self.guided)
        lay.addWidget(self.stack, 2)

        C.STATE.on_changed.append(self.refresh)
        self._fill_recent()
        if C.STATE.workspace is not None:
            self.set_workspace(C.STATE.workspace)

    # ---- workspace --------------------------------------------------------------------------------
    def _fill_recent(self) -> None:
        self.ws_combo.clear()
        for p in C.recent_workspaces():
            self.ws_combo.addItem(p.name, str(p))
            self.ws_combo.setItemData(self.ws_combo.count() - 1, str(p), Qt.ItemDataRole.ToolTipRole)
        if C.STATE.workspace is not None:
            i = self.ws_combo.findData(str(C.STATE.workspace.root))
            self.ws_combo.setCurrentIndex(i)

    def _recent_chosen(self, i: int) -> None:
        path = self.ws_combo.itemData(i)
        if path and (C.STATE.workspace is None or Path(path) != C.STATE.workspace.root):
            self.open_folder(Path(path))

    def open_folder(self, folder: Path | None = None) -> None:
        from recap.gui.dialogs import make_workspace

        if not folder:
            folder = QFileDialog.getExistingDirectory(self, "Folder with the images")
            if not folder:
                return
        try:
            ws = make_workspace(self, Path(folder))
        except RecapError as e:
            return self.say(str(e))
        if ws is not None:
            self.set_workspace(ws)

    def set_workspace(self, ws) -> None:
        if not self.guided.confirm_leave(self):
            return
        C.STATE.workspace, C.STATE.sha = ws, None
        try:
            ws.scan()
        except RecapError as e:
            self.say(str(e))
        C.remember_workspace(ws.root)
        self._fill_recent()
        self.refresh()
        if ws.upgrade is not None:
            QMessageBox.information(self, "recap", ws.upgrade.message())
        for fn in C.STATE.on_workspace:
            fn(ws)
        if not ws.has_settings:
            self.say(f"{ws.root.name} has no settings file yet: press New... and choose this folder.")
        else:
            self.say(f"{ws.root.name}: double-click an image to open it.")

    def refresh(self, *_) -> None:
        ws = C.STATE.workspace
        if ws is None:
            return
        all_rows = ws.status()
        keep = FILTERS[self.filter.currentText()]
        rows = [r for r in all_rows if keep(r)]
        C.fill(
            self.table,
            [[r["name"], r["batch"] or "",
              f"{r['n_nuclei']} ({r['n_infected']})" if r["n_nuclei"] is not None else "-",
              r["state"],
              f"{r['manual_nuclei']} ({r['counted_by']})" if r["manual_counts"] else "-",
              r["edited_masks"] or "-", "yes" if r["own_settings"] else "", r["qc_flags"] or ""]
             for r in rows],
            [r["sha256"] for r in rows],
        )  # fmt: skip
        for i, r in enumerate(rows):
            if r["state"] != "up to date":  # out of date, not run, unknown
                self.table.item(i, 3).setForeground(QColor("#e8a33d"))
        self._banner(all_rows)

    def _banner(self, rows=None) -> None:
        sha = C.STATE.sha
        rows = rows if rows is not None else (C.STATE.workspace.status() if C.STATE.workspace else [])
        r = next((r for r in rows if r["sha256"] == sha), None)
        if r is None or r["state"] == "up to date":
            return self.banner.hide()
        self.banner_text.setText(
            f"{r['name']}: not run yet." if r["state"] == "not run" else
            f"{r['name']}: its settings changed since it was run; the outlines and counts shown are old."
        )  # fmt: skip
        self.banner.show()

    def selected_shas(self) -> list[str]:
        rows = sorted({i.row() for i in self.table.selectedItems()})
        return [self.table.item(r, 0).data(Qt.ItemDataRole.UserRole) for r in rows]

    def selected_sha(self) -> str | None:
        shas = self.selected_shas()
        return shas[0] if shas else C.STATE.sha

    def say(self, text: str) -> None:
        self.msg.setText(text)

    def rescan(self) -> None:
        if C.STATE.workspace is not None:
            found = C.STATE.workspace.scan()
            self.refresh()
            self.say(f"Rescanned: {found['images']} new or changed image(s), {found['runs']} run(s).")

    # ---- an image -----------------------------------------------------------------------------------
    def open_selected(self) -> None:
        sha = self.selected_sha()
        if sha is None:
            return self.say("Select an image first.")
        self.open_image(sha)

    def open_image(self, sha: str, keep_view: bool = False) -> None:
        from recap.gui.app import add_layers
        from recap.gui.data import view_from_workspace

        if sha != C.STATE.sha and not self.guided.confirm_leave(self):
            return
        try:
            view = view_from_workspace(C.STATE.workspace, sha)
        except (RecapError, ValueError) as e:
            return self.say(str(e))
        camera = (
            (self.viewer.camera.center, self.viewer.camera.zoom)
            if keep_view and hasattr(self.viewer, "camera")
            else None
        )
        self.viewer.layers.clear()
        add_layers(self.viewer, view)
        if camera is not None:
            self.viewer.camera.center, self.viewer.camera.zoom = camera
        C.STATE.sha = sha
        for fn in C.STATE.on_open:
            fn(sha)
        self.guided.resolved = None  # load again when Guided mode is shown
        if self.mode == "guided":
            self.guided.load(sha)
        self._visibility()
        self._banner()
        self.manual.image_opened(view)
        self.say(f"{view.image.name}: " + ("; ".join(view.messages) if view.messages else
                 "manual mode: count or correct the outlines; guided mode: adjust the settings."))  # fmt: skip

    def set_mode(self, mode: str) -> None:
        if mode == self.mode:
            return
        if self.mode == "guided" and not self.guided.confirm_leave(self):
            self.mode_buttons["guided"].setChecked(True)
            return
        self.mode = mode
        self.stack.setCurrentIndex(0 if mode == "manual" else 1)
        if mode == "guided" and C.STATE.sha:
            self.guided.load(C.STATE.sha)
            self.guided.schedule()
        self._visibility()

    def _visibility(self) -> None:
        """Manual mode shows the outlines being corrected and counts; Guided mode its preview only."""
        guided = self.mode == "guided"
        for layer in self.viewer.layers:
            name = layer.name
            if name in GUIDED_ONLY:
                layer.visible = guided and name not in (PREVIEW_LAYERS[2], "recovered (DAPI + ICP8)")
            elif _is_mask(name) or name.startswith("count:"):
                layer.visible = not guided
            elif name.startswith("last count") or name == "excluded nuclei":
                if guided:
                    layer.visible = False

    def _settings_saved(self, sha: str, msg: str) -> None:
        self.refresh()
        self._banner()
        self.say(msg + (" Its counts are out of date: Run this image." if not self.banner.isHidden() else ""))

    # ---- running ------------------------------------------------------------------------------------
    def run_images(self, which: str) -> None:
        """which: selected | stale | all | current. The spot check runs only with all images."""
        from recap.runner import run_workspace

        ws = C.STATE.workspace
        if ws is None:
            return self.say("Open a workspace first.")
        try:
            cfg = ws.settings()
        except RecapError as e:
            return self.say(str(e))
        if which in ("selected", "current"):
            shas = self.selected_shas() if which == "selected" else [C.STATE.sha] if C.STATE.sha else []
            if not shas:
                return self.say("Select images first.")
        elif which == "stale":
            shas = ws.out_of_date()
            if not shas:
                return self.say("Every image is up to date.")
        else:
            shas = []
        if which == "current" and self.mode == "guided" and self.guided.unsaved():
            return self.say("Save (or undo) the Guided mode changes first: a run uses the saved settings.")
        paths = [ws.image_path(s) for s in shas] or None
        counter = None
        spot = which == "all" and cfg.validation.enabled
        if spot:
            from recap.counting import fiji_session
            from recap.fijicount import FijiCounter

            try:
                self._fiji = self._fiji or fiji_session(ws)
            except RecapError as e:
                return self.say(str(e))
            counter = FijiCounter(self._fiji, cfg.validation.count_infected, ask_import=self.asker.yesno)
        self._cancel = False
        self._running(True)
        n = len(paths) if paths else len(ws.images(cfg))
        self.say(f"Running recap on {n} image(s)" + (" (spot check first)..." if spot else "..."))
        progress = lambda i, k, p, info: self.bridge.progress.emit(i, k)  # noqa: E731
        job = lambda: run_workspace(ws, paths, self.asker.decide, progress, lambda: self._cancel, counter,  # noqa: E731
                                    spot_check=spot)  # fmt: skip
        C.run_in_background(job, self._ran, self._run_failed)

    def _running(self, on: bool) -> None:
        for b in self.run_buttons:
            b.setEnabled(not on)
        self.cancel_btn.setVisible(on)
        self.bar.setVisible(on)
        if on:
            self.bar.setMaximum(0)

    def _cancel_run(self) -> None:
        self._cancel = True
        self.say("Cancelling after the current image...")

    def _ran(self, result) -> None:
        self._running(False)
        done = [d for d in result.images if "n_nuclei" in d]
        errors = [d for d in result.images if "error" in d]
        self.refresh()
        if C.STATE.sha and any(Path(d["image"]).resolve() == C.STATE.workspace.image_path(C.STATE.sha).resolve()
                               for d in done):  # fmt: skip
            self.open_image(C.STATE.sha, keep_view=True)  # show the new outlines
        self.say(f"Run {result.status}: {len(done)} image(s) done" + (f", {len(errors)} failed (see the run log)"
                 if errors else "") + f". Outputs in {result.out_dir}.")  # fmt: skip

    def _run_failed(self, e) -> None:
        self._running(False)
        self.refresh()
        self.say(f"Run stopped: {e}")

    # ---- batches, summary, more ----------------------------------------------------------------------
    def set_batch(self) -> None:
        shas = self.selected_shas()
        if not shas:
            return self.say("Select the images of one staining batch first (Ctrl/Shift-click for several).")
        names = [b["name"] for b in C.STATE.workspace.batches()]
        name, ok = QInputDialog.getItem(
            self, "Staining batch", f"Batch for {len(shas)} image(s) (type a new name, or empty for none):",
            names or [""], 0, True,
        )  # fmt: skip
        if not ok:
            return
        C.STATE.workspace.assign_batch(shas, name.strip() or None)
        self.refresh()
        C.STATE.changed()
        if C.STATE.sha in shas:
            self.guided.resolved = None
            if self.mode == "guided":
                self.guided.load(C.STATE.sha)
        self.say(f"{len(shas)} image(s) " + (f"in batch {name.strip()!r}." if name.strip() else "out of their batch."))

    def show_summary(self) -> None:
        from recap.gui.dialogs import SummaryDialog

        sha = self.selected_sha()
        if sha:
            SummaryDialog(C.STATE.workspace, sha, self).exec()

    def show_batches(self) -> None:
        from recap.gui.dialogs import BatchesDialog

        if C.STATE.workspace is not None:
            BatchesDialog(C.STATE.workspace, self).exec()
            self.refresh()

    def fiji_location(self) -> None:
        from recap.gui.dialogs import FijiDialog

        FijiDialog(C.STATE.workspace, self).exec()
        self._fiji = None  # the next count starts the chosen Fiji

    def _set_name(self, ask: bool = False) -> str | None:
        name = C.counter_name(self, ask)
        if name:
            self.manual.name_label.setText(f"Counting and correcting as <b>{name}</b>")
        return name

    def edit_settings(self) -> None:
        ws = C.STATE.workspace
        if ws is None or not ws.has_settings:
            return self.say("This workspace has no recap.yaml yet: New... makes one.")
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(ws.settings_path)))
        self.say("recap.yaml opened in your editor. Save it, then More > Rescan folder.")

    def import_counts(self) -> None:
        sha = self.selected_sha()
        if sha is None:
            return self.say("Select the image the counts belong to first.")
        path, _ = QFileDialog.getOpenFileName(
            self, "Counts made in Fiji", "", "Cell Counter XML or points CSV (*.xml *.csv)"
        )
        if not path:
            return
        name = self._set_name()
        if name is None:
            return
        ws = C.STATE.workspace
        try:
            mc, notes, added = ws.import_count(ws.image_path(sha), Path(path), name)
        except RecapError as e:
            return self.say(f"Not imported: {e}")
        self.refresh()
        self.say(f"{'Imported' if added else 'Already there'}: {mc.nuclei} nuclei"
                 + (f", {mc.infected} VRC+" if mc.infected is not None else "") + ". " + " ".join(notes))  # fmt: skip

    def make_report(self) -> None:
        ws = C.STATE.workspace
        if ws is None:
            return
        self.say("Writing the report...")

        def done(res):
            self.say(f"Report written to {res.out}.")
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(res.out / "report.md")))

        C.run_in_background(ws.report, done, lambda e: self.say(f"No report: {e}"))

    def closeEvent(self, event):  # noqa: N802 (Qt name)
        if self._fiji is not None:
            self._fiji.close()
        super().closeEvent(event)


class ManualPage(QWidget):
    """Counting and correcting outlines by hand."""

    def __init__(self, panel: RecapPanel):
        from recap.counts import default_counter_name

        super().__init__()
        self.panel = panel
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.name_label = QLabel(f"Counting and correcting as <b>{default_counter_name()}</b>")
        lay.addWidget(self.name_label)

        box = QGroupBox("Count nuclei")
        b_lay = QVBoxLayout(box)
        row = QHBoxLayout()
        fiji_btn = QPushButton("Count in Fiji")
        fiji_btn.setToolTip("Opens the image in Fiji with the Multi-point tool (recap's adjusted view)")
        fiji_btn.clicked.connect(self.count_in_fiji)
        here_btn = QPushButton("Count here")
        here_btn.setToolTip("Click the nuclei in this window")
        here_btn.clicked.connect(self.count_here)
        row.addWidget(fiji_btn)
        row.addWidget(here_btn)
        b_lay.addLayout(row)
        row = QHBoxLayout()
        row.addWidget(QLabel("Clicks mark:"))
        self.counter_group = QButtonGroup(self)
        self.counter_buttons = []
        for c, (label, colour) in COUNT_LAYERS.items():
            b = QPushButton(label.split(": ")[1])
            b.setCheckable(True)
            b.setStyleSheet(f"QPushButton:checked {{ background: {colour}; color: black; }}")
            b.setToolTip(f"Clicks add {label.split(': ')[1]} nuclei")
            self.counter_group.addButton(b, c)
            self.counter_buttons.append(b)
            row.addWidget(b)
        self.counter_group.idClicked.connect(self.choose_counter)
        save = QPushButton("Save count")
        save.clicked.connect(self.save_count)
        row.addWidget(save)
        b_lay.addLayout(row)
        tip = QLabel("Here: press VRC- or VRC+, then click nuclei (to remove one: napari's select tool, click it, "
                     "Backspace). "
                     "In Fiji: counter 0 = VRC-, counter 1 = VRC+ (double-click the Multi-point tool to "
                     "switch); press 'Done counting' when finished.")  # fmt: skip
        tip.setWordWrap(True)
        tip.setStyleSheet("color: gray")
        b_lay.addWidget(tip)
        lay.addWidget(box)

        box = QGroupBox("Correct outlines")
        b_lay = QVBoxLayout(box)
        row = QHBoxLayout()
        for label, mode, tip in (
            ("New nucleus", "new", "Draw a nucleus recap missed, in a new colour"),
            ("Paint", "paint", "Add to the selected nucleus (choose it with Pick first)"),
            ("Pick", "pick", "Click a nucleus to select it"),
            ("Erase", "erase", "Remove parts of outlines (or a whole extra nucleus)"),
            ("Fill", "fill", "Fill a closed outline with the selected nucleus"),
        ):
            b = QPushButton(label)
            b.setToolTip(tip)
            b.clicked.connect(lambda _=False, m=mode: self.tool(m))
            row.addWidget(b)
        b_lay.addLayout(row)
        row = QHBoxLayout()
        row.addWidget(QLabel("Brush size"))
        self.brush = QSpinBox()
        self.brush.setRange(1, 60)
        self.brush.setValue(8)
        self.brush.valueChanged.connect(self._brush)
        row.addWidget(self.brush)
        row.addStretch()
        save = QPushButton("Save corrected mask")
        save.setToolTip("Corrected masks are the reference for tuning and training")
        save.clicked.connect(self.save_mask)
        row.addWidget(save)
        b_lay.addLayout(row)
        tip = QLabel("Missed nucleus: New nucleus, then paint it. Two nuclei merged: Pick one, Erase the joint, "
                     "then New nucleus for the other half. Extra object: Erase it. Ctrl+Z undoes.")  # fmt: skip
        tip.setWordWrap(True)
        tip.setStyleSheet("color: gray")
        b_lay.addWidget(tip)
        lay.addWidget(box)
        lay.addStretch()

    @property
    def viewer(self):
        return self.panel.viewer

    def image_opened(self, view) -> None:
        for b in self.counter_buttons:
            b.setChecked(False)

    # ---- counting -----------------------------------------------------------------------------------
    def count_in_fiji(self) -> None:
        from recap.counting import count_in_fiji, fiji_session

        ws, sha = C.STATE.workspace, self.panel.selected_sha()
        if sha is None:
            return self.panel.say("Select or open an image first.")
        name = self.panel._set_name()
        if name is None:
            return
        try:
            self.panel._fiji = self.panel._fiji or fiji_session(ws)
        except RecapError as e:
            return self.panel.say(f"{e} (More > Fiji location)")
        image = ws.image(sha)["image"]["name"]
        self.panel.say(f"Counting {image} in Fiji... (press 'Done counting' there)")

        def done(out):
            self.panel.refresh()
            if out.status != "done":
                return self.panel.say(f"{image}: not imported ({out.status}).")
            mc = out.count
            self.panel.say(f"Imported from Fiji: {mc.nuclei} nuclei ({mc.infected} VRC+) by {mc.counted_by}.")

        C.run_in_background(lambda: count_in_fiji(ws, sha, self.panel._fiji, name, self.panel.asker.yesno), done,
                            lambda e: self.panel.say(f"Fiji: {e}"))  # fmt: skip

    def count_here(self) -> None:
        if C.STATE.sha is None:
            return self.panel.say("Open an image first (double-click it).")
        for c in (0, 1):
            name, colour = COUNT_LAYERS[c]
            if name in self.viewer.layers:
                self.viewer.layers.remove(name)
            self.viewer.add_points(np.empty((0, 2)), name=name, face_color=colour, size=8)
        self.counter_buttons[0].setChecked(True)
        self.choose_counter(0)
        self.panel.say("Click the VRC- nuclei; press VRC+ to mark infected ones. Then Save count.")

    def choose_counter(self, c: int) -> None:
        name = COUNT_LAYERS[c][0]
        if name not in self.viewer.layers:
            return self.count_here()
        layer = self.viewer.layers[name]
        layer.mode = "add"
        self.viewer.layers.selection.active = layer

    def counted_points(self) -> list[tuple[float, float, int]]:
        pts = []
        for c, (name, _) in COUNT_LAYERS.items():
            if name in self.viewer.layers:
                pts += [(float(x), float(y), c) for y, x in np.asarray(self.viewer.layers[name].data)[:, -2:]]
        return pts

    def save_count(self) -> None:
        from recap.counting import save_count

        sha = C.STATE.sha
        if sha is None:
            return self.panel.say("Open an image and count first.")
        pts = self.counted_points()
        if not pts:
            return self.panel.say("No points: press Count here and click the nuclei first.")
        name = self.panel._set_name()
        if name is None:
            return
        mc = save_count(C.STATE.workspace, sha, pts, name, "napari")
        self.panel.refresh()
        self.panel.say(f"Saved: {mc.nuclei} nuclei ({mc.infected} VRC+) by {mc.counted_by}.")

    # ---- outlines -----------------------------------------------------------------------------------
    def mask_layer(self):
        return next((ly for ly in self.viewer.layers if _is_mask(ly.name)), None)

    def tool(self, mode: str) -> None:
        layer = self.mask_layer()
        if layer is None:
            return self.panel.say("No outlines for this image yet: run it first (Run selected).")
        if mode == "new":
            layer.selected_label = int(np.asarray(layer.data).max()) + 1
            mode = "paint"
        layer.mode = mode
        layer.brush_size = self.brush.value()
        self.viewer.layers.selection.active = layer

    def _brush(self, v: int) -> None:
        layer = self.mask_layer()
        if layer is not None:
            layer.brush_size = v

    def save_mask(self) -> None:
        sha, layer = C.STATE.sha, self.mask_layer()
        if sha is None or layer is None:
            return self.panel.say("Open an image with outlines first.")
        name = self.panel._set_name()
        if name is None:
            return
        path = C.STATE.workspace.save_mask(sha, np.asarray(layer.data), edited_by=name)
        layer.name = "nuclei (edited)"
        self.panel.refresh()
        self.panel.say(f"Saved {path.name}. It is now the reference for tuning and training.")
