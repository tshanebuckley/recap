"""recap's own window: the editing logic (no Qt), then the window with Qt off-screen and no canvas."""

import os
from types import SimpleNamespace

import numpy as np
import pytest

from helpers import make_ws, run_cli
from recap.gui.editing import CountEdit, OutlineEdit


def camera(viewer):
    from recap.gui.window import camera as cam

    return cam(viewer)


def test_count_edit():
    c = CountEdit()
    c.add(10, 20, 0)
    c.add(30, 40, 1)
    c.add(31, 41, 0)
    assert c.totals == (2, 1)
    assert not c.remove_near(100, 100, radius=5)
    assert c.remove_near(30.5, 40.5, radius=5) and c.points == [(10, 20, 0), (31, 41, 0)]  # the nearest one
    assert c.undo() and c.totals == (2, 1)
    assert c.for_saving()[0] == (20, 10, 0)  # recap stores (x, y, counter)


def _blobs():
    lab = np.zeros((60, 60), np.int32)
    lab[5:20, 5:20] = 1  # a square nucleus
    lab[30:40, 30:50] = 2  # a long one, to split
    lab[5:15, 40:46] = 3  # two pieces of one nucleus with a 1-pixel gap, to merge
    lab[5:15, 47:53] = 4
    return lab


def test_outline_edit_draw_delete_split_merge_undo():
    e = OutlineEdit(_blobs())
    assert e.n_nuclei == 4
    circle = [(50 + 6 * np.sin(t), 10 + 6 * np.cos(t)) for t in np.linspace(0, 2 * np.pi, 24)]
    new = e.draw(np.array(circle))
    assert new == 5 and (e.labels == 5).sum() > 80 and e.n_nuclei == 5
    assert e.draw(np.array([(6, 6), (6, 18), (18, 18), (18, 6)])) is None  # only inside nucleus 1: nothing free
    assert e.delete_at(10, 10) == 1 and e.n_nuclei == 4 and not (e.labels == 1).any()
    pieces = e.split(np.array([(25, 40), (45, 40)]))
    assert len(pieces) == 1 and e.n_nuclei == 5
    assert e.label_at(35, 33) != e.label_at(35, 47)  # left and right of the cut differ
    assert e.merge(3, 4) and e.n_nuclei == 4 and (e.labels[10, 46] == 3)  # the gap is closed
    assert e.split(np.array([(55, 55), (58, 58)])) == []  # crosses nothing
    for _ in range(4):
        assert e.undo()
    assert np.array_equal(e.labels, _blobs())


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("qtpy.QtWidgets")
from qtpy import QtGui  # noqa: E402


@pytest.fixture
def win(synth_dir, monkeypatch):
    from qtpy.QtGui import QDesktopServices

    from recap.gui import common
    from recap.gui.window import RecapWindow

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    make_ws(synth_dir)
    assert run_cli("run", synth_dir, "-i", synth_dir / "data" / "img1.tif").exit_code == 0
    monkeypatch.setattr(common, "STATE", common.SharedState())
    monkeypatch.setattr(common, "run_in_background", lambda fn, done, err, bridge=None: done(fn()))
    monkeypatch.setattr(common, "counter_name", lambda parent, ask=False: "ana")
    yes = QtWidgets.QMessageBox.StandardButton.Yes
    monkeypatch.setattr(QtWidgets.QMessageBox, "question", lambda *a, **k: yes)
    monkeypatch.setattr(QDesktopServices, "openUrl", lambda *a: True)
    w = RecapWindow()  # a napari ViewerModel, no canvas (no OpenGL in tests)
    w.app = app  # (a QApplication freed with the fixture's frame would take the window with it)
    w.images.open_folder(synth_dir)
    return w


def _open(w, name):
    from recap.gui import common

    sha = next(r["sha256"] for r in common.STATE.workspace.status() if r["name"] == name)
    w.images.open_image(sha)
    return sha


def _visible(w):
    return {ly.name for ly in w.viewer.layers if ly.visible}


