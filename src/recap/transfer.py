"""Moving settings files and trained StarDist models between workspaces (and people).

Settings: `export_settings` writes a copy of a workspace's recap.yaml (comments kept, a header saying
where it came from). Batch settings go with it; single-image settings do not by default, because
they name this workspace's image files. `import_settings` makes a file the workspace's settings:
its sections replace recap.yaml's own, after a backup in recap-data/settings_backups/; the
workspace keeps its own batch and image settings unless asked to take the file's.

Models: `export_model` zips a trained version (recap-data/models/stardist/vN) with
recap_model.yaml (where it came from, its scores, its parent). `import_model` takes such a zip, or
any StarDist model folder or zip (config.json and weights_*.h5), and registers it as the next
version of this workspace; `use=True` makes the workspace segment with it.

`recap settings export|import`, `recap models export|import` and the app (Images > More, the
Training tab) call these.
"""

from __future__ import annotations

import datetime as dt
import json
import shutil
import tempfile
import zipfile
from dataclasses import dataclass, field
from io import StringIO
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from recap.errors import ConfigError, RecapError

if TYPE_CHECKING:
    from recap.workspace import Workspace

MANIFEST = "recap_model.yaml"
BUNDLE_SETTINGS = "recap_settings.yaml"  # in a settings + model .zip (export_settings with_model)
FAMILIES = ("stardist",)


@dataclass
class Transfer:
    path: Path  # the file written, or the file / version brought in
    notes: list[str] = field(default_factory=list)
    backup: Path | None = None
    version: int | None = None


def _stamp() -> str:
    return dt.datetime.now().strftime("%Y%m%d_%H%M%S")


def _model_note(ws: Workspace, raw: dict[str, Any]) -> str | None:
    """If these settings segment with one of this workspace's trained versions, say how to bring it along."""
    seg = raw.get("segmentation") or {}
    model = seg.get("stardist_model") if seg.get("method", "stardist") == "stardist" else seg.get("cellpose_model")
    if not model or not str(model).replace("\\", "/").startswith("recap-data/models/"):
        return None
    version = Path(str(model)).name
    return (f"These settings use this workspace's trained model {version} ({model}). To give both, export them "
            "together: recap settings export FILE.zip --with-model (or the app's Export settings file...); "
            "a settings file alone makes the other workspace keep its own model.")  # fmt: skip


# ---- settings -----------------------------------------------------------------------------------------
def _model_version_in_use(ws: Workspace) -> int | None:
    try:
        return ws.model_in_use("stardist")
    except (RecapError, ValueError):
        return None


def export_settings(ws: Workspace, dest: Path, images: bool = False, force: bool = False,
                    with_model: bool = False) -> Transfer:  # fmt: skip
    """Write the workspace's recap.yaml to dest (without its images: section unless images).
    with_model: a .zip with the settings and the trained StarDist version they use, so that the
    other workspace gets both and its settings point at the model whatever version number it gets."""
    if with_model:
        return _export_bundle(ws, Path(dest), images, force)
    from recap.settingsfile import _yaml

    dest = Path(dest)
    if dest.exists() and not force:
        raise RecapError(f"{dest} exists already (use --force to replace it).")
    ws.settings()  # a clear message if there is none, or it is invalid
    y = _yaml()
    data = y.load(ws.settings_path.read_text(encoding="utf-8"))
    notes = []
    if not images and data.get("images"):
        notes.append(f"Left out: the settings of {len(data['images'])} single image(s) (they name this "
                     "workspace's files; --with-images keeps them).")  # fmt: skip
        del data["images"]
    buf = StringIO()
    y.dump(data, buf)
    from recap import __version__

    header = (
        f"# recap settings exported from the workspace {ws.root.name} on {dt.date.today().isoformat()} "
        f"(recap {__version__}).\n"
        "# Use: recap settings import THIS_FILE, or recap init FOLDER --from THIS_FILE\n"
    )
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(header + buf.getvalue(), encoding="utf-8")
    note = _model_note(ws, yaml.safe_load(buf.getvalue()) or {})
    if note:
        notes.append(note)
    return Transfer(dest, notes)


def _export_bundle(ws: Workspace, dest: Path, images: bool, force: bool) -> Transfer:
    version = _model_version_in_use(ws)
    if version is None:
        raise RecapError("These settings use no trained model of this workspace (the standard model, or one "
                         "from elsewhere): export the settings file on its own (.yaml).")  # fmt: skip
    if dest.suffix.lower() != ".zip":
        dest = dest.with_suffix(".zip")
    if dest.exists() and not force:
        raise RecapError(f"{dest} exists already (use --force to replace it).")
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        settings = export_settings(ws, tmp / BUNDLE_SETTINGS, images=images)
        model = export_model(ws, version, tmp / "model.zip")
        dest.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z, zipfile.ZipFile(model.path) as m:
            z.write(settings.path, BUNDLE_SETTINGS)
            for name in m.namelist():
                z.writestr(f"model/{name}", m.read(name))
    notes = [n for n in settings.notes if "--with-model" not in n]
    notes.append(f"The trained model v{version} is in the same file.")
    return Transfer(dest, notes, version=version)


