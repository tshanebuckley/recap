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


def summary(out_dir: Path) -> pd.DataFrame:
    return pd.read_csv(next(out_dir.glob("image_summary_*.csv")))
