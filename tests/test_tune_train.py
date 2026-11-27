"""Tuning against corrected masks, and training model versions (slow: needs StarDist)."""

import numpy as np
import pytest
import tifffile
import yaml

from helpers import img, make_ws, run_cli
from recap.errors import RecapError
from recap.evaluate import Score
from recap.workspace import Workspace


def _edited_workspace(synth_dir, extra=""):
    """Run, then save the program masks of two images (minus one nucleus) as corrected references."""
    make_ws(synth_dir, extra)
    assert run_cli("run", synth_dir, "-i", img(synth_dir, "img1.tif"), "-i", img(synth_dir, "img2.tif")).exit_code == 0
    ws = Workspace(synth_dir)
    for r in ws.status():
        if r["n_nuclei"]:
            lab = tifffile.imread(ws.latest_masks(r["sha256"])[1])
            lab[lab == lab.max()] = 0  # the "human" removed one nucleus
            ws.save_mask(r["sha256"], lab, edited_by="ana")
    return ws


def test_score_counts_matches():
    a = np.zeros((20, 20), int)
    a[1:5, 1:5], a[10:15, 10:15] = 1, 2
    b = a.copy()
    b[a == 2] = 0  # reference lacks nucleus 2
    s = Score()
    s.add(a, b)
    d = s.as_dict()
    assert (d["matched"], d["extra"], d["missed"]) == (1, 1, 0) and d["precision"] == 0.5 and d["recall"] == 1


def test_tune_scores_applies_and_writes_best_settings(synth_dir):
    ws = _edited_workspace(synth_dir)
    from recap.tune import apply_tuned, tune

    res = tune(ws, trials=8, seed=1)
    assert res.trials.trial.iloc[0] != 0 or res.best_scores == res.baseline  # best first
    assert res.best_scores["f1"] >= res.baseline["f1"]
    assert len(ws.tuning(res.session)) == len(res.trials) <= 8
    tuned = yaml.safe_load(res.best_config.read_text(encoding="utf-8"))
    assert "interactive" not in tuned and tuned["filters"]["min_focus"] == res.best_params["filters.min_focus"]
    assert run_cli("validate", res.best_config).exit_code == 0  # a usable settings file
    sha = ws.status()[0]["sha256"]
    ws.assign_batch([sha], "B")
    params = {"filters.min_focus": 0.6, "filters.min_circularity": None}
    assert apply_tuned(ws, params, "batch", "B") == {"filters.min_focus": 0.6}  # min_circularity is already None
    assert apply_tuned(ws, params, "workspace") == {"filters.min_focus": 0.6} and ws.settings().filters.min_focus == 0.6
    out = run_cli("tune", synth_dir, "--trials", "3", "--seed", "2", "--apply-to", "workspace")
    assert out.exit_code == 0 and "Applied to workspace" in out.output


def test_tune_needs_corrected_masks(synth_dir):
    from recap.tune import tune

    with pytest.raises(RecapError, match="corrected mask"):
        tune(Workspace(make_ws(synth_dir)), trials=2)


@pytest.mark.slow
def test_train_stardist_versions_and_compare(synth_dir):
    pytest.importorskip("stardist")
    ws = _edited_workspace(synth_dir, "segmentation: {method: stardist, merge: false, diameter: 30}\n")
    from recap.train import compare_models, train

    r1 = train(ws, epochs=1, steps_per_epoch=2)
    r2 = train(ws, start="v1", epochs=1, steps_per_epoch=2, note="from v1")
    assert (r1.version, r2.version) == (1, 2) and r1.path.exists() and r2.path.exists()  # v1 kept
    assert (r1.path / "recap-model.yaml").is_file() and (r1.path / "thresholds.json").is_file()
    assert {"new", "start"} <= set(r1.metrics) and ws.models("stardist")[1]["parent"].endswith("v1")
    rows = compare_models(ws)
    assert [r["model"] for r in rows] == ["settings: 2D_versatile_fluo", "v1", "v2"]
    assert rows[1]["n_trained_on"] >= 1
    out = run_cli("models", "list", synth_dir)
    assert "stardist v2" in out.output and "from v1" in out.output
    assert run_cli("models", "use", "v1", synth_dir).exit_code == 0 and ws.model_in_use("stardist") == 1
    assert "[in use]" in run_cli("models", "list", synth_dir).output
    assert {r["state"] for r in ws.status() if r["n_nuclei"] is not None} == {"out of date"}  # new model
    assert run_cli("models", "use", "standard", synth_dir).exit_code == 0 and ws.model_in_use("stardist") is None
