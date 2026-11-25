"""Scoring a segmentation against hand-edited masks (the workspace's reference masks).

Nuclei are matched one-to-one by overlap (IoU >= iou, as in `recap compare` and `recap report`).
precision = share of program nuclei that are real; recall = share of real nuclei found;
f1 = their harmonic mean; count_error = mean |program - reference| / reference over images.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import tifffile

from recap.compare import match
from recap.config import PipelineConfig
from recap.io import load_channels
from recap.pipeline import Stage1, finish_stage, segment_stage


@dataclass
class Score:
    tp: int = 0
    fp: int = 0
    fn: int = 0
    count_errors: list[float] = field(default_factory=list)
    ious: list[float] = field(default_factory=list)

    def add(self, pred: np.ndarray, ref: np.ndarray, iou: float = 0.5) -> None:
        m = match(pred, ref, iou)
        n_pred, n_ref = len(np.unique(pred[pred > 0])), len(np.unique(ref[ref > 0]))
        self.tp += len(m)
        self.fp += n_pred - len(m)
        self.fn += n_ref - len(m)
        self.count_errors.append(abs(n_pred - n_ref) / max(n_ref, 1))
        self.ious += [q for *_, q in m]

    def as_dict(self) -> dict[str, float]:
        p = self.tp / max(self.tp + self.fp, 1)
        r = self.tp / max(self.tp + self.fn, 1)
        return {
            "f1": round(2 * p * r / max(p + r, 1e-9), 4),
            "precision": round(p, 4),
            "recall": round(r, 4),
            "count_error": round(float(np.mean(self.count_errors)) if self.count_errors else 0.0, 4),
            "mean_iou": round(float(np.mean(self.ious)) if self.ious else 0.0, 4),
            "matched": self.tp,
            "missed": self.fn,
            "extra": self.fp,
        }


@dataclass
class Reference:
    """One image with its hand-edited mask."""

    path: Path
    sha256: str
    mask: Path

    def load(self, cfg: PipelineConfig):
        img = load_channels(self.path, cfg.input.exclude_regions)
        n = cfg.input.neuron_channel
        return (img[cfg.input.dapi_channel], img[cfg.input.vrc_channel], img[n] if n is not None else None,
                tifffile.imread(self.mask))  # fmt: skip


def score_config(
    cfg: PipelineConfig,
    refs: list[Reference],
    iou: float = 0.5,
    cache: dict | None = None,
    stage_key: str = "",
    ws=None,
) -> dict[str, float]:
    """Run the pipeline with these settings on each reference image and score the kept nuclei.
    With a cache, the slow first stage is reused for identical model-stage settings (stage_key).
    With a workspace, each image's batch and image settings are applied on top (levels.py)."""
    score = Score()
    base = cfg
    for ref in refs:
        cfg = ws.resolve(ref.sha256, base).cfg if ws is not None else base
        dapi, vrc, neuron, truth = ref.load(cfg)
        key = (ref.sha256, stage_key)
        s1: Stage1 | None = cache.get(key) if cache is not None else None
        if s1 is None:
            s1 = segment_stage(cfg, dapi, vrc)
            if cache is not None:
                cache[key] = s1
        res = finish_stage(cfg, s1, dapi, vrc, neuron)
        score.add(res.labels, truth, iou)
    return score.as_dict()
