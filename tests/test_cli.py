"""Tests that need neither Fiji nor StarDist. Run from the project folder:  pytest   (or: pixi run test)"""

import shutil
import subprocess

import pandas as pd
import pytest

import recap
from helpers import BASE, run_cli, summary  # noqa: F401


# ----------------------------------------------------------------- command line entry points
def test_version_option():
    res = run_cli("--version")
    assert res.exit_code == 0 and res.output.strip() == f"recap {recap.__version__}"


@pytest.mark.skipif(shutil.which("recap") is None, reason="recap not installed (pip install -e .)")
def test_installed_command():
    out = subprocess.run(["recap", "--version"], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == f"recap {recap.__version__}"


def test_compare_two_runs(write_config, synth_dir):
    run_cli("run", write_config(BASE + "output: {directory: ra}\n"))
    run_cli("run", write_config(BASE + "filters: {min_circularity: 0.8}\noutput: {directory: rb}\n"))
    res = run_cli("compare", synth_dir / "ra", synth_dir / "rb", "--names", "all", "strict", "--out", synth_dir / "cmp")
    assert res.exit_code == 0
    df = pd.read_csv(synth_dir / "cmp" / "comparison.csv").iloc[0]
    assert (df.all_nuclei, df.strict_nuclei, df.matched, df.only_all, df.only_strict) == (4, 3, 3, 1, 0)
    assert (synth_dir / "cmp" / "img1_compare.png").exists()


def test_comparison_run_with_another_settings_file(synth_dir, tmp_path_factory):
    from helpers import make_ws
    from recap.workspace import Workspace

    make_ws(synth_dir)
    d = synth_dir / "data"
    assert run_cli("run", synth_dir, "-i", d / "img1.tif").exit_code == 0
    other = tmp_path_factory.mktemp("other") / "strict.yaml"
    other.write_text("input: {}\nnormalization: {vrc_range: [150, 3000]}\nfilters: {min_area: 100000}\n"
                     "output: {directory: strict}\n", encoding="utf-8")  # fmt: skip
    res = run_cli("run", synth_dir, "-i", d / "img1.tif", "--settings", other)
    assert res.exit_code == 0
    assert summary(synth_dir / "strict").n_nuclei.tolist() == [0]  # its outputs, in the workspace
    ws = Workspace(synth_dir)
    ws.scan()  # (also finds the comparison run's log in the folder: still not the image's result)
    row = next(r for r in ws.status() if r["name"] == "img1.tif")
    assert row["n_nuclei"] == 4 and row["state"] == "up to date"
    res = run_cli("run", other)
    assert res.exit_code == 1 and "recap init FOLDER --from" in res.output