def _is_bundle(src: Path) -> bool:
    return src.is_file() and zipfile.is_zipfile(src) and BUNDLE_SETTINGS in zipfile.ZipFile(src).namelist()


def _model_key(seg: dict[str, Any]) -> str:
    return "cellpose_model" if seg.get("method") == "cellpose" else "stardist_model"


def import_settings(ws: Workspace, src: Path, batches: bool = False, images: bool = False,
                    model_path: str | None = None) -> Transfer:  # fmt: skip
    """Make a settings file the workspace's: its sections replace recap.yaml's own (recap.yaml is
    backed up first). batches / images: also take the file's batch / single-image settings (they
    replace the workspace's of the same name; images that are not in this workspace are skipped).

    The model: a settings + model .zip (export_settings with_model) brings its model in as the
    next version and uses it. A plain settings file naming a trained model this workspace does not
    have keeps this workspace's model (with a note). Any change of the model in use is noted."""
    from recap.config import LEVEL_SECTIONS, deprecation_notes, load_config
    from recap.settingsfile import _write, _yaml

    src = Path(src)
    if _is_bundle(src):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            with zipfile.ZipFile(src) as z:
                _safe_extract(z, tmp)
            model = import_model(ws, tmp / "model", note=f"with the settings {src.name}")
            res = import_settings(ws, tmp / BUNDLE_SETTINGS, batches, images, model_path=ws.rel(model.path))
        res.notes.insert(0, f"The model in {src.name} was added as StarDist v{model.version} and is in use.")
        return res
    try:
        raw, cfg, _ = load_config(src)
    except (ValueError, OSError) as e:
        raise ConfigError(f"{src.name} is not a valid settings file:\n{e}") from e
    y = _yaml()
    new = y.load(src.read_text(encoding="utf-8"))
    current = y.load(ws.settings_path.read_text(encoding="utf-8")) if ws.has_settings else {}
    notes = [n for n in deprecation_notes(cfg) if "input.images" not in n]
    inp = new.get("input")
    if isinstance(inp, dict):
        for k in ("images", "use_workspace_settings"):
            inp.pop(k, None)
    old_seg = dict((current or {}).get("segmentation") or {})
    try:  # (as used: defaults filled in)
        o = ws.settings().segmentation
        old_model = (o.method, o.stardist_model if o.method == "stardist" else o.cellpose_model)
    except (RecapError, ValueError):
        old_model = None
    seg_new = new.get("segmentation")
    if model_path is not None:
        if seg_new is None:
            new["segmentation"] = seg_new = {}
        seg_new["method"], seg_new["stardist_model"] = "stardist", model_path
    elif isinstance(seg_new, dict):
        theirs_model = seg_new.get(_model_key(seg_new))
        named = str(theirs_model or "").replace("\\", "/")
        if named.startswith("recap-data/models/") and not (ws.root / named).exists():
            for k in ("method", "stardist_model", "cellpose_model"):  # keep this workspace's model
                if k in old_seg:
                    seg_new[k] = old_seg[k]
                else:
                    seg_new.pop(k, None)
            notes.append(f"The file's settings use the trained model {named} of the workspace they came from, "
                         "which this workspace does not have: this workspace keeps its model"
                         + (f" ({old_model[1] or old_model[0]})" if old_model else "") + ". To take both, "
                         "export them together: recap "
                         "settings export FILE.zip --with-model.")  # fmt: skip
    for section in LEVEL_SECTIONS:
        theirs = new.pop(section, None) if section in new else None
        ours = (current or {}).get(section)
        take = batches if section == "batches" else images
        merged = dict(ours or {})
        if take and theirs:
            for name, entry in theirs.items():
                if section == "images" and not (ws.root / str(name)).is_file():
                    notes.append(f"Not imported: settings of {name} (no such image in this workspace).")
                    continue
                merged[name] = entry
            notes.append(f"{section}: the file's entries were taken ({len(theirs)}).")
        elif theirs:
            notes.append(f"{section}: the file's entries were not taken (this workspace keeps its own; "
                         f"--with-{section} takes them).")  # fmt: skip
        if merged:
            new[section] = merged
    backup = None
    if ws.has_settings:
        backup = ws.data / "settings_backups" / f"recap_{_stamp()}.yaml"
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ws.settings_path, backup)
    if not ws.settings_path.exists():
        ws.settings_path.write_text("input: {}\n", encoding="utf-8")
    _write(ws.settings_path, y, new)
    ws._doc_cache = None
    seg = ws.settings().segmentation
    now = (seg.method, seg.stardist_model if seg.method == "stardist" else seg.cellpose_model)
    if old_model is not None and now != old_model:
        notes.append(f"The model in use changed: {old_model[1] or old_model[0]} -> {now[1] or now[0]}.")
    return Transfer(ws.settings_path, notes, backup)


