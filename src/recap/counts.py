"""Manual counts (ManualCount), their CSV format, and comparison of manual and program counts."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Literal

import numpy as np
import pandas as pd
from pydantic import (
    Field,
    model_validator,
)

from recap.config import StrictModel
from recap.measure import pct
from recap.pipeline import Result

log = logging.getLogger(__name__)


def default_counter_name() -> str:
    """The name counts are saved under: the one remembered (remember_counter_name: the app asks once,
    `recap count --by NAME --remember`), else the login name."""
    from recap.fiji import load_settings

    saved = load_settings().get("counter_name")
    if saved:
        return str(saved)
    import getpass

    try:
        return getpass.getuser()
    except Exception:  # noqa: BLE001  (no login name in some containers)
        return "unknown"


def remember_counter_name(name: str) -> None:
    """Use this name for new counts and corrected masks on this computer (per user)."""
    from recap.fiji import load_settings, save_settings

    d = load_settings()
    d["counter_name"] = name.strip()
    save_settings(d)


# =============================================================================
# Manual spot check
# =============================================================================
class ManualCount(StrictModel):
    """One manually counted image; also the row format of manual_counts_*.csv."""

    image: str
    image_name: str
    sha256: str
    nuclei: int = Field(ge=0)
    infected: int | None = Field(None, ge=0)
    points: list[tuple[float, float, int]] = Field(default_factory=list)  # (x, y, 0=VRC- / 1=VRC+), pixels
    source: Literal["new", "previous", "imported"] = "new"
    counted_at: str
    counted_by: str | None = None  # who counted (for comparing counters)
    origin: str | None = None  # file the counts were imported from
    tool: str | None = None  # how: fiji | napari | cell-counter | multipoint-csv | typed

    @model_validator(mode="after")
    def _consistent(self) -> ManualCount:
        if self.infected is not None and self.infected > self.nuclei:
            raise ValueError("infected cannot exceed nuclei")
        return self


def save_manual_counts(path: Path, counts: list[ManualCount]) -> None:
    rows = []
    for c in counts:
        d = c.model_dump()
        d["points"] = json.dumps(d["points"])
        rows.append(d)
    pd.DataFrame(rows).to_csv(path, index=False)


def read_manual_counts(path: Path) -> list[ManualCount]:
    """All rows of a manual_counts CSV, or all counts in a workspace database (recap-data/recap.db).
    Several counters per image are allowed."""
    if Path(path).suffix == ".db":
        from recap.workspace import Workspace  # here, not at the top: workspace imports this module

        return Workspace.from_db(path).counts()
    df = pd.read_csv(path, dtype={"sha256": str, "counted_by": str, "origin": str})
    out: list[ManualCount] = []
    for rec in df.to_dict("records"):
        rec = {k: (None if isinstance(v, float) and np.isnan(v) else v) for k, v in rec.items()}
        rec["points"] = json.loads(rec.get("points") or "[]")
        out.append(ManualCount.model_validate(rec))
    return out


def load_manual_counts(path: Path) -> dict[str, ManualCount]:
    """One count per image (by file hash) for reuse in the spot check; the first counter wins."""
    out: dict[str, ManualCount] = {}
    for mc in read_manual_counts(path):
        if mc.sha256 in out:
            log.warning(
                "%s: several manual counts for %s; using the first (%s)",
                path,
                mc.image_name,
                out[mc.sha256].counted_by or "unnamed",
            )
            continue
        out[mc.sha256] = mc.model_copy(update={"source": "previous"})
    return out


def compare_counts(mc: ManualCount, res: Result) -> dict[str, Any]:
    t = res.table
    prog_n = len(t)
    prog_inf = int(t.infected.sum()) if prog_n else 0
    man_inf = mc.infected
    row: dict[str, Any] = dict(
        image_name=mc.image_name,
        source=mc.source,
        manual_nuclei=mc.nuclei,
        program_nuclei=prog_n,
        difference=prog_n - mc.nuclei,
        pct_difference=100 * (prog_n - mc.nuclei) / mc.nuclei if mc.nuclei else np.nan,
        manual_infected=man_inf,
        program_infected=prog_inf,
        manual_uninfected=mc.nuclei - man_inf if man_inf is not None else None,
        program_uninfected=prog_n - prog_inf,
        manual_pct_infected=100 * man_inf / mc.nuclei if (man_inf is not None and mc.nuclei) else np.nan,
        program_pct_infected=100 * prog_inf / prog_n if prog_n else np.nan,
    )
    if mc.points:
        H, W = res.labels.shape
        hits = [
            int(res.labels[min(max(int(round(y)), 0), H - 1), min(max(int(round(x)), 0), W - 1)])
            for x, y, _ in mc.points
        ]
        per_obj: dict[int, int] = {}
        for h_ in hits:
            if h_:
                per_obj[h_] = per_obj.get(h_, 0) + 1
        row.update(
            matched=sum(1 for v in per_obj.values() if v == 1),
            missed=sum(1 for h_ in hits if h_ == 0),  # manual point on background
            merged=sum(1 for v in per_obj.values() if v > 1),  # one object, several nuclei
            extra=prog_n - len(per_obj),
        )  # object with no manual point
        if man_inf is not None:
            # per-nucleus infection calls on one-to-one matches (manual call vs program call)
            infected_by_label = dict(zip(t.label, t.infected)) if prog_n else {}
            pairs = [
                (c == 1, bool(infected_by_label[h_]))
                for (_, _, c), h_ in zip(mc.points, hits)
                if h_ and per_obj[h_] == 1
            ]
            row.update(
                both_infected=sum(1 for m, p_ in pairs if m and p_),
                both_uninfected=sum(1 for m, p_ in pairs if not m and not p_),
                manual_inf_program_uninf=sum(1 for m, p_ in pairs if m and not p_),  # program too strict
                manual_uninf_program_inf=sum(1 for m, p_ in pairs if not m and p_),  # program too lenient
                infection_agreement=float(np.mean([m == p_ for m, p_ in pairs])) if pairs else np.nan,
            )
    return row


_COUNT_COLS = (
    "manual_nuclei",
    "program_nuclei",
    "difference",
    "manual_infected",
    "program_infected",
    "manual_uninfected",
    "program_uninfected",
    "matched",
    "missed",
    "merged",
    "extra",
    "both_infected",
    "both_uninfected",
    "manual_inf_program_uninf",
    "manual_uninf_program_inf",
)


def tidy_counts(df: pd.DataFrame) -> pd.DataFrame:
    """Keep counts as integers (with blanks) when some images lack infection or point data."""
    df = df.copy()
    for c in _COUNT_COLS:
        if c in df:
            df[c] = pd.to_numeric(df[c]).astype("Int64")
    return df


def summarize(df: pd.DataFrame) -> dict[str, Any]:
    man, prog = int(df.manual_nuclei.sum()), int(df.program_nuclei.sum())
    pct_diff = df.pct_difference.dropna()
    out: dict[str, Any] = dict(
        n_images=len(df),
        manual_total=man,
        program_total=prog,
        overall_pct_difference=round(100 * (prog - man) / man, 2) if man else None,
        mean_abs_pct_difference=round(float(pct_diff.abs().mean()), 2) if len(pct_diff) else None,
        max_abs_pct_difference=round(float(pct_diff.abs().max()), 2) if len(pct_diff) else None,
    )
    for col in ("missed", "merged", "extra"):
        if col in df:
            out[f"total_{col}"] = int(df[col].fillna(0).sum())

    # infected / uninfected split; manual totals only over images where infection was counted
    has_inf = df.manual_infected.notna()
    if has_inf.any():
        sub = df[has_inf]
        m_inf, m_n = int(sub.manual_infected.sum()), int(sub.manual_nuclei.sum())
        p_inf, p_n = int(sub.program_infected.sum()), int(sub.program_nuclei.sum())
        out.update(
            manual_infected=m_inf,
            manual_uninfected=m_n - m_inf,
            program_infected=p_inf,
            program_uninfected=p_n - p_inf,
            manual_pct_infected=pct(m_inf, m_n),
            program_pct_infected=pct(p_inf, p_n),
        )
        if has_inf.sum() < len(df):
            out["infection_split_images"] = int(has_inf.sum())
    for col in ("both_infected", "both_uninfected", "manual_inf_program_uninf", "manual_uninf_program_inf"):
        if col in df:
            out[col] = int(df[col].fillna(0).sum())
    if "both_infected" in df:
        n_pairs = sum(
            out[c] for c in ("both_infected", "both_uninfected", "manual_inf_program_uninf", "manual_uninf_program_inf")
        )
        out["infection_agreement"] = (
            round((out["both_infected"] + out["both_uninfected"]) / n_pairs, 3) if n_pairs else None
        )
    return out
