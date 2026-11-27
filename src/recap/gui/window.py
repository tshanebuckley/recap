"""recap's own window: napari's image canvas without napari's menus, layer list or layer controls,
and one side panel with four tabs.

    Images        the workspace, its images, running recap, batches, adding images (main_panel.py)
    Count         click = VRC-, Shift+click = VRC+, right-click = remove; totals, undo, save
    Fix outlines  draw a missed nucleus, delete, split with a line, merge two; undo, save
    Settings      Guided mode: the image's settings with a live preview (guided.py)

Each tab shows only its own layers. What the tabs do to the data is in editing.py (no Qt).
`recap gui --napari` opens the full napari with recap's panels instead.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from qtpy.QtCore import Qt, QTimer
from qtpy.QtGui import QKeySequence, QShortcut
from qtpy.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSlider,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from recap.errors import RecapError
from recap.gui import common as C
from recap.gui.editing import CountEdit, OutlineEdit
from recap.gui.guided import PREVIEW_LAYERS, GuidedPage
from recap.gui.main_panel import RecapPanel

TITLE = "recap"
EXTRAS = {  # layers without a layer list: a checkbox each, shown only in these tabs
    "excluded nuclei": ("images", "outlines"),
    "recovered (DAPI + ICP8)": ("settings",),
    "preview: excluded": ("settings",),
}
TABS = ("images", "count", "outlines", "settings")
COUNT_LAYER = "count"
DRAW_LAYER = "drawing"
COLOURS = {0: "deepskyblue", 1: "orange"}  # VRC-, VRC+
HELP = {
    "images": "Double-click an image to open it.   Zoom: scroll · Pan: drag",
    "count": "Click = VRC- · Shift+click = VRC+ · Right-click = remove · Ctrl+Z = undo · Zoom: scroll · Pan: drag",
    "outlines": "Choose a tool on the right · Ctrl+Z = undo · Zoom: scroll · Pan: drag (except while drawing)",
    "settings": "Change a setting and the outlines update · VRC+ magenta, VRC- cyan · Zoom: scroll · Pan: drag",
}


def _is_mask(name: str) -> bool:
    return name.startswith("nuclei")


def _mods(event) -> set[str]:
    return {str(getattr(m, "name", m)).lower() for m in (event.modifiers or ())}


# =============================================================================================
class CountTab(QWidget):
    """Counting by clicking on the image."""

    def __init__(self, win: RecapWindow):
        super().__init__()
        self.win = win
        self.edit = CountEdit()
        lay = QVBoxLayout(self)
        how = QLabel(
            "<b>Click</b> a nucleus: VRC- (<span style='color:deepskyblue'>blue</span>)<br>"
            "<b>Shift+click</b>: VRC+ (<span style='color:orange'>orange</span>)<br>"
            "<b>Right-click</b> a point: remove it<br>"
            "recap's outlines are hidden while you count."
        )
        how.setWordWrap(True)
        lay.addWidget(how)
        self.totals = QLabel()
        self.totals.setStyleSheet("font-size: 15px")
        lay.addWidget(self.totals)
        self.name = QLabel()
        lay.addWidget(self.name)
        row = QHBoxLayout()
        for label, fn in (("Undo", self.undo), ("Clear", self.clear), ("Save count", self.save)):
            b = QPushButton(label)
            b.clicked.connect(fn)
            row.addWidget(b)
        lay.addLayout(row)
        self.resume = QPushButton("Continue my last count")
        self.resume.clicked.connect(self.continue_last)
        self.resume.hide()
        lay.addWidget(self.resume)
        fiji = QPushButton("Count this image in Fiji instead")
        fiji.setToolTip("Opens the image in Fiji with the Multi-point tool (counter 0 = VRC-, 1 = VRC+); "
                        "the count is imported when you press Done counting there")  # fmt: skip
        fiji.clicked.connect(lambda: self.win.images.count_in_fiji(C.STATE.sha))
        lay.addWidget(fiji)
        lay.addStretch()
        self.saved_points: list = []
        self.show_totals()

    @property
    def viewer(self):
        return self.win.viewer

    def image_opened(self, sha: str) -> None:
        from recap.counts import default_counter_name

        self.edit = CountEdit()
        self.saved_points = []
        mine = [c for c in C.STATE.workspace.counts(sha) if c.counted_by == default_counter_name() and c.points]
        self._last = mine[-1] if mine else None
        self.resume.setVisible(self._last is not None)
        if self._last is not None:
            self.resume.setText(f"Continue my last count ({self._last.nuclei} points, {self._last.counted_at[:10]})")
        self.name.setText(f"Counting as <b>{default_counter_name()}</b> (change: Images > More > Your name)")
        self.redraw()

    def unsaved(self) -> bool:
        return self.edit.points != self.saved_points

    def click(self, y: float, x: float, shift: bool = False, right: bool = False) -> None:
        if C.STATE.sha is None:
            return
        if right:
            radius = 12 / max(getattr(camera(self.viewer), "zoom", 1.0), 0.05)  # about 12 screen pixels
            if not self.edit.remove_near(y, x, max(radius, 3)):
                return self.win.say("No point there to remove.")
        else:
            self.edit.add(y, x, 1 if shift else 0)
        self.redraw()

    def undo(self) -> None:
        if self.edit.undo():
            self.redraw()

    def clear(self) -> None:
        if self.edit.points and QMessageBox.question(self, "recap", "Remove all points of this count?") \
                == QMessageBox.StandardButton.Yes:  # fmt: skip
            self.edit.add(0, 0, 0)  # (so Undo brings them back)
            self.edit.points = []
            self.redraw()

    def continue_last(self) -> None:
        if self._last is not None:
            self.edit = CountEdit([(y, x, c) for x, y, c in self._last.points])
            self.saved_points = list(self.edit.points)
            self.resume.hide()
            self.redraw()

    def save(self) -> None:
        from recap.counting import save_count

        if C.STATE.sha is None or not self.edit.points:
            return self.win.say("Nothing to save: click the nuclei first.")
        name = C.counter_name(self)
        if name is None:
            return
        mc = save_count(C.STATE.workspace, C.STATE.sha, self.edit.for_saving(), name, "recap")
        self.saved_points = list(self.edit.points)
        C.STATE.changed()
        self.win.say(f"Count saved: {mc.nuclei} nuclei, {mc.infected} VRC+, by {mc.counted_by}.")

    def show_totals(self) -> None:
        neg, pos = self.edit.totals
        self.totals.setText(f"<span style='color:deepskyblue'>VRC- {neg}</span> &nbsp; "
                            f"<span style='color:orange'>VRC+ {pos}</span> &nbsp; total {neg + pos}")  # fmt: skip

    def redraw(self) -> None:
        pts = self.edit.points
        data = np.array([(y, x) for y, x, _ in pts]) if pts else np.empty((0, 2))
        colours = [COLOURS[c] for *_, c in pts]
        if COUNT_LAYER in self.viewer.layers:
            layer = self.viewer.layers[COUNT_LAYER]
            layer.data = data
        else:
            layer = self.viewer.add_points(data, name=COUNT_LAYER, size=9, border_width=0)
            layer.mode = "pan_zoom"  # clicks are ours, not napari's
        if len(colours):
            layer.face_color = colours
        self.show_totals()


# =============================================================================================
class OutlinesTab(QWidget):
    """Correcting recap's outlines one nucleus at a time."""

    TOOLS = {
        "draw": ("Draw new", "Drag around a nucleus recap missed; it is filled when you let go."),
        "delete": ("Delete", "Click an object that is not a nucleus (debris, a fragment)."),
        "split": ("Split", "Drag a line across two nuclei that were joined."),
        "merge": ("Merge", "Click one piece, then the other piece of the same nucleus."),
    }

    def __init__(self, win: RecapWindow):
        super().__init__()
        self.win = win
        self.edit: OutlineEdit | None = None
        self.tool = "draw"
        self._first: int | None = None
        lay = QVBoxLayout(self)
        row = QHBoxLayout()
        self.group = QButtonGroup(self)
        self.buttons = {}
        for k, (tool, (label, tip)) in enumerate(self.TOOLS.items()):
            b = QPushButton(label)
            b.setCheckable(True)
            b.setToolTip(tip)
            self.group.addButton(b, k)
            self.buttons[tool] = b
            row.addWidget(b)
        self.buttons["draw"].setChecked(True)
        self.group.idClicked.connect(lambda k: self.set_tool(list(self.TOOLS)[k]))
        lay.addLayout(row)
        self.how = QLabel()
        self.how.setWordWrap(True)
        lay.addWidget(self.how)
        self.state = QLabel()
        lay.addWidget(self.state)
        row = QHBoxLayout()
        undo = QPushButton("Undo")
        undo.clicked.connect(self.undo)
        save = QPushButton("Save corrected mask")
        save.setToolTip("Corrected masks are the reference for tuning and training")
        save.clicked.connect(self.save)
        row.addWidget(undo)
        row.addWidget(save)
        lay.addLayout(row)
        tip = QLabel("Correct the whole image, not only the obvious mistakes: anything left wrong teaches "
                     "tuning and training the wrong thing.")  # fmt: skip
        tip.setWordWrap(True)
        tip.setStyleSheet("color: gray")
        lay.addWidget(tip)
        lay.addStretch()
        self.set_tool("draw")

    @property
    def viewer(self):
        return self.win.viewer

    def mask_layer(self):
        return next((ly for ly in self.viewer.layers if _is_mask(ly.name)), None)

    def image_opened(self, sha: str) -> None:
        layer = self.mask_layer()
        self.edit = OutlineEdit(layer.data) if layer is not None else None
        self._first = None
        self.show_state()

    def unsaved(self) -> bool:
        return self.edit is not None and self.edit.changed

    def set_tool(self, tool: str) -> None:
        self.tool, self._first = tool, None
        self.buttons[tool].setChecked(True)
        self.how.setText(self.TOOLS[tool][1])
        self.win.drawing_tool_changed()

    def drags(self) -> bool:
        """The tool uses dragging (so dragging must not pan the image)."""
        return self.tool in ("draw", "split")

    def show_state(self) -> None:
        if self.edit is None:
            self.state.setText("No outlines for this image yet: run it first (Images > Run selected).")
        else:
            self.state.setText(
                f"{self.edit.n_nuclei} nuclei" + ("  ·  <b>unsaved changes</b>" if self.edit.changed else "")
            )

    def _update(self) -> None:
        layer = self.mask_layer()
        if layer is not None and self.edit is not None:
            layer.data = self.edit.labels
        self.show_state()

    def show_path(self, path) -> None:
        data = [np.asarray(path)] if len(path) > 1 else []
        if DRAW_LAYER in self.viewer.layers:
            self.viewer.layers[DRAW_LAYER].data = data
        elif data:
            layer = self.viewer.add_shapes(data, shape_type="path", name=DRAW_LAYER, edge_color="yellow", edge_width=2)
            layer.mode = "pan_zoom"

    def finish(self, start, path, moved: bool) -> None:
        """A click (moved=False) or a drag along path, with the current tool."""
        self.show_path([])
        if self.edit is None:
            return self.win.say("No outlines to edit: run this image first.")
        y, x = start
        if self.tool == "draw" and moved:
            lab = self.edit.draw(np.asarray(path))
            self.win.say("New nucleus added." if lab else "Nothing filled: draw around empty space.")
        elif self.tool == "split" and moved:
            new = self.edit.split(np.asarray(path))
            self.win.say(f"Split into {len(new) + 1} pieces." if new else "Nothing split: drag right across a nucleus.")
        elif self.tool == "delete" and not moved:
            if not self.edit.delete_at(y, x):
                self.win.say("Click inside an outline to delete it.")
        elif self.tool == "merge" and not moved:
            lab = self.edit.label_at(y, x)
            if not lab:
                return self.win.say("Click inside an outline.")
            if self._first is None or self._first == lab:
                self._first = lab
                return self.win.say("Now click the other piece.")
            self.edit.merge(self._first, lab)
            self._first = None
            self.win.say("Merged.")
        self._update()

    def undo(self) -> None:
        if self.edit is not None and self.edit.undo():
            self._update()

    def save(self) -> None:
        if C.STATE.sha is None or self.edit is None:
            return
        name = C.counter_name(self)
        if name is None:
            return
        path = C.STATE.workspace.save_mask(C.STATE.sha, self.edit.labels, edited_by=name)
        self.edit.changed = False
        layer = self.mask_layer()
        if layer is not None:
            layer.name = "nuclei (edited)"
        C.STATE.changed()
        self.show_state()
        self.win.say(f"Saved {path.name}: the reference for tuning and training.")


