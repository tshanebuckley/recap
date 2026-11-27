"""The napari window: recap's panels (gui/panels.py) docked on the right, and an image's layers."""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

import numpy as np

from recap import __version__
from recap.gui.data import ImageView, example_workspace
from recap.gui.single_instance import SingleInstance

TITLE = f"recap {__version__}: Reconstruction of Cellular Architecture and Phenotypes"
log = logging.getLogger(__name__)

NIXOS_GL = Path("/run/opengl-driver")  # where NixOS keeps its graphics drivers
NO_OPENGL = (
    "recap could not start OpenGL, which it needs to draw images.\n\n"
    "- NixOS: check that hardware.graphics.enable = true; is in your configuration "
    "(recap finds the driver in /run/opengl-driver itself).\n"
    "- A virtual machine or remote desktop: install Mesa, which draws without a graphics card.\n"
    "- Otherwise: update the graphics driver.\n\n"
    "More in docs/gui.md, section 'OpenGL'."
)


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


def bring_to_front(viewer_or_window) -> None:
    win = getattr(getattr(viewer_or_window, "window", None), "_qt_window", viewer_or_window)  # napari: no public handle
    if win.isMinimized():
        win.showNormal()
    win.raise_()
    win.activateWindow()


def system_gl_env(environ=None, root: Path = NIXOS_GL) -> dict[str, str] | None:
    """NixOS keeps its graphics driver in /run/opengl-driver, where the OpenGL loader in recap's
    environment (libglvnd from conda-forge) does not look: Qt then finds no EGL ("EGL not
    available") and napari fails on the first image. Returns the variables that point the loader
    there, or None: not Linux, no /run/opengl-driver, already set, or RECAP_SYSTEM_GL=0."""
    environ = os.environ if environ is None else environ
    vendors = root / "share" / "glvnd" / "egl_vendor.d"
    if not sys.platform.startswith("linux") or environ.get("RECAP_SYSTEM_GL") == "0":
        return None
    if environ.get("__EGL_VENDOR_LIBRARY_DIRS") or not vendors.is_dir():
        return None
    ld = environ.get("LD_LIBRARY_PATH", "")
    return {
        "__EGL_VENDOR_LIBRARY_DIRS": str(vendors),
        "LD_LIBRARY_PATH": str(root / "lib") + (os.pathsep + ld if ld else ""),
        "RECAP_SYSTEM_GL": "0",  # once: the restarted app does not restart again
    }


def use_system_gl() -> None:
    """On NixOS, restart recap once with system_gl_env (the library path is read only when a
    program starts, so setting it here would not reach this process)."""
    env = system_gl_env()
    if env is None:
        return
    argv = list(getattr(sys, "orig_argv", None) or [sys.executable, *sys.argv])
    log.info("Using the system's OpenGL driver in %s (NixOS): restarting with it", NIXOS_GL)
    sys.stdout.flush()
    sys.stderr.flush()
    os.execve(sys.executable, argv, {**os.environ, **env})


def opengl_works() -> bool:
    """True if Qt can make an OpenGL context current (napari's canvas needs one). Without it napari
    fails with a traceback on the first image; recap says what to do instead."""
    from qtpy.QtGui import QGuiApplication, QOffscreenSurface, QOpenGLContext

    if QGuiApplication.platformName() == "offscreen":  # tests, headless use: nothing to check
        return True
    ctx = QOpenGLContext()
    if not ctx.create():
        return False
    surface = QOffscreenSurface()
    surface.create()
    ok = ctx.makeCurrent(surface)
    if ok:
        ctx.doneCurrent()
    return bool(ok)


def say_no_opengl() -> None:
    from qtpy.QtWidgets import QMessageBox

    log.error(NO_OPENGL)  # also in the terminal (logging prints errors when nothing else is set up)
    QMessageBox.critical(None, "recap: no OpenGL", NO_OPENGL)


def keep_tensorflow_out() -> None:
    """StarDist runs in a helper process (recap.stardist_worker), so the window's process never loads
    TensorFlow. That keeps Mesa's fast llvmpipe renderer usable on Linux without a GPU (it crashed
    next to TensorFlow, so recap used to force the slow softpipe: DEF-006), avoids the stall when
    the program exits, and keeps TensorFlow's libraries from loading next to the window's thread on
    Windows (the "Not Responding" freeze on the first segmentation)."""
    from recap.stardist_worker import use_worker

    use_worker()


def finish(code: int = 0) -> None:
    """Leave the app: stop the helper process, flush the logs, then exit at once. Python's own
    shutdown, run after Qt and the native libraries have been torn down, could stall."""
    from recap.stardist_worker import use_worker

    try:
        use_worker(False)
    finally:
        logging.shutdown()
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(code)


def dock_main(viewer, main) -> None:
    """recap's panels on the right, the recap tab in front."""
    from recap.gui import panels

    dock = viewer.window.add_dock_widget(main, name="recap", area="right")
    viewer.window.add_dock_widget(panels.TuningPanel(viewer), name="Tuning", area="right", tabify=True)
    viewer.window.add_dock_widget(panels.TrainingPanel(viewer), name="Training", area="right", tabify=True)
    from qtpy.QtCore import QTimer

    dock.setMinimumWidth(460)
    QTimer.singleShot(0, dock.raise_)  # after napari has shown the tabs (it brings the last one forward)


def run(path: Path | None = None, full_napari: bool = False) -> bool:
    """Start the app on a workspace folder (default: the last one used, else the example workspace):
    recap's own window, or (full_napari) napari with recap's panels docked.
    Returns False if it was already running (that window is brought forward). After the window
    closes the process ends (finish)."""
    use_system_gl()
    keep_tensorflow_out()
    guard = SingleInstance()
    if not guard.acquire():
        return False
    try:
        from napari.qt import get_qapp
        from qtpy.QtCore import QTimer

        from recap.gui import common, panels

        get_qapp()
        if not opengl_works():
            say_no_opengl()
            guard.release()
            finish(1)

        folder = Path(path) if path else next(iter(common.recent_workspaces()), None) or example_workspace()
        if folder is not None and folder.is_file():  # a settings file or image: its workspace
            from recap.workspace import workspace_root

            folder = workspace_root(folder) or folder.parent
        if not full_napari:
            from recap.gui.window import open_window, start

            app, win = open_window(folder)
            guard.on_activate = lambda: bring_to_front(win)
            guard.listen()
            QTimer.singleShot(0, lambda: start(win, folder))
            code = app.exec()
            guard.release()
            finish(code)
            return True
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
        guard.release()
        finish(0)
    finally:
        guard.release()
    return True
