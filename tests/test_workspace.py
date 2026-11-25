"""The workspace database: scanning a folder, counts, edited masks, models and tuning records."""

import shutil

import numpy as np
import pandas as pd

from helpers import BASE, run_cli
from recap.counts import ManualCount, read_manual_counts
from recap.workspace import Workspace


def _run(write_config, name="out"):
    cfg = write_config(BASE + f"output: {{directory: {name}, save_labels: true}}\n", f"{name}.yaml")
    assert run_cli("run", cfg).exit_code == 0
    return cfg


def test_scan_status_and_image_details(write_config, synth_dir):
    _run(write_config)
    run_cli("import-counts", synth_dir / "data" / "img1.tif", "--nuclei", "5", "--vrc-pos", "1", "--by", "ana",
            "-o", synth_dir / "manual_counts.csv")  # fmt: skip
    ws = Workspace(synth_dir)
    found = ws.scan()
    assert found == {"images": 6, "runs": 1, "counts": 1}  # labels / excluded_labels are not images
    assert ws.scan() == {"images": 0, "runs": 0, "counts": 0}  # unchanged files are skipped
    rows = {r["name"]: r for r in ws.status()}
    assert rows["img1.tif"]["n_nuclei"] == 4 and rows["img1.tif"]["manual_nuclei"] == 5
    assert rows["img1.tif"]["counted_by"] == "ana" and rows["img2.tif"]["n_nuclei"] is None  # img2 not in run
    assert rows["img1.tif"]["size"] == "300x200x2"
    info = ws.image(rows["img1.tif"]["sha256"])
    assert info["results"][0]["labels_path"].endswith("img1_labels.tif") and len(info["counts"]) == 1
    res = run_cli("status", synth_dir)
    assert res.exit_code == 0 and "img1.tif" in res.output and "5 (ana)" in res.output


def test_counts_masks_and_moving_the_folder(write_config, synth_dir, tmp_path):
    _run(write_config)
    ws = Workspace(synth_dir)
    ws.scan()
    sha = next(r["sha256"] for r in ws.status() if r["name"] == "img1.tif")
    mc = ManualCount(image="x", image_name="img1.tif", sha256=sha, nuclei=3, infected=1,
                     points=[(1.0, 2.0, 0), (3.0, 4.0, 1), (5.0, 6.0, 0)], counted_at="2026-11-23T10:00:00",
                     counted_by="ben", tool="napari")  # fmt: skip
    assert ws.add_count(mc) == 1 and ws.add_count(mc) == 0  # the same count is stored once
    assert ws.counts(sha)[0].points == mc.points
    assert [c.counted_by for c in read_manual_counts(ws.db_path)] == ["ben"]  # report -m recap.db
    labels = np.zeros((200, 300), np.uint16)
    labels[10:20, 10:20], labels[50:60, 50:60] = 1, 7
    p1 = ws.save_mask(sha, labels, edited_by="ben")
    p2 = ws.save_mask(sha, labels)
    assert p1 != p2 and p1.exists() and p2.name.endswith("_edited_2.tif")  # earlier versions kept
    assert ws.reference_masks() == {sha: p2}
    edited, program, excluded = ws.latest_masks(sha)
    assert edited == p2 and program.name == "img1_labels.tif"
    assert ws.image(sha)["masks"][0]["n_nuclei"] == 2
    moved = tmp_path / "moved"
    shutil.copytree(synth_dir, moved)  # paths are relative: the copy works on its own
    ws2 = Workspace(moved)
    assert ws2.reference_masks()[sha].parent == moved / "recap-data" / "masks"
    assert ws2.image_path(sha) == moved / "data" / "img1.tif"


def test_models_and_tuning_records(synth_dir):
    ws = Workspace(synth_dir)
    assert ws.next_model_version("stardist") == 1
    d = ws.model_dir("stardist", 1)
    ws.register_model("stardist", 1, d, parent="2D_versatile_fluo", metrics={"f1": 0.9}, train_images=["a"])
    assert ws.next_model_version("stardist") == 2
    ws.set_model_metrics("stardist", 1, {"f1": 0.95})
    (m,) = ws.models("stardist")
    assert m["metrics"] == {"f1": 0.95} and m["parent"] == "2D_versatile_fluo" and m["path"] == str(d)
    ws.add_tuning("s1", synth_dir / "c.yaml", {"filters.min_focus": 0.6}, {"f1": 0.8})
    ws.add_tuning("s1", synth_dir / "c.yaml", {"filters.min_focus": 0.7}, {"f1": 0.9})
    assert [t["params"]["filters.min_focus"] for t in ws.tuning("s1")] == [0.7, 0.6]  # best first


def test_report_reads_counts_from_workspace(write_config, synth_dir):
    _run(write_config)
    ws = Workspace(synth_dir)
    ws.scan()
    sha = next(r["sha256"] for r in ws.status() if r["name"] == "img1.tif")
    ws.add_count(ManualCount(image="x", image_name="img1.tif", sha256=sha, nuclei=4, infected=1,
                             counted_at="2026-11-23T10:00:00", counted_by="ana"))  # fmt: skip
    res = run_cli("report", synth_dir / "out", "-m", ws.db_path, "-o", synth_dir / "rep")
    assert res.exit_code == 0, res.output
    vm = pd.read_csv(synth_dir / "rep" / "vs_manual.csv")
    assert list(vm.manual_nuclei) == [4]


def test_init_from_settings_file_and_42_workspace_settings_move_into_recap_yaml(synth_dir):
    from sqlmodel import select

    from helpers import run_cli
    from recap.db import LevelSettingsRow

    old = synth_dir / "old.yaml"
    old.write_text("input: {images: [data/*.tif]}  # my images\noutput: {directory: res}\n", encoding="utf-8")
    ws = Workspace(synth_dir)  # a 4.2 workspace: recap-data, workspace-level settings in the database
    with ws.session() as s:
        s.add(LevelSettingsRow(level="workspace", target="", overrides={"filters.min_focus": 0.6}))
        s.commit()
    res = run_cli("init", synth_dir, "--from", old)
    assert res.exit_code == 0 and "6 image(s)" in res.output
    text = (synth_dir / "recap.yaml").read_text(encoding="utf-8")
    assert "images: []" in text and "# my images" in text  # every image; comments kept
    ws = Workspace(synth_dir)
    assert ws.settings().filters.min_focus == 0.6  # moved from the database into recap.yaml
    with ws.session() as s:
        assert s.exec(select(LevelSettingsRow).where(LevelSettingsRow.level == "workspace")).first() is None
    assert run_cli("init", synth_dir, "--from", old).exit_code == 1  # recap.yaml exists: not replaced
    assert run_cli("validate", synth_dir).output.strip().endswith("OK: 6 image(s) found.")
