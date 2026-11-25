"""Running recap: optional spot check, batch, run log.

`recap run` and the app's Run buttons both call run_workspace. It never prints; it logs (logger
"recap") and returns a RunResult, and raises ConfigError / Cancelled:

    from recap.runner import run_workspace
    from recap.workspace import open_workspace
    result = run_workspace(open_workspace(Path("my-experiment")), progress=lambda i, n, path, info: ...)

run_pipeline runs a settings file that is not a workspace's (no batch or image settings, and
nothing is stored in a workspace until one is scanned).
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


def load_and_check(config_path: Path) -> tuple[dict, PipelineConfig, str, list[Path]]:
    """Load and validate a config, resolve its image paths and (if needed) its Fiji. Raises ConfigError."""
    try:
        raw, cfg, sha = load_config(config_path)
        paths = cfg.input.resolve_paths(config_path.parent)
    except (ValidationError, FileNotFoundError) as e:
        raise ConfigError(str(e)) from e
    return raw, cfg, sha, paths


def new_stamp(out_dir: Path) -> str:
    """Date and time of a run; a suffix if a run in the same second already used it."""
    base = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    stamp, k = base, 1
    while (Path(out_dir) / f"run_log_{stamp}.yaml").exists() or (Path(out_dir) / f"run_{stamp}.log").exists():
        k += 1
        stamp = f"{base}_{k}"
    return stamp


def run_pipeline(
    config_path: Path,
    decide: DecideFn | None = None,
    progress: ProgressFn | None = None,
    cancel: Callable[[], bool] | None = None,
    counter: CountFn | None = None,
) -> RunResult:
    """Run a settings file end to end and write all outputs into its output directory. A
    workspace's recap.yaml runs as that workspace (run_workspace).

    counter counts the spot-check images that have no previous count (validation.enabled), e.g.
    recap.fijicount.FijiCounter; decide takes the decision after the spot-check overview."""
    from recap.workspace import SETTINGS_NAME, Workspace, workspace_root

    config_path = Path(config_path)
    if config_path.name == SETTINGS_NAME and (config_path.parent / "recap-data").is_dir():
        return run_workspace(Workspace(config_path.parent), None, decide, progress, cancel, counter)
    raw, cfg, sha, paths = load_and_check(config_path)
    root = workspace_root(paths[0]) if paths else None
    if root is not None:
        log.warning(
            "These images are in the workspace %s, but this settings file is not its recap.yaml: its batch "
            "and image settings are not used. To use them: recap run %s", root, root,
        )  # fmt: skip
    out_dir = cfg.output.directory if cfg.output.directory.is_absolute() else config_path.parent / cfg.output.directory
    stamp = new_stamp(out_dir)
    log_file = setup_logging(out_dir, stamp)
    try:
        return _run(config_path, raw, cfg, sha, paths, stamp, out_dir, log_file, decide, progress, cancel, counter,
                    None)  # fmt: skip
    finally:
        close_logging()  # release run_<stamp>.log (Windows cannot move or delete an open file)


def run_workspace(
    ws,
    images: list[Path] | None = None,
    decide: DecideFn | None = None,
    progress: ProgressFn | None = None,
    cancel: Callable[[], bool] | None = None,
    counter: CountFn | None = None,
    spot_check: bool = True,
) -> RunResult:
    """Run a workspace (all its images, or `images`) with each image's own settings (recap.yaml →
    batch → image); the results are recorded in the workspace. The spot check (validation.enabled)
    reuses counts already in the workspace and counts more up to sample_size."""
    try:
        raw, cfg, sha = load_config(ws.settings_path)
    except (ValidationError, OSError) as e:
        raise ConfigError(f"{ws.settings_path}: {e}") from e
    except Exception as e:  # noqa: BLE001  (RecapError: no recap.yaml)
        raise ConfigError(str(e)) from e
    ws.scan()
    paths = [Path(p) for p in images] if images else ws.images(cfg)
    if not paths:
        raise ConfigError(f"No images to run in {ws.root}.")
    if cfg.validation.enabled and cfg.validation.previous_counts is None:
        d = cfg.model_dump()
        d["validation"].update(previous_counts=ws.db_path, top_up=True)  # reuse the workspace's counts
        cfg = PipelineConfig.model_validate(d)
    if not spot_check:
        d = cfg.model_dump()
        d["validation"]["enabled"] = False
        cfg = PipelineConfig.model_validate(d)
    out_dir = ws.output_dir(cfg)
    stamp = new_stamp(out_dir)
    log_file = setup_logging(out_dir, stamp)
    try:
        result = _run(ws.settings_path, raw, cfg, sha, paths, stamp, out_dir, log_file, decide, progress, cancel,
                      counter, ws)  # fmt: skip
    finally:
        close_logging()
    ws.record_run(out_dir / f"run_log_{stamp}.yaml")  # wherever output.directory is
    if "manual_counts" in result.files:  # the spot check's counts, also when the outputs are outside the folder
        from recap.counts import read_manual_counts

        for mc in read_manual_counts(Path(result.files["manual_counts"])):
            if mc.source == "new":
                ws.add_count(mc)
    ws.scan()
    return result


def _run(config_path, raw, cfg, sha, paths, stamp, out_dir, log_file, decide, progress, cancel, counter,
         ws) -> RunResult:  # fmt: skip
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
        run_batch(final, paths, out_dir, stamp, cache, progress, cancel, settings_for) if status == "completed" else []
    )
    if any("cancelled" in d for d in images_log):
        status = "cancelled"
    if any("n_nuclei" in d for d in images_log):
        files["all_nuclei"] = str(out_dir / f"all_nuclei_{stamp}.csv")
        files["image_summary"] = str(out_dir / f"image_summary_{stamp}.csv")

    final_json = final.model_dump(mode="json")
    final_cfg_path = out_dir / f"final_config_{stamp}.yaml"
    reusable = final.model_dump(mode="json")
    reusable["input"]["images"] = [str(p.resolve()) for p in paths]  # valid from any folder
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
            "images": images_log,
            "files": files,
        },
    )
    log.info("Done (%s). Run log and final_config written to %s", status, out_dir)
    return RunResult(status, out_dir, files, images_log, final)


def workspace_settings(cfg: PipelineConfig, ws):
    """settings_for(path) giving an image's settings with its batch and image levels, or None
    outside a workspace (or with input.use_workspace_settings: false)."""
    if ws is None:
        return None  # (in a workspace with input.use_workspace_settings: false, ws.resolve applies no levels)
    from recap.io import file_sha256

    if ws.upgrade is not None:
        log.warning(ws.upgrade.message())
    if cfg.input.use_workspace_settings:
        log.info("Workspace %s: each image with its batch and image settings", ws.root)
    done: dict[Path, object] = {}

    def settings_for(p: Path):
        if p not in done:
            done[p] = ws.resolve(file_sha256(p), cfg)
        return done[p]

    return settings_for
