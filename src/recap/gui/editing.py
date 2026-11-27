"""What the Count and Fix outlines tabs do to the data, without Qt (tested on their own).

Counting: points (y, x, counter), counter 0 = VRC-, 1 = VRC+; remove the nearest; undo.
Outlines: a label image edited one nucleus at a time: draw a new one, delete, split with a line,
merge two; undo. The tabs turn mouse clicks and drags into these calls.
"""

from __future__ import annotations

import numpy as np
from scipy import ndimage as ndi
from skimage import draw, measure, morphology

UNDO_STEPS = 30


class CountEdit:
    """A manual count in progress."""

    def __init__(self, points: list[tuple[float, float, int]] | None = None):
        self.points: list[tuple[float, float, int]] = list(points or [])  # (y, x, counter)
        self._undo: list[list[tuple[float, float, int]]] = []

    def _push(self) -> None:
        self._undo = [*self._undo[-(UNDO_STEPS - 1) :], list(self.points)]

    def add(self, y: float, x: float, counter: int) -> None:
        self._push()
        self.points.append((float(y), float(x), int(counter)))

    def remove_near(self, y: float, x: float, radius: float) -> bool:
        """Remove the point closest to (y, x) if it is within radius pixels."""
        if not self.points:
            return False
        pts = np.array([(py, px) for py, px, _ in self.points])
        d = np.hypot(pts[:, 0] - y, pts[:, 1] - x)
        k = int(d.argmin())
        if d[k] > radius:
            return False
        self._push()
        del self.points[k]
        return True

    def undo(self) -> bool:
        if not self._undo:
            return False
        self.points = self._undo.pop()
        return True

    @property
    def totals(self) -> tuple[int, int]:
        """(VRC-, VRC+)"""
        pos = sum(1 for *_, c in self.points if c == 1)
        return len(self.points) - pos, pos

    def for_saving(self) -> list[tuple[float, float, int]]:
        """Points as recap stores them: (x, y, counter)."""
        return [(x, y, c) for y, x, c in self.points]


class OutlineEdit:
    """Corrections to a label image (one integer per nucleus, 0 = background)."""

    def __init__(self, labels: np.ndarray):
        self.labels = np.asarray(labels).astype(np.int32, copy=True)
        self._undo: list[np.ndarray] = []
        self.changed = False

    def _push(self) -> None:
        self._undo = [*self._undo[-(UNDO_STEPS - 1) :], self.labels.copy()]
        self.changed = True

    def undo(self) -> bool:
        if not self._undo:
            return False
        self.labels = self._undo.pop()
        return True

    def label_at(self, y: float, x: float) -> int:
        r, c = int(round(y)), int(round(x))
        if 0 <= r < self.labels.shape[0] and 0 <= c < self.labels.shape[1]:
            return int(self.labels[r, c])
        return 0

    def next_id(self) -> int:
        return int(self.labels.max()) + 1

    @property
    def n_nuclei(self) -> int:
        return int(len(np.unique(self.labels)) - (1 if (self.labels == 0).any() else 0))

    def draw(self, path: np.ndarray) -> int | None:
        """A new nucleus inside a closed freehand outline [(y, x), ...]. Only background is filled,
        so neighbouring nuclei are not overwritten. Returns its label, or None if nothing was filled."""
        path = np.asarray(path, float)
        if len(path) < 3:
            return None
        rr, cc = draw.polygon(path[:, 0], path[:, 1], self.labels.shape)
        free = self.labels[rr, cc] == 0
        if free.sum() < 3:
            return None
        self._push()
        new = self.next_id()
        self.labels[rr[free], cc[free]] = new
        return new

    def delete_at(self, y: float, x: float) -> int | None:
        lab = self.label_at(y, x)
        if not lab:
            return None
        self._push()
        self.labels[self.labels == lab] = 0
        return lab

    def split(self, line: np.ndarray, width: int = 1) -> list[int]:
        """Cut every nucleus the drawn line [(y, x), ...] crosses. The largest piece keeps the
        nucleus's label, the others get new ones; the cut itself becomes background.
        Returns the labels of the new pieces ([] if nothing was split)."""
        line = np.asarray(line, float)
        if len(line) < 2:
            return []
        cut = np.zeros(self.labels.shape, bool)
        for (y0, x0), (y1, x1) in zip(line[:-1], line[1:], strict=True):
            rr, cc = draw.line(int(round(y0)), int(round(x0)), int(round(y1)), int(round(x1)))
            ok = (rr >= 0) & (rr < cut.shape[0]) & (cc >= 0) & (cc < cut.shape[1])
            cut[rr[ok], cc[ok]] = True
        if width > 0:
            cut = ndi.binary_dilation(cut, morphology.disk(width))
        crossed = [int(v) for v in np.unique(self.labels[cut]) if v]
        new_ids: list[int] = []
        before = self.labels.copy()
        for lab in crossed:
            obj = (self.labels == lab) & ~cut
            pieces = measure.label(obj, connectivity=1)
            if pieces.max() < 2:
                continue
            sizes = np.bincount(pieces.ravel())
            sizes[0] = 0
            keep = int(sizes.argmax())
            self.labels[(self.labels == lab) & cut] = 0
            for k in range(1, int(pieces.max()) + 1):
                if k != keep and sizes[k]:
                    nid = self.next_id()
                    self.labels[pieces == k] = nid
                    new_ids.append(nid)
        if new_ids:
            self._undo = [*self._undo[-(UNDO_STEPS - 1) :], before]
            self.changed = True
        else:
            self.labels = before
        return new_ids

    def merge(self, a: int, b: int, gap: int = 2) -> bool:
        """Make nucleus b part of nucleus a, closing a small gap between them."""
        if not a or not b or a == b:
            return False
        self._push()
        self.labels[self.labels == b] = a
        obj = self.labels == a
        sl = ndi.find_objects(obj.astype(np.int32))[0]
        pad = tuple(slice(max(s.start - gap, 0), s.stop + gap) for s in sl)
        closed = ndi.binary_closing(obj[pad], morphology.disk(gap))
        bridge = closed & (self.labels[pad] == 0)
        self.labels[pad][bridge] = a
        return True
