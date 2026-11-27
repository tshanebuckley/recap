# Changelog

## 4.4.0 — 2026-11-25
recap's own window instead of the full napari window, and adding images into batches.

- **One window** (`recap gui`): the image on the left, drawn by napari's canvas only (no layer
  list, layer controls or napari menus), and four tabs on the right: **Images** (the workspace,
  the image table, runs and More, as before), **Count**, **Fix outlines** and **Settings**
  (Guided mode). A **Show** bar under the image turns each stain on or off and sets brightness;
  each tab shows only its own layers (no recap outlines while counting; only the preview in
  Settings). Ctrl+1 to Ctrl+4 switch tabs, Ctrl+Z undoes the last point or outline edit, ◀ ▶ move
  between images. **Tuning and training** open in their own window. Leaving a tab, an image or
  the window with an unsaved count, outline corrections or settings asks first. `recap gui
  --napari` keeps the 4.3 layout (napari with docked panels).
- **Count tab**: click = VRC−, Shift+click = VRC+, right-click removes the nearest point; a drag
  pans instead of adding a point. Undo, Clear, Save count, Continue my last count, and Count
  this image in Fiji instead.
- **Fix outlines tab**: Draw new (drag around a missed nucleus; only background is filled),
  Delete (click), Split (drag a line across joined nuclei), Merge (click two pieces; a small gap
  between them is closed); Undo (30 steps), Save corrected mask. Dragging pans except while
  drawing or splitting. The editing logic is in `gui/editing.py`, without Qt.
- **Adding images**: **Add images** in the app (image files or a folder, searched with its
  subfolders) and `recap add FILES_OR_FOLDERS [--batch NAME | --no-batch] [--to SUBFOLDER]
  [--note TEXT]`. Both ask which batch the images go into, or make a new one (the command
  asks when neither option is given, and refuses when it cannot ask). Images from outside the
  workspace are copied into a subfolder named after the batch; images already in the workspace
  folder are indexed where they are; an image whose content is already in the workspace is not
  copied again but still goes into the chosen batch. recap's own outputs (label images,
  `recap-data/`) are skipped. Library: `Workspace.add_images`, `workspace.image_files_in`.
- **Channel names** in the app include their channel number (`DAPI (ch 2)`, `ICP8 (ch 1)`,
  `marker (ch 0)`), so a wrong channel in the settings shows at a glance.
- Tests: `tests/test_window.py` (the editing logic; the window with Qt off-screen: tabs, layer
  visibility, clicking, drawing, saving, asking before leaving, adding images to a batch).

## 4.3.0 — 2026-11-24
One settings file and one database per workspace; the command line and the app do the same
things through the same code.

- **Workspaces** have exactly one settings file, `recap.yaml` (`input.images: []` = every image
  in the folder), and one database, `recap-data/recap.db`. `recap init FOLDER [--from FILE]` or
  the app's **New...** makes one; a 4.2 workspace's workspace-wide settings move from the
  database into recap.yaml (comments are kept when recap writes to it: ruamel.yaml). The
  example folder is now a workspace (`examples/recap.yaml`); the Cellpose settings moved to
  `examples/other_settings/cellpose.yaml`.
- **Changed commands** (they take a workspace, default the current folder's, instead of a
  settings file): `run`, `validate`, `status`, `tune`, `train`, `models compare|list`,
  `settings show` (no `-c`), `report` (a workspace: all its runs vs all its counts),
  `fiji show|check` (`-w` instead of `-c`), `count` (recap's adjusted view by default,
  `--original`; `--remember` keeps your name). A settings file that is not a workspace's
  recap.yaml still runs on its own with `recap run FILE` (no batch or image settings).
- **New commands**: `init`, `run -i IMG / --out-of-date / --no-spot-check`, `settings unset|clear`,
  `batch rename|delete`, `tune --apply-to workspace|batch:NAME`, `train --use`, `models use`.
  `settings set` now adds to a level instead of replacing it.
- **Up to date or not**: each result records a fingerprint of the settings it used (migration
  0003); `recap status` and the app say whether each image is up to date, out of date (its
  recap.yaml, batch, own settings or model changed since) or not run.
