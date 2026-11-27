"""Shared test helpers."""

from pathlib import Path

import pandas as pd
from typer.testing import CliRunner

from recap import cli

runner = CliRunner()
BASE = "input: {images: [data/img1.tif]}\nnormalization: {vrc_range: [150, 3000]}\n"
# a workspace's recap.yaml for the synthetic images (input.images: [] = all six)
WS = "input: {images: []}\nnormalization: {vrc_range: [150, 3000]}\noutput: {directory: out, save_labels: true}\n"


def make_ws(folder: Path, extra: str = "") -> Path:
    """Make a folder a workspace with the synthetic images' settings (plus extra YAML lines)."""
    (folder / "recap.yaml").write_text(WS + extra, encoding="utf-8")
    return folder


def run_cli(*args, input=None):
    res = runner.invoke(cli.app, list(map(str, args)), input=input)
    if res.exception and not isinstance(res.exception, SystemExit):
        raise res.exception
    return res


def runs(root: Path) -> list[Path]:
    """A workspace's run folders, oldest first (processed/runs/<date>/)."""
    d = root / "processed" / "runs"
    return sorted(p for p in d.iterdir() if p.is_dir()) if d.is_dir() else []


def summary(folder: Path, k: int = -1) -> pd.DataFrame:
    """The image summary of a run folder, or of a workspace's k-th run (default: the latest)."""
    if runs(folder):
        folder = runs(folder)[k]
    return pd.read_csv(sorted(folder.glob("image_summary_*.csv"))[-1])


def img(root: Path, name: str) -> Path:
    """Where an image of a workspace is now (raw/batches/..., raw/unbatched/, or where it was left)."""
    hits = [p for p in sorted(root.rglob(name))
            if not {"processed", "recap-data"} & set(p.relative_to(root).parts)]  # fmt: skip
    assert len(hits) == 1, hits
    return hits[0]
