"""Tuning and Training panels: work on the open workspace's corrected masks.

Tuning finds better settings (recap.tune) and applies them to the workspace or a batch, as
`recap tune --apply-to`. Training makes model versions (recap.train) and chooses the one the
workspace uses, as `recap models use`; it exports and imports StarDist versions, as
`recap models export|import` (recap.transfer).
"""

from __future__ import annotations

from pathlib import Path

from qtpy.QtWidgets import (
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from recap.errors import ConfigError, RecapError
from recap.gui import common as C


class _JobPanel(QWidget):
    """Shared layout: what it works on, options, run button, progress, log."""

    def __init__(self, napari_viewer, title: str):
        super().__init__()
        self.viewer = napari_viewer
        self.bridge = C.Bridge()
        self.bridge.message.connect(lambda m: self.log.append(m))
        self.bridge.progress.connect(lambda i, n: (self.bar.setMaximum(n), self.bar.setValue(i)))
        self.lay = QVBoxLayout(self)
        head = QLabel(title)
        head.setWordWrap(True)
        self.lay.addWidget(head)
        self.where = QLabel()
        self.where.setWordWrap(True)
        self.where.setStyleSheet("color: gray")
        self.lay.addWidget(self.where)
        self.form = QFormLayout()
        self.lay.addLayout(self.form)
        self.run_btn = QPushButton("Run")
        self.lay.addWidget(self.run_btn)
        self.bar = QProgressBar()
        self.lay.addWidget(self.bar)
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(110)
        self.lay.addWidget(self.log)
        C.STATE.on_workspace.append(self.workspace_changed)
        self.workspace_changed(C.STATE.workspace)

    def workspace_changed(self, ws) -> None:
        if ws is None:
            self.where.setText("Open a workspace in the Images tab.")
            return
        masks = len(ws.reference_masks())
        self.where.setText(f"Workspace {ws.root.name}: {masks} image(s) with a corrected mask "
                           "(the Fix outlines tab).")  # fmt: skip

    def workspace(self):
        ws = C.STATE.workspace
        if ws is None:
            self.log.append("Open a workspace in the recap panel first.")
        return ws

    def busy(self, on: bool) -> None:
        self.run_btn.setEnabled(not on)
        if on:
            self.bar.setMaximum(0)  # busy indicator until progress arrives

    def failed(self, e: Exception) -> None:
        self.busy(False)
        self.log.append(f"Error: {e}")


class TuningPanel(_JobPanel):
    def __init__(self, napari_viewer):
        super().__init__(napari_viewer, "Tuning: try settings and score them against the corrected masks")
        self.trials = QSpinBox()
        self.trials.setRange(1, 500)
        self.trials.setValue(30)
        self.form.addRow("Trials", self.trials)
        self.run_btn.setText("Run tuning")
        self.run_btn.clicked.connect(self.run)
        self.results = C.table(["F1", "precision", "recall", "count error", "settings"])
        self.lay.addWidget(self.results)
        row = QHBoxLayout()
        row.addWidget(QLabel("Use the best settings for:"))
        self.target = QComboBox()
        row.addWidget(self.target, 1)
        self.apply_btn = QPushButton("Apply")
        self.apply_btn.setEnabled(False)
        self.apply_btn.clicked.connect(self.apply)
        row.addWidget(self.apply_btn)
        self.lay.addLayout(row)
        self.best = None

    def workspace_changed(self, ws) -> None:
        super().workspace_changed(ws)
        if hasattr(self, "target"):
            self.target.clear()
            self.target.addItem("the workspace (recap.yaml)", ("workspace", None))
            for b in ws.batches() if ws else []:
                self.target.addItem(f"batch: {b['name']}", ("batch", b["name"]))

    def run(self) -> None:
        ws = self.workspace()
        if ws is None:
            return
        from recap.tune import tune

        n, b = self.trials.value(), self.bridge
        self.busy(True)
        self.log.append(f"Tuning {ws.root.name}: {n} trials...")
        job = lambda: tune(ws, n, progress=lambda i, k, r: b.progress.emit(i, k))  # noqa: E731
        C.run_in_background(job, self.done, self.failed)

    def done(self, res) -> None:
        self.busy(False)
        self.best = res
        df = res.trials.head(15)
        params = [c for c in df.columns if "." in c]
        C.fill(self.results, [[r["f1"], r["precision"], r["recall"], f"{r['count_error']:.1%}",
                               ", ".join(f"{p.split('.', 1)[1]}={r[p]}" for p in params)]
                              for r in df.to_dict("records")])  # fmt: skip
        self.log.append(f"Your settings F1 {res.baseline['f1']:.3f}; best {res.best_scores['f1']:.3f}.")
        self.workspace_changed(C.STATE.workspace)  # batches may have changed meanwhile
        self.apply_btn.setEnabled(True)

    def apply(self) -> None:
        from recap.tune import apply_tuned

        level, batch = self.target.currentData()
        try:
            stored = apply_tuned(C.STATE.workspace, self.best.best_params, level, batch)
        except (RecapError, ValueError) as e:
            return self.log.append(f"Not applied: {e}")
        self.log.append(f"Applied to {self.target.currentText()}: {stored or 'nothing changed'}. "
                        "Run the out-of-date images to update the counts.")  # fmt: skip
        C.STATE.changed()


class TrainingPanel(_JobPanel):
    def __init__(self, napari_viewer):
        super().__init__(napari_viewer, "Training: new model versions from the corrected masks")
        self.method = QComboBox()
        self.method.addItems(["stardist", "cellpose"])
        self.method.currentIndexChanged.connect(self.show_versions)
        self.start = QLineEdit()
        self.start.setPlaceholderText("the model in use (or v1, v2, ...)")
        self.epochs = QSpinBox()
        self.epochs.setRange(1, 1000)
        self.epochs.setValue(20)
        self.note = QLineEdit()
        for label, w in (
            ("Model", self.method),
            ("Start from", self.start),
            ("Epochs", self.epochs),
            ("Note", self.note),
        ):
            self.form.addRow(label, w)
        self.run_btn.setText("Train new version")
        self.run_btn.clicked.connect(self.run)
        self.versions = C.table(["version", "in use", "created", "from", "train / val images", "val F1",
                                 "compare F1", "note"])  # fmt: skip
        self.lay.addWidget(self.versions)
        row = QHBoxLayout()
        for label, fn, tip in (
            ("Compare all versions", self.compare, "Score every version on the same corrected masks"),
            (
                "Use selected version",
                self.use_selected,
                "Segment the whole workspace with it (written into recap.yaml)",
            ),
            ("Use standard model", lambda: self.use(None), "The model recap ships with"),
        ):
            b = QPushButton(label)
            b.setToolTip(tip)
            b.clicked.connect(fn)
            row.addWidget(b)
        self.lay.addLayout(row)
        row = QHBoxLayout()
        for label, fn, tip in (
            ("Export selected version...", self.export_selected,
             "Write its weights to a .zip, to use in another workspace or share"),
            ("Import a model...", self.import_model,
             "A .zip exported from recap, or any StarDist model (folder or .zip with config.json and weights)"),
        ):  # fmt: skip
            b = QPushButton(label)
            b.setToolTip(tip)
            b.clicked.connect(fn)
            row.addWidget(b)
        self.lay.addLayout(row)
        self.show_versions()

    def _selected_version(self) -> int | None:
        from qtpy.QtCore import Qt

        items = self.versions.selectedItems()
        if not items:
            self.log.append("Select a version in the table first.")
            return None
        return self.versions.item(items[0].row(), 0).data(Qt.ItemDataRole.UserRole)

    def export_selected(self) -> None:
        from qtpy.QtWidgets import QFileDialog

        from recap.transfer import export_model

        ws, v = self.workspace(), self._selected_version()
        if ws is None or v is None:
            return
        if self.method.currentText() != "stardist":
            return self.log.append("Exporting is for StarDist versions.")
        path, _ = QFileDialog.getSaveFileName(self, "Export model version", f"{ws.root.name}_stardist_v{v}.zip",
                                              "Zip files (*.zip)")  # fmt: skip
        if not path:
            return
        try:
            res = export_model(ws, v, Path(path), force=True)
        except RecapError as e:
            return self.log.append(f"Not exported: {e}")
        self.log.append(f"StarDist v{v} written to {res.path}.")

    def import_model(self) -> None:
        from qtpy.QtWidgets import QFileDialog, QMessageBox

        from recap.transfer import import_model

        ws = self.workspace()
        if ws is None:
            return
        path, _ = QFileDialog.getOpenFileName(self, "Import a StarDist model", "", "Zip files (*.zip);;All files (*)")
        if not path:  # (a model folder: choose its config.json)
            return
        src = Path(path)
        if src.name == "config.json":
            src = src.parent
        use = QMessageBox.question(self, "recap", "Segment the whole workspace with the imported model now?\n"
                                   "(Every image becomes out of date.)") == QMessageBox.StandardButton.Yes  # fmt: skip
        try:
            res = import_model(ws, src, use=use)
        except (RecapError, ConfigError) as e:
            return self.log.append(f"Not imported: {e}")
        self.log.append(f"Imported as StarDist v{res.version}. " + " ".join(res.notes))
        self.method.setCurrentText("stardist")
        self.show_versions()
        C.STATE.changed()

    def workspace_changed(self, ws) -> None:
        super().workspace_changed(ws)
        if hasattr(self, "versions"):
            self.show_versions()

    def show_versions(self, *_) -> None:
        ws = C.STATE.workspace
        if ws is None:
            return C.fill(self.versions, [])
        fam = self.method.currentText()
        in_use = ws.model_in_use(fam) if ws.has_settings else None
        rows, keys = [], []
        for m in ws.models(fam):
            met = m["metrics"] or {}
            rows.append([f"v{m['version']}", "yes" if m["version"] == in_use else "", m["created_at"],
                         str(m["parent"] or "").replace("\\", "/").rsplit("/", 1)[-1],
                         f"{len(m['train_images'])} / {len(m['val_images'])}", met.get("new", {}).get("f1"),
                         met.get("compare", {}).get("f1"), m["note"]])  # fmt: skip
            keys.append(m["version"])
        C.fill(self.versions, rows, keys)

    def run(self) -> None:
        ws = self.workspace()
        if ws is None:
            return
        from recap.train import train

        fam, start = self.method.currentText(), self.start.text() or None
        epochs, note, b = self.epochs.value(), self.note.text() or None, self.bridge
        self.busy(True)
        job = lambda: train(ws, fam, start, epochs, note=note, progress=b.message.emit)  # noqa: E731
        C.run_in_background(job, self.trained, self.failed)

    def trained(self, res) -> None:
        self.busy(False)
        self.log.append(f"Saved v{res.version}: validation F1 {res.metrics['new']['f1']} "
                        f"(starting model {res.metrics['start']['f1']}). Select it and press "
                        "'Use selected version' to segment with it.")  # fmt: skip
        self.show_versions()

    def compare(self) -> None:
        ws = self.workspace()
        if ws is None:
            return
        from recap.train import compare_models

        fam, b = self.method.currentText(), self.bridge
        self.busy(True)
        job = lambda: compare_models(ws, fam, progress=b.message.emit)  # noqa: E731
        C.run_in_background(job, self.compared, self.failed)

    def compared(self, rows) -> None:
        self.busy(False)
        for r in rows:
            self.log.append(f"{r['model']}: F1 {r['f1']} (precision {r['precision']}, recall {r['recall']}; "
                            f"trained on {r['n_trained_on']} of {r['n_images']} images)")  # fmt: skip
        self.show_versions()

    def use_selected(self) -> None:
        v = self._selected_version()
        if v is not None:
            self.use(v)

    def use(self, version: int | None) -> None:
        ws = self.workspace()
        if ws is None:
            return
        what = f"v{version}" if version else "the standard model"
        if QMessageBox.question(self, "recap", f"Segment every image of {ws.root.name} with {what}? "
                                "All results become out of date until they are run again.") \
                != QMessageBox.StandardButton.Yes:  # fmt: skip
            return
        try:
            value = ws.use_model(self.method.currentText(), version)
        except RecapError as e:
            return self.log.append(f"Error: {e}")
        self.log.append(f"Now using {what} ({value}). Run the out-of-date images in the recap panel.")
        self.show_versions()
        C.STATE.changed()
