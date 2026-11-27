"""Guided mode: the open image's settings step by step, with a live preview.

Values shown are the image's effective settings (recap.yaml → batch → image), each tagged with
the level it comes from. Save stores only what differs from the level above
(Workspace.save_effective, the same as `recap settings set`). The preview reruns segmentation
only for settings that need it (gui/preview.py).
"""

from __future__ import annotations

from dataclasses import dataclass

from qtpy.QtCore import Qt, QTimer, Signal
from qtpy.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from recap.config import PipelineConfig
from recap.errors import RecapError
from recap.gui import common as C

SAVE_HELP = {
    "workspace": "Every image in the workspace (recap.yaml), unless its batch or the image sets its own value.",
    "batch": "Every image in batch {batch}, unless the image sets its own value.",
    "image": "Only this image. You will be asked why it needs its own settings.",
}


@dataclass
class Param:
    key: str
    label: str
    kind: str  # float | int | bool | choice | range
    lo: float = 0.0
    hi: float = 1.0
    step: float = 0.05
    nullable: bool = False  # "off" / "model default" checkbox
    choices: tuple = ()


STEPS: list[tuple[str, list[Param]]] = [
    ("1. Recovery (combined DAPI / ICP8 image)", [
        Param("normalization.vrc_weight", "ICP8 weight", "float", 0.1, 3.0, 0.05),
        Param("normalization.vrc_range", "ICP8 range (min, max)", "range", 0, 65535, 1, nullable=True),
    ]),
    ("2. Segmentation", [
        Param("segmentation.prob_thresh", "StarDist threshold", "float", 0.05, 0.95, 0.025, nullable=True),
        Param("segmentation.nms_thresh", "StarDist overlap", "float", 0.05, 0.95, 0.025, nullable=True),
        Param("segmentation.diameter", "Nucleus diameter (px)", "float", 5, 200, 1),
    ]),
    ("3. Fragments", [
        Param("segmentation.fragment_merge", "Merge fragments", "choice", choices=("off", "shape", "shape_vrc")),
    ]),
    ("4. Filters", [
        Param("filters.min_focus", "Min focus", "float", 0.0, 2.0, 0.05, nullable=True),
        Param("filters.min_circularity", "Min roundness", "float", 0.05, 1.0, 0.05, nullable=True),
        Param("filters.min_area", "Min area (px)", "int", 1, 100000, 10, nullable=True),
        Param("filters.max_area", "Max area (px)", "int", 1, 100000, 50, nullable=True),
        Param("filters.exclude_border", "Exclude clipped border nuclei", "bool"),
        Param("filters.border_max_missing", "Border: max missing", "float", 0.0, 0.9, 0.05, nullable=True),
    ]),
    ("5. Infection call", [
        Param("measurement.vrc_threshold", "VRC+ pixel threshold", "float", 0.0, 1.0, 0.025),
        Param("measurement.infected_fraction", "VRC+ area fraction", "float", 0.0, 1.0, 0.01),
    ]),
]  # fmt: skip
HELP = {  # tooltips: what each setting does, in plain words
    "normalization.vrc_weight": (
        "How much ICP8 adds to DAPI in the image the model sees. Raise it when infected nuclei have weak DAPI."
    ),
    "normalization.vrc_range": (
        "ICP8 values mapped to dark and bright. Fix it per imaging session from an infected control; off = per image."
    ),
    "segmentation.prob_thresh": (
        "How sure the model must be that something is a nucleus. Lower finds more (and more false ones)."
    ),
    "segmentation.nms_thresh": (
        "How much two nuclei may overlap. Raise it for crowded tissue where touching nuclei are merged."
    ),
    "segmentation.diameter": ("Typical nucleus width in pixels."),
    "segmentation.fragment_merge": ("Rejoin nuclei the model split in pieces: by shape, or by shape and ICP8."),
    "filters.min_focus": ("Drop blurred nuclei: sharpness relative to the image's typical nucleus (about 0.65)."),
    "filters.min_circularity": ("Drop crescents and pieces (0 to 1; about 0.65 keeps elongated nuclei)."),
    "filters.min_area": ("Drop objects smaller than this (debris)."),
    "filters.max_area": ("Drop objects larger than this (clumps). Infected nuclei swell: keep it generous."),
    "filters.exclude_border": ("Drop nuclei cut by the image edge or a blanked region."),
    "filters.border_max_missing": (
        "Keep border nuclei missing at most this share (0.15 = 15%); off = drop all border nuclei."
    ),
    "measurement.vrc_threshold": ("ICP8 brightness (0 to 1) that counts as viral replication in a pixel."),
    "measurement.infected_fraction": ("Share of a nucleus that must be above the pixel threshold to call it VRC+."),
}
GUIDED_KEYS = [p.key for _, ps in STEPS for p in ps]
PREVIEW_LAYERS = ("preview: VRC+", "preview: VRC-", "preview: excluded")
PREVIEW_COLOURS = ("magenta", "cyan", "gray")  # one colour per layer, so VRC+ and VRC- read apart


