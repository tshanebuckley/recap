"""Channel combination and nuclear segmentation (classical, StarDist, Cellpose), merging and small-body removal."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

import numpy as np
from scipy import ndimage as ndi
from skimage import filters, measure, morphology, segmentation
from skimage.segmentation import expand_labels, relabel_sequential

from recap.config import FragmentConfig, NormalizationConfig, SegmentationConfig
from recap.geometry import circ_of, ellipse_residual, fit_ellipse, outline_mask
from recap.modelpath import model_path

log = logging.getLogger(__name__)


def normalize(img: np.ndarray, rng: tuple[float, float]) -> np.ndarray:
    a, b = rng
    return np.clip((img - a) / (b - a + 1e-8), 0, 1).astype(np.float32)


def vrc_range_for(vrc: np.ndarray, norm: NormalizationConfig) -> tuple[float, float]:
    if norm.vrc_range is not None:
        return norm.vrc_range
    return tuple(float(v) for v in np.percentile(vrc, (1, 99.8)))


def combine(dapi, vrc, norm: NormalizationConfig):
    dapi_n = normalize(dapi, tuple(np.percentile(dapi, norm.dapi_percentiles)))
    vrc_n = normalize(vrc, vrc_range_for(vrc, norm))
    return dapi_n, vrc_n, np.maximum(dapi_n, norm.vrc_weight * vrc_n)


_THRESHOLDS: dict[str, Callable] = {
    "otsu": filters.threshold_otsu,
    "li": filters.threshold_li,
    "triangle": filters.threshold_triangle,
}


def drop_small(arr: np.ndarray, min_area: int) -> np.ndarray:
    lab = measure.label(arr) if arr.dtype == bool else arr
    keep = np.bincount(lab.ravel()) >= min_area
    keep[0] = False
    out = np.where(keep[lab], lab, 0)
    return out > 0 if arr.dtype == bool else out


def make_mask(comb, seg: SegmentationConfig):
    sm = filters.gaussian(comb, seg.sigma) if seg.sigma > 0 else comb
    mask = sm > _THRESHOLDS[seg.threshold_method](sm)
    if seg.close_radius:
        mask = ndi.binary_closing(mask, morphology.disk(seg.close_radius), border_value=0)
    mask = ndi.binary_fill_holes(mask)
    return sm, drop_small(mask, seg.min_area)


def watershed_split(mask, seg: SegmentationConfig) -> np.ndarray:
    dist = filters.gaussian(ndi.distance_transform_edt(mask), 2)
    seeds = measure.label(morphology.h_maxima(dist, seg.h))
    return segmentation.watershed(-dist, seeds, mask=mask)


_MODELS: dict[str, Any] = {}


def cellpose_model(path: str | None):
    found = model_path(path)
    path = str(found) if found else path
    key = f"cellpose:{path}"
    if key not in _MODELS:
        from cellpose import models

        _MODELS[key] = (
            models.CellposeModel(gpu=False, pretrained_model=path) if path else models.CellposeModel(gpu=False)
        )
    return _MODELS[key]


def cellpose_labels(comb, seg: SegmentationConfig) -> np.ndarray:
    return cellpose_model(seg.cellpose_model).eval(comb, diameter=seg.diameter)[0].astype(np.int32)


def stardist_model(name: str):
    found = model_path(name)
    key = str(found) if found else name  # (by absolute path: two workspaces' v1 are different models)
    if key not in _MODELS:
        from stardist.models import StarDist2D

        if found is None and ("/" in name or "\\" in name):
            raise ValueError(f"StarDist model folder not found: {name} (relative to the workspace folder)")
        _MODELS[key] = (
            StarDist2D(None, name=found.name, basedir=str(found.parent)) if found else StarDist2D.from_pretrained(name)
        )
        if _MODELS[key] is None:
            raise ValueError(f"unknown StarDist model '{name}'")
    return _MODELS[key]


def stardist_labels(comb, seg: SegmentationConfig) -> np.ndarray:
    from csbdeep.utils import normalize as csb_normalize

    model = stardist_model(seg.stardist_model)
    lab, _ = model.predict_instances(
        csb_normalize(comb, 1, 99.8), scale=seg.stardist_scale, prob_thresh=seg.prob_thresh, nms_thresh=seg.nms_thresh
    )
    return lab.astype(np.int32)


def model_labels(comb, seg: SegmentationConfig) -> np.ndarray:
    return stardist_labels(comb, seg) if seg.method == "stardist" else cellpose_labels(comb, seg)


def split_small(labels: np.ndarray, min_area: int) -> tuple[np.ndarray, int]:
    """Remove objects below min_area; return relabelled nuclei and the number removed."""
    counts = np.bincount(labels.ravel())
    small = np.flatnonzero((counts < min_area) & (counts > 0))
    small = small[small > 0]
    out = labels.copy()
    out[np.isin(out, small)] = 0
    return relabel_sequential(out)[0], int(len(small))


def merge_weak_boundaries(labels, img, ratio: float) -> np.ndarray:
    """Merge touching regions whose shared border is about as bright as the dimmer region."""
    border: dict[tuple[int, int], list[float]] = {}
    shifts = [
        ((slice(None, -1), slice(None)), (slice(1, None), slice(None))),
        ((slice(None), slice(None, -1)), (slice(None), slice(1, None))),
    ]
    for sa, sb in shifts:
        la, lb = labels[sa], labels[sb]
        m = (la != lb) & (la > 0) & (lb > 0)
        for p, q, v in zip(la[m], lb[m], (img[sa][m] + img[sb][m]) / 2):
            border.setdefault((min(p, q), max(p, q)), []).append(v)

    means = {r.label: r.intensity_mean for r in measure.regionprops(labels, img)}
    parent = {lab: lab for lab in means}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for (p, q), vals in border.items():
        if np.median(vals) >= ratio * min(means[p], means[q]):
            parent[find(p)] = find(q)
    lut = np.zeros(labels.max() + 1, dtype=np.int32)
    for lab in means:
        lut[lab] = find(lab)
    return relabel_sequential(lut[labels])[0]


def fill_each(labels) -> np.ndarray:
    out = np.zeros_like(labels)
    for r in measure.regionprops(labels):
        filled = ndi.binary_fill_holes(r.image)
        out[r.slice][filled & (out[r.slice] == 0)] = r.label
    return out


def _adjacent_pairs(labels: np.ndarray, gap: int) -> dict[int, set[int]]:
    ex = expand_labels(labels, gap) if gap else labels
    adj: dict[int, set[int]] = {}
    for sa, sb in [
        ((slice(None, -1), slice(None)), (slice(1, None), slice(None))),
        ((slice(None), slice(None, -1)), (slice(None), slice(1, None))),
    ]:
        a, b = ex[sa], ex[sb]
        m = (a != b) & (a > 0) & (b > 0)
        for p_, q_ in set(zip(a[m].tolist(), b[m].tolist())):
            adj.setdefault(p_, set()).add(q_)
            adj.setdefault(q_, set()).add(p_)
    return adj


def _merge_candidate(labels, c, n, bbox_c, bbox_n, vrc_n, fc: FragmentConfig, gap: int):
    """Geometry of the merged object c+n (bridging gaps up to `gap` px), without touching other labels."""
    H, W = labels.shape
    pad = gap + 2
    y0, x0 = max(min(bbox_c[0], bbox_n[0]) - pad, 0), max(min(bbox_c[1], bbox_n[1]) - pad, 0)
    y1, x1 = min(max(bbox_c[2], bbox_n[2]) + pad, H), min(max(bbox_c[3], bbox_n[3]) + pad, W)
    win = labels[y0:y1, x0:x1]
    mc, mn = win == c, win == n
    m = mc | mn
    closed = ndi.binary_closing(np.pad(m, gap + 1), morphology.disk(max(gap, 1)))[
        gap + 1 : -(gap + 1), gap + 1 : -(gap + 1)
    ]
    closed = ndi.binary_fill_holes(closed | m) & ((win == 0) | m)
    lab_, nlab = ndi.label(closed)
    if nlab != 1:
        return None
    r = measure.regionprops(closed.astype(np.uint8))[0]
    ys, xs = np.nonzero(outline_mask(np.pad(closed, 1)))
    e = fit_ellipse(xs.astype(float), ys.astype(float))
    bridge = (ndi.binary_dilation(mc, iterations=2) & ndi.binary_dilation(mn, iterations=2)) | (closed & ~m)
    bridge &= closed
    vb = float(np.median(vrc_n[y0:y1, x0:x1][bridge])) if bridge.any() else 0.0
    return dict(
        window=(y0, y1, x0, x1),
        mask=closed,
        area=float(r.area),
        solidity=float(r.solidity),
        circularity=circ_of(r.area, r.perimeter),
        fit_error=ellipse_residual(xs.astype(float), ys.astype(float), e) if e is not None else np.inf,
        vrc_bridge=vb,
    )


def fragment_merge(
    labels: np.ndarray, vrc_n: np.ndarray, fc: FragmentConfig, mode: str, vrc_threshold: float, min_area: int
) -> tuple[np.ndarray, list[dict]]:
    """Rejoin pieces of split nuclei. A candidate (small or irregular object) is merged with the
    adjacent object that gives the most convex result, if the merged object
      shape: solidity >= min_solidity, not less convex than the less convex piece, ellipse fit ok
      size : area within area_percentiles of non-candidate objects
      vrc  : (mode shape_vrc) median normalised ICP8 across the junction >= vrc_threshold
    Repeats up to max_iterations (a nucleus split in three needs two rounds)."""
    if mode == "off" or labels.max() == 0:
        return labels, []
    labels = labels.copy()
    records: list[dict] = []
    ref = None
    for it in range(1, fc.max_iterations + 1):
        props = {r.label: r for r in measure.regionprops(labels)}
        info = {
            l: dict(area=r.area, solidity=r.solidity, circ=circ_of(r.area, r.perimeter), bbox=r.bbox)
            for l, r in props.items()
        }
        areas = np.array([v["area"] for v in info.values()])
        med = np.median(areas[areas >= min_area]) if (areas >= min_area).any() else np.median(areas)
        cand = {
            l
            for l, v in info.items()
            if v["area"] < fc.candidate_area_ratio * med or v["circ"] < fc.candidate_circularity
        }
        if ref is None:  # size reference fixed from the first pass
            non = np.array([v["area"] for l, v in info.items() if l not in cand])
            ref = tuple(np.percentile(non, fc.area_percentiles)) if len(non) >= 10 else (0, np.inf)
        adj = _adjacent_pairs(labels, fc.gap_px)
        proposals = []
        for c in sorted(cand, key=lambda l: info[l]["area"]):
            best = None
            for n in adj.get(c, ()):
                g = _merge_candidate(labels, c, n, info[c]["bbox"], info[n]["bbox"], vrc_n, fc, fc.gap_px)
                if g is None:
                    continue
                ok = (
                    g["solidity"] >= fc.min_solidity
                    and g["solidity"] >= min(info[c]["solidity"], info[n]["solidity"])
                    and g["fit_error"] <= fc.max_fit_error
                    and ref[0] <= g["area"] <= ref[1]
                    and (mode != "shape_vrc" or g["vrc_bridge"] >= vrc_threshold)
                )
                if ok and (best is None or g["solidity"] > best[2]["solidity"]):
                    best = (c, n, g)
            if best:
                proposals.append(best)
        used: set[int] = set()
        n_applied = 0
        for c, n, g in sorted(proposals, key=lambda t: -t[2]["solidity"]):
            if c in used or n in used:
                continue
            keep = n if info[n]["area"] >= info[c]["area"] else c
            y0, y1, x0, x1 = g["window"]
            labels[y0:y1, x0:x1][g["mask"]] = keep
            used |= {c, n}
            n_applied += 1
            records.append(
                dict(
                    iteration=it,
                    piece=int(c),
                    into=int(n),
                    piece_area=int(info[c]["area"]),
                    into_area=int(info[n]["area"]),
                    merged_area=int(g["area"]),
                    merged_solidity=round(g["solidity"], 3),
                    merged_fit_error=round(g["fit_error"], 3),
                    vrc_bridge=round(g["vrc_bridge"], 3),
                    y=float(props[keep].centroid[0]),
                    x=float(props[keep].centroid[1]),
                )
            )
        if n_applied == 0:
            break
    return relabel_sequential(labels)[0], records


def finish_labels(
    raw: np.ndarray,
    sm: np.ndarray,
    seg: SegmentationConfig,
    vrc_n: np.ndarray | None = None,
    fc: FragmentConfig | None = None,
    vrc_threshold: float = 0.3,
) -> tuple[np.ndarray, int, list[dict]]:
    labels = merge_weak_boundaries(raw, sm, seg.merge_ratio) if seg.merge else raw
    labels = fill_each(labels)
    merges: list[dict] = []
    if seg.fragment_merge != "off" and vrc_n is not None:
        labels, merges = fragment_merge(
            labels, vrc_n, fc or FragmentConfig(), seg.fragment_merge, vrc_threshold, seg.min_area
        )
    labels, n_small = split_small(labels, seg.min_area)
    return labels, n_small, merges
