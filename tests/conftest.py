"""Shared fixtures: a synthetic two-channel image with known content.

Image (DAPI, VRC), 200 x 300 px:
  - one isolated uninfected nucleus (top left)
  - two neighbouring uninfected nuclei separated by a 2 px gap (bottom left)
  - one infected nucleus: DAPI as three peripheral crescents (marginalised chromatin)
    around a solid VRC core (right)
Expected: 4 nuclei, 1 VRC+.
"""

import sys
from pathlib import Path

import numpy as np
import pytest
import tifffile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))  # lets the tests run from a checkout even if recap is not installed


def make_synthetic(seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    Y, X = 200, 300
    yy, xx = np.mgrid[:Y, :X]
    dapi = np.zeros((Y, X))
    vrc = np.zeros((Y, X))

    def disk(cy, cx, r):
        return (yy - cy) ** 2 + (xx - cx) ** 2 < r**2

    dapi[disk(50, 50, 20)] = 1000
    dapi[disk(150, 60, 20)] = 1000
    dapi[disk(150, 102, 20)] = 1000
    ang = np.arctan2(yy - 100, xx - 200)
    rr = np.hypot(yy - 100, xx - 200)
    ring = (rr > 17) & (rr < 24)
    for a0 in (-3.0, -0.9, 1.2):
        dapi[ring & (np.abs(np.angle(np.exp(1j * (ang - a0)))) < 0.8)] = 1200
    vrc[rr < 17] = 2500
    dapi += rng.normal(100, 20, (Y, X))
    vrc += rng.normal(150, 20, (Y, X))
    return np.stack([dapi, vrc]).astype(np.float32)


@pytest.fixture(autouse=True)
def _own_user_settings(tmp_path, monkeypatch):
    """Every test gets its own recap user settings (Fiji, counter name, recent workspaces)."""
    monkeypatch.setenv("RECAP_CONFIG_DIR", str(tmp_path / "user-settings"))


@pytest.fixture
def synth_dir(tmp_path):
    """Folder with six synthetic images (different noise, so different file hashes)."""
    d = tmp_path / "data"
    d.mkdir()
    for i in range(6):
        tifffile.imwrite(d / f"img{i + 1}.tif", make_synthetic(seed=i))
    return tmp_path


@pytest.fixture
def write_config(synth_dir):
    def _write(text: str, name: str = "recap.yaml") -> Path:
        p = synth_dir / name
        p.write_text(text, encoding="utf-8")
        return p

    return _write


def pytest_collection_modifyitems(config, items):
    import os

    if os.environ.get("RECAP_SLOW_TESTS") == "1":
        return
    skip = pytest.mark.skip(reason="slow; run with RECAP_SLOW_TESTS=1")
    for item in items:
        if "slow" in item.keywords:
            item.add_marker(skip)
