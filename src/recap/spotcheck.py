"""Manual spot check: random selection, counting (by a counter callback), overview and decision.

The counting itself is done by whoever calls run_spot_check: the command line counts in Fiji
(recap.fijicount.FijiCounter), the desktop app can count in napari. A counter is called as
counter(image_path, rgb, k, n) and returns ("done", points, nuclei, infected) | ("skip",) | ("cancel",).
"""

from __future__ import annotations

import datetime as dt
import logging
import random
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import numpy as np
import pandas as pd

from recap.config import PipelineConfig
from recap.counts import (
    ManualCount,
    compare_counts,
    default_counter_name,
    load_manual_counts,
    save_manual_counts,
    summarize,
    tidy_counts,
)
from recap.errors import ConfigError, RecapError
from recap.io import file_sha256, load_channels
from recap.pipeline import Result, process_file
from recap.segment import combine

log = logging.getLogger(__name__)


def rgb_for_counting(dapi_n: np.ndarray, vrc_n: np.ndarray) -> np.ndarray:
    """DAPI magenta, VRC green (colour-blind safe). No segmentation shown: counting is blind."""
    rgb = np.stack([dapi_n, vrc_n, dapi_n], axis=-1)
    return (np.clip(rgb, 0, 1) * 255).astype(np.uint8)


def overview_text(df: pd.DataFrame, summary: dict[str, Any]) -> str:
    def cell(v: Any) -> str:
        if v is None or v is pd.NA or (isinstance(v, float) and np.isnan(v)):
            return "-"
        return f"{v:.1f}" if isinstance(v, float) else str(v)

    def table(cols: dict[str, str]) -> str:
        present = {k: v for k, v in cols.items() if k in df}
        view = df[list(present)].rename(columns=present).astype(object).map(cell)
        return view.to_string(index=False)

    g = summary.get
    lines = [
        "NUCLEI",
        table(
            {
                "image_name": "image",
                "source": "source",
                "manual_nuclei": "manual",
                "program_nuclei": "program",
                "pct_difference": "%diff",
                "missed": "missed",
                "merged": "merged",
                "extra": "extra",
            }
        ),
        f"Total: manual {g('manual_total')}, program {g('program_total')} "
        f"({g('overall_pct_difference')}% overall; mean |%diff| {g('mean_abs_pct_difference')}, "
        f"max {g('max_abs_pct_difference')})",
    ]
    if "total_missed" in summary:
        lines.append(f"Missed {g('total_missed')}, merged {g('total_merged')}, extra {g('total_extra')}")

    if "manual_infected" in summary:
        lines += [
            "",
            "INFECTED vs UNINFECTED",
            table(
                {
                    "image_name": "image",
                    "manual_infected": "man_inf",
                    "program_infected": "prog_inf",
                    "manual_uninfected": "man_uninf",
                    "program_uninfected": "prog_uninf",
                    "manual_pct_infected": "man_%inf",
                    "program_pct_infected": "prog_%inf",
                }
            ),
            f"Manual : {g('manual_infected')} infected / {g('manual_uninfected')} uninfected "
            f"({g('manual_pct_infected')}% infected)",
            f"Program: {g('program_infected')} infected / {g('program_uninfected')} uninfected "
            f"({g('program_pct_infected')}% infected)",
        ]
        if "infection_split_images" in summary:
            lines.append(f"(split totals cover the {g('infection_split_images')} image(s) counted with infection)")
    if "both_infected" in summary:
        lines += [
            "",
            "Per-nucleus calls on matched nuclei (rows = manual, columns = program):",
            "                 prog inf   prog uninf",
            f"  manual inf     {g('both_infected'):>8}   {g('manual_inf_program_uninf'):>10}",
            f"  manual uninf   {g('manual_uninf_program_inf'):>8}   {g('both_uninfected'):>10}",
            f"Agreement: {g('infection_agreement')}   "
            "(many manual-inf/prog-uninf: lower vrc_threshold or infected_fraction; "
            "the reverse: raise them)",
        ]

    lines += [
        "",
        "missed = manual point on background; merged = one object holding >1 manual point;",
        "extra = object with no manual point (fragment or false positive).",
        "",
        "Run all: save counts, then process every image.",
        "Save counts: save counts and config, stop.   Cancel: stop without saving the run.",
    ]
    return "\n".join(lines)


@dataclass
class SpotCheckOutcome:
    decision: Literal["run_all", "save", "cancel"]
    log: dict[str, Any]
    counts_file: Path
    comparison_file: Path | None = None


DecideFn = Callable[[str], Literal["run_all", "save", "cancel"]]
CountAnswer = tuple  # ("done", points, nuclei, infected) | ("skip",) | ("cancel",)
CountFn = Callable[[Path, np.ndarray, int, int], CountAnswer]


