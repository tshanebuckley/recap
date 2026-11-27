"""Command line interface. Only argument parsing, messages and exit codes live here; all work is
done by the library modules (see docs/architecture.md)."""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path
from typing import Annotated

import typer
import yaml
from pydantic import ValidationError

from recap import __version__, fiji
from recap.config import TEMPLATE, deprecation_notes, load_config
from recap.errors import Cancelled, ConfigError, RecapError

app = typer.Typer(
    add_completion=False,
    rich_markup_mode=None,
    no_args_is_help=True,
    help="recap (Reconstruction of Cellular Architecture and Phenotypes): count nuclei and score "
    "HSV-1 infection (VRC+) in fluorescence images.\n\n"
    "A workspace is a folder of images with one settings file (recap.yaml: every setting, also those of "
    "batches and single images), one database (recap-data/recap.db: runs, counts, masks, models) and "
    "optionally .recapignore (images to leave out). Start with `recap init FOLDER` (or `recap gui` and "
    "New... in the Images tab), "
    "check it with `recap validate FOLDER`, then `recap run FOLDER`. Commands use the workspace of the "
    "current folder when none is given. Run `recap COMMAND --help` for details; the full guide is README.md.",
)


def _version(value: bool) -> None:
    if value:
        typer.echo(f"recap {__version__}")
        raise typer.Exit()


@app.callback()
def _main(
    version: Annotated[
        bool, typer.Option("--version", callback=_version, is_eager=True, help="Show the version and exit.")
    ] = False,
) -> None:
    # Windows consoles redirected to a file use a legacy code page; never crash on a "−" or "≥".
    for stream in (sys.stdout, sys.stderr):
        if (getattr(stream, "encoding", "") or "").lower().replace("-", "") != "utf8":
            try:
                stream.reconfigure(errors="replace")
            except (AttributeError, ValueError):
                pass


ConfigArg = Annotated[Path, typer.Argument(exists=True, dir_okay=False, readable=True, help="YAML configuration file.")]
WsArg = Annotated[
    Path,
    typer.Argument(exists=True, help="Workspace folder or its recap.yaml (default: the current folder's workspace)."),
]


def _fail(msg: str, code: int = 1) -> None:
    typer.echo(msg, err=True)
    raise typer.Exit(code)


# ---------------------------------------------------------------- template / validate / run
@app.command()
def template(
    path: Annotated[Path, typer.Argument(help="Where to write the template.")] = Path("config.yaml"),
    force: Annotated[bool, typer.Option("--force", help="Overwrite an existing file.")] = False,
) -> None:
    """Write a commented settings file, to start workspaces from: `recap init FOLDER --from FILE`.

    Every setting is listed with its default and a short explanation. (A new workspace without
    --from gets the same settings.)
    """
    if path.exists() and not force:
        _fail(f"{path} exists; use --force to overwrite.")
    path.write_text(TEMPLATE, encoding="utf-8")
    typer.echo(f"Template written to {path}")


# ---------------------------------------------------------------- workspaces
def _announce(ws):
    if ws.upgrade is not None:
        typer.echo(ws.upgrade.message(), err=True)
    return ws


def _ws(path: Path = Path("."), settings: bool = True):
    """The workspace of a folder, recap.yaml or image; with settings=True it must have recap.yaml."""
    from recap.workspace import open_workspace

    try:
        ws = _announce(open_workspace(path))
        if settings:
            ws.settings()
    except (RecapError, ConfigError) as e:
        _fail(f"ERROR: {e}")
    return ws


def _is_workspace_target(path: Path) -> bool:
    from recap.workspace import SETTINGS_NAME

    p = Path(path)
    if p.is_dir():  # a workspace or a folder inside one (_ws finds it, or says how to make one)
        return True
    return p.name == SETTINGS_NAME


@app.command()
def init(
    folder: Annotated[Path, typer.Argument(exists=True, file_okay=False, help="Folder with the images.")] = Path("."),
    from_: Annotated[
        Path | None,
        typer.Option("--from", exists=True, dir_okay=False, help="Start from this settings file (copied)."),
    ] = None,
    force: Annotated[bool, typer.Option("--force", help="Replace an existing recap.yaml.")] = False,
) -> None:
    """Make a folder of images a workspace: its settings file recap.yaml and recap-data/.

    \b
    recap.yaml holds the settings for every image in the folder (list images to leave out in
    .recapignore: recap ignore). Batches and single images can then change some values (recap
    settings set, or the app's Settings tab); those are written into recap.yaml too, under
    batches: and images:. The images are moved into raw/unbatched/ (raw/batches/<batch>/ once
    they are in a batch), and recap's outputs go to processed/. Starting from an existing settings
    file copies it.
    Example:
      recap init experiment --from old_settings.yaml
    """
    from recap.workspace import init_workspace

    try:
        ws = _announce(init_workspace(folder, from_, force))
    except (RecapError, ConfigError, ValidationError) as e:
        _fail(f"ERROR: {e}")
    n = len(ws.status())
    typer.echo(f"Workspace ready: {ws.root}\n  settings: {ws.settings_path}\n  database: {ws.db_path}\n  {n} image(s)")
    if from_ is None:
        typer.echo("Check the channel numbers in recap.yaml (or in the app's Settings tab) before running.")


def _interactive() -> bool:
    """A person at a terminal (questions can be asked)."""
    return sys.stdin.isatty()


def _ask_batch(ws, n: int) -> tuple[str | None, str | None]:
    """Which batch the new images go into: an existing one, a new one, or none. (name, note)"""
    batches = [b["name"] for b in ws.batches()]
    typer.echo(f"Which staining batch are these {n} image(s) from?")
    for k, name in enumerate(batches, 1):
        typer.echo(f"  {k}) {name}")
    typer.echo("  n) a new batch\n  0) no batch")
    while True:
        choice = typer.prompt("Choice", default="n").strip().lower()
        if choice == "0":
            return None, None
        if choice == "n":
            name = typer.prompt("Name of the new batch (e.g. 'stain 2026-11-24')").strip()
            if name:
                return name, typer.prompt("Note (optional)", default="", show_default=False).strip() or None
        elif choice.isdigit() and 1 <= int(choice) <= len(batches):
            return batches[int(choice) - 1], None
        typer.echo("Please answer with a number from the list, n or 0.")