- **App**: one recap panel with the workspace (Open... / New..., recent workspaces), the image
  table (with state and a filter), **Run selected / out-of-date / all** (with progress, cancel
  and the spot check), a banner with **Run this image**, and a **Manual | Guided** switch that
  shows one set of outlines at a time and asks before dropping unsaved Guided changes. Manual
  mode: Count in Fiji, Count here with VRC− / VRC+ buttons, outline tools (New nucleus, Paint,
  Pick, Erase, Fill, brush size) with instructions, Save corrected mask. Guided mode: plain-word
  tooltips, an explanation of each Save-to level, a warning when a more specific level still
  wins, Clear this image's own settings. More: batches (rename, delete), import counts, report,
  Fiji location, your name (asked once, can be remembered), edit recap.yaml, rescan.
  Tuning: Apply the best settings to the workspace or a batch. Training: Use selected version /
  standard model, with the version in use marked.
- **Shared code** for both: `counting` (Fiji and click counts), `Workspace.save_effective`,
  `update_level`, `use_model`, `report`, `import_count`, `fiji`, `fiji.set_default`,
  `runner.run_workspace`. `docs/gui.md` lists the command and function behind every button.
- Fixes: two runs in the same second no longer overwrite each other's run log; a workspace's
  relative Fiji path works in the app too; the spot check shows each image with its own
  settings; tests no longer write to the real user settings file; report plots no longer switch
  matplotlib's global backend (safe from the app); commands work from any folder inside a
  workspace; `recap run` looks for Fiji only when the spot check needs it, and runs the spot
  check only on a whole workspace (as the app's Run all); spot-check counts are recorded even
  when the outputs are outside the folder.
- Offline bundles: the installers keep a StarDist model already in the user's cache instead of
  failing to copy over it; the Windows bundle ships the environment as `recap-env.tar` with
  `pixi-unpack.exe` (a self-extracting PowerShell script of 1.4 GiB ran out of memory);
  pixi-pack and pixi-unpack are pinned to 0.7.12.
- Windows: a second launch of the app now reliably brings the open window forward (the
  running app reads the request asynchronously; the second launch waits until it is read).
- Results from before 4.3 have no settings fingerprint, so they show as out of date until run
  again; with an invalid recap.yaml the state is "unknown".
- New: `docs/guide/`, a user guide organised by the test plan's user stories; GitHub issue
  template for defects and a pull-request checklist; `CITATION.cff`; a development-status
  notice; `.gitignore` keeps lab images, counts, results and `recap-data/` out of git everywhere.

## 4.2.0 — 2026-11-24
- **Settings levels** (`levels.py`): settings file → workspace → staining batch → image, most
  specific wins. Levels may change recovery, segmentation thresholds, fragments, measurement,
  filters and `exclude_regions`, never the model; an image's own settings need a reason.
  `recap batch assign|remove|list`, `recap settings set|show`. Run, spot check, tuning and
  training use each image's effective settings (`input.use_workspace_settings`, default on).
  New summary columns `settings`, `overridden`, `settings_reason`.
- **Database migration 0002**: `batches`, `image_batches`, `level_settings`. Workspaces from
  4.1.0 are backed up and upgraded when opened.
- **Guided mode panel**: the open image's settings in five steps (recovery, segmentation,
  fragments, filters, infection call) with live outlines and counts. Segmentation reruns only
  when a setting it depends on changes; filter and infection changes show in well under a
  second. Each value shows its level; Save stores only what differs from the level above.
- **Workspace panel**: batch and own-settings columns, multi-select, "Set batch".
- `recap gui` sets `GALLIUM_DRIVER=softpipe` on Linux (Mesa llvmpipe crashes next to TensorFlow
  on machines without a GPU).
- Verified on the day-79 image: 472 / 248 unchanged with no levels; a batch `min_area` and an
  image `vrc_weight` apply and are recorded.

## 4.1.0 — 2026-11-24
- **Database on SQLModel + Alembic** (`db.py`, `migrations/`): the workspace tables are SQLModel
  classes, upgraded by Alembic migrations shipped in the package. Opening a workspace compares
  the recap version that last wrote it with this one; if older, the database is first copied to
  `recap-data/backups/recap_v<version>_<date>_<time>.db`, then upgraded; if newer, recap refuses.
  Databases from 4.0.0 are recognised and upgraded. The CLI and the app say when this happened.
  `pixi run db-revision "..."` writes a migration; a test checks tables and migrations agree.
- **Offline bundles** (`packaging/`, `pixi run bundle <platform>`): one zip per system with the
  whole environment (pixi-pack, recap injected as a wheel), the StarDist model and installers
  (`Install recap.bat`, `Install recap.command`, `install.sh`) that unpack into the user folder,
  make a `recap` launcher and the desktop icon. Everything except Fiji. The Linux bundle was
  built, installed without pixi or a cached model, and reproduced 472 / 248.
