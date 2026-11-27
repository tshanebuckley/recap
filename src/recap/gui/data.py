"""What the app shows: one image's channels and the masks a previous run saved for it.

No Qt or napari here, so this part is tested on every system without a display.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import tifffile

from recap.config import PipelineConfig
from recap.errors import ConfigError, RecapError
from recap.io import load_channels

# Workspace opened when `recap gui` gets none and none was used before: the day-79 organoid image.
EXAMPLE_WORKSPACE = Path("examples")


@dataclass
class ImageView:
    """Everything one napari window needs for one image."""

    image: Path
    channels: dict[str, tuple[np.ndarray, str]]  # layer name -> (2D array, napari colormap)
    labels: np.ndarray | None  # counted nuclei (`*_labels.tif`), editable
    excluded: np.ndarray | None  # nuclei removed by the filters (`*_excluded_labels.tif`)
    labels_path: Path | None  # where the mask comes from
    messages: list[str] = field(default_factory=list)  # shown to the user in the window
    sha256: str | None = None
    edited: bool = False  # labels are a hand-edited mask (else the program's)
    points: list[tuple[float, float, int]] = field(default_factory=list)  # latest manual count (x, y, counter)


def project_root() -> Path:
    """The recap folder (with pixi.toml) for an editable install, else the current folder."""
    here = Path(__file__).resolve()
    for d in here.parents:
        if (d / "pixi.toml").is_file() and (d / "src" / "recap").is_dir():
            return d
    return Path.cwd()


def example_workspace() -> Path | None:
    for base in (Path.cwd(), project_root()):
        if (base / EXAMPLE_WORKSPACE / "recap.yaml").is_file():
            return base / EXAMPLE_WORKSPACE
    return None


def named_channels(img: np.ndarray, cfg: PipelineConfig | None) -> dict[str, tuple[np.ndarray, str]]:
    """Channels as napari layers: named by the settings if given, else channel 0, 1, ..."""
    if cfg is None:
        colours = ["red", "green", "blue"] if len(img) == 3 else ["gray", "green", "magenta", "cyan", "yellow"]
        return {f"channel {i}": (img[i], colours[i % len(colours)]) for i in range(len(img))}
    inp = cfg.input
    channels: dict[str, tuple[np.ndarray, str]] = {}
    # the channel numbers are in the names, so a wrong channel in the settings shows at a glance
    if inp.neuron_channel is not None:
        channels[f"marker (ch {inp.neuron_channel})"] = (img[inp.neuron_channel], "red")
    channels[f"ICP8 (ch {inp.vrc_channel})"] = (img[inp.vrc_channel], "green")
    channels[f"DAPI (ch {inp.dapi_channel})"] = (img[inp.dapi_channel], "blue")
    return channels


def view_from_workspace(ws, sha: str, cfg: PipelineConfig | None = None) -> ImageView:
    """An image of a workspace with its newest mask (hand-edited if any), excluded nuclei and latest count.
    Channels are named by the image's settings (recap.yaml, its batch, itself)."""
    if cfg is None and ws.has_settings:
        try:
            cfg = ws.resolve(sha).cfg
        except (ValueError, RecapError):
            cfg = None
    path = ws.image_path(sha)
    if path is None or not path.is_file():
        raise ConfigError(f"image file missing: {path}")
    edited, program, excluded = ws.latest_masks(sha)
    mask = edited or program
    counts = ws.counts(sha)
    view = ImageView(
        image=path,
        channels=named_channels(load_channels(path), cfg),
        labels=tifffile.imread(mask) if mask and mask.is_file() else None,
        excluded=tifffile.imread(excluded) if excluded and excluded.is_file() else None,
        labels_path=mask,
        sha256=sha,
        edited=edited is not None,
        points=list(counts[-1].points) if counts else [],
    )
    if view.labels is None:
        view.messages.append(f"no outlines for {path.name} yet: press Run selected (or Run all) to make them")
    return view
