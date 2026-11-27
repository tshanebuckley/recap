# Architecture

recap (Reconstruction of Cellular Architecture and Phenotypes) is a library with a thin command line on top. Everything the CLI does can be done from
Python without printing, which is how a GUI (napari, Qt, web) should use it.

## Modules

Arrows point from a module to what it uses; there are no cycles.

```
cli ─┬──► runner ──► spotcheck ──────────────► pipeline ──► filters ──► config
gui ─┤         │           └──► counts               │  └──► measure ──► geometry
     │         └──► runlog                           │  └──► segment ──► geometry
     ├──► counting ──► fijicount ──► fiji            └──► qc, io
     ├──► tune, train ──► evaluate ──► pipeline, compare
     │        └──► workspace ──► db (SQLModel, Alembic), levels, settingsfile, counts, importers, report
     ├──► compare, report ──► counts, io
     └──► importers ──► counts, io
errors: used everywhere
```

| Module | Responsibility | Use from a GUI? |
|---|---|---|
| `config` | Pydantic models for every YAML section; `TEMPLATE`; `load_config`. | Yes: settings forms (see below). |
| `io` | Read images (TIFF, Fiji hyperstacks, RGB exports), blank `exclude_regions`, file hashes. | Yes. |
| `segment` | Channel combination; classical / StarDist / Cellpose segmentation; merging; small bodies. | Yes, for previews. |
| `geometry` | Ellipse fitting, clipped-nucleus estimates, circularity. | Rarely. |
| `measure` | Per-nucleus intensities, infection call, focus and clipping features. | Via `pipeline`. |
| `filters` | Final filters and the kept/excluded split. | Via `pipeline`. |
| `qc` | Per-image warnings and count ranges. | Via `pipeline`. |
| `pipeline` | `process_image` / `process_file` (one image, pure), split into `segment_stage` (slow) and `finish_stage` (fast); `run_batch` (writes outputs). | **Main entry point.** |
| `counts` | `ManualCount`, its CSV format, program-vs-manual comparison. | Yes, for manual counting. |
| `importers` | Counts from Fiji Cell Counter XML / Multi-point CSV / typed totals. | Yes. |
| `report`, `compare` | Cross-run and cross-method summaries. | Yes. |
| `workspace` | A folder of images with one settings file (`recap.yaml`: every level of the settings), an optional `.recapignore` and one database (`recap-data/recap.db`): images, runs (and whether each image is up to date), manual counts, corrected masks, batch membership, model versions (and which is used), tuning, reports. `open_workspace`, `init_workspace`; moves settings older versions kept in the database into `recap.yaml` when it opens a workspace. | Yes: the app's data model. The CLI and the app call the same methods. |
| `settingsfile` | Edit `recap.yaml` keeping its comments (ruamel.yaml): the workspace's own sections and the `batches:` / `images:` entries. | Via `workspace`. |
| `ignore` | `.recapignore`: which images a workspace leaves out (gitignore-like lines). | Via `workspace`. |
| `transfer` | Export and import whole settings files (with a backup of recap.yaml; optionally with their trained model in one `.zip`) and StarDist model versions (`.zip` with `recap_model.yaml`; any StarDist model folder or zip can be imported). | Via the CLI and the app. |
| `stardist_worker` | StarDist in a helper process (`python -m recap.stardist_worker`): the app turns it on so its own process never loads TensorFlow; `segment.stardist_labels` and the app's training use it. | Via `segment`, the app. |
| `modelpath` | Finds a model named in the settings: trained versions are relative to the workspace folder, whatever folder recap was started in. | Via `segment`, `workspace`. |
| `layout` | Where a workspace keeps its files: `raw/` and `processed/`, mirrored, by batch; `Organizer` moves images and outputs to match batches (and reorganizes older workspaces once), updating the database, recap.yaml's `images:` entries and run logs; `recap-data/moves.csv`. Never moves files from outside the workspace. | Via `workspace`. |
| `counting` | Manual counting shared by `recap count`, the app and the spot check: recap's adjusted view, saving under the counter's name. | Yes. |
| `db`, `migrations` | SQLModel tables; version check, backup and Alembic upgrade when a workspace is opened. | Via `workspace`. |
| `evaluate` | Score a segmentation against edited masks (F1, precision, recall, count error). | Yes. |
| `tune` | Search settings, scored against edited masks. | Yes (Tuning panel). |
| `train` | Fine-tune StarDist / Cellpose on edited masks; versioned models; comparison. | Yes (Training panel). |
| `fijicount` | Counting in Fiji (started as its own program with `assets/fiji_count.groovy`). | Yes. |
| `levels` | Settings per staining batch and image on top of the workspace's: allowed keys, merge, diff, `nest` / `flatten` (recap.yaml's layout), `settings_hash` (the out-of-date check). Stored in recap.yaml by `workspace`. | Yes: the run, tuning, training and guided mode use it. |
| `gui` (package) | napari plugin (`napari.yaml`): the recap panel (workspace, runs, Manual and Guided modes), Tuning and Training; `recap gui`; one app per user. See `docs/gui.md` for the command behind every button. | It is the GUI. |
| `desktop` | `recap desktop-icon`: per-OS shortcut that runs `recap gui`; replaces recap's old icons. | Only for the icon command. |
| `fiji` | Find, save and check the Fiji installation; per-OS launchers and folders. | Via `fijicount`. |
| `spotcheck` | Random spot check; counting is done by a `counter` callback (Fiji or napari). | Via `runner`. |
| `runner` | `run_workspace`: the whole `recap run` and the app's Run buttons (spot check, batch, run log, recorded in the workspace); `run_pipeline(path)` the same for a workspace given as a path; `settings_file=` for comparison runs. Every run is a workspace run. | Yes, for runs. |
| `runlog` | Log file setup, config diffs, package versions. | Indirectly. |
| `cli` | Typer commands: argument parsing, messages, exit codes. Nothing else. | No. |
| `errors` | `RecapError`, `ConfigError`, `Cancelled`. | Catch these. |