- **CI** (`.github/workflows/bundle.yml`): on a `v*` tag, each system builds and test-installs its
  bundle and attaches it to the GitHub Release.
- Desktop icons from a bundle install run its `recap` launcher (`RECAP_LAUNCHER`).
- New dependencies: sqlmodel 0.0.50 (pinned: pre-1.0), alembic.

## 4.0.0 — 2026-11-23
Breaking: the in-process Fiji bridge is gone. Old settings files still load.

- **Removed**: tuning in Fiji windows (`interactive` section), pyimagej and Java. Fiji is now
  started as its own program and brings its own Java, so this works on macOS too. An old
  `interactive` section is ignored with a note; `interactive.fiji` is used as the new
  `validation.fiji`. `recap fiji check --start` and the automatic Fiji download are gone.
- **Counting in Fiji** (`fijicount.py`, `assets/fiji_count.groovy`): `recap count IMAGE...`, the
  spot check and the app open images in Fiji with the Multi-point tool; "Done counting" shows the
  totals and asks whether to import them. Points are saved every second; if Fiji is closed first,
  recap asks whether to keep them. One Fiji serves a whole spot check.
- **Workspaces** (`workspace.py`): `recap-data/recap.db` (SQLite) indexes images (by content),
  runs, manual counts, edited masks, model versions and tuning; paths relative to the folder.
  `recap status FOLDER`; `recap import-counts -w`; `recap report -m recap-data/recap.db`; the
  spot check's `previous_counts` also accepts recap.db.
- **Tuning** (`tune.py`, `recap tune`): random search over the VRC weight, StarDist threshold,
  fragment merging and focus / circularity / border filters, scored against edited masks
  (`evaluate.py`). Segmentation is rerun only when its settings change. Best settings written as
  a complete settings file; all trials stored.
- **Training** (`train.py`, `recap train`, `recap models list|compare`): fine-tune StarDist on
  edited masks into new versions (`recap-data/models/stardist/vN`, never overwritten), each with
  its training images and validation scores against the starting model. Cellpose training is
  implemented but untested. New setting `segmentation.cellpose_model`.
- **napari plugin** (`napari.yaml`, `gui/panels.py`): Workspace (status table, filters,
  per-image summary with plots, open with edited or program mask and last count, count in Fiji or
  by clicking, save counts and edited masks), Tuning and Training panels. `recap gui` accepts a
  workspace folder or a settings file and docks them.
- Manual counts gain a `tool` column (fiji, napari, cell-counter, multipoint-csv, typed).
- `pipeline.segment_stage` / `finish_stage` split the slow and fast parts of `process_image`.
- Docs: `docs/gui.md` shortened.

## 3.2.0 — 2026-11-23
Desktop app (early version).

- **`recap gui [CONFIG]`**: opens an image in napari with its channels (additive colours,
  percentile contrast), the counted-nuclei mask from the last run as editable outlines, and the
  excluded nuclei (hidden). Default: the day-79 example. One app per user: a second launch brings
  the open window forward; a crashed app does not block the next start.
- **`recap desktop-icon [--remove]`**: desktop icon that runs `recap gui` (Linux `.desktop` in the
  applications menu and on the desktop; Windows `.lnk` on the desktop and in the Start menu;
  macOS `recap.app` on the desktop). Old recap icons are removed first; files recap did not make
  are never touched. From a pixi environment the icon runs `pixi run --frozen ... recap gui`.
- **pixi**: new `gui` feature (napari 0.10, PyQt6) in both environments; solves on Linux,
  Windows and Apple-silicon macOS. pip: `pip install -e ".[gui]"`.
- **Docs**: `docs/gui.md` (layout, next steps, useful napari plugins) for whoever builds the app.
- 9 new tests (66 in total); the napari window itself is not opened in CI (no OpenGL there).

## 3.1.0 — 2026-11-23
Windows and Mac support.

- **Platforms**: `pixi.toml` now targets `linux-64`, `win-64` and `osx-arm64` (Apple silicon).
  On Windows, TensorFlow 2.21 and StarDist 0.9.2 come from PyPI (conda-forge's Windows builds are
  years old); everything else comes from conda-forge on every system. Both environments solve on
  all three platforms. Intel Macs and Windows on ARM remain unsupported (no TensorFlow 2.21).
