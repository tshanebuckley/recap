"""Exporting and importing whole settings files and StarDist models."""

import json
import zipfile

import yaml

from helpers import img, make_ws, run_cli
from recap.workspace import Workspace


def test_settings_export_and_import(synth_dir, tmp_path_factory):
    make_ws(synth_dir, "filters: {min_focus: 0.55}  # tuned on stain A\n")
    ws = Workspace(synth_dir)
    sha = ws.sha_of(img(synth_dir, "img1.tif"))
    ws.assign_batch([sha], "stain A", note="control")
    ws.update_level("batch", "stain A", {"filters.min_area": 70})
    ws.update_level("image", sha, {"filters.max_area": 900}, reason="big cells")
    out = tmp_path_factory.mktemp("shared") / "lab_settings.yaml"
    res = run_cli("settings", "export", out, "-w", synth_dir)
    assert res.exit_code == 0 and "1 single image" in res.output
    text = out.read_text(encoding="utf-8")
    assert text.startswith("# recap settings exported from the workspace") and "# tuned on stain A" in text
    doc = yaml.safe_load(text)
    assert doc["filters"]["min_focus"] == 0.55 and doc["batches"]["stain A"]["filters"] == {"min_area": 70}
    assert "images" not in doc
    assert run_cli("settings", "export", out, "-w", synth_dir).exit_code == 1  # exists: --force
    assert run_cli("settings", "export", out, "-w", synth_dir, "--with-images", "--force").exit_code == 0
    assert "images" in yaml.safe_load(out.read_text(encoding="utf-8"))

    # another workspace takes the file: its own sections replaced, its batches kept unless asked
    other = tmp_path_factory.mktemp("other")
    (other / "a.tif").write_bytes(img(synth_dir, "img2.tif").read_bytes())
    make_ws(other, "filters: {min_focus: 0.9}\n")
    ws2 = Workspace(other)
    ws2.assign_batch([ws2.sha_of(other / "raw" / "unbatched" / "a.tif")], "theirs")
    ws2.update_level("batch", "theirs", {"filters.min_area": 5})
    res = run_cli("settings", "import", out, "-w", other)
    assert res.exit_code == 0 and "saved as" in res.output
    doc2 = yaml.safe_load((other / "recap.yaml").read_text(encoding="utf-8"))
    assert doc2["filters"]["min_focus"] == 0.55 and list(doc2["batches"]) == ["theirs"]
    assert "images" not in doc2 and "# tuned on stain A" in (other / "recap.yaml").read_text(encoding="utf-8")
    (backup,) = (other / "recap-data" / "settings_backups").iterdir()
    assert yaml.safe_load(backup.read_text(encoding="utf-8"))["filters"]["min_focus"] == 0.9
    res = run_cli("settings", "import", out, "-w", other, "--with-batches", "--with-images")
    assert res.exit_code == 0 and "no such image in this workspace" in res.output
    doc2 = yaml.safe_load((other / "recap.yaml").read_text(encoding="utf-8"))
    assert sorted(doc2["batches"]) == ["stain A", "theirs"] and "images" not in doc2
    bad = out.with_name("bad.yaml")
    bad.write_text("filters: {min_focus: -3}\n", encoding="utf-8")
    assert run_cli("settings", "import", bad, "-w", other).exit_code == 1
    assert yaml.safe_load((other / "recap.yaml").read_text(encoding="utf-8"))["filters"]["min_focus"] == 0.55


def _fake_stardist(folder):
    folder.mkdir(parents=True)
    (folder / "config.json").write_text(json.dumps({"n_rays": 32, "n_dim": 2}), encoding="utf-8")
    (folder / "thresholds.json").write_text(json.dumps({"prob": 0.5, "nms": 0.3}), encoding="utf-8")
    (folder / "weights_best.h5").write_bytes(b"weights")
    (folder / "logs").mkdir()
    (folder / "logs" / "events.out").write_bytes(b"x" * 100)
    return folder


