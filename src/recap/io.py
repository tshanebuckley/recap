"""Reading images (TIFF, hyperstacks, RGB exports), blanking excluded regions, file hashes."""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

import numpy as np
import tifffile

log = logging.getLogger(__name__)


# =============================================================================
# Image processing
# =============================================================================
def load_channels(path: Path, exclude: list[tuple[int, int, int, int]] | None = None) -> np.ndarray:
    img = _read_channels(path)
    for y0, y1, x0, x1 in exclude or []:
        img[:, y0:y1, x0:x1] = 0
    return img


def _read_channels(path: Path) -> np.ndarray:
    img = tifffile.imread(path)
    if img.ndim == 4:  # Fiji hyperstack Z, C, Y, X
        img = img.max(axis=0)
    if img.ndim == 3 and img.shape[-1] <= 6 and img.shape[0] > 6:
        img = np.moveaxis(img, -1, 0)  # Y, X, C -> C, Y, X
    if img.ndim != 3:
        raise ValueError(f"{path}: expected a multichannel 2D image, got shape {img.shape}")
    return img.astype(np.float32)


def exclusion_mask(shape: tuple[int, int], regions: list[tuple[int, int, int, int]]) -> np.ndarray:
    m = np.zeros(shape, bool)
    for y0, y1, x0, x1 in regions:
        m[y0:y1, x0:x1] = True
    return m


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()