# =============================================================================================
class ViewBox(QGroupBox):
    """Which stains are shown, and how bright."""

    def __init__(self, win: RecapWindow):
        super().__init__("Show")
        self.win = win
        self.lay = QHBoxLayout(self)
        self.boxes: list[QCheckBox] = []
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(20, 400)
        self.slider.setValue(100)
        self.slider.setToolTip("Brightness of the stains")
        self.slider.valueChanged.connect(self.brightness)
        self.base: dict[str, tuple[float, float]] = {}
        self.extra_on: dict[str, bool] = {}  # remembered between images
        self.extra_boxes: dict[str, QCheckBox] = {}
        self.bright_label = QLabel("brightness")

    def image_opened(self) -> None:
        for b in self.boxes:
            b.deleteLater()
        self.boxes = []
        self.extra_boxes = {}
        while self.lay.count():
            self.lay.takeAt(0)
        self.base = {}
        for layer in self.win.channel_layers():
            box = QCheckBox(layer.name)
            box.setChecked(layer.visible)
            box.toggled.connect(lambda on, ly=layer: setattr(ly, "visible", on))
            self.lay.addWidget(box)
            self.boxes.append(box)
            self.base[layer.name] = tuple(layer.contrast_limits)
        for name in EXTRAS:
            box = QCheckBox(name.replace("preview: ", "preview "))
            box.setChecked(self.extra_on.get(name, False))
            box.setToolTip(f"Shown in the {' and '.join(EXTRAS[name])} tab(s)")
            box.toggled.connect(lambda on, n=name: (self.extra_on.__setitem__(n, on), self.win._apply_tab()))
            self.lay.addWidget(box)
            self.boxes.append(box)
            self.extra_boxes[name] = box
        self.lay.addWidget(self.bright_label)
        self.lay.addWidget(self.slider, 1)
        self.brightness(self.slider.value())
        self.show_for(self.win.current_tab())

    def show_for(self, tab: str) -> None:
        """Only the extras that belong to this tab are offered."""
        for name, box in self.extra_boxes.items():
            box.setVisible(tab in EXTRAS[name])

    def brightness(self, value: int) -> None:
        f = value / 100
        for layer in self.win.channel_layers():
            lo, hi = self.base.get(layer.name, tuple(layer.contrast_limits))
            layer.contrast_limits = (lo, lo + (hi - lo) / f)