def test_stardist_models_export_and_import(synth_dir, tmp_path_factory):
    make_ws(synth_dir)
    ws = Workspace(synth_dir)
    v1 = _fake_stardist(ws.model_dir("stardist", 1))
    ws.register_model("stardist", 1, v1, parent="2D_versatile_fluo", metrics={"f1": 0.91}, train_images=["a", "b"])
    assert run_cli("models", "use", "v1", synth_dir).exit_code == 0
    shared = tmp_path_factory.mktemp("shared")
    res = run_cli("settings", "export", shared / "s.yaml", "-w", synth_dir)
    assert "--with-model" in res.output  # the settings name this workspace's model
    res = run_cli("models", "export", "v1", shared / "v1", synth_dir)
    assert res.exit_code == 0
    with zipfile.ZipFile(shared / "v1.zip") as z:
        names = z.namelist()
        manifest = yaml.safe_load(z.read("recap_model.yaml"))
    assert "v1/weights_best.h5" in names and not any("logs" in n for n in names)
    assert manifest["metrics"] == {"f1": 0.91} and manifest["n_train_images"] == 2

    other = tmp_path_factory.mktemp("other")
    make_ws(other)
    res = run_cli("settings", "import", shared / "s.yaml", "-w", other)
    assert res.exit_code == 0 and "this workspace keeps its model" in res.output
    assert Workspace(other).settings().segmentation.stardist_model == "2D_versatile_fluo"  # not a missing path
    res = run_cli("models", "import", shared / "v1.zip", other, "--use")
    assert res.exit_code == 0 and "Imported as StarDist v1" in res.output
    ws2 = Workspace(other)
    (m,) = ws2.models("stardist")
    assert m["metrics"] == {"f1": 0.91} and "imported from v1.zip" in m["note"] and m["parent"] == "2D_versatile_fluo"
    assert (
        ws2.settings().segmentation.stardist_model == "recap-data/models/stardist/v1"
        and ws2.model_in_use("stardist") == 1
    )
    # a plain StarDist folder from elsewhere
    plain = _fake_stardist(shared / "someone_elses_model")
    res = run_cli("models", "import", plain, other)
    assert (
        res.exit_code == 0
        and "v2" in res.output
        and (other / "recap-data" / "models" / "stardist" / "v2" / "config.json").is_file()
    )
    not_a_model = shared / "nothing"
    not_a_model.mkdir()
    res = run_cli("models", "import", not_a_model, other)
    assert res.exit_code == 1 and "no StarDist model found" in res.output


def test_settings_with_their_model_in_one_zip(synth_dir, tmp_path_factory):
    make_ws(synth_dir, "filters: {min_focus: 0.61}\n")
    ws = Workspace(synth_dir)
    v1 = _fake_stardist(ws.model_dir("stardist", 1))
    ws.register_model("stardist", 1, v1, parent="2D_versatile_fluo")
    shared = tmp_path_factory.mktemp("shared")
    assert run_cli("settings", "export", shared / "both", "-w", synth_dir, "--with-model").exit_code == 1  # standard
    ws.use_model("stardist", 1)
    res = run_cli("settings", "export", shared / "both", "-w", synth_dir, "--with-model")
    assert res.exit_code == 0 and (shared / "both.zip").is_file() and "v1 is in the same file" in res.output
    other = tmp_path_factory.mktemp("other")
    make_ws(other)
    ws2 = Workspace(other)
    mine = _fake_stardist(ws2.model_dir("stardist", 1))  # the other workspace has a v1 of its own
    ws2.register_model("stardist", 1, mine, parent="2D_versatile_fluo")
    res = run_cli("settings", "import", shared / "both.zip", "-w", other)
    assert res.exit_code == 0 and "added as StarDist v2 and is in use" in res.output
    assert "model in use changed: classical -> recap-data/models/stardist/v2" in res.output
    cfg = Workspace(other).settings()
    assert cfg.filters.min_focus == 0.61 and cfg.segmentation.stardist_model == "recap-data/models/stardist/v2"
    assert Workspace(other).model_in_use("stardist") == 2
    assert {m["version"] for m in Workspace(other).models("stardist")} == {1, 2}


def test_trained_model_paths_are_found_from_any_folder(synth_dir, tmp_path_factory, monkeypatch):
    """recap.yaml names a trained version relative to the workspace: found wherever recap was started,
    and two workspaces' v1 are two different models."""
    from recap.modelpath import model_path, use_model_root

    a, b = tmp_path_factory.mktemp("a"), tmp_path_factory.mktemp("b")
    for root in (a, b):
        make_ws(root)
        _fake_stardist(root / "recap-data" / "models" / "stardist" / "v1")
    monkeypatch.chdir(tmp_path_factory.mktemp("elsewhere"))
    Workspace(a)
    assert model_path("recap-data/models/stardist/v1") == (a / "recap-data/models/stardist/v1").resolve()
    use_model_root(b)  # e.g. running workspace b
    assert model_path("recap-data/models/stardist/v1") == (b / "recap-data/models/stardist/v1").resolve()
    assert model_path("2D_versatile_fluo") is None  # a standard model's name