@app.command()
def add(
    sources: Annotated[list[Path], typer.Argument(exists=True, help="Image files and/or folders of images.")],
    workspace: Annotated[
        Path,
        typer.Option("--workspace", "-w", exists=True, file_okay=False, help="Workspace (default: current folder's)."),
    ] = Path("."),
    batch: Annotated[str | None, typer.Option(help="Put them in this staining batch (created if new).")] = None,
    no_batch: Annotated[bool, typer.Option("--no-batch", help="Add without a batch.")] = False,
    note: Annotated[str | None, typer.Option(help="Note for a new batch.")] = None,
) -> None:
    """Add images to a workspace: files, or every .tif / .tiff in folders (subfolders included).

    \b
    Images from outside the workspace are copied into raw/batches/<batch>/ (raw/unbatched/
    without a batch). Images whose content is already in the workspace are not copied again. Without
    --batch or --no-batch, recap asks which staining batch they belong to (an existing one, a
    new one, or none). The app's Add images does the same.
    Examples:
      recap add ~/scans/2026-11-24/ --batch "stain 2026-11-24"
      recap add new_image.tif
    """
    ws = _ws(workspace, settings=False)
    from recap.workspace import image_files_in

    files = image_files_in(sources)
    if not files:
        _fail("No .tif / .tiff images found in what was given.")
    if batch and no_batch:
        _fail("Give --batch or --no-batch, not both.")
    if not batch and not no_batch:
        if not _interactive():
            _fail("Say which batch: --batch NAME, or --no-batch.")
        typer.echo(f"{len(files)} image(s) found.")
        batch, ask_note = _ask_batch(ws, len(files))
        note = note or ask_note
    try:
        res = ws.add_images(files, batch, note)
    except RecapError as e:
        _fail(f"ERROR: {e}")
    typer.echo(res.message())
    typer.echo("Run them with: recap run --out-of-date")


@app.command()
def ignore(
    paths: Annotated[list[Path] | None, typer.Argument(help="Images or folders inside the workspace.")] = None,
    workspace: Annotated[
        Path,
        typer.Option("--workspace", "-w", exists=True, file_okay=False, help="Workspace (default: current folder's)."),
    ] = Path("."),
) -> None:
    """Leave images (or whole folders) out of the workspace; without paths, list what is ignored.

    \b
    They are written to the workspace's .recapignore (one path or pattern per line; edit it by hand
    for patterns such as *_bad.tif). Ignored images are not run, counted, tuned or trained on and
    are not in reports; what recap knows about them is kept. The app's Ignore does the same.
    Examples:
      recap ignore data/day79_03.tif
      recap ignore old_exports/
      recap unignore data/day79_03.tif
    """
    ws = _ws(paths[0] if paths else workspace, settings=False)
    if not paths:
        rules = ws.ignore_list.rules()
        ignored = [r for r in ws.status() if r["state"] == "ignored"]
        typer.echo(f"{ws.ignore_list.path}: " + (", ".join(rules) if rules else "nothing ignored"))
        for r in ignored:
            typer.echo(f"  {r['path']}")
        return
    try:
        done = ws.ignore(paths)
    except RecapError as e:
        _fail(f"ERROR: {e}")
    typer.echo(f"Ignored: {', '.join(done)}" if done else "Already ignored.")


@app.command()
def unignore(
    paths: Annotated[list[Path], typer.Argument(help="Ignored images or folders inside the workspace.")],
) -> None:
    """Bring ignored images (or folders) back into the workspace (see `recap ignore`)."""
    ws = _ws(paths[0], settings=False)
    try:
        done = ws.unignore(paths)
    except RecapError as e:
        _fail(f"ERROR: {e}")
    typer.echo(f"Back in the workspace: {', '.join(done)}" if done else "None of them was ignored.")


@app.command()
def validate(target: WsArg = Path(".")) -> None:
    """Check a workspace's settings (or a settings file to start one from) without running anything.

    Prints every setting as it will be used (defaults and derived values filled in) and, for a
    workspace, the number of images (and of ignored ones). Exits with status 1 if the settings
    are invalid.
    """
    ws = None
    if _is_workspace_target(target):
        ws = _ws(target)
        cfg = ws.settings()
        ws.scan()
        paths = ws.images(cfg)
    else:
        try:
            _, cfg, _ = load_config(target)
        except (ValidationError, ConfigError) as e:
            _fail(f"Invalid configuration:\n{e}")
    for note in deprecation_notes(cfg):
        typer.echo(f"note: {note}", err=True)
    resolved = cfg.model_dump(mode="json")
    resolved["segmentation"] = cfg.segmentation.resolved().model_dump(mode="json")
    typer.echo(yaml.safe_dump(resolved, sort_keys=False, default_flow_style=None))
    if ws is None:
        typer.echo(f"OK. Start a workspace from it: recap init FOLDER --from {target}")
        return
    n_ignored = len(ws.image_files(include_ignored=True)) - len(paths)
    typer.echo(f"OK: {len(paths)} image(s) found" + (f", {n_ignored} ignored (.recapignore)." if n_ignored else "."))


def _terminal_decide(text: str) -> str:
    typer.echo("\n" + text + "\n")
    choice = ""
    while choice not in ("r", "s", "c"):
        choice = typer.prompt("[r]un all / [s]ave counts / [c]ancel").strip().lower()[:1]
    return {"r": "run_all", "s": "save", "c": "cancel"}[choice]


@app.command()
def run(
    target: WsArg = Path("."),
    image: Annotated[
        list[Path] | None,
        typer.Option("--image", "-i", dir_okay=False, help="Only this image (repeatable; an old path works)."),
    ] = None,
    out_of_date: Annotated[
        bool, typer.Option("--out-of-date", help="Only images not run yet or whose settings changed since.")
    ] = False,
    no_spot_check: Annotated[bool, typer.Option("--no-spot-check", help="Skip the manual spot check.")] = False,
    settings: Annotated[
        Path | None,
        typer.Option(
            "--settings",
            exists=True,
            dir_okay=False,
            help="Comparison run: this settings file instead of recap.yaml's own sections.",
        ),  # fmt: skip
    ] = None,
) -> None:
    """Run recap on a workspace: every image with its own settings (recap.yaml → batch → image).

    \b
    Steps: optional manual spot check (validation.enabled: counts already in the workspace are
    reused, more images are counted in Fiji up to sample_size), then every image: segment,
    measure, filter, call VRC+. Each image's tables, label images and QC overlay go to its folder,
    processed/batches/<batch>/<image>/ (or processed/unbatched/<image>/); the run's summary with QC
    flags and count ranges, run log and final config go to processed/runs/<date>/. The results are
    recorded in the workspace. See docs/output_columns.md for every column. Images in the
    workspace's .recapignore are left out.

    \b
    Comparing methods: recap run FOLDER --settings other.yaml runs the same images (with their batch
    and image settings) using other.yaml instead of recap.yaml's own sections, into
    processed/comparisons/<other.yaml's output.directory>/, without the spot check and without
    replacing the images' results. Then recap report FOLDER compares it with the workspace's results.
    """
    from recap.fijicount import FijiCounter, FijiSession
    from recap.io import file_sha256
    from recap.runner import run_workspace

    if not _is_workspace_target(target):
        _fail(f"{target.name} is a settings file, not a workspace. Every run happens in a workspace:\n"
              f"  recap init FOLDER --from {target}    (FOLDER: the folder of your images)\n"
              f"  recap run FOLDER")  # fmt: skip
    ws = _ws(target)
    cfg = ws.settings()
    loc = None
    if cfg.validation.enabled and not no_spot_check:  # Fiji is needed only for the spot check
        try:
            loc = ws.fiji()
        except RecapError as e:
            _fail(f"Invalid configuration:\n{e}")
    session = FijiSession(loc.folder if loc else None,
                          ws.folder("fiji") / f"session_{dt.datetime.now():%Y%m%d_%H%M%S}")  # fmt: skip
    counter = FijiCounter(session, cfg.validation.count_infected, ask_import=lambda q: typer.confirm(q, default=True))
    try:
        images = list(image or [])
        if out_of_date:
            ws.scan()
            stale = set(ws.out_of_date())
            images = [ws.current_path(p) for p in (images or ws.images(cfg))]
            images = [p for p in images if file_sha256(p) in stale]
            if not images:
                typer.echo("Every image is up to date.")
                return
            typer.echo(f"{len(images)} image(s) not run yet or out of date.")
        full = not images  # the spot check samples the whole workspace, as Run all in the app
        result = run_workspace(ws, images or None, _terminal_decide, counter=counter,
                               spot_check=full and not no_spot_check, settings_file=settings)  # fmt: skip
    except ConfigError as e:
        _fail(f"Invalid configuration:\n{e}")
    except Cancelled as e:
        _fail(f"Cancelled: {e}")
    except RecapError as e:
        _fail(f"ERROR: {e}")
    finally:
        session.close()
    if result.status == "cancelled":
        raise typer.Exit(1)


