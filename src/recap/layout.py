"""Where a workspace keeps its files: raw images and everything recap makes, in mirrored batch folders.

    my-experiment/
        recap.yaml, .recapignore
        raw/                                  the images as acquired: recap never changes them
            batches/<batch>/<image>.tif
            unbatched/<image>.tif
        processed/                            everything recap makes from them
            batches/<batch>/<image name>/     that image's latest outputs and its corrected masks:
                <name>_labels.tif, <name>_excluded_labels.tif, <name>_nuclei.csv,
                <name>_excluded.csv, <name>_qc.png, <name>_corrected_<N>.tif
            unbatched/<image name>/
            runs/<date_time>/                 one run: run log, image summary, all nuclei, spot check
            comparisons/<name>/               runs with another settings file (recap run --settings)
            reports/<date_time>/              recap report
        recap-data/                           database, model versions, tuning, Fiji sessions, backups

organize() puts every image (except ignored ones) and its outputs where they belong: when a batch
is assigned, renamed or deleted, when images are added or dropped into the folder by hand, and the
first time recap 5.1 opens an older workspace (whose images could be anywhere and whose outputs were
in one flat folder). Each move updates the database, the image's entry in recap.yaml and the run
logs that name the file, and is appended to recap-data/moves.csv. A file that cannot be moved (open
in another program) stays where it is and is moved next time.
"""

from __future__ import annotations

import csv
import datetime as dt
import logging
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml
from sqlmodel import select

if TYPE_CHECKING:
    from recap.workspace import Workspace

log = logging.getLogger(__name__)

RAW = "raw"
PROCESSED = "processed"
BATCHES = "batches"
UNBATCHED = "unbatched"
RUNS = "runs"
COMPARISONS = "comparisons"
REPORTS = "reports"
LAYOUT_VERSION = "1"  # recap-data's meta table: the layout a workspace has been organized into
TOP = (RAW, PROCESSED)


def safe_name(name: str) -> str:
    keep = "".join(c if c.isalnum() or c in " -_.()" else "_" for c in name).strip(" .")
    return keep or "batch"


def group(batch: str | None) -> Path:
    """batches/<batch> or unbatched: the same relative folder under raw/ and processed/."""
    return Path(BATCHES) / safe_name(batch) if batch else Path(UNBATCHED)


def unique_path(p: Path) -> Path:
    """p, or p with _2, _3 ... before the suffix if something of that name exists."""
    k, out = 1, p
    while out.exists():
        k += 1
        out = p.with_name(f"{p.stem}_{k}{p.suffix}")
    return out


@dataclass
class Move:
    kind: str  # image | output | run
    old: Path
    new: Path


