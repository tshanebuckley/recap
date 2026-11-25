"""Per-nucleus measurements: intensities, infection call, chromatin fragments, focus and clipping features."""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd
from scipy import ndimage as ndi
from skimage import filters, measure
from skimage.segmentation import expand_labels

from recap.config import MeasurementConfig
from recap.geometry import clipping_estimate, ellipse_area_scale
from recap.segment import drop_small

log = logging.getLogger(__name__)


def measure_nuclei(labels, dapi, vrc, vrc_n, meas: MeasurementConfig, neuron=None) -> pd.DataFrame:
    ring_means: dict[int, float] = {}
    if neuron is not None:
        ring = expand_labels(labels, meas.neuron_ring_px)
        ring[labels > 0] = 0
        ring_means = {r.label: r.intensity_mean for r in measure.regionprops(ring, neuron)}

    rows = []
    for r in measure.regionprops(labels):
        sl, m = r.slice, r.image
        d, v = dapi[sl][m], vrc[sl][m]
        vrc_frac = float((vrc_n[sl][m] > meas.vrc_threshold).mean())
        dloc = filters.gaussian(dapi[sl], 1)
        frag = drop_small(measure.label((dloc > filters.threshold_otsu(dloc[m])) & m), meas.min_fragment_area)
        row = dict(
            label=r.label,
            y=r.centroid[0],
            x=r.centroid[1],
            area=r.area,
            solidity=r.solidity,
            dapi_integrated=float(d.sum()),
            dapi_mean=float(d.mean()),
            vrc_integrated=float(v.sum()),
            vrc_area_fraction=vrc_frac,
            n_chromatin_fragments=len(np.unique(frag)) - 1,
            infected=vrc_frac >= meas.infected_fraction,
        )
        if neuron is not None:
            row["neuron_ring_mean"] = float(ring_means.get(r.label, np.nan))
        rows.append(row)
    return pd.DataFrame(rows)


def object_features(labels: np.ndarray, comb: np.ndarray, excluded: np.ndarray, border_px: int) -> pd.DataFrame:
    """Shape and focus features per nucleus.

    focus_edge  : mean intensity gradient along the 2-px boundary band (dim or blurred -> low)
    focus_detail: std of band-passed signal (~1-2 um scale) inside the nucleus (resolved texture,
                  e.g. ICP8 speckles or chromatin; blurred -> low)
    focus_score : average of the two, each divided by its median over the image's nuclei
    Computed on the combined channel, so infected nuclei with displaced (dim) DAPI are not penalised.
    """
    if labels.max() == 0:
        return pd.DataFrame(
            columns=["label", "circularity", "focus_edge", "focus_detail", "focus_score", "touches_border"]
        )
    grad = filters.sobel(filters.gaussian(comb, 1))
    detail_img = filters.gaussian(comb, 0.7) - filters.gaussian(comb, 2.5)
    edge = np.zeros(labels.shape, bool)
    if border_px:
        edge[:border_px, :] = edge[-border_px:, :] = edge[:, :border_px] = edge[:, -border_px:] = True
    edge |= ndi.binary_dilation(excluded, iterations=max(border_px, 1)) if excluded.any() else excluded
    touching = set(np.unique(labels[edge])) - {0}
    rows = []
    for r in measure.regionprops(labels):
        m = r.image
        band = m & ~ndi.binary_erosion(m, iterations=2)
        inner = ndi.binary_erosion(m, iterations=3)
        inner = inner if inner.sum() > 10 else m
        rows.append(
            dict(
                label=r.label,
                circularity=min(1.0, 4 * np.pi * r.area / max(r.perimeter, 1) ** 2),
                focus_edge=float(grad[r.slice][band].mean()) if band.any() else 0.0,
                focus_detail=float(detail_img[r.slice][inner].std()),
                touches_border=r.label in touching,
            )
        )
    df = pd.DataFrame(rows)
    me, md = df.focus_edge.median(), df.focus_detail.median()
    df["focus_score"] = 0.5 * df.focus_edge / (me or 1) + 0.5 * df.focus_detail / (md or 1)

    clip_cols = [
        "clip_missing",
        "clip_missing_ellipse",
        "clip_missing_chord",
        "clip_fit_error",
        "clip_pred_area",
        "clip_chord_px",
    ]
    for col in clip_cols:
        df[col] = np.nan
    if touching:
        H, W = labels.shape
        scale = ellipse_area_scale(labels, set(df.label[~df.touches_border]))
        props = {r.label: r for r in measure.regionprops(labels)}
        pad = 4
        for i in df.index[df.touches_border]:
            r = props[df.at[i, "label"]]
            y0, x0, y1, x1 = r.bbox
            yy, xx = np.mgrid[y0 - pad : y1 + pad, x0 - pad : x1 + pad]
            inside = (yy >= 0) & (yy < H) & (xx >= 0) & (xx < W)
            m = np.zeros(yy.shape, bool)
            b = ~inside  # outside the image counts as cut
            m[inside] = labels[yy[inside], xx[inside]] == r.label
            b[inside] |= excluded[yy[inside], xx[inside]]
            for k, v in clipping_estimate(m, b, scale).items():
                df.at[i, k] = v
    return df


def infected_label_image(labels, df: pd.DataFrame) -> np.ndarray:
    if df.empty:
        return np.zeros_like(labels)
    return np.where(np.isin(labels, df.loc[df.infected, "label"]), labels, 0)


def pct(part: float, whole: float) -> float | None:
    return round(100 * part / whole, 1) if whole else None
