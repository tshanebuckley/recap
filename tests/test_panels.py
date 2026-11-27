"""recap's napari panels, with Qt off-screen and a stand-in viewer (no OpenGL needed)."""

import os

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("qtpy.QtWidgets")

from helpers import make_ws, run_cli  # noqa: E402
from recap.gui import common, panels  # noqa: E402
from recap.io import file_sha256  # noqa: E402
from recap.workspace import Workspace  # noqa: E402


class Layer:
    def __init__(self, data, name, **kw):
        self.data, self.name, self.mode, self.contour, self.visible = np.asarray(data), name, "pan_zoom", 0, True
        self.kw = kw


class Layers(list):
    def __init__(self):
        super().__init__()
        self.selection = type("Sel", (), {"active": None})()

    def __contains__(self, name):
        return any(ly.name == name for ly in self)

    def __getitem__(self, key):
        return next(ly for ly in self if ly.name == key) if isinstance(key, str) else super().__getitem__(key)

    def remove(self, name):
        super().remove(self[name])


class Viewer:
    def __init__(self):
        self.layers = Layers()

    def _add(self, data, name, **kw):
        ly = Layer(data, name, **kw)
        ly.visible = kw.get("visible", True)
        self.layers.append(ly)
        return ly

    add_image = add_labels = add_points = _add


@pytest.fixture
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture
def ws(synth_dir, monkeypatch):
    make_ws(synth_dir)
    assert run_cli("run", synth_dir, "-i", synth_dir / "data" / "img1.tif").exit_code == 0
    monkeypatch.setattr(common, "STATE", common.SharedState())
    monkeypatch.setattr(common, "run_in_background", lambda fn, done, err, bridge=None: done(fn()))
    monkeypatch.setattr(common, "counter_name", lambda parent, ask=False: "ana")
    yes = QtWidgets.QMessageBox.StandardButton.Yes
    monkeypatch.setattr(QtWidgets.QMessageBox, "question", lambda *a, **k: yes)
    monkeypatch.setattr(QtWidgets.QMessageBox, "information", lambda *a, **k: None)
    from qtpy.QtGui import QDesktopServices

    monkeypatch.setattr(QDesktopServices, "openUrl", lambda *a: True)
    return synth_dir


def _select(panel, *names):
    panel.table.clearSelection()
    mode = QtWidgets.QAbstractItemView.SelectionMode
    panel.table.setSelectionMode(mode.MultiSelection)
    for name in names:
        row = next(i for i in range(panel.table.rowCount()) if panel.table.item(i, 0).text() == name)
        panel.table.selectRow(row)
    panel.table.setSelectionMode(mode.ExtendedSelection)


def _state(panel, name):
    row = next(i for i in range(panel.table.rowCount()) if panel.table.item(i, 0).text() == name)
    return panel.table.item(row, 3).text()


