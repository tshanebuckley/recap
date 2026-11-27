"""Tests that need neither Fiji nor StarDist. Run from the project folder:  pytest   (or: pixi run test)"""

import pytest
import yaml
from pydantic import ValidationError

from helpers import BASE, img, run_cli, summary  # noqa: F401
from recap import config


# ----------------------------------------------------------------- configuration
def test_template_round_trip(synth_dir, tmp_path_factory):
    cfg = tmp_path_factory.mktemp("settings") / "t.yaml"
    run_cli("template", cfg)
    res = run_cli("validate", cfg)  # a settings file on its own: checked, with how to start a workspace
    assert res.exit_code == 0 and "recap init FOLDER --from" in res.output
    assert run_cli("init", synth_dir, "--from", cfg).exit_code == 0
    assert (synth_dir / "raw" / "unbatched" / "img6.tif").is_file()  # init put the images into raw/
    assert run_cli("ignore", img(synth_dir, "img6.tif")).exit_code == 0
    res = run_cli("validate", synth_dir)
    assert res.exit_code == 0 and "5 image(s) found, 1 ignored" in res.output


@pytest.mark.parametrize(
    "bad",
    [
        "input: {images: [x.tif], dapi_channel: 0, vrc_channel: 0}",  # duplicate channels
        "input: {images: [x.tif]}\nsegmentation: {sigma: two}",  # wrong type
        "input: {images: [x.tif]}\nsegmentation: {merge_ration: 0.8}",  # unknown key (typo)
        "input: {images: [x.tif]}\nnormalization: {vrc_range: [3000, 150]}",  # min >= max
        "input: {images: [x.tif]}\nfilters: {border_max_missing: 1.5}",  # out of range
        "input: {images: [x.tif]}\nfragments: {area_percentiles: [99, 1]}",
    ],
)
def test_invalid_configs_rejected(bad):
    with pytest.raises(ValidationError):
        config.PipelineConfig.model_validate(yaml.safe_load(bad))


def test_bare_yaml_off_accepted():
    cfg = config.PipelineConfig.model_validate(
        yaml.safe_load("input: {images: [x]}\nsegmentation: {fragment_merge: off}")
    )
    assert cfg.segmentation.fragment_merge == "off"


def test_yaml_error_hints_at_windows_backslashes(tmp_path):
    from recap.config import load_config
    from recap.errors import ConfigError

    p = tmp_path / "w.yaml"
    p.write_text('input:\n  images: ["C:\\Users\\pi\\data\\*.tif"]\n', encoding="utf-8")
    with pytest.raises(ConfigError, match="forward slashes"):
        load_config(p)
    res = run_cli("validate", p)
    assert res.exit_code == 1 and "forward slashes" in res.output
    p.write_text("input:\n  images: ['C:\\Users\\pi\\data\\*.tif']\n", encoding="utf-8")
    assert load_config(p)[1].input.images == ["C:\\Users\\pi\\data\\*.tif"]


def test_config_template_file_matches_recap_template():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    assert (root / "config_template.yaml").read_text(encoding="utf-8") == config.TEMPLATE, (
        "config_template.yaml is out of date: pixi run recap template config_template.yaml --force"
    )