class Organizer:
    """Moves files of one workspace and keeps everything that names them up to date."""

    def __init__(self, ws: Workspace):
        self.ws = ws
        self.moves: list[Move] = []
        # paths named by a run log or the database that lie outside this workspace (it was copied or
        # moved from elsewhere) -> the file of that name inside it, which is what they mean here
        self.aliases: dict[str, Path] = {}

    def inside(self, p: Path) -> bool:
        root = self.ws.root.resolve()
        try:
            return root in p.resolve().parents
        except OSError:
            return False

    def here(self, named: str | Path, folder: Path | None) -> Path | None:
        """A file named by a run log or the database, as a file inside this workspace (or None).
        A path outside the workspace is never used: the file of that name in `folder` (the run's
        own folder here) is, and the stale path is remembered so it can be rewritten."""
        p = self.ws.abs(str(named))
        if p is None:
            return None
        if self.inside(p):
            return p if p.is_file() else None
        cand = folder / p.name if folder is not None else None
        if cand is not None and self.inside(cand) and cand.is_file():
            self.aliases[str(named)] = cand.resolve()
            return cand
        return None

    # ---- moving ------------------------------------------------------------------------------------
    def move(self, kind: str, old: Path, new: Path) -> Path | None:
        """Move a file or folder; returns where it went, or None if it could not be moved."""
        if old.resolve() == new.resolve():
            return new
        if not (self.inside(old) and self.inside(new)):  # never take files from (or put them) elsewhere
            log.warning("not moving %s: outside the workspace %s", old, self.ws.root)
            return None
        new.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.move(str(old), str(new))
        except OSError as e:  # e.g. open in Fiji on Windows: try again next time
            log.warning("could not move %s to %s (%s); it stays where it is for now", old, new, e)
            return None
        self.moves.append(Move(kind, old.resolve(), new.resolve()))
        self._prune(old.parent)
        return new

    def _prune(self, folder: Path) -> None:
        """Remove folders a move left empty, up to (never including) the workspace folder."""
        root = self.ws.root.resolve()
        folder = folder.resolve()
        while folder != root and root in folder.parents and folder.name not in (RAW, PROCESSED, "recap-data"):
            try:
                if any(folder.iterdir()):
                    return
                folder.rmdir()
            except OSError:
                return
            folder = folder.parent

    def move_into(self, kind: str, f: Path, folder: Path) -> Path | None:
        target = folder / f.name
        if target.exists() and target.resolve() != f.resolve():
            target = unique_path(target)
        return self.move(kind, f, target)

    # ---- what goes where ---------------------------------------------------------------------------
    def raw_folder(self, batch: str | None) -> Path:
        return self.ws.root / RAW / group(batch)

    def image_folder(self, batch: str | None, image_name: str) -> Path:
        return self.ws.root / PROCESSED / group(batch) / Path(image_name).stem

    def organize(self, full: bool = False) -> list[Move]:
        """Put every image that is not ignored, and its outputs, where they belong. full: also look
        through the run logs for outputs of an older layout (the first time; slower)."""
        from recap.db import ImageRow, MaskRow, ResultRow, RunRow

        ws = self.ws
        batches, ignored = ws.image_batches(), ws.ignored_shas()
        with ws.session() as s:
            images = [im for im in s.exec(select(ImageRow)) if im.path and im.sha256 not in ignored]
            masks = s.exec(select(MaskRow)).all()
            results = s.exec(select(ResultRow)).all()
            run_dirs = {r.run_id: ws.abs(r.out_dir) for r in s.exec(select(RunRow))}
        outputs = self._outputs_from_run_logs() if full else {}
        for im in images:
            src = ws.abs(im.path)
            batch = batches.get(im.sha256)
            name = Path(im.path).name
            if src is not None and src.is_file():
                want = self.raw_folder(batch)
                if src.parent.resolve() != want.resolve():
                    moved = self.move_into("image", src, want)
                    name = moved.name if moved is not None else name
            folder = self.image_folder(batch, name)
            files: set[Path] = set()
            for r in masks:
                if r.sha256 == im.sha256 and r.path:
                    files.add(self.here(r.path, ws.data / "masks"))
            for r in results:
                if r.sha256 == im.sha256:
                    files |= {self.here(p, run_dirs.get(r.run_id)) for p in (r.labels_path, r.excluded_path,
                                                                            r.table_path) if p}  # fmt: skip
            files |= outputs.get(im.sha256, set())
            files.discard(None)
            done_folders: set[Path] = set()
            for f in sorted(p for p in files if p is not None and p.is_file()):
                if f.parent.resolve() == folder.resolve():
                    continue
                old_folder = f.parent
                if self._is_image_folder(old_folder):  # the image's folder of another batch: move all of it
                    if old_folder in done_folders:
                        continue
                    done_folders.add(old_folder)
                    for g in sorted(old_folder.iterdir()):
                        self.move_into("output", g, folder)
                    _remove_if_empty(old_folder)
                else:  # an older flat output folder, or recap-data/masks
                    self.move_into("output", f, folder)
        if full:
            self._move_old_runs()
        self._record()
        for d in (self.ws.root / RAW / BATCHES, self.ws.root / PROCESSED / BATCHES):
            if d.is_dir():
                for sub in d.iterdir():
                    _remove_if_empty(sub)
        return self.moves

    def _is_image_folder(self, folder: Path) -> bool:
        try:
            rel = folder.resolve().relative_to((self.ws.root / PROCESSED).resolve())
        except ValueError:
            return False
        return (rel.parts[:1] == (UNBATCHED,) and len(rel.parts) == 2) or (
            rel.parts[:1] == (BATCHES,) and len(rel.parts) == 3
        )

    def _outputs_from_run_logs(self) -> dict[str, set[Path]]:
        """sha256 -> every per-image output file named in the workspace's run logs (older layouts)."""
        from recap.db import RunRow

        ws = self.ws
        with ws.session() as s:
            logs = [ws.abs(r.log_path) for r in s.exec(select(RunRow)) if r.log_path]
        out: dict[str, set[Path]] = {}
        for lf in logs:
            if lf is None or not lf.is_file():
                continue
            try:
                rl = yaml.safe_load(lf.read_text(encoding="utf-8")) or {}
            except (OSError, yaml.YAMLError):
                continue
            for d in rl.get("images") or []:
                if "outputs" not in d:
                    continue
                sha = self._image_sha(d["image"])
                if sha:
                    files = {self.here(p, lf.parent) for p in d["outputs"].values()}
                    out.setdefault(sha, set()).update(f for f in files if f is not None)
        return out

    def _image_sha(self, named: str) -> str | None:
        """The image a run log names, also when the path is from where the workspace was copied
        from: by content if that file is readable, else by a unique file name in this workspace."""
        from recap.db import ImageRow

        ws = self.ws
        p = Path(named)
        if self.inside(p) or not p.is_absolute():
            return ws._sha_for(named)
        with ws.session() as s:
            rows = s.exec(select(ImageRow).where(ImageRow.name == p.name)).all()
        sha = rows[0].sha256 if len(rows) == 1 else (ws._sha_for(named) if p.is_file() else None)
        if sha is not None:
            current = next((ws.abs(r.path) for r in rows if r.sha256 == sha and r.path), None)
            if current is not None:
                self.aliases[named] = current.resolve()
        return sha

    def _move_old_runs(self) -> None:
        """Runs of an older layout (several in one output folder) each into processed/runs/<date>/."""
        from recap.db import RunRow

        ws = self.ws
        runs_dir = ws.root / PROCESSED / RUNS
        with ws.session() as s:
            rows = s.exec(select(RunRow)).all()
        for r in rows:
            lf = ws.abs(r.log_path)
            if lf is None or not lf.is_file() or lf.parent.parent.resolve() == runs_dir.resolve():
                continue
            stamp = r.stamp or lf.stem.removeprefix("run_log_")
            target = runs_dir / stamp
            old_dir = lf.parent
            for f in sorted(old_dir.glob(f"*_{stamp}.*")):  # run_log_, image_summary_, run_ (.log), ...
                if f.is_file():
                    self.move("run", f, target / f.name)
            _remove_if_empty(old_dir)

    # ---- keeping everything that names the files up to date ----------------------------------------------
    def _record(self) -> None:
        if not self.moves and not self.aliases:
            return
        from recap.db import ImageRow, MaskRow, ResultRow, RunRow

        ws = self.ws
        old_rel = {ws.rel(m.old): ws.rel(m.new) for m in self.moves}
        absolute = {str(m.old): str(m.new) for m in self.moves}
        for named, cand in self.aliases.items():  # stale paths from elsewhere: to where their file is now
            now = Path(absolute.get(str(cand), str(cand)))
            absolute[named] = str(now)
            old_rel[named] = ws.rel(now)
        renamed_images: list[tuple[str, str]] = []
        with ws.session() as s:
            for im in s.exec(select(ImageRow)):
                if im.path in old_rel:
                    renamed_images.append((im.path, old_rel[im.path]))
                    im.path, im.name = old_rel[im.path], Path(old_rel[im.path]).name
                    s.add(im)
            for m in s.exec(select(MaskRow)):
                if m.path in old_rel:
                    m.path = old_rel[m.path]
                    s.add(m)
            for r in s.exec(select(ResultRow)):
                for col in ("labels_path", "excluded_path", "table_path"):
                    if getattr(r, col) in old_rel:
                        setattr(r, col, old_rel[getattr(r, col)])
                if r.image in absolute:
                    r.image = absolute[r.image]
                s.add(r)
            s.commit()
            runs = s.exec(select(RunRow)).all()
        moved_runs = {}
        for run in runs:
            if run.log_path in old_rel:
                moved_runs[run.run_id] = (old_rel[run.log_path], ws.rel(ws.abs(old_rel[run.log_path]).parent))
        if moved_runs:
            from sqlalchemy import text

            with ws.engine.begin() as con:
                for old_id, (log_path, out_dir) in moved_runs.items():
                    con.execute(text("UPDATE results SET run_id = :new WHERE run_id = :old"),
                                {"new": log_path, "old": old_id})  # fmt: skip
                    sql = "UPDATE runs SET run_id = :new, log_path = :new, out_dir = :d WHERE run_id = :old"
                    con.execute(text(sql), {"new": log_path, "d": out_dir, "old": old_id})
        if ws.has_settings and renamed_images:
            from recap.settingsfile import rename_entry

            for old, new in renamed_images:
                rename_entry(ws.settings_path, "images", old, new)
        self._rewrite_run_logs(absolute)
        if not self.moves:
            return
        moves_csv = ws.data / "moves.csv"
        new_file = not moves_csv.exists()
        with moves_csv.open("a", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            if new_file:
                w.writerow(["moved_at", "kind", "from", "to"])
            at = dt.datetime.now().isoformat(timespec="seconds")
            for m in self.moves:
                w.writerow([at, m.kind, ws.rel(m.old), ws.rel(m.new)])
        log.info("Moved %d file(s) into the workspace's raw/ and processed/ folders (list: %s).",
                 len(self.moves), moves_csv)  # fmt: skip

    def _rewrite_run_logs(self, absolute: dict[str, str]) -> None:
        """Run logs name images and outputs by absolute path: point them at the new places."""
        from recap.runlog import dump_yaml

        ws = self.ws
        for lf in ws.root.rglob("run_log_*.yaml"):
            if (ws.data in lf.parents) or not lf.is_file():
                continue
            try:
                rl = yaml.safe_load(lf.read_text(encoding="utf-8"))
            except (OSError, yaml.YAMLError):
                continue
            new = _replace_strings(rl, absolute)
            if new != rl:
                dump_yaml(lf, new)


def _replace_strings(x: Any, mapping: dict[str, str]) -> Any:
    if isinstance(x, str):
        return mapping.get(x, x)
    if isinstance(x, list):
        return [_replace_strings(v, mapping) for v in x]
    if isinstance(x, dict):
        return {k: _replace_strings(v, mapping) for k, v in x.items()}
    return x


def _remove_if_empty(folder: Path) -> None:
    try:
        if folder.is_dir() and not any(folder.iterdir()):
            folder.rmdir()
    except OSError:
        pass
