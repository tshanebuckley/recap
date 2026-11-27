"""Importing counts made in plain Fiji (Cell Counter XML, Multi-point CSV, typed totals)."""

import pandas as pd
import pytest

from helpers import run_cli, runs
from recap import fijicount
from recap.counts import load_manual_counts, read_manual_counts
from recap.errors import RecapError
from recap.importers import import_counts

# nuclei of the synthetic image: (x, y) centres; (200, 100) is the infected one
CENTRES = [(50, 50, 1), (200, 100, 2), (60, 150, 1), (102, 150, 1)]

CELL_COUNTER_XML = """<?xml version="1.0" encoding="UTF-8"?>
<CellCounter_Marker_File>
 <Image_Properties>
  <Image_Filename>{name}</Image_Filename>
 </Image_Properties>
 <Marker_Data>
  <Current_Type>1</Current_Type>
{types}
 </Marker_Data>
</CellCounter_Marker_File>
"""


def cell_counter_xml(path, name, points, extra_type3=True):
    by_type = {}
    for x, y, t in points + ([(150, 20, 3)] if extra_type3 else []):
        by_type.setdefault(t, []).append((x, y))
    blocks = []
    for t in range(1, 5):
        marks = "".join(
            f"   <Marker><MarkerX>{x}</MarkerX><MarkerY>{y}</MarkerY><MarkerZ>1</MarkerZ></Marker>\n"
            for x, y in by_type.get(t, [])
        )
        blocks.append(f"  <Marker_Type>\n   <Type>{t}</Type>\n   <Name>Type {t}</Name>\n{marks}  </Marker_Type>")
    path.write_text(CELL_COUNTER_XML.format(name=name, types="\n".join(blocks)), encoding="utf-8")
    return path


def multipoint_csv(path, points, scale=1.0, counter=True):
    """Like Fiji's Results table after Analyze > Measure on a Multi-point selection."""
    rows = [
        {
            " ": i + 1,
            "Area": 0.04,
            "Mean": 100,
            "Min": 100,
            "Max": 100,
            "X": x * scale,
            "Y": y * scale,
            "Ch": 1,
            "Slice": 1,
            "Frame": 1,
            **({"Counter": t - 1, "Count": 1} if counter else {}),
        }
        for i, (x, y, t) in enumerate(points)
    ]
    pd.DataFrame(rows).to_csv(path, index=False)
    return path


def test_cell_counter_xml(synth_dir):
    img = synth_dir / "data" / "img1.tif"
    res = import_counts(img, cell_counter_xml(synth_dir / "m.xml", "img1.tif", CENTRES))
    mc = res.count
    assert (mc.nuclei, mc.infected, len(mc.points), mc.source) == (4, 1, 4, "imported")
    assert (200.0, 100.0, 1) in [tuple(p) for p in mc.points]
    assert any("type(s) [3]" in n for n in res.notes)


def test_multipoint_csv_calibrated(synth_dir):
    img = synth_dir / "data" / "img1.tif"
    csv = multipoint_csv(synth_dir / "mp.csv", CENTRES, scale=0.2 * 10)  # 2 units per px: out of bounds
    with pytest.raises(RecapError, match="pixel-size"):
        import_counts(img, csv)
    mc = import_counts(img, csv, pixel_size=2.0).count
    assert (mc.nuclei, mc.infected) == (4, 1) and (200.0, 100.0, 1) in [tuple(p) for p in mc.points]


def test_csv_without_counter_and_typed_totals(synth_dir):
    img = synth_dir / "data" / "img1.tif"
    res = import_counts(img, multipoint_csv(synth_dir / "nc.csv", CENTRES, counter=False))
    assert res.count.nuclei == 4 and res.count.infected is None and res.notes
    mc = import_counts(img, nuclei=5, vrc_pos=2).count
    assert (mc.nuclei, mc.infected, mc.points, mc.origin) == (5, 2, [], "typed")
    with pytest.raises(RecapError):
        import_counts(img, nuclei=2, vrc_pos=3)  # VRC+ > nuclei


def test_wrong_file_rejected(synth_dir):
    bad = synth_dir / "bad.xml"
    bad.write_text("<root/>", encoding="utf-8")
    with pytest.raises(RecapError, match="Cell Counter"):
        import_counts(synth_dir / "data" / "img1.tif", bad)


def test_import_command_replace_and_second_counter(synth_dir):
    img = synth_dir / "data" / "img1.tif"
    xml = cell_counter_xml(synth_dir / "m.xml", "img1.tif", CENTRES)
    out = synth_dir / "manual_counts.csv"
    r1 = run_cli("import-counts", img, xml, "--out", out, "--by", "ana")
    assert r1.exit_code == 0 and "Added" in r1.output and "4 nuclei (1 VRC+, 3 VRC-)" in r1.output
    r2 = run_cli("import-counts", img, xml, "--out", out, "--by", "ana")
    assert "Replaced" in r2.output
    run_cli("import-counts", img, "--nuclei", "5", "--vrc-pos", "1", "--out", out, "--by", "ben")
    rows = read_manual_counts(out)
    assert sorted(r.counted_by for r in rows) == ["ana", "ben"]
    assert len(load_manual_counts(out)) == 1  # one per image for spot-check reuse
    r = run_cli("import-counts", img, xml, "--out", out, "--uninfected-types", "1", "--infected-types", "1")
    assert r.exit_code == 1 and "both" in r.output


class NoFiji:
    def __init__(self, *a, **k):
        raise AssertionError("Fiji must not start when every count is reused")


def test_imported_counts_reused_by_spot_check(write_config, synth_dir, monkeypatch):
    monkeypatch.setattr(fijicount.FijiSession, "_start", NoFiji)
    out = synth_dir / "manual_counts.csv"
    for i in (1, 2):
        img = synth_dir / "data" / f"img{i}.tif"
        run_cli("import-counts", img, cell_counter_xml(synth_dir / f"m{i}.xml", img.name, CENTRES), "--out", out)
    cfg = write_config(
        "input: {images: [data/img1.tif, data/img2.tif]}\nnormalization: {vrc_range: [150, 3000]}\n"
        f"validation: {{enabled: true, sample_size: 2, previous_counts: {out.as_posix()}}}\n"
        "output: {directory: reuse}\n"
    )
    res = run_cli("run", cfg, input="s\n")
    assert res.exit_code == 0
    comp = pd.read_csv(next(runs(synth_dir)[-1].glob("spot_check_*.csv")))
    assert list(comp.manual_nuclei) == [4, 4] and list(comp.missed) == [0, 0]


def test_old_manual_counts_file_still_loads(synth_dir):
    """manual_counts CSVs written before 3.0 have no counted_by / origin columns."""
    old = synth_dir / "old.csv"
    pd.DataFrame(
        [
            {
                "image": "/x/img1.tif",
                "image_name": "img1.tif",
                "sha256": "abc",
                "nuclei": 4,
                "infected": 1,
                "points": "[[50, 50, 0]]",
                "source": "new",
                "counted_at": "2026-11-19T10:00:00",
            }
        ]
    ).to_csv(old, index=False)
    (mc,) = read_manual_counts(old)
    assert (mc.nuclei, mc.infected, mc.counted_by, mc.origin, mc.points) == (4, 1, None, None, [(50.0, 50.0, 0)])
    assert load_manual_counts(old)["abc"].source == "previous"
