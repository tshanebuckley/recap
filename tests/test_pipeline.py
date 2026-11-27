"""Tests that need neither Fiji nor StarDist. Run from the project folder:  pytest   (or: pixi run test)"""

import pandas as pd
import yaml

from helpers import BASE, run_cli, runs, summary  # noqa: F401


# ----------------------------------------------------------------- full classical run
def test_classical_run(write_config, synth_dir):
    cfg = write_config(BASE)
    assert run_cli("run", cfg).exit_code == 0
    s = summary(synth_dir).iloc[0]
    assert (s.n_nuclei, s.n_infected) == (4, 1)
    (run,) = runs(synth_dir)  # processed/runs/<date>/: the run's own files
    for pattern in ("run_log_*.yaml", "final_config_*.yaml", "image_summary_*.csv", "run_*.log"):
        assert list(run.glob(pattern)), pattern
    own = synth_dir / "processed" / "unbatched" / "img1"  # the image's outputs, next to its raw file's place
    assert (synth_dir / "raw" / "unbatched" / "img1.tif").is_file()
    for name in ("img1_nuclei.csv", "img1_labels.tif", "img1_qc.png"):
        assert (own / name).is_file(), name
    log = yaml.safe_load(next(run.glob("run_log_*.yaml")).read_text(encoding="utf-8"))
    assert {"input_yaml", "validated_config", "final_config", "images"} <= set(log)
    assert log["images"][0]["outputs"]["labels"] == str(own / "img1_labels.tif")
    # the final config re-validates (a starting point for another workspace)
    assert run_cli("validate", next(run.glob("final_config_*.yaml"))).exit_code == 0


def test_filters_and_exclusion_table(write_config, synth_dir):
    cfg = write_config(BASE + "filters: {min_circularity: 0.8}\n")
    run_cli("run", cfg)
    s = summary(synth_dir).iloc[0]
    # the notched infected nucleus fails a strict circularity cut and is reported, not silently lost
    assert s.n_nuclei + s.n_filtered == 4 and s.n_excluded_irregular >= 1
    ex = pd.read_csv(synth_dir / "processed" / "unbatched" / "img1" / "img1_excluded.csv")
    assert ex.exclusion.str.contains("irregular").all()


def test_run_log_is_closed_after_a_run(write_config, synth_dir):
    """Windows cannot move or delete an open file: the run log must be released."""
    import logging

    from recap import run_pipeline

    run_pipeline(write_config(BASE))
    assert not logging.getLogger("recap").handlers
    import shutil

    shutil.rmtree(synth_dir / "processed" / "runs")
