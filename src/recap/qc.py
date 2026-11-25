"""Per-image quality warnings and count ranges for the batch summary.

Flags (column qc_flags, semicolon-separated; empty = no warning):
  saturated_dapi / saturated_vrc   more than qc.saturation_max_fraction of pixels at the channel maximum
  few_nuclei                       fewer than qc.min_nuclei counted
  high_exclusion                   filters dropped more than qc.max_excluded_fraction of nuclei
  many_out_of_focus                more than qc.max_out_of_focus_fraction excluded as out of focus
  high_vrc_background              median normalised ICP8 outside nuclei above qc.vrc_background_max
  vrc_range_not_fixed              normalization.vrc_range is null (per-image scaling)
  blurry_vs_batch                  median edge sharpness < qc.blurry_vs_batch x the batch median (>= 3 images)

Count ranges (n_nuclei_low/high, n_infected_low/high): each active cutoff (min_focus,
min_circularity, border_max_missing, infected_fraction) is moved down and up by the qc deltas,
one at a time, and the lowest and highest resulting counts are reported. Segmentation is not
repeated: the per-nucleus features of kept and excluded nuclei are re-filtered.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import ndimage as ndi

from recap.config import PipelineConfig
from recap.filters import exclusion_reasons

CEILINGS = (255, 4095, 65535)


def _saturated_fraction(ch: np.ndarray) -> float:
    top = float(np.max(ch)) if ch.size else 0.0
    return float(np.mean(ch >= top)) if top in CEILINGS else 0.0


def all_nuclei(table: pd.DataFrame, excluded: pd.DataFrame | None) -> pd.DataFrame:
    """Kept and excluded nuclei in one table (features only; the exclusion column is dropped)."""
    parts = [table] + ([excluded.drop(columns=["exclusion"], errors="ignore")] if excluded is not None else [])
    return (
        pd.concat([p for p in parts if p is not None and len(p)], ignore_index=True)
        if any(p is not None and len(p) for p in parts)
        else table.iloc[0:0]
    )


def count_range(full: pd.DataFrame, cfg: PipelineConfig) -> dict[str, int]:
    f, m, q = cfg.filters, cfg.measurement, cfg.qc
    variants = [(f, m.infected_fraction)]
    if f.min_focus is not None:
        variants += [
            (f.model_copy(update={"min_focus": max(f.min_focus + d, 1e-6)}), m.infected_fraction)
            for d in (-q.focus_delta, q.focus_delta)
        ]
    if f.min_circularity is not None:
        variants += [
            (
                f.model_copy(update={"min_circularity": float(np.clip(f.min_circularity + d, 1e-6, 1))}),
                m.infected_fraction,
            )
            for d in (-q.circularity_delta, q.circularity_delta)
        ]
    if f.exclude_border and f.border_max_missing is not None:
        variants += [
            (
                f.model_copy(update={"border_max_missing": float(np.clip(f.border_max_missing + d, 0, 1))}),
                m.infected_fraction,
            )
            for d in (-q.border_missing_delta, q.border_missing_delta)
        ]
    variants += [
        (f, m.infected_fraction / q.infected_fraction_factor),
        (f, min(m.infected_fraction * q.infected_fraction_factor, 1.0)),
    ]
    n, inf = [], []
    for fv, frac in variants:
        keep = exclusion_reasons(full, fv) == "" if len(full) else pd.Series(dtype=bool)
        kept = full[keep.values] if len(full) else full
        n.append(len(kept))
        inf.append(int((kept.vrc_area_fraction >= frac).sum()) if len(kept) else 0)
    return dict(n_nuclei_low=min(n), n_nuclei_high=max(n), n_infected_low=min(inf), n_infected_high=max(inf))


def image_qc(cfg: PipelineConfig, res, img: np.ndarray) -> dict:
    """Flags and metrics for one processed image (res: pipeline Result, img: (C, Y, X) channels)."""
    q = cfg.qc
    flags: list[str] = []
    sat_d = _saturated_fraction(img[cfg.input.dapi_channel])
    sat_v = _saturated_fraction(img[cfg.input.vrc_channel])
    if sat_d > q.saturation_max_fraction:
        flags.append("saturated_dapi")
    if sat_v > q.saturation_max_fraction:
        flags.append("saturated_vrc")
    n_kept = len(res.table)
    n_excl = len(res.excluded) if res.excluded is not None else 0
    total = n_kept + n_excl
    if n_kept < q.min_nuclei:
        flags.append("few_nuclei")
    if total and n_excl / total > q.max_excluded_fraction:
        flags.append("high_exclusion")
    n_oof = res.n_excluded.get("out_of_focus", 0)
    if total and n_oof / total > q.max_out_of_focus_fraction:
        flags.append("many_out_of_focus")
    fg = (res.labels > 0) | (res.excluded_labels > 0 if res.excluded_labels is not None else False)
    bg = ~ndi.binary_dilation(fg, iterations=4)
    vrc_bg = float(np.median(res.vrc_n[bg])) if bg.any() else 0.0
    if vrc_bg > q.vrc_background_max:
        flags.append("high_vrc_background")
    if cfg.normalization.vrc_range is None:
        flags.append("vrc_range_not_fixed")
    full = all_nuclei(res.table, res.excluded)
    sharp = float(full.focus_edge.median()) if len(full) and "focus_edge" in full else float("nan")
    return dict(
        qc_flags=flags,
        saturated_dapi_fraction=round(sat_d, 5),
        saturated_vrc_fraction=round(sat_v, 5),
        vrc_background=round(vrc_bg, 4),
        median_edge_sharpness=round(sharp, 5),
        **count_range(full, cfg),
    )


def batch_qc(images: list[dict], cfg: PipelineConfig) -> None:
    """Add the batch-relative blurry flag (in place) once every image is processed."""
    done = [d for d in images if "median_edge_sharpness" in d and np.isfinite(d["median_edge_sharpness"])]
    if len(done) < 3:
        return
    med = float(np.median([d["median_edge_sharpness"] for d in done]))
    for d in done:
        if d["median_edge_sharpness"] < cfg.qc.blurry_vs_batch * med:
            d["qc_flags"].append("blurry_vs_batch")