def _mouse(w, events):
    """Drive the window's mouse handler: [(type, (y, x), button, modifiers), ...] press ... release."""
    first = events[0]
    ev = SimpleNamespace(type=first[0], position=first[1], button=first[2], modifiers=first[3])
    gen = w._mouse(w.viewer, ev)
    if gen is None:
        return
    next(gen)
    for typ, pos, button, mods in events[1:]:
        ev.type, ev.position, ev.button, ev.modifiers = typ, pos, button, mods
        try:
            next(gen)
        except StopIteration:
            break


def test_window_tabs_count_outlines_settings(win):
    from recap.gui import common
    from recap.workspace import Workspace

    w = win
    assert w.image_box.count() == 6 and "[up to date]" in w.image_box.itemText(0)
    sha = _open(w, "img1.tif")
    assert _visible(w) >= {"DAPI (ch 0)", "ICP8 (ch 1)", "nuclei (program)"}  # Images tab
    assert [b.text() for b in w.view.boxes][:2] == ["ICP8 (ch 1)", "DAPI (ch 0)"]
    offered = lambda: {n for n, b in w.view.extra_boxes.items() if not b.isHidden()}  # noqa: E731
    assert offered() == {"excluded nuclei"}
    # Count: click, shift-click, right-click near a point removes it; recap's outlines hidden
    w.tabs.setCurrentIndex(1)
    assert "nuclei (program)" not in _visible(w) and camera(w.viewer).mouse_pan and not offered()
    _mouse(w, [("mouse_press", (10.0, 10.0), 1, ()), ("mouse_release", (10.0, 10.0), 1, ())])
    _mouse(w, [("mouse_press", (20.0, 20.0), 1, ("Shift",)), ("mouse_release", (20.0, 20.0), 1, ("Shift",))])
    _mouse(w, [("mouse_press", (30.0, 30.0), 1, ()), ("mouse_release", (30.0, 30.0), 1, ())])
    _mouse(w, [("mouse_press", (30.5, 30.5), 2, ()), ("mouse_release", (30.5, 30.5), 2, ())])
    drag = [("mouse_press", (40.0, 40.0), 1, ()), *[("mouse_move", (40.0, 40.0 + k), 1, ()) for k in range(1, 30, 5)]]
    _mouse(w, [*drag, ("mouse_release", (40.0, 70.0), 1, ())])  # a drag pans: no point
    assert w.count.edit.totals == (1, 1) and w.count.unsaved()
    w.count.save()
    (mc,) = Workspace(w.images.viewer and common.STATE.workspace.root).counts(sha)
    assert (mc.nuclei, mc.infected, mc.counted_by, mc.tool) == (2, 1, "ana", "recap") and (10.0, 10.0, 0) in mc.points
    assert not w.count.unsaved()
    # Fix outlines: drawing turns panning off; a drawn outline becomes a new nucleus; delete; undo
    w.tabs.setCurrentIndex(2)
    assert "nuclei (program)" in _visible(w) and not camera(w.viewer).mouse_pan
    n0 = w.outlines.edit.n_nuclei
    ring = [(5 + 4 * np.sin(t), 5 + 4 * np.cos(t)) for t in np.linspace(0, 2 * np.pi, 16)]
    lab = w.outlines.edit.labels
    if lab[5, 5]:  # (keep the test independent of where the synthetic nuclei are)
        ring = [(y + 2, x + 2) for y, x in ring]
    _mouse(w, [("mouse_press", ring[0], 1, ()), *[("mouse_move", p, 1, ()) for p in ring[1:]],
               ("mouse_release", ring[-1], 1, ())])  # fmt: skip
    assert w.outlines.edit.n_nuclei == n0 + 1 and w.outlines.unsaved()
    w.outlines.set_tool("delete")
    assert camera(w.viewer).mouse_pan  # clicking tools: dragging pans again
    y, x = np.argwhere(w.outlines.edit.labels == 1)[0]
    _mouse(w, [("mouse_press", (float(y), float(x)), 1, ()), ("mouse_release", (float(y), float(x)), 1, ())])
    assert w.outlines.edit.n_nuclei == n0
    w.undo()  # Ctrl+Z goes to the current tab
    assert w.outlines.edit.n_nuclei == n0 + 1
    w.outlines.save()
    assert len(common.STATE.workspace.reference_masks()) == 1 and not w.outlines.unsaved()
    # Settings: only the preview; leaving with unsaved changes asks
    w.tabs.setCurrentIndex(3)
    w.guided.refresh()
    assert "preview: VRC+" in _visible(w) and "nuclei (edited)" not in _visible(w) and "count" not in _visible(w)
    assert "4 nuclei" in w.guided.readout.text()
    assert offered() == {"recovered (DAPI + ICP8)", "preview: excluded"}
    w.tabs.setCurrentIndex(0)
    assert "preview: VRC+" not in _visible(w)
    # moving to another image with unsaved work asks first (answered No: stays)
    w.tabs.setCurrentIndex(1)
    w.count.click(50, 50)
    no = QtWidgets.QMessageBox.StandardButton.No
    QtWidgets.QMessageBox.question = lambda *a, **k: no
    w.step_image(1)
    assert common.STATE.sha == sha
    ev = QtGui.QCloseEvent()  # closing with an unsaved count, answered No: the window stays open
    w.closeEvent(ev)
    assert not ev.isAccepted()
    # Tuning and Training open in their own window
    jobs = w.show_jobs().findChild(QtWidgets.QTabWidget)
    assert [jobs.tabText(i) for i in range(jobs.count())] == ["Tuning", "Training"]