@app.command()
def status(target: WsArg = Path(".")) -> None:
    """List the images in a workspace and what has been done to each.

    Scans the folder (images, `recap run` outputs, manual_counts CSVs) into recap-data/recap.db,
    then prints one line per image: batch, latest program count, whether it is up to date with
    its settings, manual counts and who made them, corrected masks, own settings and QC flags.
    """
    ws = _ws(target, settings=False)
    found = ws.scan()
    rows = ws.status()
    typer.echo(f"{ws.root}  ({len(rows)} images; new or changed: {found['images']} images, {found['runs']} runs, "
               f"{found['counts']} counts)")  # fmt: skip
    if not ws.has_settings:
        typer.echo(f"No recap.yaml yet: recap init {ws.root} --from <settings file>")
    typer.echo(f"\n{'image':36} {'batch':14} {'program':>12} {'state':11} {'manual':>16} {'edited':>6} own  qc")
    for r in rows:
        prog = f"{r['n_nuclei']} / {r['n_infected']}+" if r["n_nuclei"] is not None else "-"
        man = f"{r['manual_nuclei']} ({r['counted_by']})" if r["manual_counts"] else "-"
        typer.echo(
            f"{r['name'][:36]:36} {(r['batch'] or '-')[:14]:14} {prog:>12} {r['state']:11} {man[:16]:>16} "
            f"{r['edited_masks'] or '-':>6} {'yes' if r['own_settings'] else '-':3}  {r['qc_flags'] or ''}"
        )


# ---------------------------------------------------------------- counting
@app.command()
def count(
    images: Annotated[list[Path], typer.Argument(dir_okay=False, help="Images to count.")],
    by: Annotated[str | None, typer.Option("--by", help="Who is counting (default: the remembered name).")] = None,
    remember: Annotated[bool, typer.Option("--remember", help="Remember --by for later counts.")] = False,
    original: Annotated[
        bool, typer.Option("--original", help="Show the image file as it is, not recap's adjusted view.")
    ] = False,
    no_infection: Annotated[bool, typer.Option("--no-infection", help="Count nuclei only, no VRC+ marking.")] = False,
) -> None:
    """Count nuclei by hand in Fiji; the counts go into the images' workspace.

    recap opens each image in Fiji with the Multi-point tool, in recap's adjusted view (DAPI blue,
    ICP8 green, with the image's own settings) unless --original. Counter 0 = VRC-, counter 1 =
    VRC+: double-click the Multi-point tool to switch. Click "Done counting": Fiji shows the totals
    and asks whether to import them. If Fiji is closed first, recap asks here whether to keep the
    points. The app's "Count in Fiji" does exactly the same.

    \b
    Example:
      recap count data/img01.tif data/img02.tif --by ana --remember
    """
    from recap.counting import COUNTER_HELP, count_in_fiji, fiji_session
    from recap.counts import default_counter_name, remember_counter_name

    ws = _ws(images[0])
    if remember and by:
        remember_counter_name(by)
    by = by or default_counter_name()
    ws.scan()
    typer.echo(COUNTER_HELP + f"\nCounting as {by}.\n")
    try:
        session = fiji_session(ws)
    except RecapError as e:
        _fail(f"ERROR: {e}")
    added = 0
    try:
        for k, img in enumerate(images, 1):
            out = count_in_fiji(ws, _sha(ws, img), session, by, lambda q: typer.confirm(q, default=True),
                                original, not no_infection, f"image {k} of {len(images)}")  # fmt: skip
            if out.status == "cancel":
                typer.echo("Stopped.")
                break
            if out.status == "skip":
                typer.echo(f"{img.name}: skipped")
                continue
            mc = out.count
            added += 1
            inf = f", {mc.infected} VRC+" if mc.infected is not None else ""
            typer.echo(f"{img.name}: {mc.nuclei} nuclei{inf}  -> {ws.db_path}")
    except RecapError as e:
        _fail(f"ERROR: {e}")
    finally:
        session.close()
    typer.echo(f"{added} count(s) added.")


def _types(text: str | None) -> list[int] | None:
    if text is None:
        return None
    try:
        return [int(t) for t in text.replace(" ", "").split(",") if t]
    except ValueError:
        _fail(f"Invalid type list {text!r}: use comma-separated numbers, e.g. 2,3")


