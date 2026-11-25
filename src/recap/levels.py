"""Settings in levels: workspace → staining batch → image.

The workspace level is the workspace's settings file, recap.yaml. Batches and images hold only
the settings they change ("overrides", stored in recap.db) as a flat dict such as
{"filters.min_focus": 0.6, "normalization.vrc_range": [5, 30]}. An image's effective settings
are recap.yaml with its batch's, then its own overrides applied; the most specific level wins.

- workspace: recap.yaml, the defaults for every image in the workspace folder.
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
    d = {"input": {"images": ["x"]}}
    for k, v in overrides.items():
        set_path(d, k, v)
    try:
        PipelineConfig.model_validate(d)
    except ValueError as e:
        raise ConfigError(f"Invalid setting value:\n{e}") from e
    return overrides


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
