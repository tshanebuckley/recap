"""Settings levels: workspace (recap.yaml) → staining batch → image."""

from pathlib import Path

import pandas as pd
import pytest

from helpers import BASE, img, make_ws, run_cli, runs
from recap.config import load_config
from recap.errors import ConfigError, RecapError
from recap.io import file_sha256
from recap.levels import allowed, check_overrides, resolve
from recap.workspace import Workspace


def test_allowed_keys_and_values():
    assert allowed("filters.min_focus") and allowed("input.exclude_regions")
    assert (
        not allowed("segmentation.stardist_model") and not allowed("input.images") and not allowed("output.directory")
    )
    with pytest.raises(ConfigError, match="cannot be set"):
        check_overrides({"segmentation.method": "classical"})
    with pytest.raises(ConfigError, match="Invalid setting value"):
        check_overrides({"filters.min_circularity": 3})


def test_most_specific_level_wins(write_config):
    _, cfg, _ = load_config(write_config(BASE))
    r = resolve(
        cfg,
        [
            ("batch:B1", {"filters.min_focus": 0.6, "measurement.vrc_threshold": 0.4}),
            ("image", {"measurement.vrc_threshold": 0.2}),
        ],
    )
    assert r.cfg.filters.min_focus == 0.6 and r.cfg.measurement.vrc_threshold == 0.2
    assert r.sources == {"filters.min_focus": "batch:B1", "measurement.vrc_threshold": "image"}
    assert r.label == "workspace + batch:B1 + image"
    assert r.overridden == ["filters.min_focus", "measurement.vrc_threshold"]


def _summary(run):
    df = pd.read_csv(next(run.glob("image_summary_*.csv")))
    return df.assign(image=df.image.map(lambda x: Path(x).name)).set_index("image")


def test_run_applies_levels_and_marks_them(write_config, synth_dir):
    write_config("input: {}\nnormalization: {vrc_range: [150, 3000]}\n")
    three = [x for i in (1, 2, 3) for x in ("-i", img(synth_dir, f"img{i}.tif"))]
    assert run_cli("run", synth_dir, *three).exit_code == 0
    (plain_run,) = runs(synth_dir)
    plain = pd.read_csv(next(plain_run.glob("image_summary_*.csv")))
    assert set(plain.settings) == {"workspace"}  # no batch or image settings yet
    make_ws(synth_dir)
    assert run_cli("batch", "assign", "stain A", img(synth_dir, "img1.tif"), img(synth_dir, "img2.tif")).exit_code == 0
    assert run_cli("settings", "set", "--batch", "stain A", "filters.min_area=100000", "-w", synth_dir).exit_code == 0
    res = run_cli("settings", "set", "--image", img(synth_dir, "img2.tif"), "filters.min_area=null")
    assert res.exit_code == 1 and "reason" in res.output  # an image needs a reason
    res = run_cli(
        "settings", "set", "--image", img(synth_dir, "img2.tif"), "filters.min_area=null", "--reason", "weak DAPI"
    )
    assert res.exit_code == 0
    import yaml

    doc = yaml.safe_load((synth_dir / "recap.yaml").read_text(encoding="utf-8"))  # every level is in recap.yaml
    assert doc["batches"] == {"stain A": {"filters": {"min_area": 100000}}}
    key = "raw/batches/stain A/img2.tif"  # the image moved with its batch: its entry follows it
    assert img(synth_dir, "img2.tif") == synth_dir / key
    assert doc["images"][key]["reason"] == "weak DAPI"
    assert doc["images"][key]["filters"] == {"min_area": None}
    out = run_cli("settings", "show", img(synth_dir, "img2.tif"))
    assert "batch: stain A" in out.output and "weak DAPI" in out.output and "set by image" in out.output
    three = [x for i in (1, 2, 3) for x in ("-i", img(synth_dir, f"img{i}.tif"))]
    assert run_cli("run", synth_dir, *three).exit_code == 0
    s = _summary(runs(synth_dir)[-1])
    assert s.loc["img1.tif", "n_nuclei"] == 0  # the batch's min_area removes everything
    assert s.loc["img2.tif", "n_nuclei"] == _summary(plain_run).loc["img2.tif", "n_nuclei"]  # image undoes it
    assert s.loc["img1.tif", "settings"] == "workspace + batch:stain A"
    assert s.loc["img2.tif", "settings"] == "workspace + batch:stain A + image"
    assert s.loc["img2.tif", "settings_reason"] == "weak DAPI" and s.loc["img2.tif", "overridden"] == "filters.min_area"
    assert s.loc["img3.tif", "settings"] == "workspace"
    ws = Workspace(synth_dir)
    rows = {r["name"]: r for r in ws.status()}
    assert rows["img1.tif"]["batch"] == "stain A" and rows["img2.tif"]["own_settings"]
    assert rows["img1.tif"]["state"] == "up to date" and rows["img4.tif"]["state"] == "not run"
    assert "stain A: 2 image(s)" in run_cli("batch", "list", synth_dir).output
    # changing a batch's settings makes exactly its images out of date
    assert run_cli("settings", "set", "--batch", "stain A", "filters.min_focus=0.5", "-w", synth_dir).exit_code == 0
    rows = {r["name"]: r for r in ws.status()}
    assert [rows[f"img{i}.tif"]["state"] for i in (1, 2, 3)] == ["out of date", "out of date", "up to date"]
    res = run_cli("run", synth_dir, "--out-of-date")
    assert res.exit_code == 0 and "5 image(s) not run yet or out of date" in res.output
    assert {r["state"] for r in ws.status()} == {"up to date"}
    assert "Every image is up to date" in run_cli("run", synth_dir, "--out-of-date").output
    # removing the batch's and the image's entries from recap.yaml: no levels any more
    ws.set_level_settings("batch", "stain A", {})
    ws.set_level_settings("image", rows["img2.tif"]["sha256"], {})
    assert "batches" not in (synth_dir / "recap.yaml").read_text(encoding="utf-8")
    assert run_cli("run", synth_dir, "-i", img(synth_dir, "img1.tif"), "-i", img(synth_dir, "img2.tif")).exit_code == 0
    assert set(_summary(runs(synth_dir)[-1]).settings) == {"workspace"}