@app.command("import-counts")
def import_counts_cmd(
    image: Annotated[Path, typer.Argument(dir_okay=False, help="The image that was counted.")],
    counts: Annotated[
        Path | None, typer.Argument(exists=True, dir_okay=False, help="Cell Counter XML or points CSV.")
    ] = None,
    out: Annotated[
        Path | None,
        typer.Option("--out", "-o", help="Add to this manual_counts CSV instead (for images outside a workspace)."),
    ] = None,
    by: Annotated[str | None, typer.Option("--by", help="Who counted (default: the remembered name).")] = None,
    fmt: Annotated[
        str, typer.Option("--format", help="auto (.xml = Cell Counter, else CSV), cellcounter, csv.")
    ] = "auto",
    pixel_size: Annotated[
        float | None, typer.Option(help="CSV coordinates are calibrated: units (e.g. um) per pixel.")
    ] = None,
    uninfected_types: Annotated[
        str | None,
        typer.Option(help="Marker types counted as VRC-, comma-separated (default: Cell Counter 1, CSV counter 0)."),
    ] = None,
    infected_types: Annotated[
        str | None, typer.Option(help="Marker types counted as VRC+ (default: Cell Counter 2, CSV counter 1).")
    ] = None,
    no_infection: Annotated[bool, typer.Option("--no-infection", help="Infection was not marked.")] = False,
    nuclei: Annotated[int | None, typer.Option(min=0, help="Typed total instead of a file.")] = None,
    vrc_pos: Annotated[int | None, typer.Option(min=0, help="Typed VRC+ total (with --nuclei).")] = None,
) -> None:
    """Import counts made in plain Fiji into the image's workspace (the app: More > Import counts).

    \b
    Inputs: Cell Counter "Save Markers" XML (pixel coordinates), or the Results table of
    Analyze > Measure on a Multi-point selection saved as CSV (X, Y, Counter), or typed totals
    (--nuclei, --vrc-pos). Counts with points let `recap report` find missed, merged and extra
    nuclei, not just compare totals. Images outside a workspace: use --out to write a CSV.

    \b
    Examples:
      recap import-counts img01.tif img01_markers.xml --by ana
      recap import-counts img01.tif img01_points.csv --pixel-size 0.203 --by ana
      recap import-counts img01.tif --nuclei 472 --vrc-pos 240 --by ben
    """
    from recap.importers import add_to_counts_file, import_counts
    from recap.workspace import workspace_root

    if fmt not in ("auto", "cellcounter", "csv"):
        _fail("--format must be auto, cellcounter or csv")
    options = dict(
        fmt=fmt, pixel_size=pixel_size, uninfected_types=_types(uninfected_types),
        infected_types=_types(infected_types), no_infection=no_infection, nuclei=nuclei, vrc_pos=vrc_pos,
    )  # fmt: skip
    try:
        if out is None and workspace_root(image) is not None:
            ws = _ws(image, settings=False)
            ws.scan()
            mc, notes, added = ws.import_count(image, counts, by, **options)
            replaced, out, verb = not added, ws.db_path, "Already there"
        elif out is None:
            _fail(f"{image} is in no workspace: give --out manual_counts.csv, or make its folder one (recap init).")
        else:
            res = import_counts(image, counts, fmt, pixel_size, options["uninfected_types"],
                                options["infected_types"], no_infection, nuclei, vrc_pos, by)  # fmt: skip
            mc, notes = res.count, res.notes
            replaced, verb = add_to_counts_file(out, mc), "Replaced"
    except RecapError as e:
        _fail(f"ERROR: {e}")
    for n in notes:
        typer.echo(f"note: {n}")
    inf = "VRC+ not recorded" if mc.infected is None else f"{mc.infected} VRC+, {mc.nuclei - mc.infected} VRC-"
    typer.echo(
        f"{verb if replaced else 'Added'}: {mc.image_name}: {mc.nuclei} nuclei ({inf})"
        f"{', ' + str(len(mc.points)) + ' points' if mc.points else ''}"
        f"{', counted by ' + mc.counted_by if mc.counted_by else ''}  ->  {out}"
    )


@app.command()
def report(
    paths: Annotated[
        list[Path] | None,
        typer.Argument(
            exists=True,
            file_okay=False,
            metavar="[WORKSPACE | RUN_DIR...]",
            help="A workspace (default: the current folder's), or recap output folders.",
        ),
    ] = None,  # fmt: skip
    name: Annotated[
        list[str] | None,
        typer.Option("--name", "-n", metavar="NAME", help="Method name per run folder, in order."),
    ] = None,
    manual: Annotated[
        list[Path] | None,
        typer.Option(
            "--manual",
            "-m",
            exists=True,
            dir_okay=False,
            metavar="FILE",
            help="With run folders: manual_counts CSV or recap.db (repeatable).",
        ),
    ] = None,  # fmt: skip
    out: Annotated[
        Path | None, typer.Option("--out", "-o", help="Report folder (default: processed/reports/<date>).")
    ] = None,
    iou: Annotated[float, typer.Option(min=0.05, max=1.0, help="Min overlap to match nuclei.")] = 0.5,
) -> None:
    """Compare recap's counts with the manual counts across all images.

    \b
    For a workspace: its results (each image's latest run) and each comparison run against every
    manual count in it (the app: More > Report).
    For run folders: recap report processed/runs/DATE processed/comparisons/NAME -m recap-data/recap.db
    Writes program_counts.csv, vs_manual.csv (per image, method and counter), method_agreement.csv,
    inter_rater.csv, summary.csv (bias, limits of agreement, VRC+ agreement, missed/merged/extra),
    plots, and report.md. Manual counts match images by file content, not name.
    """
    from recap.report import build_report

    paths = list(paths or [Path(".")])
    try:
        from recap.workspace import workspace_root

        single = paths[0] if len(paths) == 1 else None
        if single is not None and not manual and not list(single.glob("run_log_*.yaml")) and workspace_root(single):
            res = _ws(paths[0], settings=False).report(out, iou)
        else:
            names = list(name or [])
            if names and len(names) != len(paths):
                _fail(f"--name given {len(names)} time(s) for {len(paths)} run folder(s)")
            res = build_report(list(zip(names or [r.name for r in paths], paths)), list(manual or []),
                               out or Path("report"), iou)  # fmt: skip
    except RecapError as e:
        _fail(f"ERROR: {e}")
    if res.summary is not None and len(res.summary):
        wanted = ("method", "n_images", "manual_total", "program_total", "total_pct_difference", "mean_difference",
                  "loa_low", "loa_high", "infection_agreement")  # fmt: skip
        cols = [c for c in wanted if c in res.summary]
        typer.echo(res.summary[cols].to_string(index=False))
        typer.echo("")
    typer.echo(f"Report written to {res.out}/ (start with report.md)")


# ---------------------------------------------------------------- compare
@app.command()
def compare(
    run_a: Annotated[Path, typer.Argument(exists=True, file_okay=False, help="Output folder of run A.")],
    run_b: Annotated[Path, typer.Argument(exists=True, file_okay=False, help="Output folder of run B.")],
    names: Annotated[tuple[str, str], typer.Option(help="Labels for the two runs.")] = ("A", "B"),
    iou: Annotated[float, typer.Option(min=0.05, max=1.0, help="Min overlap to match nuclei.")] = 0.5,
    out: Annotated[Path, typer.Option(help="Folder for comparison.csv and overlays.")] = Path("compare"),
) -> None:
    """Compare two runs on the same images.

    Reports counts, matched nuclei and VRC+ agreement.

    Typical use: StarDist vs Cellpose, i.e. a run of the workspace (processed/runs/DATE) and a
    comparison run (processed/comparisons/NAME); each image's files are found through the run log.
    Per image: counts and VRC+ calls of both runs, nuclei found by both (matched by overlap),
    nuclei found by only one, infection agreement on matched nuclei, and an overlay PNG
    (run A magenta, run B green). For many images and manual counts, use `recap report`.
    """
    from recap.compare import compare_runs

    compare_runs(run_a, run_b, names, iou, out)