SHORT_SOURCE = {"workspace": "workspace", "batch": "batch", "image": "image"}


class _ParamRow(QWidget):
    """One setting: editor, an on/off box for optional settings, and where the value comes from."""

    changed = Signal()

    def __init__(self, p: Param):
        super().__init__()
        self.p = p
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.source = QLabel("")  # where the value comes from: workspace / batch / image
        self.source.setStyleSheet("color: gray")
        self.source.setFixedWidth(76)
        lay.addWidget(self.source)
        self.use = QCheckBox("on") if p.nullable else None
        if p.kind in ("float", "int"):
            self.edit = QDoubleSpinBox() if p.kind == "float" else QSpinBox()
            if p.kind == "float":
                self.edit.setDecimals(3)
            self.edit.setRange(p.lo, p.hi)
            self.edit.setSingleStep(p.step)
            self.edit.valueChanged.connect(self.changed.emit)
            widgets = [self.edit]
        elif p.kind == "range":
            self.edit, self.edit2 = QSpinBox(), QSpinBox()
            for e in (self.edit, self.edit2):
                e.setRange(int(p.lo), int(p.hi))
                e.valueChanged.connect(self.changed.emit)
            widgets = [self.edit, self.edit2]
        elif p.kind == "bool":
            self.edit = QCheckBox()
            self.edit.toggled.connect(self.changed.emit)
            widgets = [self.edit]
        else:
            self.edit = QComboBox()
            self.edit.addItems(list(p.choices))
            self.edit.currentIndexChanged.connect(self.changed.emit)
            widgets = [self.edit]
        self.widgets = widgets
        if self.use is not None:
            self.use.toggled.connect(self._sync_enabled)
            self.use.toggled.connect(self.changed.emit)
            lay.addWidget(self.use)
        for w in widgets:
            w.setMaximumWidth(75 if p.kind == "range" else 110)
            lay.addWidget(w)
        lay.addStretch()
        self.setToolTip(HELP.get(p.key, ""))

    def set(self, value, source: str) -> None:
        self.blockSignals(True)
        for w in self.findChildren(QWidget):
            w.blockSignals(True)
        if self.use is not None:
            self.use.setChecked(value is not None)
        if value is not None:
            if self.p.kind == "range":
                self.edit.setValue(int(value[0]))
                self.edit2.setValue(int(value[1]))
            elif self.p.kind == "bool":
                self.edit.setChecked(bool(value))
            elif self.p.kind == "choice":
                self.edit.setCurrentText(str(value))
            else:
                self.edit.setValue(value)
        for w in self.findChildren(QWidget):
            w.blockSignals(False)
        self.blockSignals(False)
        self._sync_enabled()
        self.source.setText(SHORT_SOURCE.get(source.split(":")[0], source))
        self.source.setToolTip(f"value comes from: {source}")
        self.source_full = source

    def _sync_enabled(self, *_) -> None:
        on = self.use is None or self.use.isChecked()
        for w in self.widgets:
            w.setEnabled(on)

    def get(self):
        if self.use is not None and not self.use.isChecked():
            return None
        if self.p.kind == "range":
            return [self.edit.value(), self.edit2.value()]
        if self.p.kind == "bool":
            return self.edit.isChecked()
        if self.p.kind == "choice":
            return self.edit.currentText()
        return self.edit.value()


