"""Tests that need neither Fiji nor StarDist. Run from the project folder:  pytest   (or: pixi run test)"""

import shutil
import subprocess

import pandas as pd
import pytest

import recap
from helpers import BASE, img, run_cli, runs, summary  # noqa: F401


# ----------------------------------------------------------------- command line entry points
def test_version_option():
    res = run_cli("--version")
    assert res.exit_code == 0 and res.output.strip() == f"recap {recap.__version__}"


@pytest.mark.skipif(shutil.which("recap") is None, reason="recap not installed (pip install -e .)")
def test_installed_command():
    out = subprocess.run(["recap", "--version"], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == f"recap {recap.__version__}"


def test_compare_two_runs(write_config, synth_dir):
    run_cli("run", write_config(BASE))
    run_cli("run", write_config(BASE + "filters: {min_circularity: 0.8}\n"))
    ra, rb = runs(synth_dir)  # (each image's outputs are those of its latest run: copy a run's before rerunning)
    res = run_cli("run", synth_dir, "--settings", write_config(BASE + "output: {directory: all}\n", "all.yaml"))
    assert res.exit_code == 0
    comp = synth_dir / "processed" / "comparisons" / "all"
    res = run_cli("compare", comp, rb, "--names", "all", "strict", "--out", synth_dir / "cmp")
    assert res.exit_code == 0
    df = pd.read_csv(synth_dir / "cmp" / "comparison.csv").iloc[0]
    assert (df.all_nuclei, df.strict_nuclei, df.matched, df.only_all, df.only_strict) == (4, 3, 3, 1, 0)
    assert (synth_dir / "cmp" / "img1_compare.png").exists()


def test_comparison_run_with_another_settings_file(synth_dir, tmp_path_factory):
    from helpers import make_ws
    from recap.workspace import Workspace

    make_ws(synth_dir)
    assert run_cli("run", synth_dir, "-i", synth_dir / "data" / "img1.tif").exit_code == 0  # moved into raw/ first
    one = img(synth_dir, "img1.tif")
    assert one == synth_dir / "raw" / "unbatched" / "img1.tif"
    other = tmp_path_factory.mktemp("other") / "strict.yaml"
    other.write_text("input: {}\nnormalization: {vrc_range: [150, 3000]}\nfilters: {min_area: 100000}\n"
                     "output: {directory: strict}\n", encoding="utf-8")  # fmt: skip
    res = run_cli("run", synth_dir, "-i", one, "--settings", other)
    assert res.exit_code == 0
    comp = synth_dir / "processed" / "comparisons" / "strict"  # its own flat folder
    assert summary(comp).n_nuclei.tolist() == [0] and (comp / "img1_labels.tif").is_file()
    ws = Workspace(synth_dir)
    ws.scan()  # (also finds the comparison run's log in the folder: still not the image's result)
    row = next(r for r in ws.status() if r["name"] == "img1.tif")
    assert row["n_nuclei"] == 4 and row["state"] == "up to date"
    res = run_cli("run", other)
    assert res.exit_code == 1 and "recap init FOLDER --from" in res.output
    res = run_cli("settings", "show", synth_dir / "raw" / "unbatched" / "never_here.tif")
    assert res.exit_code == 1 and "not an image recap has moved" in res.output
    assert run_cli("settings", "show", synth_dir / "data" / "img1.tif").exit_code == 0  # an old path: followed
