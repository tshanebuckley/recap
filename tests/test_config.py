"""Tests that need neither Fiji nor StarDist. Run from the project folder:  pytest   (or: pixi run test)"""

import pytest
import yaml
from pydantic import ValidationError

from helpers import BASE, run_cli, summary  # noqa: F401
from recap import config


# ----------------------------------------------------------------- configuration
def test_template_round_trip(synth_dir):
    cfg = synth_dir / "t.yaml"
    run_cli("template", cfg)
    text = cfg.read_text(encoding="utf-8").replace("data/*.tif", (synth_dir / "data" / "*.tif").as_posix())
    cfg.write_text(text, encoding="utf-8")
    res = run_cli("validate", cfg)
    assert res.exit_code == 0 and "6 image(s) found" in res.output


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
