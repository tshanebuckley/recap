"""Running recap: optional spot check, batch, run log.

`recap run` and the app's Run buttons both call run_workspace. It never prints; it logs (logger
"recap") and returns a RunResult, and raises ConfigError / Cancelled:

    from recap.runner import run_workspace
    from recap.workspace import open_workspace
    result = run_workspace(open_workspace(Path("my-experiment")), progress=lambda i, n, path, info: ...)

run_pipeline(path) does the same for a workspace given as its folder or recap.yaml. Every run
happens in a workspace (recap 5.0): a settings file of its own is a starting point for one
(`recap init FOLDER --from FILE`).
"""

from __future__ import annotations

import datetime as dt
import logging
import platform
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from recap import __version__
from recap.config import PipelineConfig, deprecation_notes, load_config
from recap.errors import Cancelled, ConfigError
from recap.pipeline import ProgressFn, Result, run_batch
from recap.runlog import close_logging, diff, dump_yaml, package_versions, setup_logging
from recap.spotcheck import CountFn, DecideFn, run_spot_check

log = logging.getLogger(__name__)

PACKAGES = [
    "numpy",
    "scikit-image",
    "scipy",
    "pandas",
    "tifffile",
    "pydantic",
    "typer",
    "pyyaml",
    "napari",
    "cellpose",
    "stardist",
    "tensorflow",
    "tensorflow-cpu",
    "torch",
]


@dataclass
class RunResult:
    status: str  # completed | counts_saved | cancelled
    out_dir: Path
    files: dict[str, str] = field(default_factory=dict)
    images: list[dict[str, Any]] = field(default_factory=list)
    config: PipelineConfig | None = None  # the settings actually applied


def new_stamp(out_dir: Path) -> str:
    """Date and time of a run; a suffix if a run in the same second already used it."""
    base = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    stamp, k = base, 1
    while any((Path(out_dir) / n).exists() for n in (f"run_log_{stamp}.yaml", f"run_{stamp}.log", stamp)):
        k += 1
        stamp = f"{base}_{k}"
    return stamp


def run_pipeline(
    path: Path,
    decide: DecideFn | None = None,
    progress: ProgressFn | None = None,
    cancel: Callable[[], bool] | None = None,
    counter: CountFn | None = None,
    images: list[Path] | None = None,
) -> RunResult:
    """Run a workspace (its folder or its recap.yaml) end to end: run_workspace. A settings file
    that is not a workspace's recap.yaml is refused with how to make a workspace from it.

    counter counts the spot-check images that have no previous count (validation.enabled), e.g.
    recap.fijicount.FijiCounter; decide takes the decision after the spot-check overview."""
    from recap.errors import RecapError
    from recap.workspace import SETTINGS_NAME, Workspace, workspace_root

    path = Path(path)
    if path.is_file() and path.name != SETTINGS_NAME:
        raise ConfigError(
            f"{path.name} is a settings file, not a workspace. Every run happens in a workspace: "
            f"recap init FOLDER --from {path} makes the folder of your images one, then recap run FOLDER."
        )
    root = workspace_root(path)
    if root is None:
        raise ConfigError(f"{path} is not a workspace: recap init {path} --from <settings file>")
    try:
        ws = Workspace(root)
    except RecapError as e:
        raise ConfigError(str(e)) from e
    return run_workspace(ws, images, decide, progress, cancel, counter)


