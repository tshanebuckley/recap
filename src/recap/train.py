"""Training segmentation models on hand-edited masks, with every version kept for comparison.

Each `train()` call fine-tunes a model (by default the one in the settings, e.g. StarDist
2D_versatile_fluo, or an earlier version) on the workspace's edited masks and saves it as a new
version: recap-data/models/<family>/vN/. Versions are never overwritten. The images are split into
training and validation sets (by image; a single image is split into halves), the new and the
starting model are both scored on the validation set, and everything is recorded in recap.db and
in vN/recap-model.yaml. compare_models() scores all versions on the same images.

The model sees exactly what the pipeline gives it: the combined DAPI/VRC channel, normalised and
rescaled the same way (segmentation.stardist_scale).

StarDist training is tested. Cellpose training follows the Cellpose 4 API but has not been run
here (its weights could not be downloaded in the development environment).
"""

from __future__ import annotations

import copy
import logging
import random
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import yaml

from recap.config import PipelineConfig, load_config
from recap.errors import RecapError
from recap.evaluate import Reference, Score, score_config
from recap.modelpath import use_model_root
from recap.segment import combine
from recap.tune import references
from recap.workspace import Workspace

log = logging.getLogger(__name__)


@dataclass
class TrainResult:
    family: str
    version: int
    path: Path
    metrics: dict[str, Any]  # {"new": scores, "start": scores} on the validation images
    train_images: list[str]
    val_images: list[str]


def model_input(cfg: PipelineConfig, ref: Reference, ws: Workspace | None = None) -> tuple[np.ndarray, np.ndarray]:
    """(model input, mask) for one reference image, prepared as the pipeline prepares it, with the
    image's own batch / image settings (so weak stainings are recovered as they will be at run time)."""
    if ws is not None:
        cfg = ws.resolve(ref.sha256, cfg).cfg
    dapi, vrc, _, mask = ref.load(cfg)
    _, _, comb = combine(dapi, vrc, cfg.normalization)
    return comb.astype(np.float32), mask.astype(np.int32)


def split(refs: list[Reference], val_fraction: float, seed: int | None) -> tuple[list, list]:
    order = refs[:]
    random.Random(seed).shuffle(order)
    if len(order) < 2:
        return order, []  # a single image: split into halves later
    n_val = max(1, round(len(order) * val_fraction))
    return order[n_val:], order[:n_val]


def halves(x: np.ndarray, y: np.ndarray) -> tuple[tuple, tuple]:
    """Top half for training, bottom half for validation (one image only)."""
    h = x.shape[0] // 2
    return (x[:h], y[:h]), (x[h:], y[h:])


def _resolve_start(cfg: PipelineConfig, ws: Workspace, family: str, start: str | None) -> str:
    """The model to start from: a version number of this workspace ("v2" / "2"), a name or path."""
    if start is None:
        return cfg.segmentation.stardist_model if family == "stardist" else (cfg.segmentation.cellpose_model or "")
    v = start.lstrip("v")
    if v.isdigit():
        for m in ws.models(family):
            if m["version"] == int(v):
                return m["path"]
        raise RecapError(f"no {family} version {start} in this workspace")
    return start