# ---------------------------------------------------------------- desktop app
@app.command()
def gui(
    path: Annotated[
        Path | None,
        typer.Argument(
            exists=True,
            help="A workspace folder (default: the last one used, else the example workspace).",
        ),
    ] = None,
    napari: Annotated[
        bool, typer.Option("--napari", help="Full napari with recap's panels docked (all of napari's tools).")
    ] = False,
) -> None:
    """Open the recap desktop app.

    The image with one side panel: Images (workspace, images, runs, batches, adding images),
    Count (click = VRC-, Shift+click = VRC+, right-click = remove), Fix outlines (draw, delete,
    split, merge) and Settings (step by step with a live preview). --napari opens the full napari
    with recap's panels instead. Only one copy of the app runs at a time: starting it again
    brings the open window to the front.
    """
    try:
        from recap.gui.app import run as run_app
    except ImportError as e:
        _fail(f"The desktop app needs napari and Qt ({e}). They are in both pixi environments: pixi install")
    try:
        started = run_app(path, full_napari=napari)
    except (ConfigError, ValidationError) as e:
        _fail(f"Cannot open the app:\n{e}")
    if not started:
        typer.echo("recap is already running: its window was brought to the front.")


@app.command("desktop-icon")
def desktop_icon(
    remove: Annotated[bool, typer.Option("--remove", help="Only remove recap's icons; create none.")] = False,
) -> None:
    """Put a recap icon on the desktop that opens the app (`recap gui`).

    Old recap icons are removed first, so running this again (e.g. after moving the recap folder)
    leaves exactly one. Linux: desktop and applications menu. Windows: desktop and Start menu.
    macOS: recap.app on the desktop. Run it from the environment the icon should use
    (e.g. `pixi run recap desktop-icon`).
    """
    from recap import desktop
    from recap.errors import RecapError

    try:
        if remove:
            removed, created = desktop.remove_icons(desktop.usual_places(sys.platform)), []
        else:
            removed, created = desktop.install_icon()
    except RecapError as e:
        _fail(str(e))
    for p in removed:
        typer.echo(f"removed: {p}")
    for p in created:
        typer.echo(f"created: {p}")
    if not removed and not created:
        typer.echo("No recap icons found.")
    elif created:
        typer.echo(f"The icon runs: {' '.join(desktop.launch_command().argv)}")


# ---------------------------------------------------------------- batches and settings levels
batch_app = typer.Typer(rich_markup_mode=None)
app.add_typer(batch_app, name="batch", help="Staining batches: images stained together share settings.")
settings_app = typer.Typer(rich_markup_mode=None)
app.add_typer(
    settings_app,
    name="settings",
    help="Settings per level: workspace (recap.yaml) → staining batch → image (the most specific wins).",
)
WorkspaceOpt = Annotated[
    Path | None,
    typer.Option("--workspace", "-w", exists=True, help="Workspace folder (default: the current folder's)."),
]


def _sha(ws, image: Path) -> str:
    """The image a path names (also a path recap has moved it from since), or a clear error."""
    try:
        return ws.sha_of(image)
    except RecapError as e:
        _fail(f"ERROR: {e} (not in this workspace, and not an image recap has moved)")


def _shas(ws, images: list[Path]) -> list[str]:
    ws.scan()
    return [_sha(ws, p) for p in images]


@batch_app.command("assign")
def batch_assign(
    name: Annotated[str, typer.Argument(help="Batch name, e.g. 'stain 2026-11-03'. Created if new.")],
    images: Annotated[list[Path], typer.Argument(dir_okay=False, help="Images in this batch.")],
    note: Annotated[str | None, typer.Option(help="Note for a new batch.")] = None,
) -> None:
    """Put images in a staining batch (an image is in at most one).

    Their raw files move to raw/batches/<batch>/ and their outputs to processed/batches/<batch>/."""
    ws = _ws(images[0], settings=False)
    ws.assign_batch(_shas(ws, images), name, note)
    typer.echo(f"{len(images)} image(s) in batch {name!r}.")


@batch_app.command("remove")
def batch_remove(images: Annotated[list[Path], typer.Argument(dir_okay=False)]) -> None:
    """Take images out of their batch."""
    ws = _ws(images[0], settings=False)
    ws.assign_batch(_shas(ws, images), None)
    typer.echo(f"{len(images)} image(s) taken out of their batch.")


@batch_app.command("list")
def batch_list(target: WsArg = Path(".")) -> None:
    """Batches, their images and their settings."""
    ws = _ws(target, settings=False)
    rows = ws.batches()
    if not rows:
        typer.echo("No batches yet (recap batch assign).")
    for b in rows:
        st = ws.level_settings("batch", b["name"])
        typer.echo(f"{b['name']}: {b['n_images']} image(s)" + (f"  ({b['note']})" if b["note"] else ""))
        for k, v in st.items():
            typer.echo(f"    {k}: {v}")


@batch_app.command("rename")
def batch_rename(old: str, new: str, workspace: WorkspaceOpt = None) -> None:
    """Rename a batch (its images and settings stay)."""
    ws = _ws(workspace or Path("."), settings=False)
    try:
        ws.rename_batch(old, new)
    except RecapError as e:
        _fail(f"ERROR: {e}")
    typer.echo(f"Batch {old!r} is now {new!r}.")


@batch_app.command("delete")
def batch_delete(name: str, workspace: WorkspaceOpt = None) -> None:
    """Delete a batch and its settings; its images stay, in no batch."""
    ws = _ws(workspace or Path("."), settings=False)
    ws.delete_batch(name)
    typer.echo(f"Batch {name!r} deleted.")


def _parse_pairs(pairs: list[str]) -> dict:
    out = {}
    for p in pairs:
        if "=" not in p:
            _fail(f"Expected key=value, got {p!r} (e.g. filters.min_focus=0.6)")
        k, v = p.split("=", 1)
        out[k.strip()] = yaml.safe_load(v)
    return out


def _level(ws, batch: str | None, image: Path | None) -> tuple[str, str, str]:

    if batch and image:
        _fail("Give --batch or --image, not both.")
    if image:
        ws.scan()
        return "image", _sha(ws, image), str(image)
    if batch:
        if batch not in {b["name"] for b in ws.batches()}:
            _fail(f"No batch {batch!r} (recap batch list).")
        return "batch", batch, f"batch {batch!r}"
    return "workspace", "", f"the workspace ({ws.settings_path.name})"


