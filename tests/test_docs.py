"""docs/output_columns.md must list exactly the columns recap writes."""

import re
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from helpers import img, run_cli, runs
from recap import fijicount, segment
from recap.config import FragmentConfig
from test_import import CENTRES, cell_counter_xml

DOC = Path(__file__).resolve().parents[1] / "docs" / "output_columns.md"


def documented() -> dict[str, tuple[list[str], set[str]]]:
    """{section title: (columns in order, optional columns)} from the markdown tables."""
    out, title = {}, None
    for line in DOC.read_text(encoding="utf-8").splitlines():
        h = re.match(r"^#{2,3} `([^`]+\.csv)`", line)
        if line.startswith("#"):
            title = None
        if h:
            title = h.group(1)
            out[title] = ([], set())
            continue
        row = re.match(r"^\| `([^`]+)` \| (.*) \|$", line)
        if row and title:
            out[title][0].append(row.group(1).replace("<A>", "A").replace("<B>", "B"))
            if row.group(2).startswith("*(optional"):
                out[title][1].add(row.group(1))
    return out


@pytest.fixture(scope="module")
def outputs(tmp_path_factory):
    import tifffile

    from conftest import make_synthetic

    d = tmp_path_factory.mktemp("docs")
    (d / "data").mkdir()
    for i in range(2):
        tifffile.imwrite(d / "data" / f"img{i}.tif", make_synthetic(seed=i))
    base = "input: {}\nnormalization: {vrc_range: [150, 3000]}\n"
    settings = d / "recap.yaml"  # one workspace; its settings change between the runs
    settings.write_text(base + "filters: {min_circularity: 0.8}\n", encoding="utf-8")
    assert run_cli("run", d).exit_code == 0  # -> processed/runs/<date>/ and processed/unbatched/<image>/
    other = d / "other.yaml"
    other.write_text(base + "output: {directory: rb}\n", encoding="utf-8")
    assert run_cli("run", d, "--settings", other).exit_code == 0  # -> processed/comparisons/rb/
    (ra,) = runs(d)
    rb = d / "processed" / "comparisons" / "rb"
    own = d / "processed" / "unbatched" / "img0"
    mc = d / "mc.csv"
    for i in range(2):
        run_cli(
            "import-counts",
            img(d, f"img{i}.tif"),
            cell_counter_xml(d / f"m{i}.xml", f"img{i}.tif", CENTRES),
            "-o",
            mc,
            "--by",
            "ana",
        )
    run_cli("import-counts", img(d, "img0.tif"), "--nuclei", "5", "-o", mc, "--by", "ben")
    settings.write_text(
        base + f"validation: {{enabled: true, sample_size: 2, previous_counts: {mc.as_posix()}}}\n",
        encoding="utf-8",
    )

    class NoFiji:
        def __init__(self, *a, **k):
            raise AssertionError

    start, fijicount.FijiSession._start = fijicount.FijiSession._start, NoFiji
    try:
        run_cli("run", d, input="s\n")
    finally:
        fijicount.FijiSession._start = start
    rs = runs(d)[-1]
    run_cli("compare", ra, rb, "--names", "A", "B", "--out", d / "cmp")
    run_cli("report", ra, rb, "-m", mc, "-o", d / "rep")
    first = lambda folder, pattern: sorted(folder.glob(pattern))[0]  # noqa: E731
    cols = lambda p: list(pd.read_csv(p).columns)  # noqa: E731
    return {
        "<image>_nuclei.csv": cols(own / "img0_nuclei.csv"),
        "<image>_excluded.csv": cols(own / "img0_excluded.csv"),
        "all_nuclei_<stamp>.csv": cols(first(ra, "all_nuclei_*.csv")),
        "image_summary_<stamp>.csv": cols(first(ra, "image_summary_*.csv")),
        "manual_counts*.csv": cols(mc),
        "spot_check_<stamp>.csv": cols(first(rs, "spot_check_*.csv")),
        "comparison.csv": cols(d / "cmp" / "comparison.csv"),
        **{
            f"{n}.csv": cols(d / "rep" / f"{n}.csv")
            for n in ("program_counts", "vs_manual", "method_agreement", "inter_rater", "summary")
        },
    }


