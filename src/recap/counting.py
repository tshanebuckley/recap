"""Manual counting, shared by `recap count`, the app and the spot check.

One image is counted the same way wherever it is counted: in recap's adjusted view (the
normalised DAPI and ICP8 that the pipeline sees, with the image's own workspace / batch / image
settings and blanked regions) unless the original file is asked for, and saved to the workspace
under the counter's name.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import tifffile

from recap.counts import ManualCount, default_counter_name
from recap.fijicount import FijiSession, answer
from recap.io import load_channels
from recap.segment import combine
from recap.spotcheck import rgb_for_counting

COUNTER_HELP = (
    "Fiji: click each nucleus with the Multi-point tool. Counter 0 = VRC- (uninfected), counter 1 = VRC+ "
    "(infected): double-click the Multi-point tool and change 'Counter' to switch. Press 'Done counting' "
    "in the small recap window when finished."
)


@dataclass
class Outcome:
    status: str  # done | skip | cancel
    count: ManualCount | None = None


def counting_view(ws, sha: str) -> np.ndarray:
    """The RGB image to count on: normalised DAPI (blue) and ICP8 (green) with the image's own settings."""
    cfg = ws.resolve(sha).cfg
    ch = load_channels(ws.image_path(sha), cfg.input.exclude_regions)
    dapi_n, vrc_n, _ = combine(ch[cfg.input.dapi_channel], ch[cfg.input.vrc_channel], cfg.normalization)
    return rgb_for_counting(dapi_n, vrc_n)


def fiji_session(ws) -> FijiSession:
    loc = ws.fiji()
    return FijiSession(loc.folder, ws.folder("fiji") / f"session_{dt.datetime.now():%Y%m%d_%H%M%S}")


def save_count(ws, sha: str, points, by: str | None, tool: str, infected: bool = True) -> ManualCount:
    """Store a count made of points [(x, y, counter)] (counter 1 = VRC+) for an image."""
    info = ws.image(sha)["image"]
    pts = [(float(x), float(y), int(c)) for x, y, c in points]
    mc = ManualCount(
        image=str(ws.abs(info["path"])), image_name=info["name"], sha256=sha, nuclei=len(pts),
        infected=sum(1 for *_, c in pts if c == 1) if infected else None, points=pts, source="new",
        counted_at=dt.datetime.now().isoformat(timespec="seconds"), counted_by=by or default_counter_name(),
        tool=tool,
    )  # fmt: skip
    ws.add_count(mc)
    return mc


def count_in_fiji(
    ws,
    sha: str,
    session: FijiSession,
    by: str | None = None,
    ask_import: Callable[[str], bool] | None = None,
    original: bool = False,
    infected: bool = True,
    position: str = "",
) -> Outcome:
    """Show one image in Fiji, wait for the person, and save the count to the workspace."""
    name = ws.image(sha)["image"]["name"]
    if original:
        image = ws.image_path(sha)
    else:
        image = session.dir / f"{Path(name).stem}_for_counting.tif"
        tifffile.imwrite(image, counting_view(ws, sha), photometric="rgb")  # same pixel grid as the file
    res = session.count(image, name, position or name, infected)
    ans = answer(res, infected, ask_import)
    if ans[0] != "done":
        return Outcome(ans[0])
    return Outcome("done", save_count(ws, sha, ans[1], by, "fiji", infected))