- **pip**: `requirements.txt` installs `tensorflow` on Mac and `tensorflow-cpu` elsewhere.
- **Fiji search**: Windows and Mac launcher names (`fiji-windows-x64`, `ImageJ-win64`,
  `fiji-macos-arm64`, `ImageJ-macosx`, ...) and per-OS install folders (`C:\Fiji.app`,
  `%LOCALAPPDATA%`, Desktop, Downloads, `/Applications/Fiji.app`, and `Fiji` as well as
  `Fiji.app`). Launchers inside `Fiji.app/Contents/MacOS/` and `.bat` wrappers are followed.
  Settings live in `%APPDATA%\recap\settings.yaml` on Windows. Java is found as
  `java.exe` under `JAVA_HOME`.
- **Fix**: a missing Windows path in `interactive.fiji` (e.g. `C:\Fiji.app`) was taken as a
  Maven download coordinate; it is now reported as not found.
- **macOS**: `recap run` stops at once with an explanation when tuning is on, or when the spot
  check would need new counts (pyimagej cannot open Fiji windows from a command-line program on
  macOS). A spot check whose sample is covered by `previous_counts` (e.g. from
  `recap import-counts`) runs normally.
- **Fix (Windows)**: all text files are read and written as UTF-8; `report.md` with "VRC−" and
  configs with non-ASCII comments no longer fail. Console output never fails on symbols.
- **Settings files**: invalid YAML gives a readable error, with a hint when Windows backslashes
  inside double quotes are the cause.
- **CI**: tests on Ubuntu, Windows and macOS; lint on Ubuntu. Tests use a stand-in for Fiji
  everywhere, so the spot-check tests also run on the macOS runner.
- **Fix**: the run log file is closed when a run ends (Windows cannot move or delete an open
  file; matters for a GUI that runs several times in one session).
- `.gitattributes`: LF line endings in the repo on every OS; TIFF/PNG/ZIP marked binary.
- 8 new tests (57 in total).
- License holder: Shane Buckley (MIT); author in package metadata; README acknowledgements.

## 3.0.2 — 2026-11-23
- `.envrc` for direnv: activates the default pixi environment on `cd` and reloads when
  `pixi.lock` changes (`direnv allow` once). `.direnv/` ignored by git.

## 3.0.1 — 2026-11-23
- Documentation: the name recap stands for **Reconstruction of Cellular Architecture and Phenotypes**; stated in the README,
  docs, contributor guide, `recap --help`, the settings template, report headers and package metadata.

## 3.0.0 — 2026-11-23
Breaking: code layout and library interface (settings files and existing outputs still work).

- **Library + thin CLI.** `cli.py` split into modules (`config`, `io`, `segment`, `geometry`,
  `measure`, `filters`, `qc`, `pipeline`, `counts`, `importers`, `report`, `compare`, `fiji`,
  `tuning`, `spotcheck`, `runner`, `runlog`, `errors`). Public interface in `recap`:
  `load_config`, `process_image`, `process_file`, `run_batch`, `run_pipeline` (with `progress`,
  `cancel`, `decide` callbacks), `Result`, `RunResult`, `RecapError`, `ConfigError`, `Cancelled`.
  Library code never prints or exits. See `docs/architecture.md`.
- **`recap import-counts`**: counts made in plain Fiji (Cell Counter XML, Multi-point CSV with
  optional `--pixel-size`, typed totals) into a manual_counts CSV; `--by` records the counter;
  re-importing replaces. Manual counts gain `counted_by` and `origin` columns (old files still load).
- **`recap report`**: several runs and manual counts across all images: program vs manual (bias,
  limits of agreement, % VRC+, missed / merged / extra, per-nucleus VRC+ agreement), methods vs
  each other, counters vs each other, plots and `report.md`.
- **QC**: new `qc` settings; `image_summary` gains `qc_flags`, count ranges
  (`n_nuclei_low/high`, `n_infected_low/high`), saturation, VRC background and sharpness.
- **Help text** rewritten: short overview, one-line command summaries, details and examples per
  command.
- **Repo**: ruff lint/format settings, GitHub Actions CI (pixi), `pixi run test|lint|format`,
  `CONTRIBUTING.md`, `LICENSE` (MIT, holder to fill in), `docs/output_columns.md` kept in sync by a
  test, tests split by area (49).