def test_settings_unset_clear_and_workspace_level(synth_dir):
    make_ws(synth_dir, "filters: {min_focus: 0.5}  # keep me\n")
    assert run_cli("settings", "set", "filters.min_focus=0.7", "-w", synth_dir).exit_code == 0
    text = (synth_dir / "recap.yaml").read_text(encoding="utf-8")
    assert "min_focus: 0.7" in text and "# keep me" in text  # written into recap.yaml, comments kept
    run_cli("batch", "assign", "B", img(synth_dir, "img1.tif"))
    run_cli("settings", "set", "--batch", "B", "filters.min_area=50", "filters.max_area=900", "-w", synth_dir)
    assert run_cli("settings", "unset", "filters.min_area", "--batch", "B", "-w", synth_dir).exit_code == 0
    ws = Workspace(synth_dir)
    assert ws.level_settings("batch", "B") == {"filters.max_area": 900}
    run_cli("settings", "set", "--image", img(synth_dir, "img1.tif"), "filters.min_area=5", "--reason", "x")
    assert run_cli("settings", "clear", "--image", img(synth_dir, "img1.tif")).exit_code == 0
    assert ws.level_settings("image", file_sha256(img(synth_dir, "img1.tif"))) == {}
    assert run_cli("batch", "rename", "B", "C", "-w", synth_dir).exit_code == 0
    assert ws.level_settings("batch", "C") == {"filters.max_area": 900}
    assert run_cli("batch", "delete", "C", "-w", synth_dir).exit_code == 0
    assert ws.batches() == [] and ws.image_batches() == {}


def test_save_effective_stores_only_differences(synth_dir):
    make_ws(synth_dir)
    ws = Workspace(synth_dir)
    ws.scan()
    sha = file_sha256(img(synth_dir, "img1.tif"))
    ws.assign_batch([sha], "B")
    cfg = ws.resolve(sha).cfg.model_copy(deep=True)
    cfg.filters.min_area = 40
    held, shadowed = ws.save_effective("batch", sha, cfg, ["filters.min_area", "filters.min_focus"])
    assert held == {"filters.min_area": 40} and shadowed == []
    ws.update_level("image", sha, {"filters.min_area": 10}, reason="dim")
    cfg.filters.min_area = 60
    held, shadowed = ws.save_effective("workspace", sha, cfg, ["filters.min_area"])
    assert held == {"filters.min_area": 60} and shadowed == ["filters.min_area"]  # batch and image still win
    assert ws.settings().filters.min_area == 60
    cfg.filters.min_area = 40  # same as the batch now: the image's value goes
    held, _ = ws.save_effective("image", sha, cfg, ["filters.min_area"])
    assert held == {} and ws.level_settings("image", sha) == {}


def test_model_cannot_vary_by_level(synth_dir):
    ws = Workspace(make_ws(synth_dir))
    with pytest.raises(ConfigError):
        ws.set_level_settings("batch", "B", {"segmentation.stardist_model": "other"})
    with pytest.raises(RecapError, match="reason"):
        ws.set_level_settings("image", file_sha256(img(synth_dir, "img1.tif")), {"filters.min_focus": 0.5})
