"""The workspace database: scanning a folder, counts, edited masks, models and tuning records."""

import shutil

import numpy as np
import pandas as pd

from helpers import BASE, run_cli
from recap.counts import ManualCount, read_manual_counts
from recap.workspace import Workspace


def _run(write_config, name="out"):
    cfg = write_config(BASE + f"output: {{directory: {name}, save_labels: true}}\n")
    assert run_cli("run", cfg).exit_code == 0
    return cfg


def test_scan_status_and_image_details(write_config, synth_dir):
    _run(write_config)
    run_cli("import-counts", synth_dir / "data" / "img1.tif", "--nuclei", "5", "--vrc-pos", "1", "--by", "ana",
            "-o", synth_dir / "manual_counts.csv")  # fmt: skip
    ws = Workspace(synth_dir)
    found = ws.scan()
    assert found == {"images": 0, "runs": 0, "counts": 1}  # the run indexed the images and itself
    assert ws.scan() == {"images": 0, "runs": 0, "counts": 0}  # unchanged files are skipped
    rows = {r["name"]: r for r in ws.status()}
    assert len(rows) == 6 and "img1_labels.tif" not in rows  # labels / excluded_labels are not images
    assert rows["img2.tif"]["state"] == "ignored"  # BASE listed only img1: the others went to .recapignore
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


def test_init_from_settings_file_drops_input_images(synth_dir):
    old = synth_dir / "old.yaml"
    old.write_text("input: {images: [data/*.tif], dapi_channel: 0}  # my images\nimages: {x.tif: {reason: r}}\n"
                   "output: {directory: res}\n", encoding="utf-8")  # fmt: skip
    res = run_cli("init", synth_dir, "--from", old)
    assert res.exit_code == 0 and "6 image(s)" in res.output
    text = (synth_dir / "recap.yaml").read_text(encoding="utf-8")
    assert "images:" not in text and "x.tif" not in text and "# my images" in text  # not copied; comments kept
    assert run_cli("init", synth_dir, "--from", old).exit_code == 1  # recap.yaml exists: not replaced
    assert run_cli("validate", synth_dir).output.strip().endswith("OK: 6 image(s) found.")


def _make_44_database(ws: Workspace, rows: list[tuple], notes: dict[str, str]) -> None:
    """Turn a workspace's database back into recap 4.4's: settings per level in level_settings,
    batch notes in batches.note."""
    import json
    import sqlite3

    ws.engine.dispose()
    con = sqlite3.connect(ws.db_path)
    con.execute("CREATE TABLE level_settings (id INTEGER PRIMARY KEY, level TEXT NOT NULL, target TEXT NOT NULL, "
                "overrides JSON, reason TEXT, updated_by TEXT, updated_at TEXT)")  # fmt: skip
    con.execute("ALTER TABLE batches ADD COLUMN note TEXT")
    for level, target, overrides, reason in rows:
        con.execute("INSERT INTO level_settings (level, target, overrides, reason, updated_by, updated_at) "
                    "VALUES (?, ?, ?, ?, 'ana', '2026-11-20T10:00:00')",
                    (level, target, json.dumps(overrides), reason))  # fmt: skip
    for name, note in notes.items():
        con.execute("UPDATE batches SET note = ? WHERE name = ?", (note, name))
    con.execute("UPDATE alembic_version SET version_num = '0003'")
    con.execute("UPDATE meta SET value = '4.4.0' WHERE key = 'recap_version'")
    con.commit()
    con.close()


