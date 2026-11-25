"""Final per-nucleus filters (focus, shape, size, border) and the kept/excluded split."""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd
from skimage.segmentation import relabel_sequential

from recap.config import FilterConfig

log = logging.getLogger(__name__)


EXCLUSION_REASONS = ("border", "too_small", "too_large", "irregular", "out_of_focus")


def border_excluded(table: pd.DataFrame, f: FilterConfig) -> pd.Series:
    """True for border nuclei to drop. Without border_max_missing every border nucleus is dropped;
    with it, a border nucleus is kept when all three hold:
      missingness: estimated clipped fraction <= border_max_missing
      shape      : ellipse fit of the uncut outline within border_max_fit_error
      size       : predicted full area within border_area_percentiles of non-border nuclei"""
    touching = table.touches_border.astype(bool)
    if f.border_max_missing is None or not touching.any():
        return touching
    inner = table.area[~touching]
    lo, hi = np.percentile(inner, f.border_area_percentiles) if len(inner) >= 10 else (0, np.inf)
    fit_ok = table.clip_fit_error.fillna(np.inf) <= f.border_max_fit_error
    keep = (table.clip_missing <= f.border_max_missing) & fit_ok & table.clip_pred_area.between(lo, hi)
    return touching & ~keep


def exclusion_reasons(table: pd.DataFrame, f: FilterConfig) -> pd.Series:
    """Semicolon-joined reasons per nucleus; empty string = kept."""
    if table.empty:
        return pd.Series([], dtype=str)
    checks = {
        "border": border_excluded(table, f) if f.exclude_border else None,
        "too_small": table.area < f.min_area if f.min_area is not None else None,
        "too_large": table.area > f.max_area if f.max_area is not None else None,
        "irregular": table.circularity < f.min_circularity if f.min_circularity is not None else None,
        "out_of_focus": table.focus_score < f.min_focus if f.min_focus is not None else None,
    }
    parts = [np.where(v, k, "") for k, v in checks.items() if v is not None]
    if not parts:
        return pd.Series([""] * len(table), index=table.index)
    joined = [";".join(x for x in row if x) for row in zip(*parts)]
    return pd.Series(joined, index=table.index)


def apply_filters(labels, table: pd.DataFrame, f: FilterConfig):
    """Split into kept and excluded nuclei. Kept labels are relabelled 1..n to match the kept table."""
    reasons = exclusion_reasons(table, f)
    table = table.assign(exclusion=reasons.values)
    keep = table.exclusion == ""
    kept_ids = table.label[keep].to_numpy()
    kept_lab = np.where(np.isin(labels, kept_ids), labels, 0)
    excl_lab = np.where(np.isin(labels, table.label[~keep].to_numpy()), labels, 0)
    kept_lab, fwd, _ = relabel_sequential(kept_lab)
    kept = table[keep].drop(columns="exclusion").copy()
    kept["label"] = [int(fwd[l]) for l in kept.label]
    counts = {k: int(reasons.str.contains(k).sum()) for k in EXCLUSION_REASONS}
    return kept_lab, kept.reset_index(drop=True), excl_lab, table[~keep].reset_index(drop=True), counts