- Run log: logging from every module goes to `run_*.log`; `recap compare` no longer exits on a
  missing file (raises `RecapError`).

## 2.9.0 — 2026-11-23
- `recap fiji` is now a command group:
  - `recap fiji set PATH [--force]` — checks a Fiji installation (folder or launcher) and saves it
    as the user default in `~/.config/recap/settings.yaml`; refuses invalid or failing installs.
  - `recap fiji check [-c config] [--start]` — validates the Fiji that would be used (layout,
    ImageJ version, launcher, plugins, Java version, pyimagej); `--start` launches it headless.
    Exit status 1 on failure.
  - `recap fiji show [-c config]` (also plain `recap fiji`) and `recap fiji unset`.
- The saved default is part of the search order (after config and environment variables, before
  PATH and common folders); a saved folder that no longer exists is skipped with a note.
- Tests: set / unset / check / precedence / Java parsing — 30 tests.

## 2.8.0 — 2026-11-23
- recap finds an installed Fiji automatically: `interactive.fiji` (now optional), then
  `$RECAP_FIJI` / `$FIJI_HOME`, launchers on PATH (symlinks and wrapper scripts followed),
  common install folders, else download. A bad `interactive.fiji` path fails before processing.
- New `recap fiji [config]`: shows which Fiji would be used, how it was found, and the Java version.
- Run log records the Fiji used (`fiji:` with endpoint and how it was found).
- `pixi.toml`: commented `[activation.env] RECAP_FIJI` for a lab-wide Fiji location.
- Tests: Fiji resolution (config, env var, PATH symlink, wrapper script, fallback, command) — 24 tests.

## 2.7.0 — 2026-11-23
- Renamed to **recap** and packaged: `pyproject.toml` installs a `recap` command
  (`recap template | validate | run | compare`, `recap --version`; also `python -m recap`).
- Code moved to `src/recap/` (`cli.py`, `compare.py`). The comparison tool is now `recap compare`.
- pixi installs recap in editable mode in every environment; tasks `segment` / `compare` replaced
  by the `recap` command (`pixi run recap ...` or `pixi shell`).
- Run log records `tool: recap` and `version` (was `script_version`); logger name `recap`.
- Tests: `recap --version`, installed command, `recap compare` (18 tests).

## Packaging update to 2.6.0 — 2026-11-23 (program unchanged)
- `pixi.toml` with `default` (StarDist, TensorFlow CPU) and `cellpose` (Cellpose 4, PyTorch CPU)
  environments, both with Fiji support (Java 21); tasks `segment`, `compare`, `test`.
- `examples/hippoc_d79_cellpose.yaml` and a run-comparison script for StarDist vs Cellpose.
- `environment.yml` now uses Java 21.

## 2.6.0 — 2026-11-20
- Optional fragment merging (`segmentation.fragment_merge: "off" | shape | shape_vrc`, `fragments`
  section): rejoins small or irregular pieces to a neighbour when the merged object is convex,
  ellipse-shaped and normal-sized; `shape_vrc` also requires ICP8 across the junction.
  Merges logged to `<image>_merges.csv`. Default off.
- Fiji tuning step 3 offers the fragment-merge mode.
- A bare `off` in YAML is accepted (YAML reads it as false).

## 2.5.0 — 2026-11-19
- Border nuclei with minimal clipping can be kept (`filters.border_max_missing`). Missing fraction
  is estimated from an ellipse fit to the uncut outline and the cut-edge length; shape
  (`border_max_fit_error`) and size (`border_area_percentiles`) checks guard against slivers.
- Clipping estimates added to the per-nucleus tables; Fiji tuning step 4 has the cutoff.

## 2.4.0 — 2026-11-19
- Final filters: focus (relative to the image's median nucleus, computed on the combined
  channel), circularity, min / max area, image border. Excluded nuclei written with reasons.
- Fiji tuning gains a filters step (now five steps).

## 2.3.0 — 2026-11-19
- StarDist segmentation method; `merge` switch; small DAPI bodies reported separately;
  `input.exclude_regions` to blank burned-in annotations.

## 2.2.0
- Infected / uninfected split in spot-check overview, comparison and per-image summary.

## 2.1.0
- Manual spot check in Fiji: random subset, overview, Cancel / Save counts / Run all,
  reuse of earlier counts by file hash.

## 2.0.0
- Typer CLI, YAML configuration validated with pydantic, Fiji tuning via pyimagej, run logs.