def test_44_workspace_settings_move_into_recap_yaml(synth_dir):
    import sqlite3

    import yaml

    from helpers import make_ws

    make_ws(synth_dir, "# my comment\n")
    ws = Workspace(synth_dir)
    ws.scan()
    sha = {r["name"]: r["sha256"] for r in ws.status()}
    ws.assign_batch([sha["img1.tif"], sha["img2.tif"]], "stain A")
    ws.assign_batch([sha["img3.tif"]], "stain B")
    _make_44_database(ws, [
        ("workspace", "", {"filters.min_focus": 0.6}, None),  # (4.2)
        ("batch", "stain A", {"filters.min_area": 50, "normalization.vrc_range": [3, 22]}, None),
        ("image", sha["img2.tif"], {"normalization.vrc_weight": 1.4}, "weak DAPI"),
    ], {"stain A": "control slide 4", "stain B": "no settings, just a note"})  # fmt: skip
    ws = Workspace(synth_dir)  # opening it with 5.0: moved into recap.yaml, then the database is upgraded
    assert ws.upgrade is not None and ws.upgrade.from_version == "4.4.0"
    text = (synth_dir / "recap.yaml").read_text(encoding="utf-8")
    doc = yaml.safe_load(text)
    assert "# my comment" in text and doc["filters"]["min_focus"] == 0.6
    assert doc["batches"]["stain A"] == {"note": "control slide 4", "filters": {"min_area": 50},
                                         "normalization": {"vrc_range": [3, 22]}}  # fmt: skip
    assert doc["batches"]["stain B"] == {"note": "no settings, just a note"}
    assert doc["images"]["data/img2.tif"] == {"reason": "weak DAPI", "by": "ana", "date": "2026-11-20",
                                              "normalization": {"vrc_weight": 1.4}}  # fmt: skip
    assert list(doc)[-2:] == ["batches", "images"]  # after the workspace's own sections
    r = ws.resolve(sha["img2.tif"])
    assert (r.batch, r.reason, r.cfg.filters.min_area, r.cfg.normalization.vrc_weight) == (
        "stain A",
        "weak DAPI",
        50,
        1.4,
    )
    assert {b["name"]: b["note"] for b in ws.batches()} == {"stain A": "control slide 4",
                                                             "stain B": "no settings, just a note"}  # fmt: skip
    con = sqlite3.connect(ws.db_path)
    tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "level_settings" not in tables and "note" not in [c[1] for c in con.execute("PRAGMA table_info(batches)")]
    con.close()
    assert list((synth_dir / "recap-data" / "backups").glob("*4.4.0*"))  # the 4.4 database was backed up


def test_batch_rename_delete_and_moved_images_follow_in_recap_yaml(synth_dir):
    import yaml

    from helpers import make_ws

    make_ws(synth_dir)
    ws = Workspace(synth_dir)
    ws.scan()
    sha = {r["name"]: r["sha256"] for r in ws.status()}
    ws.assign_batch([sha["img1.tif"]], "A", note="first")
    ws.update_level("batch", "A", {"filters.min_area": 70})
    ws.update_level("image", sha["img1.tif"], {"filters.min_focus": 0.5}, reason="blurry corner")
    ws.rename_batch("A", "A2")
    doc = yaml.safe_load((synth_dir / "recap.yaml").read_text(encoding="utf-8"))
    assert doc["batches"] == {"A2": {"note": "first", "filters": {"min_area": 70}}}
    (synth_dir / "moved").mkdir()
    (synth_dir / "data" / "img1.tif").rename(synth_dir / "moved" / "img1.tif")  # its own settings follow it
    ws.scan()
    doc = yaml.safe_load((synth_dir / "recap.yaml").read_text(encoding="utf-8"))
    assert list(doc["images"]) == ["moved/img1.tif"] and ws.resolve(sha["img1.tif"]).cfg.filters.min_focus == 0.5
    ws.delete_batch("A2")
    assert "batches" not in yaml.safe_load((synth_dir / "recap.yaml").read_text(encoding="utf-8"))


