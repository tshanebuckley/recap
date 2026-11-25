"""Importing manual counts made in plain Fiji, so counting does not depend on recap's Fiji link.

Supported inputs
  Cell Counter XML   The Cell Counter plugin, "Save Markers". Pixel coordinates.
                     Default mapping: type 1 = VRC-, type 2 = VRC+.
  Points CSV         Multi-point tool, Analyze > Measure, then save the Results table. Needs X and
                     Y columns; a Counter (or Type / Class) column gives the marker type. X/Y are in
                     calibrated units if the image has a scale: pass pixel_size (units per pixel).
                     Default mapping: counter 0 = VRC-, counter 1 = VRC+ (as in recap's spot check).
  Typed totals       nuclei (and optionally VRC+) without points.
"""

from __future__ import annotations

import datetime as dt
import logging
import xml.etree.ElementTree as ET
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import pandas as pd
import tifffile
from pydantic import ValidationError

from recap.counts import ManualCount, read_manual_counts, save_manual_counts
from recap.errors import RecapError
from recap.io import file_sha256

log = logging.getLogger(__name__)
Format = Literal["auto", "cellcounter", "csv"]


@dataclass
class ImportResult:
    count: ManualCount
    notes: list[str] = field(default_factory=list)


def read_cell_counter_xml(path: Path) -> tuple[str | None, list[tuple[float, float, int]]]:
    """(image file name stored in the XML, [(x, y, type), ...])."""
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError as e:
        raise RecapError(f"{path}: not a valid XML file ({e})") from e
    if root.tag != "CellCounter_Marker_File":
        raise RecapError(f"{path}: not a Cell Counter marker file (root element {root.tag!r})")
    name = root.findtext("Image_Properties/Image_Filename")
    pts = []
    for mt in root.iter("Marker_Type"):
        t = int(mt.findtext("Type", "0"))
        for m in mt.iter("Marker"):
            pts.append((float(m.findtext("MarkerX")), float(m.findtext("MarkerY")), t))
    return name, pts


def read_points_table(path: Path) -> tuple[list[tuple[float, float, int]], bool]:
    """Points from a CSV/TSV with X and Y columns. Returns (points, has_type_column)."""
    df = pd.read_csv(path, sep=None, engine="python")
    cols = {str(c).strip().lower(): c for c in df.columns}
    if "x" not in cols or "y" not in cols:
        raise RecapError(f"{path}: needs X and Y columns (found {list(df.columns)})")
    tcol = next((cols[c] for c in ("counter", "type", "class") if c in cols), None)
    types = df[tcol].astype(int) if tcol is not None else pd.Series(0, index=df.index)
    pts = [(float(x), float(y), int(t)) for x, y, t in zip(df[cols["x"]], df[cols["y"]], types)]
    return pts, tcol is not None


def _image_shape(image: Path) -> tuple[int, int]:
    with tifffile.TiffFile(image) as tf:
        shape = tf.series[0].shape
    axes = tf.series[0].axes if hasattr(tf.series[0], "axes") else ""
    if axes:
        return shape[axes.index("Y")], shape[axes.index("X")]
    return shape[-2], shape[-1]


def import_counts(
    image: Path,
    counts_file: Path | None = None,
    fmt: Format = "auto",
    pixel_size: float | None = None,
    uninfected_types: Iterable[int] | None = None,
    infected_types: Iterable[int] | None = None,
    no_infection: bool = False,
    nuclei: int | None = None,
    vrc_pos: int | None = None,
    counted_by: str | None = None,
) -> ImportResult:
    """Build a ManualCount for `image` from a Fiji export (or typed totals)."""
    notes: list[str] = []
    base = dict(
        image=str(image.resolve()),
        image_name=image.name,
        sha256=file_sha256(image),
        source="imported",
        counted_at=dt.datetime.now().isoformat(timespec="seconds"),
        counted_by=counted_by,
    )
    if counts_file is None:
        if nuclei is None:
            raise RecapError("give a counts file, or --nuclei (and optionally --vrc-pos) for typed totals")
        try:
            mc = ManualCount(
                **base, nuclei=nuclei, infected=None if no_infection else vrc_pos, origin="typed", tool="typed"
            )
        except ValidationError as e:
            raise RecapError(f"invalid totals: {e.errors()[0]['msg']}") from e
        return ImportResult(mc, notes)
    if nuclei is not None or vrc_pos is not None:
        raise RecapError("give either a counts file or typed totals, not both")

    kind = fmt
    if kind == "auto":
        kind = "cellcounter" if counts_file.suffix.lower() == ".xml" else "csv"
    if kind == "cellcounter":
        xml_name, pts = read_cell_counter_xml(counts_file)
        if xml_name and Path(xml_name).stem != image.stem:
            notes.append(f"the XML was saved for {xml_name!r}, importing it for {image.name!r}")
        un = set(uninfected_types or {1})
        inf = set(infected_types or {2})
        typed = True
    else:
        pts, typed = read_points_table(counts_file)
        un = set(uninfected_types or {0})
        inf = set(infected_types or {1})
        if pixel_size:
            pts = [(x / pixel_size, y / pixel_size, t) for x, y, t in pts]
    if un & inf:
        raise RecapError(f"types {sorted(un & inf)} are listed as both VRC- and VRC+")

    H, W = _image_shape(image)
    outside = [(x, y) for x, y, _ in pts if not (0 <= x <= W and 0 <= y <= H)]
    if outside:
        raise RecapError(
            f"{len(outside)} point(s) lie outside the {W} x {H} px image (e.g. {outside[0]}). "
            "If the image is calibrated, the CSV is in calibrated units: pass --pixel-size."
        )
    if pts and max(x for x, _, _ in pts) < 0.05 * W and max(y for _, y, _ in pts) < 0.05 * H:
        notes.append(
            "all points sit in the top-left corner: are the coordinates in calibrated units? (use --pixel-size)"
        )

    if not typed:  # no type column: every point is a nucleus
        keep = [(x, y, 0) for x, y, _ in pts]
        infected = None
        notes.append("no Counter/Type column: all points counted as nuclei, VRC+ not recorded")
    else:
        other = sorted({t for *_, t in pts if t not in un | inf})
        if other:
            notes.append(
                f"ignored {sum(t in other for *_, t in pts)} marker(s) of type(s) {other} "
                f"(VRC- = {sorted(un)}, VRC+ = {sorted(inf)})"
            )
        keep = [(x, y, 1 if t in inf else 0) for x, y, t in pts if t in un | inf]
        infected = None if no_infection else sum(c for *_, c in keep)
        if not no_infection and infected == 0 and keep:
            notes.append(f"no VRC+ markers (type(s) {sorted(inf)}); if infection was not marked, use --no-infection")
    tool = "cell-counter" if kind == "cellcounter" else "multipoint-csv"
    mc = ManualCount(
        **base, nuclei=len(keep), infected=infected, points=keep, origin=str(counts_file.resolve()), tool=tool
    )
    return ImportResult(mc, notes)


def add_to_counts_file(path: Path, mc: ManualCount) -> bool:
    """Append to (or create) a manual_counts CSV. An entry for the same image and counter is replaced.
    Returns True if an entry was replaced."""
    rows = read_manual_counts(path) if path.exists() else []
    kept = [r for r in rows if not (r.sha256 == mc.sha256 and (r.counted_by or "") == (mc.counted_by or ""))]
    save_manual_counts(path, kept + [mc])
    return len(kept) != len(rows)
