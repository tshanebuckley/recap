"""Settings in levels: workspace → staining batch → image.

All three live in the workspace's recap.yaml. Its sections (input:, filters:, ...) are the
workspace level, the defaults for every image. Two more sections hold the levels below it, each
with only the values it changes:

    batches:
      stain 2026-10-03:              # a staining batch (which images are in it: recap.db)
        note: infected control slide 4
        normalization: {vrc_range: [3, 22]}
    images:
      data/day79_07.tif:             # one image, by its path in the workspace
        reason: weak DAPI, valid image
        by: ana
        date: 2026-11-25
        normalization: {vrc_weight: 1.4}

In code a level's values are flat ({"filters.min_focus": 0.6}); nest() and flatten() convert.
An image's effective settings are the workspace's with its batch's, then its own values applied;
the most specific level wins.

- batch: images stained together share settings (assign with `recap batch assign` or the app).
- image: corrections for a valid image whose staining is weaker or uneven. Needs a reason, and is
  marked in every output (image_summary: settings, overridden), so it is never hidden.

What a level may change: the recovery, segmentation, fragment, infection and filter settings, and
the regions to blank (input.exclude_regions). The model (segmentation.method, stardist_model,
cellpose_model) stays the same for the whole workspace, so results remain comparable and model
versions can be compared.
"""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from recap.config import PipelineConfig
from recap.errors import ConfigError

LEVELS = ("workspace", "batch", "image")
SECTIONS = ("normalization", "segmentation", "fragments", "measurement", "filters")
INPUT_KEYS = ("exclude_regions",)
FIXED = ("segmentation.method", "segmentation.stardist_model", "segmentation.cellpose_model")


def allowed(key: str) -> bool:
    if key in FIXED or "." not in key:
        return False
    section, name = key.split(".", 1)
    return section in SECTIONS or (section == "input" and name in INPUT_KEYS)


def get_path(d: dict, key: str) -> Any:
    section, name = key.split(".", 1)
    return d.get(section, {}).get(name)


def set_path(d: dict, key: str, value: Any) -> None:
    section, name = key.split(".", 1)
    d.setdefault(section, {})[name] = value


def check_overrides(overrides: dict[str, Any]) -> dict[str, Any]:
    """Raise ConfigError for a key a level may not change or a value the settings reject."""
    bad = [k for k in overrides if not allowed(k)]
    if bad:
        raise ConfigError(
            f"These settings cannot be set per workspace, batch or image: {', '.join(bad)}. Allowed: "
            f"{', '.join(SECTIONS)} sections and input.exclude_regions (not the model)."
        )
    d: dict[str, Any] = {"input": {}}
    for k, v in overrides.items():
        set_path(d, k, v)
    try:
        PipelineConfig.model_validate(d)
    except ValueError as e:
        raise ConfigError(f"Invalid setting value:\n{e}") from e
    return overrides


BATCH_INFO = ("note",)  # what a batch's entry holds besides settings
IMAGE_INFO = ("reason", "by", "date")  # what an image's entry holds besides settings


def flatten(entry: dict[str, Any] | None) -> dict[str, Any]:
    """A level's entry in recap.yaml as flat values: {"filters": {"min_focus": 0.6}} -> {"filters.min_focus": 0.6}.
    Its note, reason, by and date are left out."""
    out: dict[str, Any] = {}
    for section, values in (entry or {}).items():
        if isinstance(values, dict):
            for name, v in values.items():
                out[f"{section}.{name}"] = list(v) if isinstance(v, tuple) else v
    return out


def nest(values: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Flat values as recap.yaml sections, in the order of the settings file."""
    out: dict[str, dict[str, Any]] = {}
    order = ("input", *SECTIONS)
    for k in sorted(values, key=lambda k: (order.index(k.split(".")[0]) if k.split(".")[0] in order else 99, k)):
        set_path(out, k, values[k])
    return out


@dataclass
class Resolved:
    """An image's effective settings, and where each changed value came from."""

    cfg: PipelineConfig
    sources: dict[str, str] = field(default_factory=dict)  # key -> "batch:<name>" | "image"
    batch: str | None = None
    reason: str | None = None  # why the image has its own settings
    applied: list[str] = field(default_factory=list)  # levels with settings for this image, in order

    @property
    def label(self) -> str:
        """Short description for the outputs: the workspace, then every level with settings for this
        image (even where a more specific level replaced all its values)."""
        return " + ".join(["workspace", *self.applied])

    @property
    def overridden(self) -> list[str]:
        """Keys changed for this batch or image (not the workspace's recap.yaml)."""
        return sorted(k for k, s in self.sources.items() if s != "workspace")


def resolve(base: PipelineConfig, levels: list[tuple[str, dict[str, Any]]]) -> Resolved:
    """Apply [(source, overrides), ...] in order (batch, image) to the base settings (recap.yaml)."""
    d = copy.deepcopy(base.model_dump(mode="json"))
    sources: dict[str, str] = {}
    applied = [source for source, overrides in levels if overrides]
    for source, overrides in levels:
        for k, v in (overrides or {}).items():
            if allowed(k):
                set_path(d, k, v)
                sources[k] = source
    try:
        cfg = PipelineConfig.model_validate(d)
    except ValueError as e:
        raise ConfigError(f"The combined settings are invalid ({', '.join(s for s, _ in levels)}):\n{e}") from e
    return Resolved(cfg, sources, applied=applied)


def diff(base: PipelineConfig, changed: PipelineConfig, keys: list[str]) -> dict[str, Any]:
    """The keys whose value differs between two settings (what a level needs to store)."""
    a, b = base.model_dump(mode="json"), changed.model_dump(mode="json")
    return {k: get_path(b, k) for k in keys if get_path(a, k) != get_path(b, k)}


def settings_hash(cfg: PipelineConfig) -> str:
    """Fingerprint of everything that changes an image's counts (channels, blanked regions, model,
    and every section a level may change). A result whose fingerprint differs from the image's
    current settings is out of date."""
    d = cfg.model_dump(mode="json")
    d["segmentation"] = cfg.segmentation.resolved().model_dump(mode="json")  # derived values filled in, as run
    keep = {k: d[k] for k in SECTIONS}
    keep["input"] = {k: d["input"][k] for k in ("dapi_channel", "vrc_channel", "neuron_channel", "exclude_regions")}
    return hashlib.sha256(json.dumps(keep, sort_keys=True).encode()).hexdigest()[:16]