def run_workspace(
    ws,
    images: list[Path] | None = None,
    decide: DecideFn | None = None,
    progress: ProgressFn | None = None,
    cancel: Callable[[], bool] | None = None,
    counter: CountFn | None = None,
    spot_check: bool = True,
    settings_file: Path | None = None,
) -> RunResult:
    """Run a workspace (all its images, or `images`) with each image's own settings (recap.yaml →
    batch → image); the results are recorded in the workspace. The spot check (validation.enabled)
    reuses counts already in the workspace and counts more up to sample_size.

    settings_file: a comparison run (e.g. another segmentation method). That file's settings take
    the place of recap.yaml's own sections (the batch and image settings still apply), the outputs
    go to its output.directory (relative to the workspace), there is no spot check, and the run is
    not recorded as the images' results: compare it with `recap report RUN_A RUN_B -m recap.db`."""
    from recap.modelpath import use_model_root

    use_model_root(ws.root)  # trained models named in its settings: this workspace's (recap-data/models/...)
    config_path = Path(settings_file) if settings_file else ws.settings_path
    try:
        raw, cfg, sha = load_config(config_path)
    except (ValidationError, OSError) as e:
        raise ConfigError(f"{config_path}: {e}") from e
    except Exception as e:  # noqa: BLE001  (RecapError: no recap.yaml)
        raise ConfigError(str(e)) from e
    shas = [ws.sha_of(p) for p in images] if images else None  # (before scan may move them into raw/)
    ws.scan()
    paths = [ws.image_path(h) for h in shas] if shas else ws.images(cfg)
    ignored = [p for p in paths if ws.is_ignored(p)]
    if ignored:
        raise ConfigError(
            f"{', '.join(p.name for p in ignored)}: ignored in this workspace (.recapignore). "
            "Bring it back first: recap unignore IMAGE, or Stop ignoring in the app."
        )
    if not paths:
        raise ConfigError(f"No images to run in {ws.root}.")
    if cfg.validation.enabled and cfg.validation.previous_counts is None:
        d = cfg.model_dump()
        d["validation"].update(previous_counts=ws.db_path, top_up=True)  # reuse the workspace's counts
        cfg = PipelineConfig.model_validate(d)
    if not spot_check or settings_file:
        d = cfg.model_dump()
        d["validation"]["enabled"] = False
        cfg = PipelineConfig.model_validate(d)
    from recap.layout import COMPARISONS, RUNS

    image_dir = None
    rotated = None  # an Organizer: the images' earlier outputs moved during this run (recorded at the end)
    if settings_file:  # a comparison run: one flat folder of its own, under processed/comparisons/
        out_dir = ws.output_dir() / COMPARISONS / Path(cfg.output.directory).name
        stamp = new_stamp(out_dir)
    else:  # processed/runs/<stamp>/, and each image's outputs in its own folder
        from recap.layout import Organizer

        stamp = new_stamp(ws.output_dir() / RUNS)
        out_dir = ws.run_dir(stamp)
        folders: dict[Path, Path] = {}
        rotated = Organizer(ws)

        def image_dir(p: Path) -> Path:
            if p not in folders:  # (called once per image, just before its new outputs are written)
                from recap.io import file_sha256

                sha_p = file_sha256(p)
                ws.keep_previous_outputs(sha_p, rotated)  # current -> previous/, previous -> previous_runs/<date>.zip
                folders[p] = ws.image_folder(sha_p)
            return folders[p]

    log_file = setup_logging(out_dir, stamp)
    try:
        result = _run(config_path, raw, cfg, sha, paths, stamp, out_dir, log_file, decide, progress, cancel,
                      counter, ws, comparison=settings_file is not None, image_dir=image_dir)  # fmt: skip
    finally:
        close_logging()
        if rotated is not None:  # (also after a cancel or an error: the records follow the files moved)
            rotated._record(keep_logs={out_dir / f"run_log_{stamp}.yaml"})  # (this run's log names its new outputs)
    if settings_file:
        return result
    ws.record_run(out_dir / f"run_log_{stamp}.yaml")
    if "manual_counts" in result.files:  # the spot check's counts, also when the outputs are outside the folder
        from recap.counts import read_manual_counts

        for mc in read_manual_counts(Path(result.files["manual_counts"])):
            if mc.source == "new":
                ws.add_count(mc)
    ws.scan()
    return result