def camera(viewer):
    """The viewer's camera (napari 0.9+ moved it to viewer.scene.camera)."""
    scene = getattr(viewer, "scene", None)
    return scene.camera if scene is not None and hasattr(scene, "camera") else viewer.camera


# =============================================================================================
class RecapWindow(QMainWindow):
    def __init__(self, viewer=None, canvas: QWidget | None = None):
        """viewer: a napari ViewerModel; canvas: the widget that draws it (None in tests)."""
        super().__init__()
        from napari.components import ViewerModel

        self.setWindowTitle(TITLE)
        self.viewer = viewer if viewer is not None else ViewerModel(title=TITLE)
        self.asker = C.Asker(self)
        split = QSplitter(Qt.Orientation.Horizontal)
        left = QWidget()
        l_lay = QVBoxLayout(left)
        l_lay.setContentsMargins(0, 0, 0, 0)
        l_lay.addLayout(self._top_bar())
        if canvas is not None:
            l_lay.addWidget(canvas, 1)
        self.view = ViewBox(self)
        # the Show bar scrolls sideways on a narrow window instead of making the window wider than the
        # screen (Windows laptops at 125-150% scaling: setGeometry warnings, a window partly off screen)
        bar = QScrollArea()
        bar.setWidget(self.view)
        bar.setWidgetResizable(True)
        bar.setFrameShape(QScrollArea.Shape.NoFrame)
        bar.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        bar.setFixedHeight(self.view.sizeHint().height() + bar.horizontalScrollBar().sizeHint().height() + 12)
        self.view_bar = bar
        l_lay.addWidget(bar)
        self.help = QLabel()
        self.help.setWordWrap(True)
        self.help.setStyleSheet("color: gray")
        l_lay.addWidget(self.help)
        split.addWidget(left)

        right = QWidget()
        r_lay = QVBoxLayout(right)
        self.guided = GuidedPage(self.viewer)
        self.images = RecapPanel(self.viewer, modes=False, guided=self.guided)
        self.images.confirm_leave = self.confirm_leave
        r_lay.addWidget(self.images.banner)  # out-of-date banner and messages: above every tab
        r_lay.addWidget(self.images.msg)
        self.tabs = QTabWidget()
        self.count = CountTab(self)
        self.outlines = OutlinesTab(self)
        for w, label in ((self.images, "Images"), (self.count, "Count"), (self.outlines, "Fix outlines"),
                         (self.guided, "Settings")):  # fmt: skip
            self.tabs.addTab(w, label)
        self._tab = 0
        self.tabs.currentChanged.connect(self._tab_changed)
        r_lay.addWidget(self.tabs, 1)
        # wide enough for the Settings page's rows (label, level, on, value) without clipping
        rows = (
            self.guided.scroll.widget().sizeHint().width() + self.guided.scroll.verticalScrollBar().sizeHint().width()
        )
        right.setMinimumWidth(380)  # (narrower than its rows: the Settings page scrolls sideways)
        self._side_width = max(430, rows + 40)  # its width when the screen allows (see fit_to_screen)
        split.addWidget(right)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 1)
        self.split = split
        self.setCentralWidget(split)
        for k in range(4):
            QShortcut(QKeySequence(f"Ctrl+{k + 1}"), self, activated=lambda k=k: self.tabs.setCurrentIndex(k))
        QShortcut(QKeySequence.StandardKey.Undo, self, activated=self.undo)

        C.STATE.on_open.append(self.image_opened)
        C.STATE.on_changed.append(self.fill_images)
        C.STATE.on_workspace.append(lambda ws: self.fill_images())
        self.viewer.mouse_drag_callbacks.append(self._mouse)
        self._tab_changed(0)

    # ---- top bar: add images, move between images ---------------------------------------------------
    def _top_bar(self) -> QHBoxLayout:
        row = QHBoxLayout()
        add = QPushButton("Add images")  # (a push button sizes itself for the menu arrow)
        add.setToolTip("Copy image files or a folder of images into the workspace, into a batch")
        menu = QMenu(add)
        menu.addAction("Image files...", lambda: self.images.add_images(False))
        menu.addAction("A folder of images...", lambda: self.images.add_images(True))
        add.setMenu(menu)
        row.addWidget(add)
        jobs = QPushButton("Tuning and training")
        jobs.setToolTip("Workspace-wide jobs that use the corrected masks: tune settings, train a model")
        jobs.clicked.connect(self.show_jobs)
        row.addWidget(jobs)
        row.addStretch()
        prev = QPushButton("◀")
        prev.setFixedWidth(32)
        prev.setToolTip("Previous image")
        prev.clicked.connect(lambda: self.step_image(-1))
        self.image_box = QComboBox()
        self.image_box.setMinimumWidth(160)
        self.image_box.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.image_box.setMinimumContentsLength(14)  # (long image names are cut, not widening the window)
        self.image_box.activated.connect(lambda i: self._goto(self.image_box.itemData(i)))
        nxt = QPushButton("▶")
        nxt.setFixedWidth(32)
        nxt.setToolTip("Next image")
        nxt.clicked.connect(lambda: self.step_image(1))
        for w in (prev, self.image_box, nxt):
            row.addWidget(w)
        return row

    def closeEvent(self, event):  # noqa: N802 (Qt name)
        """Closing asks about unsaved counts, outlines and settings, and closes Fiji if recap started it."""
        if not self.confirm_leave():
            event.ignore()
            return
        if self.images._fiji is not None:
            self.images._fiji.close()
        super().closeEvent(event)

    def show_jobs(self) -> QDialog:
        """Tuning and Training in their own window: they work on the whole workspace, not this image."""
        if getattr(self, "_jobs", None) is None:
            from .jobs import TrainingPanel, TuningPanel

            d = QDialog(self)
            d.setWindowTitle("recap: tuning and training")
            tabs = QTabWidget()
            tabs.addTab(TuningPanel(self.viewer), "Tuning")
            tabs.addTab(TrainingPanel(self.viewer), "Training")
            QVBoxLayout(d).addWidget(tabs)
            d.resize(640, 700)
            self._jobs = d
        self._jobs.show()
        self._jobs.raise_()
        return self._jobs

    def fill_images(self, *_) -> None:
        ws = C.STATE.workspace
        self.image_box.blockSignals(True)
        self.image_box.clear()
        for r in ws.status() if ws else []:
            if r["state"] == "ignored" and r["sha256"] != C.STATE.sha:
                continue  # (ignored images: Images tab, filter "ignored images")
            self.image_box.addItem(f"{r['name']}  [{r['state']}]", r["sha256"])
            self.image_box.setItemData(
                self.image_box.count() - 1, f"{r['path']}  [{r['state']}]", Qt.ItemDataRole.ToolTipRole
            )  # (the full name; the box may cut it)
        if C.STATE.sha:
            self.image_box.setCurrentIndex(self.image_box.findData(C.STATE.sha))
        self.image_box.setToolTip(self.image_box.currentData(Qt.ItemDataRole.ToolTipRole) or "")
        self.image_box.blockSignals(False)

    def step_image(self, d: int) -> None:
        n = self.image_box.count()
        if n:
            i = (max(self.image_box.currentIndex(), 0) + d) % n
            self._goto(self.image_box.itemData(i))

    def _goto(self, sha: str | None) -> None:
        if sha and sha != C.STATE.sha:
            self.images.open_image(sha)
        self.fill_images()

    # ---- an image opened (by any route) ------------------------------------------------------------
    def channel_layers(self):
        skip = {*PREVIEW_LAYERS, "recovered (DAPI + ICP8)"}
        return [ly for ly in self.viewer.layers if ly.__class__.__name__ == "Image" and ly.name not in skip]

    def image_opened(self, sha: str) -> None:
        for layer in self.viewer.layers:
            if _is_mask(layer.name):
                layer.contour = 1  # outlines, so the stains stay visible
                layer.mode = "pan_zoom"
        self.view.image_opened()
        self.count.image_opened(sha)
        self.outlines.image_opened(sha)
        self.fill_images()
        self._apply_tab()
        QTimer.singleShot(0, self.keep_on_screen)  # (after the new layers and labels have their sizes)

    def keep_on_screen(self) -> None:
        """A window that has grown past the screen (new content, a long name) is brought back inside it."""
        screen = self.screen() if hasattr(self, "screen") else None
        if screen is None or self.isMaximized() or self.isFullScreen():
            return
        avail = screen.availableGeometry()
        w, h = min(self.width(), avail.width()), min(self.height(), avail.height())
        if (w, h) != (self.width(), self.height()):
            self.resize(max(w, self.minimumSizeHint().width()), max(h, self.minimumSizeHint().height()))
            self.move(max(avail.x(), min(self.x(), avail.right() - self.width())),
                      max(avail.y(), min(self.y(), avail.bottom() - self.height())))  # fmt: skip

    def confirm_leave(self, parent=None) -> bool:
        """Before another image or workspace: unsaved counts, outline fixes or settings?"""
        what = [w for w, on in (("a count", self.count.unsaved()), ("outline corrections", self.outlines.unsaved()))
                if on]  # fmt: skip
        if what and QMessageBox.question(
            self, "recap", f"This image has unsaved {' and '.join(what)}. Discard them?"
        ) != QMessageBox.StandardButton.Yes:  # fmt: skip
            return False
        return self.guided.confirm_leave(parent or self)

    # ---- tabs --------------------------------------------------------------------------------------
    def current_tab(self) -> str:
        return TABS[self.tabs.currentIndex()]

    def _tab_changed(self, index: int) -> None:
        if TABS[self._tab] == "settings" and index != self._tab and not self.guided.confirm_leave(self):
            self.tabs.blockSignals(True)
            self.tabs.setCurrentIndex(self._tab)
            self.tabs.blockSignals(False)
            return
        self._tab = index
        if TABS[index] == "settings" and C.STATE.sha:
            self.guided.load(C.STATE.sha)
            self.guided.schedule()
            QTimer.singleShot(0, self.fit_side_panel)
        self._apply_tab()
        QTimer.singleShot(0, self.keep_on_screen)

    def fit_side_panel(self) -> None:
        """Widen the side panel to the Settings rows (no sideways scrolling) if the image keeps 500 px."""
        scroll = self.guided.scroll
        need = scroll.widget().sizeHint().width() + scroll.verticalScrollBar().sizeHint().width() + 40
        left, right = self.split.sizes()
        if right < need:
            grow = min(need - right, max(0, left - 500))
            if grow > 0:
                self.split.setSizes([left - grow, right + grow])

    def _apply_tab(self) -> None:
        """Only the current tab's layers are visible."""
        tab = self.current_tab()
        self.view.show_for(tab)
        for layer in self.viewer.layers:
            name = layer.name
            if _is_mask(name):
                layer.visible = tab in ("images", "outlines")
            elif name == COUNT_LAYER:
                layer.visible = tab == "count"
            elif name in (PREVIEW_LAYERS[0], PREVIEW_LAYERS[1]):
                layer.visible = tab == "settings"
            elif name in EXTRAS:
                layer.visible = tab in EXTRAS[name] and self.view.extra_on.get(name, False)
            elif name.startswith("last count") or name == DRAW_LAYER:
                layer.visible = name == DRAW_LAYER
        self.help.setText(HELP[tab])
        self.drawing_tool_changed()

    def drawing_tool_changed(self) -> None:
        """Dragging pans the image, except while drawing or splitting outlines."""
        if hasattr(self, "outlines"):
            camera(self.viewer).mouse_pan = not (self.current_tab() == "outlines" and self.outlines.drags())

    def undo(self) -> None:
        {"count": self.count.undo, "outlines": self.outlines.undo}.get(self.current_tab(), lambda: None)()

    def say(self, text: str) -> None:
        self.images.say(text)

    # ---- mouse on the image --------------------------------------------------------------------------
    def _mouse(self, viewer, event):
        tab = self.current_tab()
        if tab not in ("count", "outlines") or C.STATE.sha is None:
            return
        start = np.asarray(event.position[-2:], float)
        path, moved = [start], False
        drawing = tab == "outlines" and self.outlines.drags()
        zoom = max(getattr(camera(viewer), "zoom", 1.0), 0.05)
        right = event.button == 2
        shift = "shift" in _mods(event)
        yield
        while event.type == "mouse_move":
            p = np.asarray(event.position[-2:], float)
            if np.hypot(*(p - start)) * zoom > 4:  # more than about 4 screen pixels: a drag
                moved = True
            if drawing:
                path.append(p)
                self.outlines.show_path(path)
            yield
        if tab == "count":
            if not moved:
                self.count.click(*start, shift=shift, right=right)
        elif not right:
            self.outlines.finish(start, path, moved)


