"""A workspace: a folder of images with one settings file and one database.

    my-experiment/
        recap.yaml                 THE settings of this workspace (every image; batches and single
                                   images may change some values, stored in recap.db)
        *.tif                      the images (anywhere below the folder)
        results/...                outputs of `recap run` (output.directory in recap.yaml)
        recap-data/
            recap.db               SQLite index (recap/db.py): images, runs, counts, masks, models, tuning
            backups/               copies made before each upgrade to a new recap version
            masks/                 hand-edited masks (*_edited_N.tif)
            fiji/                  Fiji counting sessions
            models/<family>/vN/    trained model versions (never overwritten)
            tuning/                tuning results

The database is the index that answers "what has been done to which image": it is rebuilt from
the files by scan() (images, runs, manual_counts CSVs), and is the only store of counts and masks
made in the app or with `recap count`. Images are identified by content (SHA-256), so renamed or
moved files keep their history. Paths inside the folder are stored relative to it, so the whole
folder can be moved or copied to another computer.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import tifffile
import yaml
from sqlalchemy import func
from sqlmodel import Session, select

from recap.counts import ManualCount, default_counter_name, read_manual_counts
from recap.db import (
    BatchRow,
    CountRow,
    ImageBatchRow,
    ImageRow,
    LevelSettingsRow,
    MaskRow,
    ModelRow,
    ResultRow,
    RunRow,
    TuningRow,
    open_database,
)
from recap.errors import RecapError
from recap.io import file_sha256

log = logging.getLogger(__name__)

DATA_DIR = "recap-data"
DB_NAME = "recap.db"
SETTINGS_NAME = "recap.yaml"
IMAGE_SUFFIXES = (".tif", ".tiff")
DERIVED_SUFFIXES = ("_labels", "_excluded_labels")  # recap outputs, not images to analyse


def now() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def image_metadata(path: Path) -> dict[str, Any]:
    """Shape, type and pixel size from the TIFF header (pixels are not read)."""
    with tifffile.TiffFile(path) as tf:
        series = tf.series[0]
        shape, axes = series.shape, series.axes
        page = tf.pages[0]
        pixel_size = None
        try:
            xres = page.tags["XResolution"].value
            unit = page.tags.get("ResolutionUnit")
            if xres and xres[0]:
                px = xres[1] / xres[0]  # units per pixel
                unit_val = getattr(unit.value, "value", unit.value) if unit is not None else 1
                if tf.imagej_metadata and tf.imagej_metadata.get("unit") in ("micron", "um", "µm"):
                    pixel_size = px
                elif unit_val == 3:  # centimetre
                    pixel_size = px * 1e4
        except (KeyError, ZeroDivisionError, TypeError, AttributeError):
            pass
    height, width = shape[axes.index("Y")], shape[axes.index("X")]
    other = [n for a, n in zip(axes, shape) if a not in "YX"]
    if "S" in axes or "C" in axes:  # RGB samples or labelled channels
        channels = shape[axes.index("S" if "S" in axes else "C")]
    else:  # unlabelled extra axis (e.g. a plain (2, y, x) stack) = channels
        channels = other[0] if len(other) == 1 else 1
    return dict(width=width, height=height, channels=channels, dtype=str(series.dtype), pixel_size=pixel_size)


@dataclass
class AddResult:
    added: list[Path]  # new images now in the workspace
    already: list[Path]  # chosen files whose content was already in the workspace
    copied: list[Path]  # where copies were made (subset of added)
    batch: str | None
    folder: Path  # where copies go

    def message(self) -> str:
        parts = [f"{len(self.added)} image(s) added"]
        if self.copied:
            parts.append(f"copied to {self.folder.name}/")
        if self.already:
            parts.append(f"{len(self.already)} already in the workspace")
        if self.batch:
            parts.append(f"batch {self.batch!r}")
        return ", ".join(parts) + "."


def image_files_in(sources: list[Path]) -> list[Path]:
    """Image files among the given files and in the given folders (recap outputs and recap-data skipped)."""
    out: list[Path] = []
    for src in sources:
        src = Path(src)
        cands = [src] if src.is_file() else sorted(p for p in src.rglob("*") if p.is_file())
        for p in cands:
            if p.suffix.lower() not in IMAGE_SUFFIXES or DATA_DIR in p.parts:
                continue
            if p.stem.endswith(DERIVED_SUFFIXES) or "_edited_" in p.stem or p.stem.endswith("_for_counting"):
                continue
            out.append(p)
    seen, unique = set(), []
    for p in out:
        if p.resolve() not in seen:
            seen.add(p.resolve())
            unique.append(p)
    return unique


def safe_folder_name(name: str) -> str:
    keep = "".join(c if c.isalnum() or c in " -_." else "_" for c in name).strip(" .")
    return keep or "images"


def unique_path(p: Path) -> Path:
    """p, or p with _2, _3 ... before the suffix if a file of that name exists."""
    k, out = 1, p
    while out.exists():
        k += 1
        out = p.with_name(f"{p.stem}_{k}{p.suffix}")
    return out


def workspace_root(path: Path) -> Path | None:
    """The workspace a file or folder belongs to: the nearest folder at or above it with a
    recap.yaml or a recap-data/recap.db. None if there is none."""
    p = Path(path).resolve()
    if p.is_file() and p.name == SETTINGS_NAME:
        return p.parent
    start = p if p.is_dir() else p.parent
    for d in (start, *start.parents):
        if (d / SETTINGS_NAME).is_file() or (d / DATA_DIR / DB_NAME).is_file():
            return d
    return None


def open_workspace(path: Path) -> Workspace:
    """The workspace that a folder, its recap.yaml or one of its images belongs to."""
    root = workspace_root(path)
    if root is None:
        raise RecapError(
            f"{path} is not in a recap workspace. Make its folder one first: recap init FOLDER "
            "(or New... in the recap panel in the app)."
        )
    return Workspace(root)


def init_workspace(folder: Path, settings_from: Path | None = None, force: bool = False) -> Workspace:
    """Make a folder a workspace: recap.yaml (a copy of settings_from, else the template) and
    recap-data/. An existing recap.yaml is kept unless force."""
    from recap.config import load_config, workspace_template

    folder = Path(folder).resolve()
    if not folder.is_dir():
        raise RecapError(f"folder not found: {folder}")
    target = folder / SETTINGS_NAME
    if target.exists() and not force and settings_from is not None:
        raise RecapError(f"{target} exists already (use --force to replace it).")
    if settings_from is not None:
        settings_from = Path(settings_from).resolve()
        raw, cfg, _ = load_config(settings_from)
        text = settings_from.read_text(encoding="utf-8")
        target.write_text(text, encoding="utf-8")
        from recap.settingsfile import set_values

        fixes: dict[str, Any] = {"input.images": []}  # a relative output folder now lives in the workspace
        if cfg.validation.fiji and not Path(cfg.validation.fiji).is_absolute():
            fixes["validation.fiji"] = _relative_to(settings_from.parent / cfg.validation.fiji, folder)
        prev = cfg.validation.previous_counts
        if prev is not None and not prev.is_absolute():
            fixes["validation.previous_counts"] = _relative_to(settings_from.parent / prev, folder)
        set_values(target, fixes)
        if cfg.input.images:
            log.info("input.images of %s replaced by [] (every image in the workspace)", settings_from.name)
    elif not target.exists() or force:
        target.write_text(workspace_template(), encoding="utf-8")
    ws = Workspace(folder)
    ws.scan()
    return ws


def _relative_to(p: Path, base: Path) -> str:
    import os

    try:
        return Path(os.path.relpath(p.resolve(), base)).as_posix()
    except ValueError:  # another drive on Windows
        return p.resolve().as_posix()


class Workspace:
    def __init__(self, root: Path):
        from recap import __version__

        self.root = Path(root).resolve()
        if not self.root.is_dir():
            raise RecapError(f"workspace folder not found: {root}")
        self.data = self.root / DATA_DIR
        self.data.mkdir(exist_ok=True)
        self.db_path = self.data / DB_NAME
        self.settings_path = self.root / SETTINGS_NAME
        # backs up and upgrades a database from an older recap; self.upgrade says what was done (or None)
        self.engine, self.upgrade = open_database(self.db_path, __version__)
        self._fold_workspace_level()

    # ---- the settings file -------------------------------------------------------------------------
    @property
    def has_settings(self) -> bool:
        return self.settings_path.is_file()

    def settings(self):
        """recap.yaml as validated settings. Raises RecapError if the workspace has none yet."""
        from recap.config import load_config

        if not self.has_settings:
            raise RecapError(
                f"{self.root.name} has no settings file ({SETTINGS_NAME}) yet. Create it: "
                f"recap init {self.root} --from <your settings file>, or open the folder in the app."
            )
        try:
            return load_config(self.settings_path)[1]
        except (ValueError, OSError) as e:
            raise RecapError(f"{self.settings_path} is invalid:\n{e}") from e

    def output_dir(self, cfg=None) -> Path:
        d = (cfg or self.settings()).output.directory
        return d if d.is_absolute() else self.root / d

    def images(self, cfg=None) -> list[Path]:
        """The workspace's images: every image in the folder, or those matching input.images."""
        cfg = cfg or self.settings()
        files = self.image_files()
        if not cfg.input.images:
            return files
        import fnmatch

        pats = [Path(p).as_posix() for p in cfg.input.images]
        return [p for p in files if any(fnmatch.fnmatch(self.rel(p), q) or fnmatch.fnmatch(str(p), q) for q in pats)]

    def fiji(self):
        """Where Fiji is, from recap.yaml (validation.fiji, relative to the workspace) or the usual places."""
        from recap.fiji import resolve_fiji

        setting = self.settings().validation.fiji if self.has_settings else None
        try:
            return resolve_fiji(setting, self.root)
        except ValueError as e:
            raise RecapError(str(e)) from e

    def _fold_workspace_level(self) -> None:
        """recap 4.2 kept workspace-wide overrides in the database; they belong in recap.yaml."""
        if not self.has_settings:
            return
        old = self.level_settings("workspace")
        if not old:
            return
        from recap.settingsfile import set_values

        set_values(self.settings_path, old)
        with self.session() as s:
            for row in s.exec(select(LevelSettingsRow).where(LevelSettingsRow.level == "workspace")):
                s.delete(row)
            s.commit()
        log.warning("Workspace settings from the database moved into %s: %s", self.settings_path.name, old)

    @classmethod
    def from_db(cls, db_path: Path) -> Workspace:
        return cls(Path(db_path).resolve().parent.parent)

    def session(self) -> Session:
        return Session(self.engine, expire_on_commit=False)

    # ---- paths: relative inside the workspace, absolute outside --------------------------------
    def rel(self, p: Path | str) -> str:
        p = Path(p).resolve()
        try:
            return p.relative_to(self.root).as_posix()
        except ValueError:
            return str(p)

    def abs(self, s: str | None) -> Path | None:
        if not s:
            return None
        p = Path(s)
        return p if p.is_absolute() else self.root / p

    def folder(self, name: str) -> Path:
        d = self.data / name
        d.mkdir(parents=True, exist_ok=True)
        return d

    # ---- scanning ------------------------------------------------------------------------------
    def image_files(self) -> list[Path]:
        out = []
        for p in sorted(self.root.rglob("*")):
            if p.suffix.lower() not in IMAGE_SUFFIXES or DATA_DIR in p.relative_to(self.root).parts:
                continue
            if p.stem.endswith(DERIVED_SUFFIXES) or "_edited_" in p.stem:
                continue
            out.append(p)
        return out

    def scan(self) -> dict[str, int]:
        """Index images, run outputs and manual_counts CSVs below the folder. Unchanged files are skipped."""
        n_img = n_runs = n_counts = 0
        with self.session() as s:
            known = {r.path: r for r in s.exec(select(ImageRow))}
            for p in self.image_files():
                st = p.stat()
                k = known.get(self.rel(p))
                if k and k.size_bytes == st.st_size and k.mtime == st.st_mtime:
                    continue
                try:
                    meta = image_metadata(p)
                except Exception as e:  # noqa: BLE001  (not a readable image: skip it)
                    log.warning("skipped %s: %s", p, e)
                    continue
                s.merge(ImageRow(sha256=file_sha256(p), path=self.rel(p), name=p.name, size_bytes=st.st_size,
                                 mtime=st.st_mtime, seen_at=now(), **meta))  # fmt: skip
                n_img += 1
            s.commit()
        logs = set(self.root.rglob("run_log_*.yaml"))
        try:
            out = self.output_dir()
            if out.is_dir() and not out.resolve().is_relative_to(self.root):  # outputs kept outside the folder
                logs |= set(out.glob("run_log_*.yaml"))
        except (RecapError, ValueError):
            pass
        for log_file in sorted(logs):
            n_runs += self._index_run(log_file)
        for csv in sorted(self.root.rglob("manual_counts*.csv")):
            if DATA_DIR in csv.relative_to(self.root).parts:
                continue
            try:
                for mc in read_manual_counts(csv):
                    n_counts += self.add_count(mc.model_copy(update={"origin": mc.origin or self.rel(csv)}))
            except Exception as e:  # noqa: BLE001
                log.warning("skipped %s: %s", csv, e)
        return {"images": n_img, "runs": n_runs, "counts": n_counts}

    def _sha_for(self, path: str) -> str | None:
        p = Path(path)
        with self.session() as s:
            row = s.exec(select(ImageRow).where(ImageRow.path == self.rel(p))).first()
        if row:
            return row.sha256
        return file_sha256(p) if p.is_file() else None

    def record_run(self, log_file: Path) -> int:
        """Record a run from its run log (also done by scan for logs inside the folder)."""
        return self._index_run(Path(log_file))

    def _index_run(self, log_file: Path) -> int:
        run_id = self.rel(log_file)
        with self.session() as s:
            if s.get(RunRow, run_id) is not None:
                return 0
        try:
            rl = yaml.safe_load(log_file.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as e:
            log.warning("skipped %s: %s", log_file, e)
            return 0
        run, seg = rl.get("run", {}), (rl.get("final_config") or {}).get("segmentation", {})
        model = seg.get("stardist_model") if seg.get("method") == "stardist" else seg.get("cellpose_model")
        rows = []
        for d in rl.get("images") or []:
            if "n_nuclei" not in d:
                continue
            sha = self._sha_for(d["image"])
            if sha is None:
                continue
            outs = d.get("outputs", {})
            rows.append(
                ResultRow(
                    run_id=run_id, sha256=sha, image=d["image"], n_nuclei=d["n_nuclei"], n_infected=d["n_infected"],
                    n_nuclei_low=d.get("n_nuclei_low"), n_nuclei_high=d.get("n_nuclei_high"),
                    n_infected_low=d.get("n_infected_low"), n_infected_high=d.get("n_infected_high"),
                    qc_flags=", ".join(d.get("qc_flags") or []),
                    labels_path=self.rel(outs["labels"]) if outs.get("labels") else None,
                    excluded_path=self.rel(outs["excluded_labels"]) if outs.get("excluded_labels") else None,
                    table_path=self.rel(outs["table"]) if outs.get("table") else None,
                    settings_hash=d.get("settings_hash"),
                )
            )  # fmt: skip
        with self.session() as s:
            s.add(RunRow(run_id=run_id, out_dir=self.rel(log_file.parent), stamp=run.get("timestamp"),
                         status=run.get("status"), config_path=(rl.get("config_file") or {}).get("path"),
                         method=seg.get("method"), model=model, recap_version=run.get("version"),
                         log_path=self.rel(log_file)))  # fmt: skip
            for r in rows:
                s.merge(r)
            s.commit()
        return 1

    # ---- what the app shows ----------------------------------------------------------------------
    def status(self) -> list[dict[str, Any]]:
        """One row per image: metadata, latest result, manual counts and edited masks."""
        with self.session() as s:
            images = s.exec(select(ImageRow).order_by(ImageRow.path)).all()
            latest: dict[str, tuple] = {}
            q = select(ResultRow, RunRow).join(RunRow, ResultRow.run_id == RunRow.run_id).order_by(RunRow.stamp)
            for res, run in s.exec(q):
                latest[res.sha256] = (res, run)  # later runs overwrite earlier ones: the latest wins
            counts: dict[str, list[CountRow]] = {}
            for c in s.exec(select(CountRow).order_by(CountRow.counted_at)):
                counts.setdefault(c.sha256, []).append(c)
            masks = dict(s.exec(select(MaskRow.sha256, func.count()).group_by(MaskRow.sha256)).all())
            batches = self.image_batches()
            own = {r.target for r in s.exec(select(LevelSettingsRow).where(LevelSettingsRow.level == "image"))}
        current = self.current_hashes([im.sha256 for im in images])
        rows = []
        for im in images:
            res, run = latest.get(im.sha256, (None, None))
            cs = counts.get(im.sha256, [])
            if res is None:
                state = "not run"
            elif current.get(im.sha256) is None:
                state = "unknown"  # no valid recap.yaml to compare with
            elif res.settings_hash == current[im.sha256]:
                state = "up to date"
            else:
                state = "out of date"  # (results from before 4.3 have no fingerprint: out of date too)
            rows.append(
                {
                    "name": im.name,
                    "path": im.path,
                    "sha256": im.sha256,
                    "size": f"{im.width}x{im.height}x{im.channels}",
                    "segmented": run.stamp if run else None,
                    "method": run.method if run else None,
                    "n_nuclei": res.n_nuclei if res else None,
                    "n_infected": res.n_infected if res else None,
                    "qc_flags": res.qc_flags if res else None,
                    "manual_counts": len(cs),
                    "counted_by": ", ".join(sorted({c.counted_by or "?" for c in cs})) or None,
                    "manual_nuclei": cs[-1].nuclei if cs else None,
                    "edited_masks": masks.get(im.sha256, 0),
                    "batch": batches.get(im.sha256),
                    "own_settings": im.sha256 in own,
                    "state": state,  # up to date | out of date (settings changed since the run) | not run | unknown
                }
            )
        return rows

    def image(self, sha: str) -> dict[str, Any]:
        """Everything known about one image (for its summary page)."""
        with self.session() as s:
            im = s.get(ImageRow, sha)
            if im is None:
                raise RecapError(f"image {sha[:12]} is not in this workspace")
            q = (
                select(ResultRow, RunRow)
                .join(RunRow, ResultRow.run_id == RunRow.run_id)
                .where(ResultRow.sha256 == sha)
                .order_by(RunRow.stamp)
            )
            results = [
                {**r.model_dump(), "stamp": run.stamp, "method": run.method, "model": run.model,
                 "config_path": run.config_path}
                for r, run in s.exec(q)
            ]  # fmt: skip
            masks = [m.model_dump() for m in s.exec(select(MaskRow).where(MaskRow.sha256 == sha).order_by(MaskRow.id))]
        return {"image": im.model_dump(), "results": results, "counts": self.counts(sha), "masks": masks}

    def image_path(self, sha: str) -> Path:
        return self.abs(self.image(sha)["image"]["path"])

    # ---- manual counts ---------------------------------------------------------------------------
    def add_count(self, mc: ManualCount) -> int:
        """Store a manual count; returns 1 if new, 0 if this exact count was already stored."""
        by = mc.counted_by or ""
        with self.session() as s:
            dup = s.exec(
                select(CountRow).where(
                    CountRow.sha256 == mc.sha256, CountRow.counted_by == by, CountRow.counted_at == mc.counted_at
                )
            ).first()
            if dup is not None:
                return 0
            s.add(CountRow(sha256=mc.sha256, image=mc.image, image_name=mc.image_name, counted_by=by, tool=mc.tool,
                           source=mc.source, counted_at=mc.counted_at, nuclei=mc.nuclei, infected=mc.infected,
                           origin=mc.origin, points=[list(p) for p in mc.points]))  # fmt: skip
            s.commit()
        return 1

    def counts(self, sha: str | None = None) -> list[ManualCount]:
        q = select(CountRow).order_by(CountRow.counted_at)
        if sha:
            q = q.where(CountRow.sha256 == sha)
        with self.session() as s:
            rows = s.exec(q).all()
        out = []
        for r in rows:
            d = r.model_dump(exclude={"id"})
            d["points"] = [tuple(p) for p in (r.points or [])]
            d["counted_by"] = r.counted_by or None
            out.append(ManualCount.model_validate(d))
        return out

    # ---- edited masks --------------------------------------------------------------------------------
    def save_mask(
        self, sha: str, labels: np.ndarray, edited_by: str | None = None, from_run: str | None = None, note=None
    ) -> Path:
        """Save a hand-edited mask as a new file (earlier versions are kept) and record it."""
        name = Path(self.image(sha)["image"]["name"]).stem
        d = self.folder("masks")
        k = 1 + sum(1 for _ in d.glob(f"{name}__{sha[:8]}_edited_*.tif"))
        path = d / f"{name}__{sha[:8]}_edited_{k}.tif"
        tifffile.imwrite(path, np.asarray(labels).astype(np.uint16))
        n = int(len(np.unique(labels)) - (1 if (labels == 0).any() else 0))
        with self.session() as s:
            by = edited_by or default_counter_name()
            s.add(MaskRow(sha256=sha, path=self.rel(path), from_run=from_run, edited_by=by, edited_at=now(),
                          n_nuclei=n, note=note))  # fmt: skip
            s.commit()
        return path

    def reference_masks(self) -> dict[str, Path]:
        """Latest edited mask per image: the ground truth for tuning, training and comparing models."""
        with self.session() as s:
            rows = s.exec(select(MaskRow).order_by(MaskRow.id)).all()
        return {r.sha256: self.abs(r.path) for r in rows}

    def latest_masks(self, sha: str) -> tuple[Path | None, Path | None, Path | None]:
        """(edited mask, program mask, program excluded mask) for an image; None where missing."""
        info = self.image(sha)
        edited = self.abs(info["masks"][-1]["path"]) if info["masks"] else None
        res = info["results"][-1] if info["results"] else {}
        return edited, self.abs(res.get("labels_path")), self.abs(res.get("excluded_path"))

    # ---- models and tuning ---------------------------------------------------------------------------
    def next_model_version(self, family: str) -> int:
        with self.session() as s:
            v = s.exec(select(func.max(ModelRow.version)).where(ModelRow.family == family)).one()
        return (v or 0) + 1

    def model_dir(self, family: str, version: int) -> Path:
        return self.folder(f"models/{family}") / f"v{version}"

    def register_model(self, family: str, version: int, path: Path, parent: str, **info: Any) -> None:
        with self.session() as s:
            s.add(ModelRow(family=family, version=version, path=self.rel(path), parent=parent, created_at=now(),
                           settings=info.get("settings", {}), train_images=info.get("train_images", []),
                           val_images=info.get("val_images", []), metrics=info.get("metrics", {}),
                           note=info.get("note")))  # fmt: skip
            s.commit()

    def set_model_metrics(self, family: str, version: int, metrics: dict[str, Any]) -> None:
        with self.session() as s:
            row = s.exec(select(ModelRow).where(ModelRow.family == family, ModelRow.version == version)).one()
            row.metrics = metrics
            s.add(row)
            s.commit()

    def models(self, family: str | None = None) -> list[dict[str, Any]]:
        q = select(ModelRow).order_by(ModelRow.family, ModelRow.version)
        if family:
            q = q.where(ModelRow.family == family)
        with self.session() as s:
            rows = [r.model_dump() for r in s.exec(q)]
        for r in rows:
            r["path"] = str(self.abs(r["path"]))
        return rows

    def add_tuning(self, session: str, config_path: Path, params: dict, scores: dict) -> None:
        with self.session() as s:
            s.add(TuningRow(session=session, created_at=now(), config_path=str(config_path), params=params,
                            scores=scores, f1=scores.get("f1")))  # fmt: skip
            s.commit()

    def tuning(self, session: str | None = None) -> list[dict[str, Any]]:
        q = select(TuningRow).order_by(TuningRow.f1.desc())
        if session:
            q = q.where(TuningRow.session == session)
        with self.session() as s:
            return [r.model_dump() for r in s.exec(q)]

    # ---- staining batches and settings per level -------------------------------------------------
    def batches(self) -> list[dict[str, Any]]:
        """Batches with their number of images."""
        with self.session() as s:
            rows = s.exec(select(BatchRow).order_by(BatchRow.name)).all()
            n = dict(s.exec(select(ImageBatchRow.batch_id, func.count()).group_by(ImageBatchRow.batch_id)).all())
        return [{**b.model_dump(), "n_images": n.get(b.id, 0)} for b in rows]

    def image_batches(self) -> dict[str, str]:
        """sha256 -> batch name."""
        with self.session() as s:
            q = select(ImageBatchRow.sha256, BatchRow.name).join(BatchRow, ImageBatchRow.batch_id == BatchRow.id)
            return dict(s.exec(q).all())

    def assign_batch(self, shas: list[str], name: str | None, note: str | None = None) -> None:
        """Put images in a batch (created if new); name None takes them out of any batch."""
        with self.session() as s:
            for sha in shas:
                old = s.get(ImageBatchRow, sha)
                if old is not None:
                    s.delete(old)
            s.flush()
            if name:
                batch = s.exec(select(BatchRow).where(BatchRow.name == name)).first()
                if batch is None:
                    batch = BatchRow(name=name, note=note, created_at=now())
                    s.add(batch)
                    s.flush()
                for sha in shas:
                    s.add(ImageBatchRow(sha256=sha, batch_id=batch.id))
            s.commit()

    def rename_batch(self, old: str, new: str) -> None:
        with self.session() as s:
            if s.exec(select(BatchRow).where(BatchRow.name == new)).first() is not None:
                raise RecapError(f"a batch named {new!r} exists already")
            b = s.exec(select(BatchRow).where(BatchRow.name == old)).one()
            b.name = new
            s.add(b)
            for row in s.exec(select(LevelSettingsRow).where(LevelSettingsRow.level == "batch",
                                                             LevelSettingsRow.target == old)):  # fmt: skip
                row.target = new
                s.add(row)
            s.commit()

    def delete_batch(self, name: str) -> None:
        """Remove a batch and its settings; its images stay, in no batch."""
        with self.session() as s:
            b = s.exec(select(BatchRow).where(BatchRow.name == name)).first()
            if b is None:
                return
            for row in s.exec(select(ImageBatchRow).where(ImageBatchRow.batch_id == b.id)):
                s.delete(row)
            for row in s.exec(select(LevelSettingsRow).where(LevelSettingsRow.level == "batch",
                                                             LevelSettingsRow.target == name)):  # fmt: skip
                s.delete(row)
            s.delete(b)
            s.commit()

    def level_settings(self, level: str, target: str = "") -> dict[str, Any]:
        """The overrides stored for a batch or an image ({} if none)."""
        with self.session() as s:
            row = s.exec(
                select(LevelSettingsRow).where(LevelSettingsRow.level == level, LevelSettingsRow.target == target)
            ).first()
        return dict(row.overrides or {}) if row else {}

    def level_reason(self, sha: str) -> str | None:
        with self.session() as s:
            row = s.exec(
                select(LevelSettingsRow).where(LevelSettingsRow.level == "image", LevelSettingsRow.target == sha)
            ).first()
        return row.reason if row else None

    def set_level_settings(
        self, level: str, target: str, overrides: dict[str, Any], reason: str | None = None, by: str | None = None
    ) -> None:
        """Replace a batch's or an image's overrides ({} removes them). An image needs a reason.
        The workspace level is recap.yaml: see update_level."""
        from recap.levels import check_overrides

        if level not in ("batch", "image"):
            raise RecapError("set_level_settings stores batch and image settings; the workspace's are in recap.yaml")
        if level == "image" and overrides and not (reason or self.level_reason(target) or "").strip():
            raise RecapError("Settings for a single image need a reason (e.g. 'weak DAPI staining').")
        check_overrides(overrides)
        with self.session() as s:
            row = s.exec(
                select(LevelSettingsRow).where(LevelSettingsRow.level == level, LevelSettingsRow.target == target)
            ).first()
            if not overrides:
                if row is not None:
                    s.delete(row)
            else:
                row = row or LevelSettingsRow(level=level, target=target)
                row.overrides, row.reason = dict(overrides), reason or row.reason
                row.updated_by, row.updated_at = by or default_counter_name(), now()
                s.add(row)
            s.commit()

    def update_level(
        self,
        level: str,
        target: str = "",
        values: dict[str, Any] | None = None,
        remove: list[str] | tuple = (),
        reason: str | None = None,
        by: str | None = None,
    ) -> dict[str, Any]:
        """Set some values at a level and remove others (the rest of that level is kept).
        workspace: written into recap.yaml (its comments kept); batch / image: recap.db.
        Returns what the level now holds (for the workspace: the values written)."""
        from recap.levels import check_overrides

        values = dict(values or {})
        check_overrides(values)
        if level == "workspace":
            if remove:
                raise RecapError("The workspace's settings are recap.yaml: set a value instead of removing it.")
            if values:
                from recap.settingsfile import set_values

                self.settings()  # a clear message if there is no recap.yaml
                set_values(self.settings_path, values)
            return values
        stored = {k: v for k, v in self.level_settings(level, target).items() if k not in remove}
        stored.update(values)
        self.set_level_settings(level, target, stored, reason, by)
        return stored

    def save_effective(self, level: str, sha: str, cfg, keys: list[str], reason: str | None = None):
        """Store the values of `keys` in cfg (an image's settings as edited, e.g. in Guided mode) at
        one level: workspace, the image's batch, or the image. Only what differs from the level
        above is stored; keys of this level that now equal the level above are removed.
        Returns (what the level holds now, keys a more specific level still overrides for this image)."""
        from recap.levels import get_path, resolve

        base = self.settings()
        batch = self.image_batches().get(sha)
        above = []
        if level == "image" and batch:
            above.append((f"batch:{batch}", self.level_settings("batch", batch)))
        if level == "batch" and not batch:
            raise RecapError("This image is in no batch: put it in one first.")
        parent = (
            resolve(base, above).cfg.model_dump(mode="json") if level != "workspace" else base.model_dump(mode="json")
        )
        new = cfg.model_dump(mode="json")
        changed = {k: get_path(new, k) for k in keys if get_path(new, k) != get_path(parent, k)}
        same = [k for k in keys if k not in changed]
        target = {"workspace": "", "batch": batch or "", "image": sha}[level]
        if level == "workspace":
            held = self.update_level("workspace", "", changed)
        else:
            held = self.update_level(level, target, changed, remove=same, reason=reason)
        below = []
        if level == "workspace" and batch:
            below += list(self.level_settings("batch", batch))
        if level in ("workspace", "batch"):
            below += list(self.level_settings("image", sha))
        return held, sorted(set(below) & set(keys))

    def resolve(self, sha: str, base=None):
        """An image's effective settings: recap.yaml (or base) → its batch → itself."""
        from recap.levels import resolve

        base = base or self.settings()
        if not base.input.use_workspace_settings:
            return resolve(base, [])
        batch = self.image_batches().get(sha)
        levels = []
        if batch:
            levels.append((f"batch:{batch}", self.level_settings("batch", batch)))
        levels.append(("image", self.level_settings("image", sha)))
        r = resolve(base, levels)
        r.batch, r.reason = batch, self.level_reason(sha)
        return r

    def current_hashes(self, shas: list[str]) -> dict[str, str | None]:
        """Each image's settings fingerprint now (None for all if there is no valid recap.yaml)."""
        from recap.levels import settings_hash

        try:
            base = self.settings()
        except RecapError:
            return {}
        out = {}
        for sha in shas:
            try:
                out[sha] = settings_hash(self.resolve(sha, base).cfg)
            except (RecapError, ValueError):
                out[sha] = None
        return out

    def out_of_date(self) -> list[str]:
        """Images not run yet or run with settings that have changed since."""
        return [r["sha256"] for r in self.status() if r["state"] in ("not run", "out of date")]

    def run_folders(self) -> list[tuple[str, Path]]:
        """(name, folder) of every output folder with runs, named by the folder (most recent last)."""
        with self.session() as s:
            runs = s.exec(select(RunRow).order_by(RunRow.stamp)).all()
        seen: dict[str, Path] = {}
        for r in runs:
            d = self.abs(r.out_dir)
            if d is not None and d.is_dir():
                seen.pop(r.out_dir, None)
                seen[r.out_dir] = d
        return [(d.name, d) for d in seen.values()]

    def report(self, out: Path | None = None, iou: float = 0.5):
        """`recap report` for this workspace: its run folders against its manual counts."""
        from recap.report import build_report

        runs = self.run_folders()
        if not runs:
            raise RecapError("No runs in this workspace yet: run recap first (recap run, or Run in the app).")
        out = Path(out) if out else self.folder("reports") / dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        return build_report(runs, [self.db_path], out, iou)

    # ---- adding images -----------------------------------------------------------------------------
    def add_images(
        self,
        sources: list[Path],
        batch: str | None = None,
        subfolder: str | None = None,
        note: str | None = None,
        progress=None,
    ) -> AddResult:
        """Bring image files, or every image in folders, into the workspace and (optionally) a batch.

        Files outside the workspace are copied into `subfolder` (default: the batch's name, else
        "images"); files already inside are used where they are. An image whose content is
        already in the workspace is not copied again. `batch` is created if new; None = no batch.
        `recap add` and the app's Add images both use this."""
        import shutil

        files = image_files_in(sources)
        if not files:
            raise RecapError("No .tif / .tiff images found in what was chosen.")
        known = {r["sha256"]: r["name"] for r in self.status()}
        dest = self.root / safe_folder_name(subfolder or batch or "images")
        added, existing, moved = [], [], []
        shas = []
        for k, src in enumerate(files, 1):
            if progress:
                progress(k, len(files), src)
            sha = file_sha256(src)
            if sha in known or sha in shas:
                existing.append(src)
                shas.append(sha)
                continue
            if src.resolve().is_relative_to(self.root):
                target = src  # already in the folder: index it where it is
            else:
                dest.mkdir(parents=True, exist_ok=True)
                target = unique_path(dest / src.name)
                shutil.copy2(src, target)
                moved.append(target)
            added.append(target)
            shas.append(sha)
        self.scan()
        if batch:
            self.assign_batch(shas, batch, note)
        return AddResult(added, existing, moved, batch, dest)

    # ---- models in use -------------------------------------------------------------------------------
    def use_model(self, family: str, version: int | None) -> str:
        """Segment the whole workspace with a trained version (None: the standard model) by writing
        it into recap.yaml. Returns the model setting written."""
        from recap.settingsfile import set_values

        if version is None:
            value = "2D_versatile_fluo" if family == "stardist" else None
        else:
            m = next((m for m in self.models(family) if m["version"] == version), None)
            if m is None:
                raise RecapError(f"no {family} version v{version} in this workspace")
            value = self.rel(m["path"])
        key = "segmentation.stardist_model" if family == "stardist" else "segmentation.cellpose_model"
        set_values(self.settings_path, {"segmentation.method": family, key: value})
        return value

    def model_in_use(self, family: str) -> int | None:
        """The version recap.yaml uses for this family, or None (a standard or outside model)."""
        cfg = self.settings()
        current = cfg.segmentation.stardist_model if family == "stardist" else cfg.segmentation.cellpose_model
        if cfg.segmentation.method != family or not current:
            return None
        cur = self.abs(current).resolve() if not Path(current).is_absolute() else Path(current).resolve()
        for m in self.models(family):
            if Path(m["path"]).resolve() == cur:
                return m["version"]
        return None

    # ---- counts made elsewhere -----------------------------------------------------------------------
    def import_count(self, image: Path, counts_file: Path | None, by: str | None = None, **options) -> tuple:
        """Import a Cell Counter XML / points CSV (or typed totals) for an image of this workspace.
        Returns (ManualCount, notes, added: bool)."""
        from recap.importers import import_counts

        res = import_counts(
            Path(image), counts_file, options.get("fmt", "auto"), options.get("pixel_size"),
            options.get("uninfected_types"), options.get("infected_types"), options.get("no_infection", False),
            options.get("nuclei"), options.get("vrc_pos"), by or default_counter_name(),
        )  # fmt: skip
        added = bool(self.add_count(res.count))
        return res.count, res.notes, added
