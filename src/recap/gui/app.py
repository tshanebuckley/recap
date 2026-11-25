"""The napari window: recap's panels (gui/panels.py) docked on the right, and an image's layers."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import numpy as np

from recap import __version__
from recap.gui.data import ImageView, example_workspace
from recap.gui.single_instance import SingleInstance

TITLE = f"recap {__version__}: Reconstruction of Cellular Architecture and Phenotypes"


def add_layers(viewer, view: ImageView) -> None:
    """Channels as additive colour layers; the excluded nuclei (hidden); the latest manual count
    (hidden); and the mask (editable, selected so napari's paint / erase / fill tools work at once)."""
    for name, (data, colormap) in view.channels.items():
        lo, hi = (float(v) for v in np.percentile(data, (0.5, 99.8)))  # like recap's normalization
        limits = (lo, hi) if hi > lo else (float(data.min()), float(data.max()) or 1.0)
        viewer.add_image(data, name=name, colormap=colormap, blending="additive", contrast_limits=limits)
    if view.excluded is not None:
        viewer.add_labels(view.excluded, name="excluded nuclei", opacity=0.5, visible=False)
    for c, colour in ((0, "deepskyblue"), (1, "orange")):
        pts = [(y, x) for x, y, k in view.points if k == c]
        if pts:
            viewer.add_points(np.array(pts), name=f"last count: VRC{'+' if c else '-'}", face_color=colour,
                              size=8, visible=False)  # fmt: skip
    if view.labels is not None:
        name = "nuclei (edited)" if view.edited else "nuclei (program)"
        layer = viewer.add_labels(view.labels, name=name, opacity=0.5)
        layer.contour = 2  # outlines, so the nuclei underneath stay visible
        viewer.layers.selection.active = layer


def bring_to_front(viewer) -> None:
    win = viewer.window._qt_window  # napari exposes no public handle for this yet
    if win.isMinimized():
        win.showNormal()
    win.raise_()
    win.activateWindow()


def software_gl_fix() -> None:
    """Linux without a GPU (VMs, remote desktops) draws with Mesa's llvmpipe, which crashes when it
    compiles a shader after TensorFlow is loaded (both carry LLVM). softpipe has no LLVM. Mesa reads
    GALLIUM_DRIVER only for its software renderers, so real GPU drivers are unaffected."""
    if sys.platform.startswith("linux"):
        os.environ.setdefault("GALLIUM_DRIVER", "softpipe")


def dock_main(viewer, main) -> None:
    """recap's panels on the right, the recap tab in front."""
    from recap.gui import panels

    dock = viewer.window.add_dock_widget(main, name="recap", area="right")
    viewer.window.add_dock_widget(panels.TuningPanel(viewer), name="Tuning", area="right", tabify=True)
    viewer.window.add_dock_widget(panels.TrainingPanel(viewer), name="Training", area="right", tabify=True)
    from qtpy.QtCore import QTimer

    dock.setMinimumWidth(460)
    QTimer.singleShot(0, dock.raise_)  # after napari has shown the tabs (it brings the last one forward)


def run(path: Path | None = None) -> bool:
    """Start the app on a workspace folder (default: the last one used, else the example workspace).
    Returns False if it was already running (that window is brought forward)."""
    software_gl_fix()
    guard = SingleInstance()
    if not guard.acquire():
        return False
    try:
        from qtpy.QtCore import QTimer

        from recap.gui import common, panels

        folder = Path(path) if path else next(iter(common.recent_workspaces()), None) or example_workspace()
        if folder is not None and folder.is_file():  # a settings file or image: its workspace
            from recap.workspace import workspace_root

            folder = workspace_root(folder) or folder.parent
        import napari

        viewer = napari.Viewer(title=TITLE)
        guard.on_activate = lambda: bring_to_front(viewer)
        guard.listen()
        main = panels.RecapPanel(viewer)
        dock_main(viewer, main)

        def start():
            if folder is not None:
                main.open_folder(folder)
            ws = common.STATE.workspace
            if ws is not None and ws.has_settings:
                rows = ws.status()
                if rows:
                    main.open_image(rows[0]["sha256"])
            else:
                main.say("Open a folder of images: Open... (or New... to make one a workspace).")

        QTimer.singleShot(0, start)  # after the window is shown, so questions appear on top of it
        napari.run()
    finally:
        guard.release()
    return True