## Public interface

Names importable from `recap` directly (lazily, so `import recap` is fast):

```python
from recap import load_config, process_image, process_file, run_batch, run_pipeline
from recap import PipelineConfig, Result, RunResult, RecapError, ConfigError, Cancelled
```

These are the stable interface: changing their signatures or return types is a major version
change. Everything else is internal and may change in minor versions; if a GUI needs it, promote
it to the public list and add a test.

### A workspace

```python
from recap.workspace import open_workspace, init_workspace
ws = open_workspace(Path("experiment"))           # or init_workspace(folder, settings_from=None)
ws.settings()                                     # recap.yaml as a PipelineConfig
rows = ws.status()                                # per image: batch, counts, state (up to date / out of date / not run), ...
r = ws.resolve(sha)                               # an image's settings: r.cfg, r.sources, r.batch, r.reason
ws.save_effective("batch", sha, edited_cfg, keys) # store edited values at a level (only the differences)
ws.update_level("image", sha, {"filters.min_focus": 0.6}, reason="dim")   # = recap settings set
ws.use_model("stardist", 2); ws.report(); ws.import_count(image, xml_file)
ws.ignore([image_or_folder]); ws.unignore([...]); ws.images()   # .recapignore; images() leaves ignored ones out
ws.image_folder(sha); ws.run_dir(stamp); ws.organize()           # processed/.../<image>/, processed/runs/<stamp>/
ws.current_path(old_path); ws.sha_of(path)                      # follow recap's moves (recap-data/moves.csv)
ws.keep_previous_outputs(sha)                                   # before a run: current -> previous/, previous -> zip
```

### One image (previews, interactive work)

```python
cfg = ws.resolve(sha).cfg                         # or: raw, cfg, sha = load_config(Path("settings.yaml"))
                                                  # (a file's batches: and images: are not part of cfg)
res = process_file(cfg, Path("image.tif"))        # or process_image(cfg, dapi, vrc, neuron)
res.labels            # int array, 0 = background, 1..n = counted nuclei (rows of res.table)
res.table             # pandas DataFrame, one row per counted nucleus (docs/output_columns.md)
res.excluded_labels   # nuclei removed by filters; res.excluded has their rows and reasons
res.dapi_n, res.vrc_n # normalised channels (0-1) for display
res.n_small_bodies, res.n_excluded, res.merges
```

`process_image` is pure: no files, no printing, no global state except a model cache
(`segment._MODELS`) so StarDist / Cellpose load once per process.

For live previews, run `pipeline.segment_stage` once (the slow part: normalisation and
StarDist / Cellpose) and `pipeline.finish_stage` for each change of the fast settings
(fragment merging, filters, infection call), as `evaluate.score_config` does.

### A batch

```python
from recap.runner import run_workspace
result = run_workspace(
    ws,
    images=None,                             # or a list of image paths
    spot_check=True,                         # validation.enabled in recap.yaml
    progress=lambda i, n, path, info: ...,   # after each image; info = that image's summary row
    cancel=lambda: stop_requested,           # checked before each image
    decide=None,                             # spot-check decision after the overview
    counter=None,                            # counts spot-check images (e.g. fijicount.FijiCounter)
)
result.status   # "completed" | "counts_saved" | "cancelled"
result.files    # paths of image_summary, all_nuclei, run log, final config, ...
```

Run it in a worker thread; `progress` is called from that thread. On cancel, outputs of the
images already finished are still written. Errors: `ConfigError` (invalid settings or files),
`Cancelled` (user cancelled at the spot check). Logging goes to the `recap` logger;
attach your own handler to show it in the GUI.

### Settings forms

The settings are pydantic models with types, ranges and descriptions, so a GUI can generate
its forms instead of hard-coding them:

```python
schema = PipelineConfig.model_json_schema()      # JSON Schema: types, defaults, min/max, descriptions
cfg = PipelineConfig.model_validate(form_values) # the same validation as the YAML file
```

Saving `cfg.model_dump(mode="json")` as YAML gives a file `recap run` accepts. To change a
workspace's recap.yaml without losing its comments, use `settingsfile.set_values` (or
`Workspace.update_level("workspace", "", values)`).

## Conventions

- Library code never prints and never exits: it logs, returns results and raises `RecapError`s.
- Images are loaded as `(channel, y, x)` float32; label images are integers, 0 = background.
- Every user-visible output column is documented in `docs/output_columns.md` (checked by a test).
- Settings live in `config.py` only; a new setting needs a field (with description), a line in
  `TEMPLATE`, and a test.