@settings_app.command("set")
def settings_set(
    pairs: Annotated[list[str], typer.Argument(help="key=value, e.g. filters.min_focus=0.6")],
    batch: Annotated[str | None, typer.Option(help="Set for this staining batch.")] = None,
    image: Annotated[Path | None, typer.Option(dir_okay=False, help="Set for this image.")] = None,
    reason: Annotated[str | None, typer.Option(help="Required for an image: why it needs its own settings.")] = None,
    workspace: WorkspaceOpt = None,
) -> None:
    """Set values for the workspace, a batch or an image: all written into recap.yaml.

    Values are YAML: normalization.vrc_range=[5,30], filters.min_focus=0.6, filters.min_circularity=null.
    Other values of that level stay. The workspace's go into recap.yaml's own sections, a batch's
    under batches: NAME, an image's under images: PATH. The app's Settings tab saves the same way.

    \b
    Examples:
      recap settings set filters.min_focus=0.6
      recap settings set --batch "stain 2026-11-03" normalization.vrc_range=[5,30]
      recap settings set --image img07.tif measurement.vrc_threshold=0.25 --reason "weak ICP8 staining"
    """
    ws = _ws(image or workspace or Path("."))
    level, target, what = _level(ws, batch, image)
    try:
        ws.update_level(level, target, _parse_pairs(pairs), reason=reason)
    except (RecapError, ConfigError) as e:
        _fail(f"ERROR: {e}")
    typer.echo(f"Saved for {what}: {_parse_pairs(pairs)}")


@settings_app.command("unset")
def settings_unset(
    keys: Annotated[list[str], typer.Argument(help="Keys to remove, e.g. filters.min_focus")],
    batch: Annotated[str | None, typer.Option(help="From this staining batch.")] = None,
    image: Annotated[Path | None, typer.Option(dir_okay=False, help="From this image.")] = None,
    workspace: WorkspaceOpt = None,
) -> None:
    """Remove values from a batch or an image: the level above applies again."""
    ws = _ws(image or workspace or Path("."))
    level, target, what = _level(ws, batch, image)
    if level == "workspace":
        _fail("Give --batch or --image (the workspace's values are recap.yaml: set them instead).")
    ws.update_level(level, target, {}, remove=keys)
    typer.echo(f"Removed from {what}: {', '.join(keys)}")


@settings_app.command("clear")
def settings_clear(
    batch: Annotated[str | None, typer.Option(help="This staining batch.")] = None,
    image: Annotated[Path | None, typer.Option(dir_okay=False, help="This image.")] = None,
    workspace: WorkspaceOpt = None,
) -> None:
    """Remove all of a batch's or an image's settings (the app: Guided mode > Clear this image's settings)."""
    ws = _ws(image or workspace or Path("."))
    level, target, what = _level(ws, batch, image)
    if level == "workspace":
        _fail("Give --batch or --image.")
    ws.set_level_settings(level, target, {})
    typer.echo(f"Settings of {what} cleared.")


@settings_app.command("export")
def settings_export(
    dest: Annotated[Path, typer.Argument(dir_okay=False, help="The file to write (.yaml; .zip with --with-model).")],
    workspace: WorkspaceOpt = None,
    with_images: Annotated[bool, typer.Option("--with-images", help="Also the single-image settings.")] = False,
    with_model: Annotated[
        bool, typer.Option("--with-model", help="A .zip with the settings and the trained model they use.")
    ] = False,
    force: Annotated[bool, typer.Option("--force", help="Replace an existing file.")] = False,
) -> None:
    """Write the workspace's whole settings file (recap.yaml) to share or reuse.

    \b
    Batch settings go with it; single-image settings only with --with-images (they name this
    workspace's files). Use it elsewhere with recap settings import FILE or recap init FOLDER --from FILE.
    If the settings use one of this workspace's trained models, --with-model puts both in one .zip:
    importing it adds the model to the other workspace and makes its settings use it.
    """
    from recap.transfer import export_settings

    ws = _ws(workspace or Path("."))
    try:
        res = export_settings(ws, dest, images=with_images, force=force, with_model=with_model)
    except (RecapError, ConfigError) as e:
        _fail(f"ERROR: {e}")
    typer.echo(f"Settings written to {res.path}")
    for n in res.notes:
        typer.echo(f"note: {n}")


@settings_app.command("import")
def settings_import(
    src: Annotated[
        Path, typer.Argument(exists=True, dir_okay=False, help="A settings file (.yaml), or settings + model (.zip).")
    ],
    workspace: WorkspaceOpt = None,
    with_batches: Annotated[bool, typer.Option("--with-batches", help="Also take the file's batch settings.")] = False,
    with_images: Annotated[
        bool, typer.Option("--with-images", help="Also take the file's single-image settings.")
    ] = False,
) -> None:
    """Make a settings file the workspace's settings (recap.yaml is backed up first).

    \b
    The file's sections replace recap.yaml's own (every image's settings). The workspace keeps its
    batch and single-image settings unless --with-batches / --with-images (then the file's entries
    replace those of the same name). Images whose settings changed are out of date: recap run --out-of-date.
    The backup is in recap-data/settings_backups/. A .zip from settings export --with-model also adds its
    model as the next version and uses it; a plain file that names a trained model this workspace does
    not have leaves the workspace's model as it is.
    """
    from recap.transfer import import_settings

    ws = _ws(workspace or Path("."), settings=False)
    try:
        res = import_settings(ws, src, batches=with_batches, images=with_images)
    except (RecapError, ConfigError) as e:
        _fail(f"ERROR: {e}")
    typer.echo(f"{src.name} is now the workspace's settings ({res.path})."
               + (f" Previous recap.yaml saved as {res.backup}." if res.backup else ""))  # fmt: skip
    for n in res.notes:
        typer.echo(f"note: {n}")
    typer.echo("Run the images whose settings changed: recap run --out-of-date")


@settings_app.command("show")
def settings_show(image: Annotated[Path, typer.Argument(dir_okay=False)]) -> None:
    """An image's effective settings: what differs from recap.yaml, and which level set it."""
    from recap.levels import get_path

    ws = _ws(image)
    ws.scan()
    base = ws.settings()
    r = ws.resolve(_sha(ws, image), base)
    typer.echo(f"{image.name}: {r.label}" + (f"   batch: {r.batch}" if r.batch else ""))
    if r.reason:
        typer.echo(f"reason for its own settings: {r.reason}")
    b, eff = base.model_dump(mode="json"), r.cfg.model_dump(mode="json")
    if not r.sources:
        typer.echo("  (no batch or image settings: recap.yaml applies as is)")
    for k, src in sorted(r.sources.items()):
        typer.echo(f"  {k}: {get_path(eff, k)}   (recap.yaml: {get_path(b, k)}; set by {src})")