# =============================================================================================
def open_window(folder: Path | None):
    """The recap window with napari's canvas (needs a screen and OpenGL)."""
    from napari.components import ViewerModel
    from napari.qt import QtViewer, get_qapp, get_stylesheet

    app = get_qapp()
    viewer = ViewerModel(title=TITLE)
    qt_viewer = QtViewer(viewer, show_welcome_screen=False)
    win = RecapWindow(viewer, qt_viewer.canvas.native)
    win._qt_viewer = qt_viewer  # keep it alive
    win.setStyleSheet(get_stylesheet("dark"))
    fit_to_screen(win)
    win.show()
    return app, win


def fit_to_screen(win, width: int = 1500, height: int = 950) -> None:
    """Open at 1500 x 950, or smaller on a smaller screen (a laptop at 125-150% display scaling has
    about 1280 x 720 to offer): asking for more than the screen gives Windows' setGeometry warnings
    and a window partly off screen. Maximized when even that is too tight."""
    screen = win.screen() if hasattr(win, "screen") else None
    avail = screen.availableGeometry() if screen is not None else None
    if avail is None:
        win.resize(width, height)
        return
    w, h = min(width, int(avail.width() * 0.95)), min(height, int(avail.height() * 0.92))
    win.resize(w, h)
    win.move(avail.x() + (avail.width() - w) // 2, avail.y() + (avail.height() - h) // 2)
    split, side = getattr(win, "split", None), getattr(win, "_side_width", None)
    if split is not None and side:  # the side panel at its full width if the image keeps at least 500
        side = min(side, max(380, w - 500))

        def widths():
            total = sum(split.sizes()) or w
            split.setSizes([total - side, side])

        widths()
        QTimer.singleShot(0, widths)  # (again once shown: the splitter shares out its space when it appears)
    if w < 1100 or h < 650:
        win.showMaximized()


def start(win: RecapWindow, folder: Path | None) -> None:
    """Open the workspace (asking how to make one if needed) and its first image."""
    if folder is not None:
        win.images.open_folder(folder)
    ws = C.STATE.workspace
    if ws is not None and ws.has_settings:
        rows = ws.status()
        if rows:
            try:
                win.images.open_image(rows[0]["sha256"])
            except RecapError as e:
                win.say(str(e))
    else:
        win.say("Open a folder of images: Images tab > Open... (or New... to make one a workspace).")