def train(
    ws: Workspace,
    family: str = "stardist",
    start: str | None = None,
    epochs: int = 20,
    steps_per_epoch: int = 50,
    val_fraction: float = 0.2,
    seed: int | None = 0,
    note: str | None = None,
    progress: Callable[[str], None] | None = None,
) -> TrainResult:
    """Fine-tune a model on the workspace's corrected masks; save it as a new version."""
    use_model_root(ws.root)  # (its recap-data/models/..., whatever else is open)
    config_path = ws.settings_path
    _, cfg, _ = load_config(config_path)
    refs = references(ws, cfg)
    trn, val = split(refs, val_fraction, seed)
    data_trn = [model_input(cfg, r, ws) for r in trn]
    data_val = [model_input(cfg, r, ws) for r in val]
    if not val:  # one image: top half trains, bottom half validates
        a, b = halves(*data_trn[0])
        data_trn, data_val, val = [a], [b], trn
    version = ws.next_model_version(family)
    out = ws.model_dir(family, version)
    start_model = _resolve_start(cfg, ws, family, start)
    say = progress or (lambda msg: log.info(msg))
    say(f"training {family} v{version} from {start_model or 'default'} on {len(data_trn)} image(s)")
    if family == "stardist":
        _train_stardist(cfg, start_model, out, data_trn, data_val, epochs, steps_per_epoch)
        new_cfg = _with_model(cfg, "stardist", out)
        start_cfg = cfg if start is None else _with_model(cfg, "stardist", Path(start_model))
    elif family == "cellpose":
        model_file = _train_cellpose(start_model, out, data_trn, data_val, epochs)
        new_cfg = _with_model(cfg, "cellpose", model_file)
        start_cfg = cfg if start is None else _with_model(cfg, "cellpose", Path(start_model))
    else:
        raise RecapError(f"unknown model family {family!r} (stardist or cellpose)")
    val_refs = val if len(refs) > 1 else []
    metrics = {
        "validation_images": len(val_refs) or "bottom half of the only image",
        "new": _score_halves(new_cfg, data_val) if not val_refs else score_config(new_cfg, val_refs, ws=ws),
        "start": _score_halves(start_cfg, data_val) if not val_refs else score_config(start_cfg, val_refs, ws=ws),
    }
    info = {
        "family": family,
        "version": version,
        "started_from": start_model,
        "settings": {"epochs": epochs, "steps_per_epoch": steps_per_epoch, "val_fraction": val_fraction,
                     "seed": seed, "config": str(config_path.resolve())},
        "train_images": [r.sha256 for r in trn],
        "val_images": [r.sha256 for r in val],
        "metrics": metrics,
        "note": note,
    }  # fmt: skip
    (out / "recap-model.yaml").write_text(yaml.safe_dump(info, sort_keys=False), encoding="utf-8")
    ws.register_model(
        family, version, out, parent=start_model, settings=info["settings"], train_images=info["train_images"],
        val_images=info["val_images"], metrics=metrics, note=note,
    )  # fmt: skip
    say(f"v{version}: f1 {metrics['new']['f1']} (start model: {metrics['start']['f1']})")
    return TrainResult(family, version, out, metrics, info["train_images"], info["val_images"])


def _with_model(cfg: PipelineConfig, family: str, path: Path) -> PipelineConfig:
    d = cfg.model_dump()
    d["segmentation"]["method"] = family
    d["segmentation"]["stardist_model" if family == "stardist" else "cellpose_model"] = str(path)
    return PipelineConfig.model_validate(d)


def _score_halves(cfg: PipelineConfig, data: list[tuple[np.ndarray, np.ndarray]]) -> dict[str, float]:
    """Score raw model output on prepared (input, mask) pairs (used for the half-image validation)."""
    from recap.segment import model_labels

    seg = cfg.segmentation.resolved()
    score = Score()
    for x, y in data:
        score.add(model_labels(x, seg), y)
    return score.as_dict()