def _run(config_path, raw, cfg, sha, paths, stamp, out_dir, log_file, decide, progress, cancel, counter,
         ws, comparison: bool = False, image_dir=None) -> RunResult:  # fmt: skip
    log.info("recap %s | config %s | %d image(s)", __version__, config_path, len(paths))
    for note in deprecation_notes(cfg):
        log.warning(note)

    validated = cfg.model_dump(mode="json")
    resolved = PipelineConfig.model_validate(
        {**cfg.model_dump(), "segmentation": cfg.segmentation.resolved().model_dump()}
    )
    if cfg.segmentation.method != "classical" and cfg.segmentation.merge:
        log.warning(
            "segmentation.merge is true with method=%s; boundary merging tends to fuse touching "
            "nuclei in model output. Consider merge: false.",
            cfg.segmentation.method,
        )

    final = resolved
    if cfg.normalization.vrc_range is None:
        log.warning(
            "vrc_range is null: VRC is percentile-normalised per image, which amplifies "
            "background in fields without infected cells. Set a fixed range."
        )

    settings_for = workspace_settings(final, ws)
    cache: dict[Path, Result] = {}
    status, validation_log, files = "completed", None, {"log": str(log_file)}
    if final.validation.enabled:
        try:
            outcome = run_spot_check(
                final, paths, out_dir, stamp, config_path.parent, cache, decide, counter, settings_for
            )
        except (ValidationError, ValueError, FileNotFoundError) as e:
            raise ConfigError(f"spot check failed: {e}") from e
        validation_log = outcome.log
        files["manual_counts"] = str(outcome.counts_file)
        if outcome.decision == "cancel":
            log.warning(
                "Cancelled at the spot check. Manual counts entered so far are kept in %s "
                "and can be reused via validation.previous_counts.",
                outcome.counts_file,
            )
            raise Cancelled(f"cancelled at the spot check; counts kept in {outcome.counts_file}")
        files["spot_check"] = str(outcome.comparison_file)
        if outcome.decision == "save":
            status = "counts_saved"
            log.info("Counts and configuration saved; batch not run.")

    images_log = (
        run_batch(final, paths, out_dir, stamp, cache, progress, cancel, settings_for, image_dir)
        if status == "completed"
        else []
    )
    if any("cancelled" in d for d in images_log):
        status = "cancelled"
    if any("n_nuclei" in d for d in images_log):
        files["all_nuclei"] = str(out_dir / f"all_nuclei_{stamp}.csv")
        files["image_summary"] = str(out_dir / f"image_summary_{stamp}.csv")

    final_json = final.model_dump(mode="json")
    final_cfg_path = out_dir / f"final_config_{stamp}.yaml"
    reusable = final.model_dump(mode="json")  # the settings applied: a starting point for recap init --from
    reusable["output"]["directory"] = str(out_dir.resolve())
    if "manual_counts" in files:  # rerun reuses these counts instead of recounting
        reusable["validation"]["previous_counts"] = str(Path(files["manual_counts"]).resolve())
        reusable["validation"]["seed"] = validation_log["seed"]
    dump_yaml(final_cfg_path, reusable)
    files["final_config"] = str(final_cfg_path)

    dump_yaml(
        out_dir / f"run_log_{stamp}.yaml",
        {
            "run": {
                "status": status,
                "timestamp": stamp,
                "tool": "recap",
                "version": __version__,
                "command": " ".join(sys.argv),
                "comparison": comparison,  # run with another settings file: not the images' results
                "python": sys.version.split()[0],
                "platform": platform.platform(),
                "packages": package_versions(PACKAGES),
            },
            "config_file": {"path": str(config_path.resolve()), "sha256": sha},
            "input_yaml": raw,  # exactly as written by the user
            "validated_config": validated,  # after pydantic defaults and type coercion
            "resolved_config": resolved.model_dump(mode="json"),  # derived values filled in
            "final_config": final_json,  # what was actually applied
            "changes_from_input": diff(validated, final_json),
            "deprecations": deprecation_notes(cfg),
            "spot_check": validation_log,
            "image_files": [str(p.resolve()) for p in paths],  # every image of the run (report, compare)
            "images": images_log,
            "files": files,
        },
    )
    log.info("Done (%s). Run log and final_config written to %s", status, out_dir)
    return RunResult(status, out_dir, files, images_log, final)


def workspace_settings(cfg: PipelineConfig, ws):
    """settings_for(path) giving an image's settings with its batch and image levels."""
    if ws is None:
        return None
    from recap.io import file_sha256

    if ws.upgrade is not None:
        log.warning(ws.upgrade.message())
    log.info("Workspace %s: each image with its batch and image settings", ws.root)
    done: dict[Path, object] = {}

    def settings_for(p: Path):
        if p not in done:
            done[p] = ws.resolve(file_sha256(p), cfg)
        return done[p]

    return settings_for
