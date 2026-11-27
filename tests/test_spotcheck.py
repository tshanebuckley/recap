"""Tests that need neither Fiji nor StarDist. Run from the project folder:  pytest   (or: pixi run test)"""

import pandas as pd

from helpers import BASE, run_cli, runs, summary  # noqa: F401
from recap import fijicount


# ----------------------------------------------------------------- spot check (scripted counts)
class FakeFiji:
    """Replaces counting in Fiji (FijiCounter.__call__): scripted manual counts."""

    started = 0

    def __call__(self, image_path, rgb, k, n):
        FakeFiji.started += 1
        if FakeFiji.started == 1:
            return ("skip",)
        if FakeFiji.started == 2:
            return ("done", [], 4, 1)  # typed counts
        pts = [(50, 50, 0), (200, 100, 1), (60, 150, 0), (102, 150, 0), (250, 20, 0)]  # last one on background
        return ("done", pts, len(pts), 1)


def test_spot_check_save_then_reuse(write_config, synth_dir, monkeypatch):
    monkeypatch.setattr(fijicount.FijiCounter, "__call__", FakeFiji.__call__)
    FakeFiji.started = 0
    cfg = write_config(
        "input: {images: [data/*.tif]}\nnormalization: {vrc_range: [150, 3000]}\n"
        "validation: {enabled: true, sample_size: 3, seed: 7}\noutput: {directory: sc}\n"
    )
    assert run_cli("run", cfg, input="s\n").exit_code == 0  # "save counts" after the overview
    (out,) = runs(synth_dir)  # processed/runs/<date>/
    counts = pd.read_csv(next(out.glob("manual_counts_*.csv")))
    comp = pd.read_csv(next(out.glob("spot_check_*.csv")))
    assert len(counts) == 3 and FakeFiji.started == 4  # one skipped, replaced from the pool
    assert comp.missed.dropna().sum() == 2  # the background click, in both point-counted images
    assert not list(out.glob("image_summary_*.csv"))  # "Save counts" stops before the batch
    # running the workspace again reuses every count (kept in it): Fiji is not started, the batch runs ("r")
    FakeFiji.started = 0
    assert run_cli("run", synth_dir, input="r\n").exit_code == 0
    assert FakeFiji.started == 0 and len(summary(synth_dir)) == 6