class GuidedPage(QWidget):
    """Guided mode of the recap panel (see the module docstring)."""

    saved = Signal(str, str)  # (sha of the image whose settings were saved, what was saved)

    def __init__(self, viewer):
        super().__init__()
        self.viewer = viewer
        self.session = None
        self._source = None
        self.sha = None
        self.base = None  # recap.yaml
        self.resolved = None  # the image's saved effective settings
        self.busy = self.dirty = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.title = QLabel("Open an image (double-click it in the table).")
        self.title.setWordWrap(True)
        lay.addWidget(self.title)
        inner = QWidget()
        inner_lay = QVBoxLayout(inner)
        self.rows: dict[str, _ParamRow] = {}
        for step, params in STEPS:
            box = QGroupBox(step)
            form = QFormLayout(box)
            for p in params:
                row = _ParamRow(p)
                row.changed.connect(self.schedule)
                self.rows[p.key] = row
                label = QLabel(p.label)
                label.setToolTip(HELP.get(p.key, ""))
                form.addRow(label, row)
            inner_lay.addWidget(box)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(inner)
        lay.addWidget(scroll)
        self.scroll = scroll
        self.readout = QLabel("")
        self.readout.setWordWrap(True)
        lay.addWidget(self.readout)
        save_row = QHBoxLayout()
        self.level = QComboBox()
        self.level.currentIndexChanged.connect(self._explain)
        save_row.addWidget(QLabel("Save to:"))
        save_row.addWidget(self.level, 1)
        save = QPushButton("Save")
        save.clicked.connect(self.save)
        reset = QPushButton("Undo changes")
        reset.setToolTip("Back to the saved settings of this image")
        reset.clicked.connect(lambda: self.load(self.sha, force=True))
        save_row.addWidget(save)
        save_row.addWidget(reset)
        lay.addLayout(save_row)
        self.explain = QLabel("")
        self.explain.setWordWrap(True)
        self.explain.setStyleSheet("color: gray")
        lay.addWidget(self.explain)
        self.clear_btn = QPushButton("Clear this image's own settings")
        self.clear_btn.setToolTip(
            "Remove everything saved for this image only: its batch's and the workspace's values apply"
        )
        self.clear_btn.clicked.connect(self.clear_image)
        lay.addWidget(self.clear_btn)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(300)  # wait until the value stops changing
        self.timer.timeout.connect(self.refresh)

    # ---- loading -----------------------------------------------------------------------------------
    def load(self, sha: str | None, force: bool = False) -> None:
        """Show an image's saved settings (force: also when it is already shown)."""
        if sha is None or (sha == self.sha and not force and self.resolved is not None):
            return
        from recap.gui.preview import PreviewSession
        from recap.io import load_channels
        from recap.levels import get_path

        ws = C.STATE.workspace
        try:
            self.base = ws.settings()
            self.resolved = ws.resolve(sha, self.base)
        except RecapError as e:
            self.title.setText(str(e))
            return
        self.sha = sha
        cfg = self.resolved.cfg
        path = ws.image_path(sha)
        inp = cfg.input
        source = (sha, inp.dapi_channel, inp.vrc_channel, inp.neuron_channel, str(inp.exclude_regions))
        if self.session is None or self._source != source:  # keep the segmentation after a save
            img = load_channels(path, inp.exclude_regions)
            n = inp.neuron_channel
            self.session = PreviewSession(img[inp.dapi_channel], img[inp.vrc_channel],
                                          img[n] if n is not None else None)  # fmt: skip
            self._source = source
        d = cfg.model_dump(mode="json")
        for key, row in self.rows.items():
            row.set(get_path(d, key), self.resolved.sources.get(key, "workspace"))
        batch = self.resolved.batch
        self.level.blockSignals(True)
        self.level.clear()
        self.level.addItem("workspace", "workspace")
        if batch:
            self.level.addItem(f"batch: {batch}", "batch")
        self.level.addItem("this image", "image")
        self.level.setCurrentIndex(1 if batch else 0)
        self.level.blockSignals(False)
        self._explain()
        self.clear_btn.setEnabled(bool(ws.level_settings("image", sha)))
        self.title.setText(
            f"<b>{path.name}</b>" + (f"  batch: {batch}" if batch else "  (no batch: select images, then Set batch)")
            + (f"<br>own settings: {self.resolved.reason}" if self.resolved.reason else "")
        )  # fmt: skip
        self.schedule()

    def _explain(self, *_) -> None:
        level = self.level.currentData() or "workspace"
        batch = self.resolved.batch if self.resolved else ""
        self.explain.setText(SAVE_HELP[level].format(batch=batch))

    def current(self) -> PipelineConfig:
        from recap.levels import set_path

        d = self.resolved.cfg.model_dump(mode="json")
        for key, row in self.rows.items():
            set_path(d, key, row.get())
        return PipelineConfig.model_validate(d)

    def unsaved(self) -> list[str]:
        """Settings changed here and not saved."""
        if self.resolved is None:
            return []
        from recap.levels import get_path

        saved = self.resolved.cfg.model_dump(mode="json")
        return [k for k, row in self.rows.items() if row.get() != get_path(saved, k)]

    def confirm_leave(self, parent=None) -> bool:
        """True if there is nothing unsaved, or the person agrees to drop it."""
        changes = self.unsaved()
        if not changes:
            return True
        answer = QMessageBox.question(
            parent or self, "recap: unsaved settings",
            f"Guided mode has {len(changes)} unsaved change(s) for this image "
            f"({', '.join(k.split('.', 1)[1] for k in changes)}). Discard them?",
        )  # fmt: skip
        return answer == QMessageBox.StandardButton.Yes

    # ---- preview ----------------------------------------------------------------------------------
    def schedule(self) -> None:
        if self.session is not None and self.isVisible():
            self.timer.start()

    def showEvent(self, event):  # noqa: N802 (Qt name)
        super().showEvent(event)
        self.schedule()

    def refresh(self) -> None:
        if self.session is None:
            return
        if self.busy:
            self.dirty = True  # run again when the current update finishes
            return
        try:
            cfg = self.current()
        except ValueError as e:
            self.readout.setText(f"Invalid combination: {e}")
            return
        self._show_image("recovered (DAPI + ICP8)", self.session.recovered(cfg))  # instant
        slow = self.session.needs_segmentation(cfg)
        self.busy = True
        self.readout.setText("Segmenting..." if slow else "Updating...")
        C.run_in_background(lambda: self.session.update(cfg), self.shown, self.failed)

    def shown(self, pv) -> None:
        from napari.utils.colormaps import DirectLabelColormap

        from recap.gui.preview import split_labels

        self.busy = False
        for name, data in zip(PREVIEW_LAYERS, split_labels(pv.result), strict=True):
            if name in self.viewer.layers:
                self.viewer.layers[name].data = data
            else:
                layer = self.viewer.add_labels(data, name=name, opacity=0.8, visible=name != PREVIEW_LAYERS[2])
                layer.contour = 2
                colour = PREVIEW_COLOURS[PREVIEW_LAYERS.index(name)]
                layer.colormap = DirectLabelColormap(color_dict={None: colour, 0: "transparent"})
        c = pv.counts
        excl = ", ".join(f"{k} {v}" for k, v in c["excluded"].items()) or "none"
        self.readout.setText(
            f"<b>{c['nuclei']} nuclei</b>, {c['vrc_pos']} VRC+ ({c['pct_vrc_pos']}%) "
            f"<span style='color:magenta'>VRC+</span> / <span style='color:#00bcd4'>VRC-</span>; "
            f"excluded: {excl}; fragments merged: {c['fragment_merges']}"
        )
        if self.dirty:
            self.dirty = False
            self.refresh()

    def failed(self, e) -> None:
        self.busy = False
        self.readout.setText(f"Error: {e}")

    def _show_image(self, name: str, data) -> None:
        if name in self.viewer.layers:
            self.viewer.layers[name].data = data
        else:
            self.viewer.add_image(data, name=name, colormap="gray", visible=False)

    # ---- saving -------------------------------------------------------------------------------------
    def save(self) -> None:
        if self.session is None:
            return
        ws, level = C.STATE.workspace, self.level.currentData()
        reason = None
        if level == "image":
            reason, ok = QInputDialog.getText(
                self, "Settings for this image", "Why does this image need its own settings?",
                text=self.resolved.reason or "",
            )  # fmt: skip
            if not ok or not reason.strip():
                return self.readout.setText("Not saved: an image's own settings need a reason.")
        try:
            held, shadowed = ws.save_effective(level, self.sha, self.current(), GUIDED_KEYS, reason)
        except (RecapError, ValueError) as e:
            return self.readout.setText(f"Not saved: {e}")
        msg = f"Saved to {self.level.currentText()}: {len(held)} value(s)."
        if shadowed:
            msg += f" This image's own or batch settings still set {', '.join(shadowed)}."
        self.load(self.sha, force=True)
        self.saved.emit(self.sha, msg)

    def clear_image(self) -> None:
        ws = C.STATE.workspace
        if self.sha is None:
            return
        if QMessageBox.question(self, "recap", "Remove this image's own settings?") != QMessageBox.StandardButton.Yes:
            return
        ws.set_level_settings("image", self.sha, {})
        self.load(self.sha, force=True)
        self.saved.emit(self.sha, "This image's own settings were removed.")
