"""Tests that need neither Fiji nor StarDist. Run from the project folder:  pytest   (or: pixi run test)"""

import pandas as pd
import yaml

from helpers import BASE, run_cli, summary  # noqa: F401


# ----------------------------------------------------------------- full classical run
def test_classical_run(write_config, synth_dir):
    cfg = write_config(BASE + "output: {directory: out}\n")
    assert run_cli("run", cfg).exit_code == 0
    s = summary(synth_dir / "out").iloc[0]
    assert (s.n_nuclei, s.n_infected) == (4, 1)
    out = synth_dir / "out"
    for pattern in ("img1_nuclei.csv", "img1_labels.tif", "img1_qc.png", "run_log_*.yaml", "final_config_*.yaml"):
        assert list(out.glob(pattern)), pattern
    log = yaml.safe_load(next(out.glob("run_log_*.yaml")).read_text(encoding="utf-8"))
    assert {"input_yaml", "validated_config", "final_config", "images"} <= set(log)
    # the final config re-validates and reproduces the run
    assert run_cli("validate", next(out.glob("final_config_*.yaml"))).exit_code == 0


def test_filters_and_exclusion_table(write_config, synth_dir):
    cfg = write_config(BASE + "filters: {min_circularity: 0.8}\noutput: {directory: outf}\n")
    run_cli("run", cfg)
    s = summary(synth_dir / "outf").iloc[0]
    # the notched infected nucleus fails a strict circularity cut and is reported, not silently lost
    assert s.n_nuclei + s.n_filtered == 4 and s.n_excluded_irregular >= 1
    ex = pd.read_csv(synth_dir / "outf" / "img1_excluded.csv")
    assert ex.exclusion.str.contains("irregular").all()


def test_run_log_is_closed_after_a_run(write_config, synth_dir):
    """Windows cannot move or delete an open file: the run log must be released."""
    import logging

    from recap import run_pipeline

    run_pipeline(write_config(BASE + "output: {directory: closed}\n"))
    assert not logging.getLogger("recap").handlers
    import shutil

    shutil.rmtree(synth_dir / "closed")