def run_spot_check(
    cfg: PipelineConfig,
    paths: list[Path],
    out_dir: Path,
    stamp: str,
    base: Path,
    cache: dict[Path, Result],
    decide: DecideFn | None = None,
    counter: CountFn | None = None,
    settings_for: Callable[[Path], Any] | None = None,
) -> SpotCheckOutcome:
    """Random manual spot check. Images without a previous count are counted by counter(...);
    the overview decision is taken by decide(overview_text)."""
    v = cfg.validation
    seed = v.seed if v.seed is not None else random.SystemRandom().randrange(2**32)
    rng = random.Random(seed)
    counts_file = out_dir / f"manual_counts_{stamp}.csv"
    vlog: dict[str, Any] = {"seed": seed, "counts_file": str(counts_file)}

    candidates = list(paths)
    n_target = v.target_size(len(candidates))
    if n_target < (round(v.sample_fraction * len(candidates)) if v.sample_fraction else v.sample_size):
        log.warning("Spot check capped at %d image(s) (only %d available)", n_target, len(candidates))
    vlog["requested_size"] = n_target

    previous: dict[str, ManualCount] = {}
    hashes: dict[Path, str] = {}
    if v.previous_counts is not None:
        prev_path = v.previous_counts if v.previous_counts.is_absolute() else base / v.previous_counts
        previous = load_manual_counts(prev_path)
        hashes = {p: file_sha256(p) for p in candidates}  # match by content, not by name
        vlog["previous_counts"] = {
            "path": str(prev_path.resolve()),
            "sha256": file_sha256(prev_path),
            "entries": len(previous),
        }
        selected = [p for p in candidates if hashes[p] in previous]
        unmatched = len(previous) - len(selected)
        if unmatched:
            log.warning("%d previous count(s) match no image in this run and were ignored", unmatched)
        rest = [p for p in candidates if p not in selected]
        rng.shuffle(rest)
        if v.top_up and len(selected) < n_target:
            k = n_target - len(selected)
            selected, rest = selected + rest[:k], rest[k:]
        pool = rest
        log.info("Spot check: %d image(s) from previous counts", sum(hashes[p] in previous for p in selected))
    else:
        shuffled = candidates[:]
        rng.shuffle(shuffled)
        selected, pool = shuffled[:n_target], shuffled[n_target:]
    if not selected:
        raise ValueError("spot check selected no images (previous_counts matched nothing; set top_up: true?)")

    counts: list[ManualCount] = []
    skipped: list[str] = []
    queue = list(selected)
    i = 0
    while i < len(queue):
        p = queue[i]
        sha = hashes.get(p) or file_sha256(p)
        if sha in previous:
            counts.append(previous[sha].model_copy(update={"image": str(p.resolve())}))
            i += 1
            continue
        if counter is None:
            need = [q.name for q in queue[i:] if (hashes.get(q) or file_sha256(q)) not in previous]
            raise ConfigError(
                f"The spot check needs manual counts for {len(need)} image(s) without one "
                f"({', '.join(need)}), but no way to count was given."
            )
        pcfg = settings_for(p).cfg if settings_for else cfg  # the view the image will be analysed with
        img = load_channels(p, pcfg.input.exclude_regions)
        dapi_n, vrc_n, _ = combine(img[pcfg.input.dapi_channel], img[pcfg.input.vrc_channel], pcfg.normalization)
        n_new = sum(1 for q in queue if (hashes.get(q) or "") not in previous)
        k_new = sum(1 for c in counts if c.source == "new") + 1
        ans = counter(p, rgb_for_counting(dapi_n, vrc_n), k_new, n_new)
        if ans[0] == "cancel":
            save_manual_counts(counts_file, counts)
            return SpotCheckOutcome("cancel", {**vlog, "decision": "cancel"}, counts_file)
        if ans[0] == "skip":
            skipped.append(str(p))
            log.info("Skipped %s", p.name)
            queue.pop(i)
            if pool:
                queue.append(pool.pop(0))
            continue
        _, points, nuclei, infected = ans
        counts.append(
            ManualCount(
                image=str(p.resolve()),
                image_name=p.name,
                sha256=sha,
                nuclei=nuclei,
                infected=infected,
                points=points,
                source="new",
                counted_at=dt.datetime.now().isoformat(timespec="seconds"),
                counted_by=v.counted_by or default_counter_name(),
                tool=getattr(counter, "tool", None),
            )
        )
        save_manual_counts(counts_file, counts)  # saved after every image: nothing is lost
        log.info("Counted %s: %d nuclei", p.name, nuclei)
        i += 1
    save_manual_counts(counts_file, counts)

    rows = []
    by_path = {Path(c.image): c for c in counts}
    for p in queue:
        cache[p] = process_file(settings_for(p).cfg if settings_for else cfg, p)
        rows.append(compare_counts(by_path[p.resolve()], cache[p]))
    df = tidy_counts(pd.DataFrame(rows))
    summary = summarize(df)
    text = overview_text(df, summary)
    log.info("Spot check overview:\n%s", text)

    if decide is None:
        raise RecapError("the spot check needs a decision but no decide callback was given")
    decision = decide(text)

    vlog.update(
        selected=[str(p) for p in queue],
        skipped=skipped,
        reused_from_previous=[c.image_name for c in counts if c.source == "previous"],
        newly_counted=[c.image_name for c in counts if c.source == "new"],
        summary=summary,
        decision=decision,
    )
    if decision == "cancel":
        return SpotCheckOutcome("cancel", vlog, counts_file)
    comparison_file = out_dir / f"spot_check_{stamp}.csv"
    df.to_csv(comparison_file, index=False)
    vlog["comparison_file"] = str(comparison_file)
    return SpotCheckOutcome(decision, vlog, counts_file, comparison_file)
