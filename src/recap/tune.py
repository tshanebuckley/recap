"""Tuning: try settings, score each against hand-edited masks, keep the best.

Tuning searches the shared settings (the settings file, as a workspace-wide starting point);
each image's batch and image settings are applied on top, so a setting a batch or image overrides
is not searched for that image.

Random search over a few settings that matter most (filters, fragment merging, StarDist
thresholds, the VRC weight). The slow segmentation step is rerun only when a setting it depends
on changes (pipeline.MODEL_STAGE_SETTINGS), so filter-only trials take a fraction of a second.
Every trial is stored in the workspace; the best settings are written as a complete settings
file to start from.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import random
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from recap.config import PipelineConfig
from recap.errors import Cancelled, RecapError
from recap.evaluate import Reference, score_config
from recap.io import file_sha256
from recap.pipeline import MODEL_STAGE_SETTINGS
from recap.workspace import Workspace

log = logging.getLogger(__name__)

# Settings searched by default, with the values tried. Override with tune(..., space=...).
DEFAULT_SPACE: dict[str, list[Any]] = {
    "normalization.vrc_weight": [0.5, 0.75, 1.0, 1.25],
    "segmentation.prob_thresh": [None, 0.4, 0.5, 0.6],
    "segmentation.fragment_merge": ["off", "shape", "shape_vrc"],
    "filters.min_focus": [None, 0.5, 0.6, 0.65, 0.7],
    "filters.min_circularity": [None, 0.55, 0.6, 0.65, 0.7],
    "filters.border_max_missing": [None, 0.1, 0.15, 0.25],
}


@dataclass
class TuneResult:
    session: str
    trials: pd.DataFrame  # one row per trial: the settings tried and their scores, best first
    baseline: dict[str, float]  # scores of the settings as given
    best_params: dict[str, Any]
    best_scores: dict[str, float]
    best_config: Path  # complete settings file with the best values


def set_path(d: dict, dotted: str, value: Any) -> None:
    section, key = dotted.split(".", 1)
    d[section][key] = value


def get_path(d: dict, dotted: str) -> Any:
    section, key = dotted.split(".", 1)
    return d[section].get(key)


def references(ws: Workspace, cfg: PipelineConfig | None = None) -> list[Reference]:
    """The workspace's images that have a corrected (hand-edited) mask."""
    masks = ws.reference_masks()
    refs = []
    for p in ws.images(cfg):
        sha = file_sha256(p)
        if sha in masks:
            refs.append(Reference(p, sha, masks[sha]))
    if not refs:
        raise RecapError(
            "No image in this workspace has a corrected mask yet. Correct one in the app (Manual mode: "
            "Correct outlines, then Save corrected mask); tuning and training compare against those masks."
        )
    return refs


def tune(
    ws: Workspace,
    trials: int = 30,
    seed: int | None = None,
    space: dict[str, list[Any]] | None = None,
    iou: float = 0.5,
    progress: Callable[[int, int, dict], None] | None = None,
    cancel: Callable[[], bool] | None = None,
) -> TuneResult:
    from recap.config import load_config

    config_path = ws.settings_path
    raw, cfg, _ = load_config(config_path)
    space = space or DEFAULT_SPACE
    if cfg.segmentation.method != "stardist":
        space = {k: v for k, v in space.items() if k != "segmentation.prob_thresh"}
    refs = references(ws, cfg)
    base = cfg.model_dump(mode="json")
    rng = random.Random(seed)
    combos = [{k: get_path(base, k) for k in space}]  # trial 0 = the settings as given
    seen = {json.dumps(combos[0], sort_keys=True)}
    for _ in range(trials * 20):
        if len(combos) >= trials:
            break
        c = {k: rng.choice(v) for k, v in space.items()}
        if json.dumps(c, sort_keys=True) not in seen:
            seen.add(json.dumps(c, sort_keys=True))
            combos.append(c)

    def stage_key(params: dict[str, Any]) -> str:
        return json.dumps({k: v for k, v in params.items() if k.startswith(MODEL_STAGE_SETTINGS)}, sort_keys=True)

    combos = combos[:1] + sorted(combos[1:], key=stage_key)  # baseline first; then trials sharing a segmentation
    session = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    cache: dict = {}
    rows = []
    for i, params in enumerate(combos):
        if cancel and cancel():
            raise Cancelled("tuning cancelled")
        d = json.loads(json.dumps(base))
        for k, v in params.items():
            set_path(d, k, v)
        try:
            trial_cfg = PipelineConfig.model_validate(d)
        except ValueError as e:
            log.info("trial %d skipped (invalid combination): %s", i, e)
            continue
        key = stage_key(params)
        if not any(k[1] == key for k in cache):
            cache.clear()  # one segmentation setting at a time in memory
        scores = score_config(trial_cfg, refs, iou, cache, key, ws)
        ws.add_tuning(session, config_path.resolve(), params, scores)
        rows.append({**params, **scores, "trial": i})
        if progress:
            progress(i + 1, len(combos), {**params, **scores})
        log.info("trial %d/%d: f1 %.3f %s", i + 1, len(combos), scores["f1"], params)
    df = pd.DataFrame(rows).sort_values(["f1", "count_error"], ascending=[False, True]).reset_index(drop=True)
    best = df.iloc[0]
    best_params = {k: _plain(best[k]) for k in space}
    best_scores = {k: _plain(best[k]) for k in ("f1", "precision", "recall", "count_error", "mean_iou")}
    baseline = df[df.trial == 0].iloc[0]
    out = ws.folder(f"tuning/{session}")
    df.to_csv(out / "trials.csv", index=False)
    tuned = json.loads(json.dumps(raw))
    tuned.pop("interactive", None)
    for k, v in best_params.items():
        tuned.setdefault(k.split(".", 1)[0], {})
        set_path(tuned, k, v)
    tuned.setdefault("input", {})["images"] = [str(r.path) for r in refs]
    if "output" in tuned and not Path(str(tuned["output"].get("directory", "results"))).is_absolute():
        tuned["output"]["directory"] = str((config_path.parent / tuned["output"]["directory"]).resolve())
    best_config = out / "best_config.yaml"
    best_config.write_text(
        f"# Tuned by recap on {len(refs)} image(s) with edited masks; session {session}\n"
        f"# f1 {best_scores['f1']} (settings as given: {baseline['f1']})\n" + yaml.safe_dump(tuned, sort_keys=False),
        encoding="utf-8",
    )
    return TuneResult(
        session,
        df,
        {k: _plain(baseline[k]) for k in best_scores},
        best_params,
        best_scores,
        best_config,
    )


def apply_tuned(ws: Workspace, params: dict[str, Any], level: str = "workspace", batch: str | None = None) -> dict:
    """Use tuned values: write them into recap.yaml (workspace) or a batch's settings. Only values
    that differ from recap.yaml are stored for a batch. Returns what was stored."""
    base = ws.settings().model_dump(mode="json")
    changed = {k: v for k, v in params.items() if get_path(base, k) != v}
    if level == "workspace":
        return ws.update_level("workspace", "", changed)
    if level == "batch" and batch:
        same = [k for k in params if k not in changed]
        return ws.update_level("batch", batch, changed, remove=same)
    raise ValueError("level must be 'workspace' or 'batch' (with a batch name)")


def _plain(v: Any) -> Any:
    """numpy / pandas scalars (and NaN for None) to plain Python for YAML and JSON."""
    if v is None or (isinstance(v, float) and v != v):
        return None
    return v.item() if hasattr(v, "item") else v