def test_workspace_run_count_correct_and_save(app, ws, monkeypatch):
    viewer = Viewer()
    p = panels.RecapPanel(viewer)
    p.open_folder(ws)
    assert p.table.rowCount() == 6 and _state(p, "img1.tif") == "up to date" and _state(p, "img2.tif") == "not run"
    assert common.recent_workspaces() == [ws]  # remembered for the next start
    _select(p, "img2.tif")
    p.open_selected()
    assert not p.banner.isHidden() and "not run yet" in p.banner_text.text()
    p.run_images("current")  # the banner's button
    assert _state(p, "img2.tif") == "up to date" and p.banner.isHidden() and "nuclei (program)" in viewer.layers
    _select(p, "img1.tif")
    p.open_selected()
    names = [ly.name for ly in viewer.layers]
    assert names[-1] == "nuclei (program)" and {"DAPI (ch 0)", "ICP8 (ch 1)"} <= set(names)
    # count here
    p.manual.count_here()
    viewer.layers["count: VRC-"].data = np.array([[10.0, 20.0], [30.0, 40.0]])  # (row, col) = (y, x)
    p.manual.choose_counter(1)
    assert viewer.layers.selection.active.name == "count: VRC+"
    viewer.layers["count: VRC+"].data = np.array([[50.0, 60.0]])
    p.manual.save_count()
    (mc,) = Workspace(ws).counts()
    assert (mc.nuclei, mc.infected, mc.counted_by, mc.tool) == (3, 1, "ana", "napari") and (20.0, 10.0, 0) in mc.points
    # correct outlines
    mask = viewer.layers["nuclei (program)"]
    p.manual.tool("new")
    assert mask.mode == "paint" and mask.selected_label == mask.data.max() + 1
    mask.data = mask.data.copy()
    mask.data[mask.data == mask.data.max()] = 0  # the user deletes a nucleus
    p.manual.save_mask()
    assert mask.name == "nuclei (edited)" and len(Workspace(ws).reference_masks()) == 1
    p.filter.setCurrentText("no corrected mask")
    assert p.table.rowCount() == 5
    p.filter.setCurrentText("all images")
    _select(p, "img1.tif")
    p.open_selected()  # reopens with the corrected mask and the saved count
    assert "nuclei (edited)" in viewer.layers and "last count: VRC+" in viewer.layers
    from recap.gui.dialogs import SummaryDialog

    assert "img1.tif" in SummaryDialog(Workspace(ws), common.STATE.sha).windowTitle()


def test_batches_guided_save_and_out_of_date(app, ws, monkeypatch):
    viewer = Viewer()
    p = panels.RecapPanel(viewer)
    p.open_folder(ws)
    monkeypatch.setattr(QtWidgets.QInputDialog, "getItem", lambda *a, **k: ("stain A", True))
    _select(p, "img1.tif", "img2.tif")
    p.set_batch()
    w = Workspace(ws)
    assert set(w.image_batches().values()) == {"stain A"} and len(w.image_batches()) == 2
    _select(p, "img1.tif")
    p.open_selected()
    p.set_mode("guided")
    g = p.guided
    g.refresh()  # (the window would start it from a timer once the page is shown)
    assert "preview: VRC+" in viewer.layers and not viewer.layers["nuclei (program)"].visible  # one set of outlines
    assert "4 nuclei" in g.readout.text() and g.level.currentData() == "batch"
    assert "batch stain A" in g.explain.text()
    row = g.rows["filters.min_area"]
    row.use.setChecked(True)
    row.edit.setValue(100000)
    g.refresh()
    assert "0 nuclei" in g.readout.text() and g.session.n_segmentations == 1  # filters: no new segmentation
    assert g.unsaved() == ["filters.min_area"]
    g.save()
    assert w.level_settings("batch", "stain A") == {"filters.min_area": 100000}
    assert _state(p, "img1.tif") == "out of date" and not p.banner.isHidden()
    p.set_mode("manual")
    assert viewer.layers["nuclei (program)"].visible and not viewer.layers["preview: VRC+"].visible
    p.run_images("stale")  # img1 (settings changed) and the five never run
    assert {_state(p, f"img{i}.tif") for i in range(1, 7)} == {"up to date"}
    # an image's own settings, then cleared
    monkeypatch.setattr(QtWidgets.QInputDialog, "getText", lambda *a, **k: ("weak DAPI", True))
    p.set_mode("guided")
    g.rows["filters.min_area"].use.setChecked(False)
    g.level.setCurrentIndex(g.level.findData("image"))
    g.save()
    sha = file_sha256(ws / "data" / "img1.tif")
    assert w.level_settings("image", sha) == {"filters.min_area": None} and w.level_reason(sha) == "weak DAPI"
    assert g.rows["filters.min_area"].source_full == "image" and g.clear_btn.isEnabled()
    g.clear_image()
    assert w.level_settings("image", sha) == {}


