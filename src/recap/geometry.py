"""Shape geometry: direct ellipse fit, clipped-nucleus estimates, circularity."""

from __future__ import annotations

import logging

import numpy as np
from scipy import ndimage as ndi
from skimage import measure

log = logging.getLogger(__name__)


# ---- clipping estimate for nuclei cut by the image edge or an excluded region -------------------
def fit_ellipse(x: np.ndarray, y: np.ndarray):
    """Direct least-squares ellipse fit (Fitzgibbon, Pilu & Fisher 1999; Halir & Flusser 1998).
    Works on partial arcs. Returns (cx, cy, a, b, theta) with a >= b, or None."""
    if len(x) < 6:
        return None
    mx, my = x.mean(), y.mean()
    sx, sy = max(x.std(), 1e-6), max(y.std(), 1e-6)
    xn, yn = (x - mx) / sx, (y - my) / sy
    D1 = np.c_[xn * xn, xn * yn, yn * yn]
    D2 = np.c_[xn, yn, np.ones_like(xn)]
    S1, S2, S3 = D1.T @ D1, D1.T @ D2, D2.T @ D2
    try:
        T = -np.linalg.solve(S3, S2.T)
    except np.linalg.LinAlgError:
        return None
    M = S1 + S2 @ T
    M = np.array([M[2] / 2, -M[1], M[0] / 2])
    _, v = np.linalg.eig(M)
    v = np.real(v)
    idx = np.flatnonzero(4 * v[0] * v[2] - v[1] ** 2 > 0)
    if len(idx) == 0:
        return None
    A, B, C = v[:, idx[0]]
    Dd, E, F = T @ v[:, idx[0]]
    A, B, C, Dd, E, F = (
        A / sx**2,
        B / (sx * sy),
        C / sy**2,
        -2 * A * mx / sx**2 - B * my / (sx * sy) + Dd / sx,
        -2 * C * my / sy**2 - B * mx / (sx * sy) + E / sy,
        A * mx**2 / sx**2 + B * mx * my / (sx * sy) + C * my**2 / sy**2 - Dd * mx / sx - E * my / sy + F,
    )
    den = B * B - 4 * A * C
    if den >= 0:
        return None
    cx, cy = (2 * C * Dd - B * E) / den, (2 * A * E - B * Dd) / den
    num = 2 * (A * E * E + C * Dd * Dd - B * Dd * E + den * F)
    root = np.sqrt((A - C) ** 2 + B * B)
    q1, q2 = num * (A + C + root), num * (A + C - root)
    if q1 <= 0 or q2 <= 0:
        return None
    a, b = np.sqrt(q1) / -den, np.sqrt(q2) / -den
    theta = 0.5 * np.arctan2(-B, C - A)
    return cx, cy, max(a, b), min(a, b), theta if a >= b else theta + np.pi / 2


def ellipse_residual(x, y, e) -> float:
    cx, cy, a, b, t = e
    xr = (x - cx) * np.cos(t) + (y - cy) * np.sin(t)
    yr = -(x - cx) * np.sin(t) + (y - cy) * np.cos(t)
    return float(np.median(np.abs(np.hypot(xr / a, yr / b) - 1)))


def outline_mask(mask: np.ndarray) -> np.ndarray:
    return mask & ~ndi.binary_erosion(mask, border_value=0)


def chord_missing(chord: float, visible_area: float) -> float:
    """Missing fraction of a disc cut by a straight chord of the given length, with the given
    visible area (solves A = R^2 (pi - phi + sin phi cos phi), c = 2 R sin phi). 0.5 when the
    visible part is no larger than a half disc on that chord."""
    if chord <= 0 or visible_area <= 0:
        return 0.0
    if visible_area <= np.pi * chord**2 / 8:
        return 0.5
    lo, hi = 1e-6, np.pi / 2
    for _ in range(60):
        phi = (lo + hi) / 2
        area = (chord / (2 * np.sin(phi))) ** 2 * (np.pi - phi + np.sin(phi) * np.cos(phi))
        lo, hi = (phi, hi) if area > visible_area else (lo, phi)
    phi = (lo + hi) / 2
    return float((phi - np.sin(phi) * np.cos(phi)) / np.pi)


def clipping_estimate(mask: np.ndarray, border: np.ndarray, area_scale: float) -> dict:
    """Estimate how much of a nucleus is cut off. mask: the nucleus; border: pixels outside the
    image or in an excluded region (same crop, padded so the cut is visible).
      ellipse: fit an ellipse to the free (uncut) outline -> predicted full area
      chord  : length of the cut edge + visible area, solved as a cut disc
    clip_missing is the mean of the two (validated by simulated cuts of intact nuclei)."""
    out = dict(
        clip_missing=1.0,
        clip_missing_ellipse=1.0,
        clip_missing_chord=np.nan,
        clip_fit_error=np.nan,
        clip_pred_area=np.nan,
        clip_chord_px=0.0,
    )
    edge = outline_mask(mask)
    near = ndi.binary_dilation(border, iterations=2)
    ys, xs = np.nonzero(edge)
    on = near[ys, xs]
    if on.sum() >= 2:  # cut length: span along the cut direction
        pts = np.c_[xs[on], ys[on]].astype(float)
        pts -= pts.mean(0)
        proj = pts @ np.linalg.svd(pts, full_matrices=False)[2][0]
        out["clip_chord_px"] = float(proj.max() - proj.min() + 1)
    area = float(mask.sum())
    out["clip_missing_chord"] = chord_missing(out["clip_chord_px"], area)
    free = ~on
    e = fit_ellipse(xs[free].astype(float), ys[free].astype(float)) if free.sum() >= 8 else None
    if e is not None:
        full = np.pi * e[2] * e[3] * area_scale
        out.update(
            clip_missing_ellipse=float(np.clip(1 - area / full, 0, 1)),
            clip_fit_error=ellipse_residual(xs[free].astype(float), ys[free].astype(float), e),
        )
    out["clip_missing"] = float(np.mean([out["clip_missing_ellipse"], out["clip_missing_chord"]]))
    out["clip_pred_area"] = area / max(1 - out["clip_missing"], 1e-3)
    return out


def ellipse_area_scale(labels: np.ndarray, label_ids, n: int = 200) -> float:
    """Ratio of true area to the area of an ellipse fitted to the pixel outline, from intact
    nuclei of the same image (corrects the ~half-pixel inset of outline pixels)."""
    ratios = []
    for r in measure.regionprops(labels):
        if r.label not in label_ids:
            continue
        m = np.pad(r.image, 2)
        ys, xs = np.nonzero(outline_mask(m))
        e = fit_ellipse(xs.astype(float), ys.astype(float))
        if e is not None:
            ratios.append(m.sum() / (np.pi * e[2] * e[3]))
        if len(ratios) >= n:
            break
    return float(np.median(ratios)) if ratios else 1.06


def circ_of(area: float, perim: float) -> float:
    return min(1.0, 4 * np.pi * area / max(perim, 1) ** 2)
