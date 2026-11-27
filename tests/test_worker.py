"""StarDist in its helper process (recap.stardist_worker), as the app uses it."""

import os

import numpy as np
import pytest

from recap.stardist_worker import Worker, WorkerError, use_worker, worker


def test_helper_answers_reports_errors_and_restarts():
    w = Worker()
    try:
        first = w.call("ping")
        assert first["pid"] != os.getpid() and not first["tensorflow_loaded"]
        with pytest.raises(WorkerError, match="ValueError: boom"):
            w.call("fail", message="boom")
        assert w.call("ping")["pid"] == first["pid"]  # an error in the work: same process goes on
        with pytest.raises(WorkerError, match="stopped"):
            w.call("exit")  # the process dies without answering
        assert w.call("ping")["pid"] != first["pid"]  # started again on the next request
        with pytest.raises(WorkerError, match="unknown request"):
            w.call("nonsense")
    finally:
        w.stop()
    assert w._proc is None


def test_stardist_goes_to_the_helper_when_in_use(synth_dir, monkeypatch):
    from helpers import make_ws
    from recap.config import SegmentationConfig
    from recap.segment import stardist_labels
    from recap.workspace import Workspace

    make_ws(synth_dir)
    (synth_dir / "recap-data" / "models" / "stardist" / "v1").mkdir(parents=True)
    Workspace(synth_dir)
    seen = {}

    def fake_predict(self, img, model, scale, prob, nms):
        seen.update(model=model, shape=img.shape, scale=scale)
        return np.zeros(img.shape, np.int32)

    monkeypatch.setattr(Worker, "predict", fake_predict)
    use_worker()
    try:
        seg = SegmentationConfig(method="stardist", stardist_model="recap-data/models/stardist/v1")
        out = stardist_labels(np.ones((20, 30), np.float32), seg)
        assert out.shape == (20, 30) and seen["shape"] == (20, 30)
        assert seen["model"] == str((synth_dir / "recap-data/models/stardist/v1").resolve())  # resolved here
    finally:
        use_worker(False)
    assert worker() is None


def test_training_from_the_app_runs_in_the_helper(synth_dir, monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    QtWidgets = pytest.importorskip("qtpy.QtWidgets")
    from helpers import make_ws
    from recap.gui import common
    from recap.gui.jobs import TrainingPanel
    from recap.workspace import Workspace

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])  # noqa: F841
    make_ws(synth_dir)
    monkeypatch.setattr(common, "STATE", common.SharedState())
    common.STATE.workspace = Workspace(synth_dir)
    calls = []

    class FakeHelper:
        def call(self, op, progress=None, **kw):
            calls.append((op, kw))
            progress("epoch 1/1")
            return "trained"

    monkeypatch.setattr("recap.stardist_worker._WORKER", FakeHelper())
    monkeypatch.setattr(common, "run_in_background", lambda fn, done, err, bridge=None: done(fn()))
    panel = TrainingPanel(None)
    monkeypatch.setattr(panel, "trained", lambda res: calls.append(("done", res)))
    panel.method.setCurrentText("stardist")
    panel.run()
    assert calls[0][0] == "train" and calls[0][1]["workspace"] == str(synth_dir) and calls[0][1]["family"] == "stardist"
    assert calls[-1] == ("done", "trained")


def test_window_stays_on_the_screen():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    QtWidgets = pytest.importorskip("qtpy.QtWidgets")
    from recap.gui.window import RecapWindow, fit_to_screen

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])  # noqa: F841
    win = RecapWindow()
    avail = win.screen().availableGeometry()
    fit_to_screen(win)
    assert win.width() <= max(avail.width(), win.minimumSizeHint().width())
    assert win.height() <= max(avail.height(), win.minimumSizeHint().height())
    assert win.minimumSizeHint().width() < 912  # fits a 1366 x 768 laptop at 150% display scaling
    win.showNormal()
    win.resize(avail.width() + 900, avail.height() + 700)  # grown past the screen
    win.keep_on_screen()
    assert win.width() <= max(avail.width(), win.minimumSizeHint().width())
    assert win.height() <= max(avail.height(), win.minimumSizeHint().height())


@pytest.mark.skipif(not os.environ.get("RECAP_SLOW_TESTS"), reason="slow; run with RECAP_SLOW_TESTS=1")
def test_real_stardist_in_the_helper_matches_in_process(synth_dir):
    from conftest import make_synthetic
    from recap.config import SegmentationConfig
    from recap.segment import stardist_labels

    img = make_synthetic(seed=3)[0].astype(np.float32)
    seg = SegmentationConfig(method="stardist")
    here = stardist_labels(img, seg)
    use_worker()
    try:
        there = stardist_labels(img, seg)
    finally:
        use_worker(False)
    assert there.max() == here.max() and np.array_equal(there > 0, here > 0)
