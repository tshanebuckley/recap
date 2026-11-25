"""Live preview for guided mode (no Qt): rerun only what a settings change needs.

The slow stage (normalise, combine, StarDist / Cellpose) reruns only when a setting it depends on
changes (pipeline.MODEL_STAGE_SETTINGS); fragment merging, filters and the infection call rerun
on its saved result in a fraction of a second.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

import numpy as np

from recap.config import PipelineConfig
from recap.levels import get_path
from recap.pipeline import MODEL_STAGE_SETTINGS, Result, Stage1, finish_stage, segment_stage
from recap.segment import combine


def stage_key(cfg: PipelineConfig) -> str:
    d = cfg.model_dump(mode="json")
    keys = [k for k in MODEL_STAGE_SETTINGS if "." in k] + [f"normalization.{k}" for k in d["normalization"]]
    return json.dumps({k: get_path(d, k) for k in keys}, sort_keys=True)


@dataclass
class Preview:
    result: Result
    recovered: np.ndarray  # the combined DAPI/VRC image the model sees
    resegmented: bool  # the slow stage ran for this update
    counts: dict


class PreviewSession:
    """One image's channels; update(cfg) gives the result for those settings."""

    def __init__(self, dapi: np.ndarray, vrc: np.ndarray, neuron: np.ndarray | None = None):
        self.dapi, self.vrc, self.neuron = dapi, vrc, neuron
        self._key: str | None = None
        self._s1: Stage1 | None = None
        self.n_segmentations = 0

    def needs_segmentation(self, cfg: PipelineConfig) -> bool:
        return stage_key(cfg) != self._key

    def recovered(self, cfg: PipelineConfig) -> np.ndarray:
        """The combined channel only: instant, for the Recovery step."""
        return combine(self.dapi, self.vrc, cfg.normalization)[2]

    def update(self, cfg: PipelineConfig) -> Preview:
        again = self.needs_segmentation(cfg)
        if again:
            self._s1 = segment_stage(cfg, self.dapi, self.vrc)
            self._key = stage_key(cfg)
            self.n_segmentations += 1
        res = finish_stage(cfg, self._s1, self.dapi, self.vrc, self.neuron)
        n = len(res.table)
        n_inf = int(res.table.infected.sum()) if n else 0
        counts = {
            "nuclei": n,
            "vrc_pos": n_inf,
            "pct_vrc_pos": round(100 * n_inf / n, 1) if n else 0.0,
            "excluded": {k: v for k, v in res.n_excluded.items() if v},
            "fragment_merges": len(res.merges),
        }
        return Preview(res, self._s1.comb, again, counts)


def split_labels(res: Result) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(VRC+ nuclei, VRC- nuclei, excluded nuclei) as label images, for coloured preview layers."""
    lab = res.labels
    inf_ids = res.table.loc[res.table.infected, "label"].to_numpy() if len(res.table) else np.array([], int)
    pos = np.where(np.isin(lab, inf_ids), lab, 0)
    neg = np.where((lab > 0) & ~np.isin(lab, inf_ids), lab, 0)
    exc = res.excluded_labels if res.excluded_labels is not None else np.zeros_like(lab)
    return pos, neg, exc
