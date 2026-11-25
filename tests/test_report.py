"""recap report: methods vs manual counts, methods vs each other, counters vs each other."""

import pandas as pd

from helpers import run_cli
from test_import import CENTRES, cell_counter_xml


def test_report_end_to_end(write_config, synth_dir):
    imgs = "input: {images: [data/img1.tif, data/img2.tif, data/img3.tif]}\nnormalization: {vrc_range: [150, 3000]}\n"
    run_cli("run", write_config(imgs + "output: {directory: run_all}\n", "a.yaml"))
    run_cli("run", write_config(imgs + "filters: {min_circularity: 0.8}\noutput: {directory: run_strict}\n", "b.yaml"))
    mfile = synth_dir / "manual_counts.csv"
    for i in (1, 2):
        img = synth_dir / "data" / f"img{i}.tif"
        run_cli(
            "import-counts",
            img,
            cell_counter_xml(synth_dir / f"m{i}.xml", img.name, CENTRES),
            "-o",
            mfile,
            "--by",
            "ana",
        )
    run_cli(
        "import-counts", synth_dir / "data" / "img1.tif", "--nuclei", "5", "--vrc-pos", "1", "-o", mfile, "--by", "ben"
    )
    out = synth_dir / "rep"
    res = run_cli(
        "report", synth_dir / "run_all", synth_dir / "run_strict", "-n", "all", "-n", "strict", "-m", mfile, "-o", out
    )
    assert res.exit_code == 0, res.output
    for f in (
        "program_counts.csv",
        "vs_manual.csv",
        "method_agreement.csv",
        "inter_rater.csv",
        "summary.csv",
        "counts_vs_manual.png",
        "agreement_nuclei.png",
        "report.md",
    ):
        assert (out / f).exists(), f

    vm = pd.read_csv(out / "vs_manual.csv")
    assert len(vm) == 6  # 2 methods x 3 manual counts
    ana_all = vm[(vm.method == "all") & (vm.counted_by == "ana")]
    assert list(ana_all.difference) == [0, 0] and list(ana_all.missed) == [0, 0]
    ana_strict = vm[(vm.method == "strict") & (vm.counted_by == "ana")]
    assert list(ana_strict.difference) == [-1, -1] and list(ana_strict.missed) == [1, 1]  # infected one dropped

    s = pd.read_csv(out / "summary.csv").set_index("method")
    assert s.loc["all", "manual_total"] == 13 and s.loc["all", "program_total"] == 12  # 4 + 4 + 5 (ben)
    assert s.loc["strict", "missed"] == 2 and s.loc["all", "infection_agreement"] == 1.0

    ma = pd.read_csv(out / "method_agreement.csv")
    assert len(ma) == 3 and set(ma.only_a) == {1} and set(ma.only_b) == {0}

    ir = pd.read_csv(out / "inter_rater.csv")
    assert len(ir) == 1 and ir.iloc[0].difference == 1

    md = (out / "report.md").read_text(encoding="utf-8")
    assert "## Program vs manual" in md and "## Methods vs each other" in md and "## Between counters" in md


def test_report_without_manual_and_bad_names(write_config, synth_dir):
    run_cli(
        "run",
        write_config(
            "input: {images: [data/img1.tif]}\nnormalization: {vrc_range: [150, 3000]}\noutput: {directory: solo}\n"
        ),
    )
    res = run_cli("report", synth_dir / "solo", "-o", synth_dir / "rep2")
    assert res.exit_code == 0 and (synth_dir / "rep2" / "program_counts.csv").exists()
    assert not (synth_dir / "rep2" / "summary.csv").exists()
    res = run_cli("report", synth_dir / "solo", "-n", "a", "-n", "b", "-o", synth_dir / "rep3")
    assert res.exit_code == 1
    res = run_cli("report", synth_dir / "data", "-o", synth_dir / "rep4")  # not an output folder
    assert res.exit_code == 1 and "recap output folder" in res.output