def test_add_images_dialog(win, tmp_path_factory, monkeypatch):
    import shutil

    from recap.gui import common, dialogs

    src = tmp_path_factory.mktemp("outside")  # (the workspace is tmp_path itself)
    shutil.copy(common.STATE.workspace.root / "data" / "img1.tif", src / "again.tif")  # same content
    (src / "sub").mkdir()
    import tifffile

    from conftest import make_synthetic

    tifffile.imwrite(src / "sub" / "fresh.tif", make_synthetic(seed=42))
    d = dialogs.AddImagesDialog(common.STATE.workspace, [src / "again.tif", src / "sub" / "fresh.tif"])
    assert d.choice.currentData() == dialogs.NEW_BATCH and d.batch() is None
    d.name.setText("stain 2026-11-25")
    assert d.batch() == "stain 2026-11-25" and "stain 2026-11-25/" in d.where.text()
    d.choice.setCurrentIndex(d.choice.findData(dialogs.NO_BATCH))
    assert d.batch() is None and not d.name.isEnabled()
    # the whole flow: a folder, then the batch question
    monkeypatch.setattr(QtWidgets.QFileDialog, "getExistingDirectory", lambda *a, **k: str(src))

    def answer(dialog):
        dialog.choice.setCurrentIndex(dialog.choice.findData(dialogs.NEW_BATCH))
        dialog.name.setText("stain B")
        return QtWidgets.QDialog.DialogCode.Accepted

    monkeypatch.setattr(dialogs.AddImagesDialog, "exec", answer)
    win.images.add_images(True)
    rows = {r["name"]: r for r in common.STATE.workspace.status()}
    assert rows["fresh.tif"]["batch"] == "stain B" and rows["img1.tif"]["batch"] == "stain B"
    assert "1 image(s) added" in win.images.msg.text() and "1 already in the workspace" in win.images.msg.text()
    assert win.image_box.count() == 7


def test_ignore_and_bring_back_in_the_images_tab(win):
    from recap.gui import common

    w, panel = win, win.images
    assert panel.table.rowCount() == 6
    panel.table.selectRow(0)
    name = panel.table.item(0, 0).text()
    panel.set_ignored(True)
    assert "1 image(s) ignored" in panel.msg.text()
    assert panel.table.rowCount() == 5 and w.image_box.count() == 5  # gone from "all images" and the image list
    panel.filter.setCurrentIndex(panel.filter.count() - 1)
    assert panel.filter.currentText() == "ignored images (1)" and panel.table.item(0, 0).text() == name
    assert name in (common.STATE.workspace.root / ".recapignore").read_text(encoding="utf-8")
    panel.table.selectRow(0)
    panel.set_ignored(False)
    assert panel.table.rowCount() == 0 and "back in the workspace" in panel.msg.text()
    panel.filter.setCurrentIndex(0)
    assert panel.table.rowCount() == 6 and w.image_box.count() == 6
