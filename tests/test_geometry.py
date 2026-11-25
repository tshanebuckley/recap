"""Tests that need neither Fiji nor StarDist. Run from the project folder:  pytest   (or: pixi run test)"""

import numpy as np

from helpers import BASE, run_cli, summary  # noqa: F401
from recap import config, geometry, segment


# ----------------------------------------------------------------- border clipping geometry
def test_fit_ellipse_partial_arc():
    t = np.linspace(0, 0.8 * np.pi, 60)
    x = 50 + 15 * np.cos(t) * np.cos(0.4) - 9 * np.sin(t) * np.sin(0.4)
    y = 40 + 15 * np.cos(t) * np.sin(0.4) + 9 * np.sin(t) * np.cos(0.4)
    cx, cy, a, b, _ = geometry.fit_ellipse(x, y)
    assert np.allclose([cx, cy, a, b], [50, 40, 15, 9], atol=1e-6)


def test_chord_missing_geometry():
    R = 20.0
    for d in (2.0, 6.0, 12.0):  # cut depth
        phi = np.arccos(1 - d / R)
        removed = R * R * (phi - np.sin(phi) * np.cos(phi))
        est = geometry.chord_missing(2 * R * np.sin(phi), np.pi * R * R - removed)
        assert abs(est - removed / (np.pi * R * R)) < 1e-4
    assert geometry.chord_missing(40, np.pi * 400 / 2) == 0.5  # half disc or less


def test_clipping_estimate_on_cut_disc():
    yy, xx = np.mgrid[:70, :70]
    disc = (yy - 35) ** 2 + (xx - 35) ** 2 < 20**2
    for cut_row, true_missing in ((50, None), (42, None)):
        border = yy >= cut_row
        visible = disc & ~border
        true_missing = 1 - visible.sum() / disc.sum()
        est = geometry.clipping_estimate(visible, border, area_scale=1.06)["clip_missing"]
        assert abs(est - true_missing) < 0.08, (cut_row, est, true_missing)


# ----------------------------------------------------------------- fragment merging
def test_fragment_merge_rejoins_split_but_not_neighbours():
    yy, xx = np.mgrid[:120, :200]
    lab = np.zeros((120, 200), np.int32)
    whole = (yy - 60) ** 2 + (xx - 50) ** 2 < 16**2  # same size as the reference nuclei
    lab[whole] = 1
    lab[whole & (xx > 58)] = 2  # nucleus 1 split: piece 2 is a cap
    lab[((yy - 60) ** 2 + (xx - 120) ** 2 < 15**2)] = 3  # two separate touching nuclei
    lab[((yy - 60) ** 2 + (xx - 149) ** 2 < 15**2)] = 4
    for k in range(5):  # reference nuclei for the size range
        lab[((yy - 20) ** 2 + (xx - 20 - 36 * k) ** 2 < 16**2)] = 10 + k
        lab[((yy - 100) ** 2 + (xx - 20 - 36 * k) ** 2 < 16**2)] = 20 + k
    vrc = np.zeros(lab.shape)
    out, rec = segment.fragment_merge(lab, vrc, config.FragmentConfig(), "shape", 0.3, min_area=50)
    assert len(np.unique(out[whole])) == 1  # split nucleus rebuilt
    a, b = out[60, 120], out[60, 149]
    assert a != b  # neighbours kept apart
    # with the ICP8 test and no ICP8 signal, nothing is merged
    out2, rec2 = segment.fragment_merge(lab, vrc, config.FragmentConfig(), "shape_vrc", 0.3, min_area=50)
    assert rec2 == []
