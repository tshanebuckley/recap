# recap 4.4.0

**recap: Reconstruction of Cellular Architecture and Phenotypes**

> [!WARNING]
> **Under development.** recap is research software that is still being built and validated:
> its counts have been checked against manual counts on a small number of images, on Linux, and
> features, commands and file formats may still change. Check its results against manual counts
> before relying on them, and keep copies of your images. You use recap at your own risk: as the
> [MIT license](LICENSE) states, it is provided "as is", without warranty of any kind, and the
> authors are not liable for any claim or damages arising from its use.

Nuclear segmentation, counting and HSV-1 infection scoring for fluorescence images of
neural tissue (DAPI + a viral replication compartment marker such as ICP8).

Nuclei are found in a combined image, max(DAPI, VRC), so infected nuclei whose chromatin is
pushed to the rim by replication compartments are still found whole. Each nucleus is called
VRC+ or VRC−, filtered (focus, shape, size, image border) and written to tables with a full
run log, per-image QC warnings and a count range. Manual counts (in Fiji, which recap opens, or
by clicking in recap's app) are compared with one or more segmentation methods. Hand-corrected
masks are used to tune the settings and to train new model versions.

A **workspace** is a folder of images with one settings file, `recap.yaml`, and one database,
`recap-data/recap.db`. The command line and the app work on the same workspace and call the same
library functions, so anything done in one shows in the other. Commands use the workspace of the
current folder when none is given.

```
recap init FOLDER [--from FILE]  # make a folder of images a workspace (or the app: New...)
recap validate [FOLDER]          # check its settings, list its images
recap run [FOLDER]               # run every image with its own settings (-i IMG, --out-of-date)
recap status [FOLDER]            # every image: batch, recap's count, up to date?, manual counts, ...
recap batch assign|remove|list|rename|delete   # staining batches
recap settings set|unset|clear|show            # settings for the workspace, a batch or an image
recap count IMG...               # count by hand in Fiji; counts go into the workspace
recap import-counts IMG [FILE]   # add counts made in plain Fiji (Cell Counter XML, Multi-point CSV, totals)
recap report [FOLDER]            # recap vs manual counts across all images (or RUN_DIR... -m FILE)
recap compare RUN_A RUN_B        # two runs, image by image, with overlays
recap tune [FOLDER]              # better settings, scored against corrected masks (--apply-to)
recap train [FOLDER]             # a new model version from corrected masks
recap models list|compare|use    # versions, their scores, and which one the workspace uses
recap fiji set|check|show|unset  # the Fiji recap opens for counting
recap add FILES/FOLDERS [--batch NAME]  # copy images into the workspace, into a batch
recap gui [FOLDER]               # desktop app: everything above, plus counting, outlines and settings
recap desktop-icon               # desktop icon that opens the app
recap template FILE              # a settings file for a run outside any workspace
recap --version
```

`recap --help` and `recap COMMAND --help` explain each command; [`docs/gui.md`](docs/gui.md) has
the command for every button in the app. **New to recap? Start with the
[user guide](docs/guide/README.md).**

## Contents

| Path | What it is |
|---|---|
| `src/recap/` | The package: library modules plus a thin command line (`cli.py`) |
| `docs/guide/` | **User guide**: the workflow step by step, organised by the test plan's user stories |
| `docs/architecture.md` | Module map and the library interface (for a GUI or scripts) |
| `docs/output_columns.md` | Every column of every output CSV |
| `docs/gui.md` | The desktop app: running it, code layout, next steps, useful napari plugins |
| `pyproject.toml` | Package definition; installs the `recap` command; lint settings |
| `pixi.toml` | pixi project: `default` environment (StarDist) and `cellpose` environment |
| `config_template.yaml` | Every setting, with comments and defaults (same as `recap template`) |
| `examples/` | The example workspace for the day-79 organoid image (`recap.yaml`: StarDist, 472 nuclei, 248 VRC+) and a Cellpose settings file to compare with |
| `requirements.txt`, `environment.yml` | pip / conda alternatives to pixi |
| `packaging/` | Offline install bundles: build script, installers (`pixi run bundle <platform>`) |
| `tests/` | Tests (no Fiji or StarDist model needed) |
| `.github/workflows/ci.yml` | GitHub Actions: lint, then tests on Linux, Windows and macOS, on every push |
| `.envrc` | direnv: activates the pixi environment on `cd` (run `direnv allow` once) |
| `CONTRIBUTING.md`, `CHANGELOG.md`, `LICENSE`, `CITATION.cff` | Development guide, version history, license, how to cite |
| `.github/ISSUE_TEMPLATE/`, `pull_request_template.md` | Defect reports (same fields as the test plan) and the pull-request checklist |

## Install

| System | Status |
|---|---|
| Linux x86-64 | Supported; developed and validated here |
| Windows 10/11 (x64), native | Supported (pixi or pip). StarDist/TensorFlow come from PyPI on Windows |
| Windows via WSL2 | Supported: it is Linux. Keep images in the Linux filesystem (`~/data`), not `/mnt/c` |
| Mac with Apple silicon (M1–M4) | Supported, including counting in Fiji (Fiji runs as its own program; see [Fiji](#fiji)) |
| Intel Mac, Windows on ARM | Not supported (no TensorFlow 2.21 builds); Cellpose alone may work |

Windows and macOS are tested by CI only; the real-image validation was done on Linux.

### pixi (recommended, all systems)

pixi installs Python, TensorFlow, PyTorch and napari, keeps StarDist and Cellpose in separate
environments, and installs recap in **editable** mode, so the `recap` command is available in
every environment. Tested with pixi 0.84.2 (Python 3.12). Install pixi first:

- Linux, Mac, WSL2: `curl -fsSL https://pixi.sh/install.sh | sh`
- Windows (PowerShell): `powershell -ExecutionPolicy ByPass -c "irm -useb https://pixi.sh/install.ps1 | iex"`

Then, in a terminal (on Windows: PowerShell or Windows Terminal) in the unpacked folder:

```bash
cd recap-4.1.0
pixi install                 # default environment: StarDist + Fiji support + desktop app + recap
pixi run test                # tests, under a minute
pixi install -e cellpose     # optional: Cellpose environment (CPU PyTorch)
```

Use `pixi run recap ...` (or `pixi run -e cellpose recap ...`), or `pixi shell` and then plain
`recap ...`. Commit the `pixi.lock` that the first install writes: it covers all three systems,
so every machine gets the same packages.

**Windows notes.** Keep the project in a short path such as `C:\Users\you\recap`: TensorFlow
has deeply nested files, and if an install stops with a path-length error, enable Windows long-path
support (a one-time administrator setting; ask IT if you cannot change it). In settings files,
write Windows paths with forward slashes (`C:/Users/you/data/*.tif`), which avoids YAML's
backslash escapes. GPU TensorFlow is not available on native Windows; StarDist runs on the CPU
(fine for this pipeline), or use WSL2 for GPU.

**Automatic activation with direnv** (optional; Linux, Mac, WSL2): the repo includes an
`.envrc` that activates the default pixi environment whenever you `cd` into the folder, so
`recap`, `pytest` and `ruff` work without `pixi run`. It reloads when `pixi.lock` changes.
Allow it once per clone:

```bash
direnv allow
```

(Install direnv and its shell hook first: https://direnv.net. For the Cellpose environment,
use `pixi shell -e cellpose` or `pixi run -e cellpose ...` as above. On Windows, use
`pixi shell` instead.)

### Offline bundle (flash drive)

For computers without pixi or internet: each GitHub Release has one zip per system
(`recap-<version>-win-64.zip`, `-osx-arm64.zip`, `-linux-64.zip`, about 1.4 GiB each) with
everything except Fiji, including the StarDist model. Copy the zip to the drive, unzip it on the
target computer and double-click **Install recap.bat** (Windows) or **Install recap.command**
(Mac), or run `./install.sh` (Linux). It installs into the user's folder without administrator
rights, puts a recap icon on the desktop, and takes a few minutes. It needs about 12 GB free
while installing (7 GB once installed). Build a bundle yourself with `pixi run bundle win-64`
(or `osx-arm64`, `linux-64`; any system can build any of them), into `dist/`.

### pip + venv (tested with Python 3.13 on Linux)

```bash
python3 -m venv .venv && source .venv/bin/activate     # Windows: py -m venv .venv; .venv\Scripts\activate
pip install -r requirements.txt      # pinned, tested versions (StarDist, TensorFlow CPU)
pip install -e .                     # installs the recap command (editable)
pytest -q
```

`requirements.txt` picks `tensorflow-cpu` on Linux/Windows and `tensorflow` on Mac (no
`tensorflow-cpu` for Mac). Add `pip install -e ".[cellpose]"` for Cellpose (pulls PyTorch from
PyPI; large), and `pip install -e ".[gui]"` for the desktop app (napari, Qt).

### conda

```bash
conda env create -f environment.yml && conda activate recap && pip install -e ".[gui]"
```

## Quick start

Copy `Experiment_Hippoc_org_day_79_Acute_VimentinR_ICP8_G.tif` into `examples/` and run:

```bash
recap run examples                                       # results in examples/results/
recap init path/to/images --from examples/recap.yaml     # your own images: a new workspace
recap run path/to/images
```

Or open the app (`recap gui`), press **New...**, choose the folder, and copy a settings file or
start from the defaults. Images from elsewhere: **Add images** in the app, or
`recap add FILES_OR_FOLDERS --batch NAME` (copies them in; asks for the batch if you leave it out). In a workspace's `recap.yaml`, `input.images: []` means every image in
the folder (or give globs relative to it); a settings file run on its own (`recap run file.yaml`)
lists its images relative to itself. Channel indices are 0-based after loading as
(channel, y, x); for RGB exports red = 0, green = 1, blue = 2. Z-stacks are max-projected.

**First StarDist run:** the model (about 5 MB) downloads from GitHub and is cached in
`~/.keras/models/StarDist2D/`. Cellpose 4 caches its weights in `~/.cellpose/models/` and needs
minutes per image on a CPU.

## Validating against manual counts

A typical comparison of StarDist, Cellpose and manual counting:

```bash
# 1. run both methods on the same images
pixi run recap run examples                                            # -> examples/results/
pixi run -e cellpose recap run examples/other_settings/cellpose.yaml   # -> examples/results_cellpose/

# 2. count by hand: recap opens Fiji; counts go into examples/recap-data/recap.db
recap count examples/*.tif --by ana --remember
#    or import counts made in plain Fiji (the app: More > Import counts)
recap import-counts examples/img01.tif img01_markers.xml --by ben      # Cell Counter
recap import-counts examples/img02.tif --nuclei 472 --vrc-pos 240     # totals only

# 3. report: every run folder of the workspace against every manual count in it
recap report examples                 # -> examples/recap-data/reports/<date>/ (the app: More > Report)
```

`report.md` summarises it; `summary.csv` has, per method, the total difference, the
per-image bias with 95% limits of agreement, % VRC+ (program vs manual) and, when counts have
clicked points, missed / merged / extra nuclei and per-nucleus VRC+ agreement.
`method_agreement.csv` compares the methods nucleus by nucleus, and `inter_rater.csv` compares
counters who counted the same image. Images are matched by file content, so renamed copies
still match.

**Counting in Fiji.** `recap count`, the spot check (`validation.enabled`) and the app's "Count in
Fiji" open the image in Fiji with the Multi-point tool, in recap's adjusted view (normalised DAPI
blue and ICP8 green, with the image's own settings; `recap count --original` shows the file as
it is). Counter 0 = VRC−, counter 1 = VRC+: double-click the Multi-point tool to switch.
Click "Done counting": Fiji shows the totals and asks whether to import them. Counts are saved
under your name: the app asks once (and can remember it), `recap count --by NAME --remember`. Points are saved
every second, so if Fiji is closed first, recap asks whether to keep them. Or count in plain Fiji
and import:

- *Cell Counter* plugin: type 1 for VRC− nuclei, type 2 for VRC+,
  then "Save Markers" (XML, pixel coordinates). Other types are ignored with a note.
- *Multi-point tool*: counter 0 for VRC−, counter 1 for VRC+, then Analyze > Measure and save the
  Results table as CSV. If the image has a scale (Image > Properties), the coordinates are in
  calibrated units: pass `--pixel-size` (units per pixel). Points outside the image are
  rejected with that hint.
- Other type numbers: `--uninfected-types` / `--infected-types`. Infection not marked:
  `--no-infection`.

Counts with clicked points are much more informative than totals: they show *which* nuclei
the program missed, merged or added.

## QC warnings and count ranges

Every image in `image_summary_*.csv` gets:

- `qc_flags`: `saturated_dapi`, `saturated_vrc`, `few_nuclei`, `high_exclusion`,
  `many_out_of_focus`, `high_vrc_background`, `vrc_range_not_fixed`, `blurry_vs_batch`
  (sharpness well below the rest of the batch, needs >= 3 images). Thresholds in the `qc`
  settings; flagged images are also logged.
- `n_nuclei_low` / `n_nuclei_high` (and the same for VRC+): the count when each filter cutoff
  (focus, circularity, border clipping, infection fraction) is moved a little down and up, one
  at a time. A wide range means the count depends on a cutoff more than you may want.

## Settings overview

See `config_template.yaml` (or `recap template`) for every key with comments.

| Section | Main settings |
|---|---|
| `input` | images, channel indices, `exclude_regions` (blank burned-in timestamps / scale bars) |
| `normalization` | `vrc_range`: fix it per imaging session from an infected control |
| `segmentation` | `method` (`stardist` recommended; `classical`; `cellpose`), `diameter`, `merge` (false for StarDist), `fragment_merge` |
| `fragments` | thresholds for `fragment_merge` |
| `measurement` | infection call: `vrc_threshold`, `infected_fraction` |
| `filters` | `min_focus`, `min_circularity`, `min_area` / `max_area`, `exclude_border` + `border_max_missing` |
| `qc` | warning thresholds and count-range steps |
| `validation` | random manual spot check counted in Fiji, reuse of earlier counts, `fiji` location |
| `output` | output folder, label images, QC images |

## Outputs

Per run, in `output.directory`: `<image>_nuclei.csv`, `<image>_excluded.csv`, label images,
`<image>_qc.png` (white = VRC−, red = VRC+, blue = excluded), `image_summary_*.csv`,
`all_nuclei_*.csv`, `run_log_*.yaml` (settings as written / validated / applied, versions,
hashes), and `final_config_*.yaml` (re-running it reproduces the batch). Every column is
described in [`docs/output_columns.md`](docs/output_columns.md).

## Workspaces

A workspace is a folder of images (anywhere below it) with:

- `recap.yaml`: the settings for every image. The only settings file of the workspace; batches
  and single images can change some values (next section), stored in the database.
- `recap-data/recap.db`: an SQLite index of images, runs, manual counts, corrected masks,
  batches and their settings, model versions and tuning results; plus `masks/`, `models/`,
  `tuning/`, `reports/`, `fiji/` and `backups/`.

Images are identified by content, and paths are stored relative to the folder, so the folder can
be moved or shared. Make one with `recap init FOLDER [--from settings.yaml]` or the app's
**New...**; a folder used with recap 4.2 (a `recap-data/` but no `recap.yaml`) is made a workspace
the same way, and its workspace-wide settings move from the database into `recap.yaml`. When a
newer recap opens a workspace, it first copies `recap.db` to
`recap-data/backups/recap_v<old version>_<date>_<time>.db`, then upgrades it; an older recap
refuses to open a workspace used by a newer one.

`recap status` (or the app's table) lists every image: batch, recap's count, whether that result
is **up to date** with the image's current settings (changing recap.yaml, a batch, an image's own
settings or the model makes it **out of date**), manual counts and by whom, corrected masks, own
settings and QC flags. `recap run --out-of-date` (the app: **Run out-of-date**) runs only those.

## Settings for a workspace, a staining batch or one image

Images stained together should be analysed with the same settings; an image whose staining is
weak but usable can get its own. Each level changes only what it needs, and the most specific
wins: **workspace (recap.yaml) → batch → image**.

```bash
recap batch assign "stain 2026-10-03" data/day79_*.tif      # images stained together
recap settings set filters.min_focus=0.6                     # the workspace: written into recap.yaml
recap settings set normalization.vrc_range="[3, 30]" --batch "stain 2026-10-03"
recap settings set normalization.vrc_weight=1.5 --image data/day79_04.tif --reason "weak ICP8"
recap settings show data/day79_04.tif        # effective settings, and where each comes from
recap settings clear --image data/day79_04.tif               # back to the batch's settings
```

The app's **Settings** tab (Guided mode) saves the same way: change values with the preview, choose *Save to:*
workspace, the batch or this image. Levels can change recovery, segmentation thresholds,
fragment merging, filters, the infection call and `exclude_regions`. They cannot change the
model, so every image in a workspace is segmented by the same one. An image's own settings need
a reason. `recap run`, the spot check, tuning and training all use each image's effective
settings (turn the batch and image levels off with `input.use_workspace_settings: false`); the
image summary lists the levels used (`settings`), what they changed (`overridden`) and the
reason. Training on images with their corrected settings is what makes a trained model hold up
on images of varying quality.

## Tuning and training

Correct recap's outlines in the app (**Fix outlines** tab: Draw new, Delete, Split, Merge; then
**Save corrected mask**). Corrected masks are the reference for:

```bash
recap tune --trials 30 --apply-to workspace   # best settings into recap.yaml (or batch:NAME); the app: Tuning > Apply
recap train --epochs 20                       # new StarDist version -> recap-data/models/stardist/vN
recap train --start v1                        # continue from an earlier version
recap models compare                          # every version on the same images
recap models use v2                           # segment the workspace with v2 (the app: Use selected version)
```

Tuning tries combinations of the VRC weight, StarDist threshold, fragment merging and the
focus / circularity / border filters, and scores each by matching nuclei to the corrected masks.
Training fine-tunes on the corrected masks and saves a new version; versions are never overwritten,
and each records what it was trained on and its validation score. `recap models use` writes the
chosen version into recap.yaml, which makes every result out of date until the images are run
again. Cellpose training (`--method cellpose`) is written
against the Cellpose 4 API but has not been run yet.

## Fiji

recap opens Fiji as its own program (Fiji brings its own Java). Tell it where Fiji is once:

```bash
recap fiji set /opt/Fiji.app          # a Fiji folder, or a launcher inside it (e.g. your alias target)
recap fiji set C:/Users/you/Fiji.app  # Windows (forward slashes are fine)
recap fiji set /Applications/Fiji.app # Mac
recap fiji check                      # folder layout, ImageJ version, launcher, bundled Java
```

In the app: More > *Fiji location*. Search order: `validation.fiji` in the workspace's recap.yaml
(relative to the workspace), `$RECAP_FIJI` / `$FIJI_HOME`, the saved default
(`~/.config/recap/settings.yaml`; Windows: `%APPDATA%\recap\settings.yaml`), a launcher on `PATH`,
common install folders. The counting script (`src/recap/assets/fiji_count.groovy`) has been
tested against a stand-in, not yet in a real Fiji: try it once on each lab machine.

## Desktop app

```bash
pixi run recap gui                       # the last workspace (first time: the example workspace)
pixi run recap gui path/to/workspace     # a workspace folder
pixi run recap desktop-icon              # desktop icon that opens the app
```

One window: the image on the left (drawn by [napari](https://napari.org), with only what
counting and correcting need), and four tabs on the right. Ctrl+1 to Ctrl+4 switch tabs; Ctrl+Z
undoes the last point or outline edit.

- **Top bar**: **Add images** (files or a folder; asks which batch, or makes a new one),
  **Tuning and training** (their own window), and ◀ the image list ▶.
- **Images**: the workspace (Open... / New..., recent workspaces), the image table (filter: out
  of date, not counted, no corrected mask), **Run selected / Run out-of-date / Run all**, Set
  batch, Summary, and More: batches, import counts, report, Fiji location, your name, edit
  recap.yaml. A banner says when the open image's results are out of date, with **Run this image**.
- **Count**: click = VRC−, Shift+click = VRC+, right-click removes a point; Undo, Save count;
  or count this image in Fiji instead.
- **Fix outlines**: Draw new (drag around a missed nucleus), Delete, Split (drag a line across),
  Merge (click two pieces); Undo, Save corrected mask.
- **Settings** (Guided): the image's settings in five steps with a live preview (VRC+ magenta,
  VRC− cyan), saved to the workspace, the batch or this image.

Each tab shows only its own layers; leaving a tab, an image or the app with unsaved work asks
first. One copy of the app runs at a time. `recap gui --napari` opens the 4.3 layout (full
napari with recap's docked panels) for anything the window does not cover. See
[`docs/gui.md`](docs/gui.md).

## Using recap from Python

```python
from pathlib import Path
from recap import process_file
from recap.runner import run_workspace
from recap.workspace import open_workspace

ws = open_workspace(Path("examples"))
img = ws.status()[0]                                  # images: name, batch, counts, state, ...
cfg = ws.resolve(img["sha256"]).cfg                   # its settings: recap.yaml → batch → image
res = process_file(cfg, ws.image_path(img["sha256"]))  # labels, per-nucleus table, exclusions
run = run_workspace(ws, progress=lambda i, n, path, info: print(i, n))
```

The library never prints; see [`docs/architecture.md`](docs/architecture.md) for the module
map, the stable interface, progress/cancel hooks and settings schemas for GUI forms.

## Development

```bash
pixi run test      # tests
pixi run lint      # ruff (what CI runs)
pixi run format    # apply formatting
pixi run bundle win-64             # offline install bundle (packaging/) into dist/
pixi run db-revision "what changed"   # database migration after changing a table in src/recap/db.py
```

See [`CONTRIBUTING.md`](CONTRIBUTING.md) for where things go and how to add settings, columns
and commands.

## Known limitations

- Validated on one image only (total count vs manual). Per-nucleus checking on more images is
  needed before relying on the cutoffs (focus 0.65, circularity 0.65, border clipping 15%,
  infection 5%), which were set by eye on that image.
- "VRC+" means detectable replication compartments, not all infected cells.
- The Cellpose method and Cellpose training have not been run on real data.
- Counting in a real Fiji is untested (the protocol is tested with a stand-in).
- Windows and macOS are covered by the test suite in CI, not by a real-image run; the app and
  desktop icons were checked on Linux.
- 8-bit RGB exports limit intensity measurements; raw 16-bit channels are preferable.

## Acknowledgements

Thanks to Leonardo D'Aiuto for the test images, manual counts and biological guidance used to
develop and validate recap.

## License

recap (Reconstruction of Cellular Architecture and Phenotypes) is © 2026 Shane Buckley and
released under the MIT license (see `LICENSE`): provided "as is", without warranty; see the
development status at the top of this file. To cite recap, use `CITATION.cff` (GitHub: "Cite
this repository").
