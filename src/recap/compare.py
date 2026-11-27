"""Compare two recap runs on the same images (e.g. StarDist vs Cellpose).

    recap compare RUN_A_DIR RUN_B_DIR [--names stardist cellpose] [--out compare]

For each image present in both runs it reports counts, VRC+ calls, and object-level agreement:
nuclei are matched one-to-one when their overlap (IoU) is at least --iou (default 0.5).
  matched      : found by both methods
  only_<A/B>   : found by one method only (missed, split or merged by the other)
  infection_agreement : same VRC+/VRC- call on matched nuclei
It also writes <image>_compare.png: A outlines in magenta, B in green, on the DAPI channel.
The image paths come from run A's run log; the channels and blanked regions from its final_config_*.yaml.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import tifffile
import yaml
from skimage import io, segmentation

from recap.config import PipelineConfig
from recap.errors import RecapError
from recap.io import load_channels


def latest(folder: Path, pattern: str) -> Path:
    hits = sorted(folder.glob(pattern))
    if not hits:
        raise RecapError(f"no {pattern} in {folder}: is this a recap output folder?")
    return hits[-1]


def run_images(folder: Path) -> list[Path]:
    """The images of a run folder's latest run: image_files in its run log (recap 5.0), or the
    input.images that older recap versions wrote into final_config."""
    try:
        rl = yaml.safe_load(latest(folder, "run_log_*.yaml").read_text(encoding="utf-8")) or {}
    except RecapError:
        rl = {}
    if rl.get("image_files"):
        return [Path(p) for p in rl["image_files"]]
    raw = yaml.safe_load(latest(folder, "final_config_*.yaml").read_text(encoding="utf-8")) or {}
    return [Path(p) for p in (raw.get("input") or {}).get("images") or []]


def match(a: np.ndarray, b: np.ndarray, iou_min: float) -> list[tuple[int, int, float]]:
    """Greedy one-to-one matching of labels by IoU."""
    m = (a > 0) & (b > 0)
    pairs, inter = np.unique(np.c_[a[m], b[m]], axis=0, return_counts=True)
    area_a = np.bincount(a.ravel())
    area_b = np.bincount(b.ravel())
    cand = sorted(((n / (area_a[p] + area_b[q] - n), p, q) for (p, q), n in zip(pairs, inter)), reverse=True)
    used_a, used_b, out = set(), set(), []
    for iou, p, q in cand:
        if iou < iou_min:
            break
        if p in used_a or q in used_b:
            continue
        used_a.add(p)
        used_b.add(q)
        out.append((int(p), int(q), float(iou)))
    return out


def compare_runs(
    run_a: Path, run_b: Path, names=("A", "B"), iou: float = 0.5, out: Path = Path("compare")
) -> pd.DataFrame:
    na, nb = names
    out.mkdir(parents=True, exist_ok=True)

    cfg = PipelineConfig.model_validate(
        yaml.safe_load(latest(run_a, "final_config_*.yaml").read_text(encoding="utf-8"))
    )
    images = {p.stem: p for p in run_images(run_a)}
    sa = pd.read_csv(latest(run_a, "image_summary_*.csv"))
    sb = pd.read_csv(latest(run_b, "image_summary_*.csv"))

    rows = []
    for stem, path in images.items():
        la_p, lb_p = run_a / f"{stem}_labels.tif", run_b / f"{stem}_labels.tif"
        if not (la_p.exists() and lb_p.exists()):
            print(f"skipping {stem}: labels missing in one run")
            continue
        la, lb = tifffile.imread(la_p), tifffile.imread(lb_p)
        ta = pd.read_csv(run_a / f"{stem}_nuclei.csv").set_index("label")
        tb = pd.read_csv(run_b / f"{stem}_nuclei.csv").set_index("label")
        pairs = match(la, lb, iou)
        agree = [bool(ta.at[p, "infected"]) == bool(tb.at[q, "infected"]) for p, q, _ in pairs]
        ra = sa[sa.image.map(lambda s: Path(s).stem) == stem].iloc[0]
        rb = sb[sb.image.map(lambda s: Path(s).stem) == stem].iloc[0]
        rows.append(
            {
                "image": stem,
                f"{na}_nuclei": int(ra.n_nuclei),
                f"{nb}_nuclei": int(rb.n_nuclei),
                f"{na}_vrc_pos": int(ra.n_infected),
                f"{nb}_vrc_pos": int(rb.n_infected),
                f"{na}_pct_vrc_pos": ra.pct_infected,
                f"{nb}_pct_vrc_pos": rb.pct_infected,
                "matched": len(pairs),
                f"only_{na}": int(la.max()) - len(pairs),
                f"only_{nb}": int(lb.max()) - len(pairs),
                "mean_iou_matched": round(float(np.mean([p[2] for p in pairs])), 3) if pairs else np.nan,
                "infection_agreement": round(float(np.mean(agree)), 3) if agree else np.nan,
            }
        )
        # overlay on DAPI
        img = load_channels(path, cfg.input.exclude_regions)
        d = img[cfg.input.dapi_channel]
        d = np.clip(d / max(np.percentile(d, 99.8), 1e-6), 0, 1) * 0.8
        rgb = np.stack([d, d, d], -1)
        rgb[segmentation.find_boundaries(la, mode="inner")] = [1, 0, 1]
        rgb[segmentation.find_boundaries(lb, mode="inner")] = [0, 1, 0]
        io.imsave(out / f"{stem}_compare.png", (rgb * 255).astype(np.uint8), check_contrast=False)

    df = pd.DataFrame(rows)
    df.to_csv(out / "comparison.csv", index=False)
    with pd.option_context("display.width", 200, "display.max_columns", 30):
        print(df.T.to_string(header=False))
    print(f"\nwritten: {out / 'comparison.csv'} and *_compare.png ({na} magenta, {nb} green)")
    return df
