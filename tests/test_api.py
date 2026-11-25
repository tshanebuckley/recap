"""The library interface used by a GUI: no printing, progress callbacks, cancelling."""

import numpy as np
import pandas as pd

import recap
from helpers import BASE
from recap import load_config, process_file, run_pipeline
from recap.errors import ConfigError


def test_process_file(write_config, synth_dir):
    cfg_path = write_config(BASE)
    _, cfg, _ = load_config(cfg_path)
    res = process_file(cfg, synth_dir / "data" / "img1.tif")
    assert res.labels.max() == 4 and len(res.table) == 4 and int(res.table.infected.sum()) == 1
    assert res.labels.dtype.kind in "iu" and res.labels.shape == (200, 300)


def test_run_pipeline_progress_and_cancel(write_config, synth_dir, capsys):
    cfg = write_config(
        "input: {images: [data/*.tif]}\nnormalization: {vrc_range: [150, 3000]}\noutput: {directory: api}\n"
    )
    calls = []
    res = run_pipeline(cfg, progress=lambda i, n, path, info: calls.append((i, n, path.name, info["n_nuclei"])))
    assert res.status == "completed" and len(calls) == 6 and calls[-1][:2] == (6, 6)
    assert all(c[3] == 4 for c in calls)
    assert "image_summary" in res.files and res.config.segmentation.min_area > 0
    assert capsys.readouterr().out == ""  # library prints nothing to stdout

    done = []
    res = run_pipeline(cfg, progress=lambda i, n, p, info: done.append(i), cancel=lambda: len(done) >= 2)
    assert res.status == "cancelled" and done == [1, 2]
    summary = pd.read_csv(res.files["image_summary"])
    assert len(summary) == 2  # outputs of finished images are still written


def test_run_pipeline_config_error(write_config):
    bad = write_config("input: {images: [nothing/*.tif]}\n", "bad.yaml")
    try:
        run_pipeline(bad)
    except ConfigError as e:
        assert "no image matches" in str(e)
    else:
        raise AssertionError("ConfigError expected")


def test_lazy_api_names():
    assert recap.__version__ and callable(recap.process_image) and recap.RunResult
    assert np.__name__


def test_settings_schema_for_gui_forms(write_config):
    from recap import PipelineConfig

    schema = PipelineConfig.model_json_schema()
    seg = schema["$defs"]["SegmentationConfig"]["properties"]
    assert seg["method"]["enum"] == ["classical", "stardist", "cellpose"]
    assert "description" in seg["diameter"] and seg["diameter"]["exclusiveMinimum"] == 0
    _, cfg, _ = load_config(write_config(BASE))
    again = PipelineConfig.model_validate(cfg.model_dump(mode="json"))  # form values -> same validation
    assert again == cfg