# ---------------------------------------------------------------- tuning and training
@app.command()
def tune(
    target: WsArg = Path("."),
    trials: Annotated[int, typer.Option(min=1, help="Settings combinations to try (the first is yours).")] = 30,
    seed: Annotated[int | None, typer.Option(help="Random seed, for a repeatable search.")] = None,
    apply_to: Annotated[
        str | None,
        typer.Option("--apply-to", help="Use the best values right away: 'workspace' or 'batch:NAME'."),
    ] = None,
) -> None:
    """Find better settings by scoring them against your corrected masks.

    Tries combinations of the settings that matter most (VRC weight, StarDist threshold, fragment
    merging, focus / circularity / border filters) on the workspace's images that have a corrected
    mask. Each is scored by matching nuclei to the corrected mask (F1: balance of real nuclei found
    and extra nuclei avoided). All trials are kept in the workspace. --apply-to writes the best
    values into recap.yaml or a batch (the app: Tuning > Apply).
    """
    from recap.tune import apply_tuned
    from recap.tune import tune as run_tune

    ws = _ws(target)
    try:
        res = run_tune(ws, trials, seed, progress=lambda i, n, r: typer.echo(f"  {i}/{n}: F1 {r['f1']:.3f}"))
    except RecapError as e:
        _fail(f"ERROR: {e}")
    b, t = res.baseline, res.best_scores
    typer.echo(f"\nYour settings:  F1 {b['f1']:.3f}  precision {b['precision']:.3f}  recall {b['recall']:.3f}  "
               f"count error {b['count_error']:.1%}")  # fmt: skip
    typer.echo(f"Best found:     F1 {t['f1']:.3f}  precision {t['precision']:.3f}  recall {t['recall']:.3f}  "
               f"count error {t['count_error']:.1%}")  # fmt: skip
    for k, v in res.best_params.items():
        typer.echo(f"  {k}: {v}")
    typer.echo(f"\nAll trials: {res.best_config.parent / 'trials.csv'}")
    if apply_to:
        level, _, batch = apply_to.partition(":")
        try:
            stored = apply_tuned(ws, res.best_params, level, batch or None)
        except (ValueError, RecapError, ConfigError) as e:
            _fail(f"ERROR: {e}")
        typer.echo(f"Applied to {apply_to}: {stored or 'nothing changed'}. Rerun: recap run --out-of-date")
    else:
        typer.echo("To use them: recap tune --apply-to workspace (or batch:NAME), or Apply in the app.")


@app.command()
def train(
    target: WsArg = Path("."),
    method: Annotated[str, typer.Option(help="stardist or cellpose (cellpose: untested).")] = "stardist",
    start: Annotated[
        str | None,
        typer.Option(help="Model to start from: a version (v2), a name or a folder. Default: the one in use."),
    ] = None,
    epochs: Annotated[int, typer.Option(min=1)] = 20,
    steps: Annotated[int, typer.Option(min=1, help="Training steps per epoch (StarDist).")] = 50,
    val_fraction: Annotated[float, typer.Option(min=0.05, max=0.9, help="Share of images held out.")] = 0.2,
    note: Annotated[str | None, typer.Option(help="Stored with the version.")] = None,
    use: Annotated[bool, typer.Option("--use", help="Use the new version for this workspace right away.")] = False,
) -> None:
    """Fine-tune the segmentation model on your corrected masks; saved as a new version.

    Versions go to recap-data/models/<method>/vN and are never overwritten. Images are split into
    training and validation sets; the new model and the one it started from are scored on the
    validation images, so you can see whether training helped. Use a version with
    `recap models use vN` (or --use; the app: Training > Use selected version).
    Training runs on the CPU: expect minutes to an hour depending on images and epochs.
    """
    from recap.train import train as run_train

    ws = _ws(target)
    try:
        res = run_train(ws, method, start, epochs, steps, val_fraction, note=note, progress=typer.echo)
    except RecapError as e:
        _fail(f"ERROR: {e}")
    n, s0 = res.metrics["new"], res.metrics["start"]
    typer.echo(f"\nSaved {res.family} v{res.version}: {res.path}")
    typer.echo(f"Validation F1: new {n['f1']:.3f}, starting model {s0['f1']:.3f}")
    if use:
        ws.use_model(res.family, res.version)
        typer.echo(f"This workspace now uses v{res.version}. Rerun: recap run --out-of-date")
    else:
        typer.echo(f"To use it:  recap models use v{res.version}")


models_app = typer.Typer(rich_markup_mode=None)
app.add_typer(models_app, name="models", help="List, compare and choose trained model versions.")


@models_app.command("list")
def models_list(target: WsArg = Path(".")) -> None:
    """Trained model versions in a workspace, with their validation scores and the one in use."""
    ws = _ws(target, settings=False)
    rows = ws.models()
    if not rows:
        typer.echo("No trained models yet (recap train).")
    in_use = {f: ws.model_in_use(f) for f in ("stardist", "cellpose")} if ws.has_settings else {}
    for m in rows:
        new = (m["metrics"] or {}).get("new", {})
        cmp = (m["metrics"] or {}).get("compare", {})
        typer.echo(
            f"{m['family']} v{m['version']}{'  [in use]' if in_use.get(m['family']) == m['version'] else ''}  "
            f"{m['created_at']}  from {m['parent']}  train {len(m['train_images'])} / val {len(m['val_images'])} "
            f"images  val F1 {new.get('f1', '-')}"
            + (f"  compare F1 {cmp['f1']}" if cmp else "")
            + (f"  ({m['note']})" if m["note"] else "")
        )
        typer.echo(f"    {m['path']}")


@models_app.command("compare")
def models_compare(
    target: WsArg = Path("."),
    method: Annotated[str, typer.Option(help="stardist or cellpose.")] = "stardist",
) -> None:
    """Score the model in use and every saved version on all images with corrected masks.

    n_trained_on counts images a version was trained on (its scores there are optimistic);
    f1_unseen_only scores only the others.
    """
    import pandas as pd

    from recap.train import compare_models

    ws = _ws(target)
    try:
        rows = compare_models(ws, method, progress=typer.echo)
    except RecapError as e:
        _fail(f"ERROR: {e}")
    df = pd.DataFrame(rows)
    out = ws.folder("models") / f"compare_{method}.csv"
    df.to_csv(out, index=False)
    typer.echo("\n" + df.to_string(index=False) + f"\n\nSaved: {out}")


@models_app.command("use")
def models_use(
    version: Annotated[str, typer.Argument(help="A version (v2) or 'standard' for the model recap ships with.")],
    target: WsArg = Path("."),
    method: Annotated[str, typer.Option(help="stardist or cellpose.")] = "stardist",
) -> None:
    """Segment the whole workspace with this model version (written into recap.yaml)."""
    ws = _ws(target)
    v = None if version.lower() == "standard" else version.lstrip("v")
    if v is not None and not v.isdigit():
        _fail("Give a version such as v2, or 'standard'.")
    try:
        value = ws.use_model(method, int(v) if v else None)
    except RecapError as e:
        _fail(f"ERROR: {e}")
    typer.echo(f"recap.yaml: segmentation.method: {method}, model: {value}\nEvery image is now out of date: "
               "recap run --out-of-date")  # fmt: skip