def test_recapignore_and_old_input_images(synth_dir):
    from helpers import make_ws

    make_ws(synth_dir)
    (synth_dir / "old").mkdir()
    (synth_dir / "data" / "img6.tif").rename(synth_dir / "old" / "img6.tif")
    ws = Workspace(synth_dir)
    ws.scan()
    assert len(ws.images()) == 6
    d = synth_dir / "data"
    assert ws.ignore([d / "img1.tif", synth_dir / "old"]) == ["data/img1.tif", "old/"]
    assert ws.ignore([d / "img1.tif"]) == []  # already
    names = sorted(p.name for p in ws.images())
    assert names == ["img2.tif", "img3.tif", "img4.tif", "img5.tif"]
    states = {r["name"]: r["state"] for r in ws.status()}
    assert states["img1.tif"] == states["img6.tif"] == "ignored" and len(states) == 6  # still listed, as ignored
    assert ws.unignore([synth_dir / "old" / "img6.tif"]) == ["old/img6.tif"]  # inside an ignored folder: !path
    assert "!old/img6.tif" in (synth_dir / ".recapignore").read_text(encoding="utf-8")
    assert ws.unignore([d / "img1.tif"]) == ["data/img1.tif"]
    assert len(ws.images()) == 6
    # command line, and runs refuse ignored images
    assert run_cli("ignore", d / "img2.tif").exit_code == 0
    assert "data/img2.tif" in run_cli("ignore", "-w", synth_dir).output
    res = run_cli("run", synth_dir, "-i", d / "img2.tif")
    assert res.exit_code == 1 and "ignored" in res.output
    assert run_cli("unignore", d / "img2.tif").exit_code == 0
    # an old recap.yaml listing images: the others become ignored, the key goes
    (synth_dir / ".recapignore").unlink()
    (synth_dir / "recap.yaml").write_text("input: {images: [data/img1.tif, data/img2.tif]}\n", encoding="utf-8")
    ws = Workspace(synth_dir)
    assert sorted(p.name for p in ws.images()) == ["img1.tif", "img2.tif"]
    assert "images" not in (synth_dir / "recap.yaml").read_text(encoding="utf-8")


def test_add_images_files_folders_batches_and_duplicates(synth_dir, tmp_path, monkeypatch):
    import shutil

    from helpers import make_ws, run_cli

    ws_dir = tmp_path / "ws"
    ws_dir.mkdir()
    make_ws(ws_dir)
    src = synth_dir / "data"
    extra = tmp_path / "scans" / "day2"
    extra.mkdir(parents=True)
    shutil.copy(src / "img3.tif", extra / "img3.tif")
    shutil.copy(src / "img4.tif", extra / "img4.tif")
    (extra / "img4_labels.tif").write_bytes((src / "img4.tif").read_bytes())  # a recap output: skipped
    ws = Workspace(ws_dir)
    res = ws.add_images([src / "img1.tif"], batch="stain A", note="first")
    assert [p.name for p in res.added] == ["img1.tif"] and (ws_dir / "stain A" / "img1.tif").is_file()
    res = ws.add_images([tmp_path / "scans"], batch="stain B")  # a folder: subfolders included
    assert sorted(p.name for p in res.added) == ["img3.tif", "img4.tif"] and res.already == []
    res = ws.add_images([src / "img1.tif", src / "img2.tif"], batch="stain B")  # img1: same content
    assert [p.name for p in res.added] == ["img2.tif"] and [p.name for p in res.already] == ["img1.tif"]
    assert "1 already in the workspace" in res.message()
    batches = {r["name"]: r["batch"] for r in ws.status()}
    assert batches == {"img1.tif": "stain B", "img2.tif": "stain B", "img3.tif": "stain B", "img4.tif": "stain B"}
    inside = ws_dir / "loose.tif"
    shutil.copy(src / "img5.tif", inside)
    res = ws.add_images([inside])  # already in the folder: indexed where it is, no batch
    assert res.added == [inside] and res.copied == [] and not (ws_dir / "images").exists()
    res = ws.add_images([src / "img6.tif"])
    assert (ws_dir / "images" / "img6.tif").is_file()  # no batch: the 'images' folder
    # the command: asks which batch when not told
    assert run_cli("add", src / "img2.tif", "-w", ws_dir).exit_code == 1  # not interactive: must say
    monkeypatch.setattr("recap.cli._interactive", lambda: True)
    res = run_cli("add", tmp_path / "scans", "-w", ws_dir, input="1\n")  # 1) the first batch listed
    assert res.exit_code == 0 and "Which staining batch" in res.output and "2 already in the workspace" in res.output
    res = run_cli("add", src / "img1.tif", "-w", ws_dir, "--batch", "stain C", "--to", "redo")
    assert res.exit_code == 0 and "0 image(s) added" in res.output
    assert {r["name"]: r["batch"] for r in Workspace(ws_dir).status()}["img1.tif"] == "stain C"