def merges_columns() -> list[str]:
    yy, xx = np.mgrid[:120, :200]
    lab = np.zeros((120, 200), np.int32)
    whole = (yy - 60) ** 2 + (xx - 50) ** 2 < 16**2
    lab[whole] = 1
    lab[whole & (xx > 58)] = 2
    for k in range(5):
        lab[((yy - 20) ** 2 + (xx - 20 - 36 * k) ** 2 < 16**2)] = 10 + k
        lab[((yy - 100) ** 2 + (xx - 20 - 36 * k) ** 2 < 16**2)] = 20 + k
    _, rec = segment.fragment_merge(lab, np.zeros(lab.shape), FragmentConfig(), "shape", 0.3, 50)
    return list(pd.DataFrame(rec).assign(image="x").columns)


def check(name, actual, docs):
    expected, optional = docs[name]
    missing_in_docs = [c for c in actual if c not in expected]
    stale_in_docs = [c for c in expected if c not in actual and c not in optional]
    assert not missing_in_docs and not stale_in_docs, (
        f"{name}: undocumented {missing_in_docs}, documented but not written {stale_in_docs}"
    )


def test_documented_columns_match_outputs(outputs):
    docs = documented()
    nuclei, _ = docs["<image>_nuclei.csv"]
    docs["<image>_excluded.csv"] = (nuclei + docs["<image>_excluded.csv"][0], docs["<image>_nuclei.csv"][1])
    docs["all_nuclei_<stamp>.csv"] = docs["<image>_nuclei.csv"]
    spot = [c for c in docs["spot_check_<stamp>.csv"][0] if c not in ("image_name", "source")]
    vs = docs["vs_manual.csv"][0]
    docs["vs_manual.csv"] = (vs[:3] + spot + vs[3:], set())
    for name, actual in outputs.items():
        check(name, actual, docs)
    check("<image>_merges.csv", merges_columns(), docs)


def test_every_section_is_checked(outputs):
    titles = set(documented())
    assert titles == set(outputs) | {"<image>_merges.csv"}, titles ^ (set(outputs) | {"<image>_merges.csv"})


def test_relative_links_in_docs_resolve():
    """README, docs/ and the user guide: every relative link points at a file (and heading) that exists."""
    root = DOC.parents[1]
    files = [root / "README.md", root / "CONTRIBUTING.md", *sorted((root / "docs").rglob("*.md"))]
    bad = []
    for md in files:
        text = md.read_text(encoding="utf-8")
        for target in re.findall(r"\]\(([^)\s]+)\)", text):
            if target.startswith(("http://", "https://", "mailto:")):
                continue
            path, _, anchor = target.partition("#")
            dest = (md.parent / path).resolve() if path else md
            if not dest.exists():
                bad.append(f"{md.relative_to(root)} -> {target}")
            elif anchor and dest.suffix == ".md":
                heads = re.findall(r"^#+ (.+)$", dest.read_text(encoding="utf-8"), flags=re.M)
                slugs = {re.sub(r"[^a-z0-9 -]", "", h.lower()).strip().replace(" ", "-") for h in heads}
                if anchor not in slugs:
                    bad.append(f"{md.relative_to(root)} -> {target} (no such heading)")
    assert not bad, bad
    guide = (root / "docs" / "guide" / "README.md").read_text(encoding="utf-8")
    stories = set(re.findall(r"## (US-\d+)", "".join(p.read_text(encoding="utf-8")
                                                     for p in (root / "docs" / "guide").glob("0*.md"))))  # fmt: skip
    assert stories == {f"US-{i:02d}" for i in range(1, 32)}, sorted(stories)  # every story has a section
    assert "US-31" in guide