@models_app.command("export")
def models_export(
    version: Annotated[str, typer.Argument(help="A version of this workspace, e.g. v2.")],
    dest: Annotated[Path, typer.Argument(dir_okay=False, help="The .zip to write.")],
    target: WsArg = Path("."),
    force: Annotated[bool, typer.Option("--force", help="Replace an existing file.")] = False,
) -> None:
    """Write a trained StarDist version (its weights, thresholds and config) to a .zip.

    The zip also holds recap_model.yaml: where it came from, its scores and the model it was
    trained from. Bring it into another workspace with recap models import FILE.zip.
    """
    from recap.transfer import export_model

    ws = _ws(target, settings=False)
    v = version.lstrip("v")
    if not v.isdigit():
        _fail("Give a version such as v2 (recap models list).")
    try:
        res = export_model(ws, int(v), dest, force=force)
    except RecapError as e:
        _fail(f"ERROR: {e}")
    typer.echo(f"StarDist v{v} written to {res.path}")


@models_app.command("import")
def models_import(
    src: Annotated[
        Path, typer.Argument(exists=True, help="A .zip from recap models export, or a StarDist model folder or zip.")
    ],
    target: WsArg = Path("."),
    use: Annotated[bool, typer.Option("--use", help="Segment the workspace with it (recap.yaml).")] = False,
    note: Annotated[str | None, typer.Option(help="A note to keep with it.")] = None,
) -> None:
    """Bring in StarDist weights as the next version of this workspace.

    Accepts a zip made by recap models export, or any StarDist model (a folder or zip with
    config.json and weights_best.h5). recap models compare scores it on your corrected masks.
    """
    from recap.transfer import import_model

    ws = _ws(target, settings=use)
    try:
        res = import_model(ws, src, note=note, use=use)
    except (RecapError, ConfigError) as e:
        _fail(f"ERROR: {e}")
    typer.echo(f"Imported as StarDist v{res.version} ({ws.rel(res.path)}).")
    for n in res.notes:
        typer.echo(n)
    if use:
        typer.echo("Every image is now out of date: recap run --out-of-date")


# ---------------------------------------------------------------- fiji
fiji_app = typer.Typer(rich_markup_mode=None)
app.add_typer(fiji_app, name="fiji")


def _fiji_location(workspace: Path | None):
    from recap.workspace import workspace_root

    root = workspace_root(workspace or Path("."))
    try:
        if root is not None:
            ws = _ws(root, settings=False)
            setting = ws.settings().validation.fiji if ws.has_settings else None
            return setting, ws.fiji()
        return None, fiji.resolve_fiji(None)
    except (RecapError, ValueError) as e:
        _fail(f"ERROR: {e}")


def _print_checks(results: list[tuple[str, str]]) -> None:
    for st, msg in results:
        typer.echo(f"  [{st.upper():4}] {msg}")


@fiji_app.callback(invoke_without_command=True)
def _fiji(ctx: typer.Context) -> None:
    """Show, set and check the Fiji recap opens for counting (the app: More > Fiji location).

    recap starts Fiji as its own program for counting and the spot check. Fiji brings its own
    Java. Search order: validation.fiji in the workspace's recap.yaml, $RECAP_FIJI / $FIJI_HOME,
    the default saved with `recap fiji set`, a Fiji launcher on PATH, common install folders.
    Shell aliases are invisible to programs: pass your alias target to `recap fiji set`.
    """
    if ctx.invoked_subcommand is None:
        fiji_show(None)


@fiji_app.command("show")
def fiji_show(workspace: WorkspaceOpt = None) -> None:
    """Which Fiji would be used, and how it was found."""
    setting, loc = _fiji_location(workspace)
    typer.echo(f"validation.fiji setting: {setting if setting else '(not set: automatic search)'}")
    if loc.folder is None:
        typer.echo("Fiji:  not found")
        typer.echo("looked in:\n  " + "\n  ".join(loc.checked))
        typer.echo("\n" + fiji.NOT_FOUND_HINT)
        raise typer.Exit(1)
    typer.echo(f"Fiji:  {loc.folder}\nfound: {loc.source}")
    exe = fiji.launcher(loc.folder)
    typer.echo(f"starts with: {exe if exe else '(no launcher for this system!)'}")


@fiji_app.command("set")
def fiji_set(
    path: Annotated[str, typer.Argument(help="Fiji folder, or a launcher inside it (e.g. your alias target).")],
    force: Annotated[bool, typer.Option("--force", help="Save even if checks fail.")] = False,
) -> None:
    """Check a Fiji installation and save it as your default.

    Accepts the Fiji folder or a launcher inside it. Runs the same checks as `recap fiji check`
    and saves only if none fail (unless --force). Saved in ~/.config/recap/settings.yaml
    (Windows: %APPDATA%\\recap\\settings.yaml). On Windows, forward slashes work in the path.
    """
    try:
        d, results, saved = fiji.set_default(path, force)
    except ValueError as e:
        _fail(f"ERROR: {e}")
    _print_checks(results)
    if not saved:
        _fail("\nNot saved: fix the failures above, or use --force.")
    typer.echo(f"\nSaved: Fiji = {d}\n       in {fiji.settings_path()}")
    loc = fiji.resolve_fiji(None)
    if loc.folder != d:
        typer.echo(f"Note: {loc.source} currently takes precedence ({loc.folder}).")


@fiji_app.command("unset")
def fiji_unset() -> None:
    """Forget the saved default Fiji."""
    settings = fiji.load_settings()
    if "fiji" not in settings:
        typer.echo("No saved Fiji setting.")
        return
    old = settings.pop("fiji")
    settings.pop("fiji_set_at", None)
    fiji.save_settings(settings)
    typer.echo(f"Removed saved Fiji setting ({old}).")


@fiji_app.command("check")
def fiji_check(workspace: WorkspaceOpt = None) -> None:
    """Validate the Fiji that would be used (exit status 1 on failure).

    Checks the folder layout, ImageJ version, the launcher for this system and Fiji's bundled Java.
    """
    _, loc = _fiji_location(workspace)
    if loc.folder is None:
        _fail("Fiji: not found\n" + fiji.NOT_FOUND_HINT)
    typer.echo(f"Fiji:  {loc.folder}\nfound: {loc.source}\n")
    results = fiji.check_fiji(loc.folder)
    _print_checks(results)
    failed = any(st == "fail" for st, _ in results)
    typer.echo("\nResult: " + ("FAILED" if failed else "OK"))
    if failed:
        raise typer.Exit(1)


if __name__ == "__main__":
    app()