# ---- StarDist models --------------------------------------------------------------------------------
def export_model(ws: Workspace, version: int, dest: Path, family: str = "stardist", force: bool = False) -> Transfer:
    """Zip a trained version with recap_model.yaml (where it came from, scores, parent)."""
    from recap import __version__

    if family not in FAMILIES:
        raise RecapError(f"exporting {family} models is not supported (only {', '.join(FAMILIES)})")
    m = next((m for m in ws.models(family) if m["version"] == version), None)
    if m is None:
        raise RecapError(f"no {family} version v{version} in this workspace (recap models list)")
    folder = Path(m["path"])
    if not folder.is_dir():
        raise RecapError(f"the files of v{version} are missing: {folder}")
    dest = Path(dest)
    if dest.suffix.lower() != ".zip":
        dest = dest.with_suffix(".zip")
    if dest.exists() and not force:
        raise RecapError(f"{dest} exists already (use --force to replace it).")
    manifest = {
        "family": family, "version": version, "parent": m.get("parent"), "created_at": m.get("created_at"),
        "metrics": m.get("metrics") or {}, "settings": m.get("settings") or {}, "note": m.get("note"),
        "n_train_images": len(m.get("train_images") or []), "n_val_images": len(m.get("val_images") or []),
        "exported_from": ws.root.name, "exported_at": dt.datetime.now().isoformat(timespec="seconds"),
        "recap_version": __version__,
    }  # fmt: skip
    dest.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(MANIFEST, yaml.safe_dump(manifest, sort_keys=False))
        for f in sorted(folder.rglob("*")):
            rel = f.relative_to(folder)
            if f.is_file() and "logs" not in rel.parts:  # (TensorBoard logs: large, not needed)
                z.write(f, Path(f"v{version}") / rel)
    return Transfer(dest, [], version=version)


def _safe_extract(z: zipfile.ZipFile, target: Path) -> None:
    root = target.resolve()
    for name in z.namelist():
        out = (target / name).resolve()
        if out != root and root not in out.parents:
            raise RecapError(f"refusing to extract {name}: it would land outside {target}")
    z.extractall(target)


def _stardist_folder(top: Path) -> Path:
    """The folder with a StarDist model's config.json and weights (top itself or one level below)."""
    for d in [top, *sorted(p for p in top.rglob("*") if p.is_dir())]:
        if (d / "config.json").is_file() and any(d.glob("weights_*.h5")):
            try:
                conf = json.loads((d / "config.json").read_text(encoding="utf-8"))
            except (OSError, ValueError) as e:
                raise RecapError(f"{d / 'config.json'} is not readable: {e}") from e
            if "n_rays" not in conf:
                raise RecapError(f"{d} has a config.json, but not a StarDist one (no n_rays).")
            return d
    raise RecapError("no StarDist model found: expected config.json and weights_best.h5 (or weights_last.h5).")


def import_model(ws: Workspace, src: Path, family: str = "stardist", note: str | None = None,
                 use: bool = False) -> Transfer:  # fmt: skip
    """Register a StarDist model (a recap export, or any StarDist model folder or zip) as the next
    version of this workspace. use: make the workspace segment with it (recap.yaml)."""
    if family not in FAMILIES:
        raise RecapError(f"importing {family} models is not supported (only {', '.join(FAMILIES)})")
    src = Path(src)
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        if src.is_file() and zipfile.is_zipfile(src):
            with zipfile.ZipFile(src) as z:
                _safe_extract(z, tmp)
            top = tmp
        elif src.is_dir():
            top = src
        else:
            raise RecapError(f"{src}: expected a .zip from recap models export, or a StarDist model folder")
        manifest: dict[str, Any] = {}
        for mf in [top / MANIFEST, *top.rglob(MANIFEST)]:
            if mf.is_file():
                manifest = yaml.safe_load(mf.read_text(encoding="utf-8")) or {}
                break
        if manifest.get("family", family) != family:
            raise RecapError(f"{src.name} holds a {manifest['family']} model, not {family}")
        folder = _stardist_folder(top)
        version = ws.next_model_version(family)
        target = ws.model_dir(family, version)
        target.mkdir(parents=True, exist_ok=False)
        for f in folder.rglob("*"):
            if f.is_file() and f.name != MANIFEST and "logs" not in f.relative_to(folder).parts:
                out = target / f.relative_to(folder)
                out.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(f, out)
    origin = f"imported from {src.name}"
    if manifest:
        origin += (
            f" (v{manifest.get('version')} of {manifest.get('exported_from')}, recap {manifest.get('recap_version')})"
        )
    ws.register_model(family, version, target, parent=manifest.get("parent") or "imported",
                      metrics=manifest.get("metrics") or {}, settings=manifest.get("settings") or {},
                      note="; ".join(x for x in (note, origin, manifest.get("note")) if x))  # fmt: skip
    notes = []
    if use:
        notes.append(f"The workspace now segments with v{version} (recap.yaml).")
        ws.use_model(family, version)
    return Transfer(target, notes, version=version)