def test_more_menu_import_report_and_dialogs(app, ws, monkeypatch):
    from test_import import CENTRES, cell_counter_xml

    viewer = Viewer()
    p = panels.RecapPanel(viewer)
    p.open_folder(ws)
    xml = cell_counter_xml(ws / "m.xml", "img1.tif", CENTRES)
    monkeypatch.setattr(QtWidgets.QFileDialog, "getOpenFileName", lambda *a, **k: (str(xml), ""))
    _select(p, "img1.tif")
    p.import_counts()
    assert "Imported: 4 nuclei" in p.msg.text()
    p.make_report()
    assert "Report written" in p.msg.text() and list((ws / "recap-data" / "reports").glob("*/report.md"))
    from recap.gui.dialogs import BatchesDialog, FijiDialog

    Workspace(ws).assign_batch([file_sha256(ws / "data" / "img1.tif")], "B")
    d = BatchesDialog(Workspace(ws))
    assert d.t.rowCount() == 1 and d.t.item(0, 0).text() == "B"
    assert "Fiji" in FijiDialog(Workspace(ws)).info.text()


def test_new_workspace_from_a_settings_file(app, synth_dir, monkeypatch):
    monkeypatch.setattr(common, "STATE", common.SharedState())
    (synth_dir / "old.yaml").write_text("input: {images: [data/*.tif]}\noutput: {directory: res}\n", encoding="utf-8")
    from recap.gui import dialogs

    def press_copy(box):
        box.clickedButton = lambda: box.buttons()[0]  # "Copy a settings file..."
        return 0

    monkeypatch.setattr(QtWidgets.QMessageBox, "exec", press_copy)
    monkeypatch.setattr(QtWidgets.QFileDialog, "getOpenFileName", lambda *a, **k: (str(synth_dir / "old.yaml"), ""))
    new = dialogs.make_workspace(None, synth_dir)
    assert new.has_settings and new.settings().input.images == [] and len(new.status()) == 6
    assert new.settings().output.directory.as_posix() == "res"


def test_tuning_and_training_panels(app, ws):
    w = Workspace(ws)
    w.scan()
    sha = next(r["sha256"] for r in w.status() if r["name"] == "img1.tif")
    import tifffile

    w.save_mask(sha, tifffile.imread(w.latest_masks(sha)[1]))
    common.STATE.workspace = w
    t = panels.TuningPanel(Viewer())
    assert "1 image(s) with a corrected mask" in t.where.text()
    t.trials.setValue(3)
    t.run()
    assert t.results.rowCount() >= 1 and t.apply_btn.isEnabled()
    t.apply()
    assert "Applied to the workspace" in t.log.toPlainText()
    w.register_model("stardist", 1, w.model_dir("stardist", 1), parent="2D_versatile_fluo",
                     metrics={"new": {"f1": 0.9}}, train_images=[sha], note="first")  # fmt: skip
    tr = panels.TrainingPanel(Viewer())
    assert tr.versions.rowCount() == 1 and tr.versions.item(0, 0).text() == "v1"
    tr.versions.selectRow(0)
    tr.use_selected()
    assert w.model_in_use("stardist") == 1 and tr.versions.item(0, 1).text() == "yes"


def test_questions_from_a_background_thread(app, monkeypatch):
    """The spot check and Fiji ask the person from the worker thread and wait for the answer."""
    import threading
    import time

    yes = QtWidgets.QMessageBox.StandardButton.Yes
    monkeypatch.setattr(QtWidgets.QMessageBox, "question", lambda *a, **k: yes)
    asker = common.Asker(None)
    got = []
    th = threading.Thread(target=lambda: got.append(asker.yesno("Import 3 points?")))
    th.start()
    deadline = time.time() + 5
    while th.is_alive() and time.time() < deadline:
        app.processEvents()
        time.sleep(0.01)
    assert got == [True]
    assert asker.yesno("same thread") is True  # no deadlock when asked from the window's thread