# ---- StarDist ---------------------------------------------------------------------------------
def _patch_size(data: list[tuple[np.ndarray, np.ndarray]], scale: float) -> tuple[int, int]:
    h = min(int(x.shape[0] * scale) for x, _ in data)
    w = min(int(x.shape[1] * scale) for x, _ in data)
    side = max(16, min(256, min(h, w) // 16 * 16))  # square (rotation augmentation); U-Net needs multiples of 16
    return side, side


def _augment(x: np.ndarray, y: np.ndarray):
    k = np.random.randint(4)
    x, y = np.rot90(x, k), np.rot90(y, k)
    if np.random.rand() < 0.5:
        x, y = np.fliplr(x), np.fliplr(y)
    return x * np.random.uniform(0.8, 1.2), y  # brightness jitter


def _train_stardist(cfg, start_model: str, out: Path, data_trn, data_val, epochs: int, steps: int) -> None:
    from csbdeep.utils import normalize
    from skimage.transform import rescale
    from stardist.models import StarDist2D

    from recap.segment import stardist_model

    scale = cfg.segmentation.stardist_scale

    def prep(pairs):
        xs, ys = [], []
        for x, y in pairs:
            x = normalize(x, 1, 99.8).astype(np.float32)  # as stardist_labels() does
            if scale != 1:
                x = rescale(x, scale, order=1, preserve_range=True, anti_aliasing=True).astype(np.float32)
                y = rescale(y, scale, order=0, preserve_range=True, anti_aliasing=False).astype(np.int32)
            xs.append(x)
            ys.append(y)
        return xs, ys

    x_trn, y_trn = prep(data_trn)
    x_val, y_val = prep(data_val)
    base = stardist_model(start_model)
    conf = copy.deepcopy(base.config)
    conf.train_patch_size = _patch_size(data_trn + data_val, scale)
    conf.train_epochs, conf.train_steps_per_epoch = epochs, steps
    conf.train_batch_size = 2
    conf.train_learning_rate = 1e-4  # fine-tuning: smaller steps than training from scratch
    conf.train_reduce_lr = {"factor": 0.5, "patience": max(2, epochs // 4), "min_delta": 0}
    out.parent.mkdir(parents=True, exist_ok=True)
    model = StarDist2D(conf, name=out.name, basedir=str(out.parent))
    model.keras_model.set_weights(base.keras_model.get_weights())
    model.train(x_trn, y_trn, validation_data=(x_val, y_val), augmenter=_augment, epochs=epochs,
                steps_per_epoch=steps)  # fmt: skip
    model.optimize_thresholds(x_val, y_val)  # writes thresholds.json next to the weights


# ---- Cellpose (not run in development; see module docstring) ----------------------------------
def _train_cellpose(start_model: str, out: Path, data_trn, data_val, epochs: int) -> Path:
    from cellpose import models
    from cellpose import train as cp_train

    kw = {"pretrained_model": start_model} if start_model else {}  # default: Cellpose-SAM
    model = models.CellposeModel(gpu=False, **kw)
    out.mkdir(parents=True, exist_ok=True)
    path, *_ = cp_train.train_seg(
        model.net,
        train_data=[x for x, _ in data_trn],
        train_labels=[y for _, y in data_trn],
        test_data=[x for x, _ in data_val],
        test_labels=[y for _, y in data_val],
        n_epochs=epochs,
        learning_rate=1e-5,
        weight_decay=0.1,
        batch_size=1,
        save_path=str(out),
        model_name=out.name,
    )
    return Path(path)


# ---- comparing versions -------------------------------------------------------------------------
def compare_models(
    ws: Workspace, family: str = "stardist", progress: Callable[[str], None] | None = None
) -> list[dict[str, Any]]:
    """Score the settings' model and every saved version on all images with edited masks.

    Images a version was trained on are counted separately (n_trained_on): its scores on those
    are optimistic. Results are also stored with each version in the workspace."""
    _, cfg, _ = load_config(ws.settings_path)
    use_model_root(ws.root)  # (its recap-data/models/..., whatever else is open)
    refs = references(ws, cfg)
    candidates = [("settings: " + (cfg.segmentation.stardist_model if family == "stardist"
                                   else cfg.segmentation.cellpose_model or "cpsam"), None, cfg, set())]  # fmt: skip
    for m in ws.models(family):
        candidates.append((f"v{m['version']}", m, _with_model(cfg, family, Path(m["path"])), set(m["train_images"])))
    rows = []
    for name, m, c, trained in candidates:
        if progress:
            progress(f"scoring {name}")
        unseen = [r for r in refs if r.sha256 not in trained]
        row = {
            "model": name,
            "n_images": len(refs),
            "n_trained_on": len(refs) - len(unseen),
            **score_config(c, refs, ws=ws),
        }
        if unseen and len(unseen) < len(refs):
            row["f1_unseen_only"] = score_config(c, unseen, ws=ws)["f1"]
        rows.append(row)
        if m is not None:
            ws.set_model_metrics(family, m["version"], {**(m["metrics"] or {}), "compare": row})
    return rows
