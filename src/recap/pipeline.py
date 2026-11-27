"""Processing one image (process_image) and a batch of images (run_batch), with output files."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import tifffile
from skimage import filters, io, segmentation
from skimage.segmentation import expand_labels

from recap.config import FilterConfig, PipelineConfig
from recap.filters import EXCLUSION_REASONS, apply_filters
from recap.io import exclusion_mask, load_channels
from recap.levels import settings_hash
from recap.measure import infected_label_image, measure_nuclei, object_features, pct
from recap.qc import batch_qc, image_qc
from recap.segment import (
    combine,
    finish_labels,
    make_mask,
    model_labels,
    watershed_split,
)

log = logging.getLogger(__name__)


def save_qc(path: Path, dapi_n, vrc_n, labels, df, excluded_labels=None) -> None:
    """White = kept VRC-, red = kept VRC+, blue = excluded by filters."""
    rgb = np.zeros(dapi_n.shape + (3,), np.float32)
    rgb[..., 2], rgb[..., 1] = dapi_n, vrc_n
    if excluded_labels is not None and excluded_labels.any():
        rgb[segmentation.find_boundaries(excluded_labels, mode="inner")] = [0.25, 0.55, 1.0]
    b = segmentation.find_boundaries(labels, mode="outer")
    inf = expand_labels(infected_label_image(labels, df), 1) > 0
    rgb[b] = [1, 1, 1]
    rgb[b & inf] = [1, 0.2, 0.2]
    io.imsave(path, (rgb * 255).astype(np.uint8), check_contrast=False)


@dataclass
class Result:
    labels: np.ndarray
    table: pd.DataFrame
    dapi_n: np.ndarray
    vrc_n: np.ndarray
    n_small_bodies: int = 0
    excluded_labels: np.ndarray | None = None
    excluded: pd.DataFrame | None = None
    n_excluded: dict[str, int] = field(default_factory=dict)
    merges: list = field(default_factory=list)  # keep last: Result is built positionally


# Settings that change the first (expensive) stage; tuning reruns it only when one of these changes.
MODEL_STAGE_SETTINGS = ("normalization",) + tuple(
    f"segmentation.{k}"
    for k in ("method", "diameter", "sigma", "threshold_method", "close_radius", "min_area", "h",
              "stardist_model", "stardist_scale", "prob_thresh", "nms_thresh", "cellpose_model")
)  # fmt: skip


@dataclass
class Stage1:
    """Output of the first stage: normalised channels, combined channel and the raw segmentation."""

    dapi_n: np.ndarray
    vrc_n: np.ndarray
    comb: np.ndarray
    smoothed: np.ndarray
    raw: np.ndarray


def segment_stage(cfg: PipelineConfig, dapi, vrc) -> Stage1:
    """Normalise, combine and segment (StarDist / Cellpose / classical): the slow part."""
    seg = cfg.segmentation.resolved()
    dapi_n, vrc_n, comb = combine(dapi, vrc, cfg.normalization)
    if seg.method == "classical":
        sm, mask = make_mask(comb, seg)
        raw = watershed_split(mask, seg)
    else:
        sm = filters.gaussian(comb, seg.sigma) if seg.sigma > 0 else comb
        raw = model_labels(comb, seg)
    return Stage1(dapi_n, vrc_n, comb, sm, raw)


def finish_stage(cfg: PipelineConfig, s1: Stage1, dapi, vrc, neuron=None) -> Result:
    """Fragment merging, measurement, filters and the VRC+ call on a first-stage result: the fast part."""
    seg = cfg.segmentation.resolved()
    labels, n_small, merges = finish_labels(
        s1.raw, s1.smoothed, seg, s1.vrc_n, cfg.fragments, cfg.measurement.vrc_threshold
    )
    res = measure_and_filter(cfg, labels, s1.comb, dapi, vrc, s1.dapi_n, s1.vrc_n, neuron, n_small)
    res.merges = merges
    return res


def process_image(cfg: PipelineConfig, dapi, vrc, neuron=None) -> Result:
    """Segment, measure and filter one image (2D channel arrays). Pure: writes nothing, prints nothing.

    The settings are used as given; call cfg.segmentation.resolved() first only if you need the
    derived values in your own code (process_image resolves them itself)."""
    return finish_stage(cfg, segment_stage(cfg, dapi, vrc), dapi, vrc, neuron)


def measure_and_filter(
    cfg: PipelineConfig,
    labels,
    comb,
    dapi,
    vrc,
    dapi_n,
    vrc_n,
    neuron=None,
    n_small: int = 0,
    filters_cfg: FilterConfig | None = None,
) -> Result:
    f = filters_cfg or cfg.filters
    table = measure_nuclei(labels, dapi, vrc, vrc_n, cfg.measurement, neuron)
    feats = object_features(labels, comb, exclusion_mask(labels.shape, cfg.input.exclude_regions), f.border_px)
    if not table.empty:
        table = table.merge(feats, on="label", how="left")
    kept_lab, kept, excl_lab, excl, counts = apply_filters(labels, table, f)
    return Result(kept_lab, kept, dapi_n, vrc_n, n_small, excl_lab, excl, counts)


ProgressFn = Callable[[int, int, Path, dict[str, Any]], None]

SUMMARY_COLUMNS = (
    (
        "image",
        "n_nuclei",
        "n_infected",
        "n_uninfected",
        "pct_infected",
        "n_nuclei_low",
        "n_nuclei_high",
        "n_infected_low",
        "n_infected_high",
        "qc_flags",
        "n_small_bodies",
        "n_fragment_merges",
        "n_filtered",
    )
    + tuple(f"n_excluded_{k}" for k in EXCLUSION_REASONS)
    + ("saturated_dapi_fraction", "saturated_vrc_fraction", "vrc_background", "median_edge_sharpness")
    + ("settings", "overridden", "settings_reason")
)


def process_channels(cfg: PipelineConfig, img: np.ndarray) -> Result:
    """Process a loaded (channel, y, x) array using the channel indices from cfg.input."""
    n_idx = cfg.input.neuron_channel
    return process_image(
        cfg, img[cfg.input.dapi_channel], img[cfg.input.vrc_channel], img[n_idx] if n_idx is not None else None
    )


def process_file(cfg: PipelineConfig, path: Path) -> Result:
    """Load one image file (with exclude_regions blanked) and process it."""
    return process_channels(cfg, load_channels(path, cfg.input.exclude_regions))


def run_batch(
    cfg: PipelineConfig,
    paths: list[Path],
    out_dir: Path,
    stamp: str,
    cache: dict[Path, Result] | None = None,
    progress: ProgressFn | None = None,
    cancel: Callable[[], bool] | None = None,
    settings_for: Callable[[Path], Any] | None = None,
    image_dir: Callable[[Path], Path] | None = None,
) -> list[dict[str, Any]]:
    """Process every image and write the per-image and batch outputs into out_dir (per-image
    outputs into image_dir(path) instead, if given: a workspace's processed/.../<image>/).

    progress(i, n, path, info) is called after each image (info is that image's summary, or
    {"error": ...}); if cancel() returns True the batch stops before the next image and the
    outputs of the images already done are still written. settings_for(path) gives an image's
    own settings (a levels.Resolved: workspace / batch / image levels); None = cfg for all."""
    cache = cache or {}
    base_cfg = cfg
    images_log, tables = [], []
    for i, p in enumerate(paths, 1):
        if cancel is not None and cancel():
            log.warning("Batch cancelled after %d of %d image(s)", i - 1, len(paths))
            images_log.append({"cancelled": [str(q) for q in paths[i - 1 :]]})
            break
        resolved = settings_for(p) if settings_for is not None else None
        cfg = resolved.cfg if resolved is not None else base_cfg
        try:
            img = load_channels(p, cfg.input.exclude_regions)
            res = cache[p] if p in cache else process_channels(cfg, img)  # cache: from the spot check
        except Exception as e:  # keep the batch going
            log.exception("Failed on %s", p)
            images_log.append({"image": str(p), "error": repr(e)})
            if progress:
                progress(i, len(paths), p, images_log[-1])
            continue
        outputs = {}
        folder = image_dir(p) if image_dir is not None else out_dir
        folder.mkdir(parents=True, exist_ok=True)
        stem = folder / p.stem
        table = res.table.copy()
        table.insert(0, "image", p.name)
        table.to_csv(f"{stem}_nuclei.csv", index=False)
        outputs["table"] = f"{stem}_nuclei.csv"
        if res.merges:
            pd.DataFrame(res.merges).assign(image=p.name).to_csv(f"{stem}_merges.csv", index=False)
            outputs["merges"] = f"{stem}_merges.csv"
        if res.excluded is not None and len(res.excluded):
            ex = res.excluded.copy()
            ex.insert(0, "image", p.name)
            ex.to_csv(f"{stem}_excluded.csv", index=False)
            outputs["excluded"] = f"{stem}_excluded.csv"
        if cfg.output.save_labels:
            tifffile.imwrite(f"{stem}_labels.tif", res.labels.astype(np.uint16))
            outputs["labels"] = f"{stem}_labels.tif"
            if res.excluded_labels is not None and res.excluded_labels.any():
                tifffile.imwrite(f"{stem}_excluded_labels.tif", res.excluded_labels.astype(np.uint16))
                outputs["excluded_labels"] = f"{stem}_excluded_labels.tif"
        if cfg.output.save_qc:
            save_qc(Path(f"{stem}_qc.png"), res.dapi_n, res.vrc_n, res.labels, res.table, res.excluded_labels)
            outputs["qc"] = f"{stem}_qc.png"
        n_inf = int(table.infected.sum()) if len(table) else 0
        n_uninf = len(table) - n_inf
        n_excl = len(res.excluded) if res.excluded is not None else 0
        log.info(
            "%s: %d nuclei, %d infected, %d uninfected; %d fragments merged; %d small bodies and "
            "%d filtered nuclei excluded %s",
            p.name,
            len(table),
            n_inf,
            n_uninf,
            len(res.merges),
            res.n_small_bodies,
            n_excl,
            {k: v for k, v in res.n_excluded.items() if v},
        )
        images_log.append(
            {
                "image": str(p),
                "shape": list(img.shape),
                "n_nuclei": len(table),
                "n_infected": n_inf,
                "n_uninfected": n_uninf,
                "pct_infected": pct(n_inf, len(table)),
                "n_small_bodies": res.n_small_bodies,
                "n_fragment_merges": len(res.merges),
                "n_filtered": n_excl,
                **{f"n_excluded_{k}": res.n_excluded.get(k, 0) for k in EXCLUSION_REASONS},
                **image_qc(cfg, res, img),
                "settings": resolved.label if resolved is not None else "settings file",
                "settings_hash": settings_hash(cfg),
                "overridden": resolved.overridden if resolved is not None else [],
                "settings_reason": resolved.reason if resolved is not None else None,
                "outputs": outputs,
            }
        )
        if resolved is not None and resolved.overridden:
            log.info("%s: own settings (%s): %s", p.name, resolved.label, ", ".join(resolved.overridden))
        if images_log[-1]["qc_flags"]:
            log.warning("%s: QC flags: %s", p.name, ", ".join(images_log[-1]["qc_flags"]))
        tables.append(table)
        if progress:
            progress(i, len(paths), p, images_log[-1])
    cfg = base_cfg
    batch_qc(images_log, cfg)
    for d in images_log:
        if "blurry_vs_batch" in d.get("qc_flags", []):
            log.warning(
                "%s: QC flag: blurry_vs_batch (sharpness %.4f)", Path(d["image"]).name, d["median_edge_sharpness"]
            )
    if tables:
        pd.concat(tables, ignore_index=True).to_csv(out_dir / f"all_nuclei_{stamp}.csv", index=False)
    ok = [d for d in images_log if "n_nuclei" in d]
    if ok:
        rows = [{k: (";".join(d[k]) if k in ("qc_flags", "overridden") else d[k]) for k in SUMMARY_COLUMNS} for d in ok]
        pd.DataFrame(rows, columns=list(SUMMARY_COLUMNS)).to_csv(out_dir / f"image_summary_{stamp}.csv", index=False)
    return images_log


process = process_image  # backwards-compatible name
