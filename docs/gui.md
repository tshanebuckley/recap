# The recap desktop app

```bash
pixi run recap gui [FOLDER]                # recap's window (default: the last workspace)
pixi run recap gui --napari [FOLDER]       # the 4.3 layout: full napari with recap's docked panels
pixi run recap desktop-icon                # icon that runs the first line
xvfb-run -a pixi run recap gui             # Linux without a screen (testing)
```

One window: the image on the left, drawn by napari's canvas alone (no layer list, layer
controls or napari menus), a **Show** bar under it (each stain on or off, the extras of the
current tab, brightness), and four tabs on the right: **Images**, **Count**, **Fix outlines**,
**Settings**. The top bar has **Add images**, **Tuning and training** (their own window) and ◀
the image list ▶. How to use them, step by step: the [user guide](guide/README.md). Every
button calls the same library function as the command beside it, on the same `recap.yaml` and
`recap-data/recap.db`:

| In the app | Command | Library |
|---|---|---|
| Images: Open... / New... (folder without recap.yaml) | `recap init FOLDER [--from FILE]` | `workspace.init_workspace` |
| Add images (files or a folder), then the batch (copied into raw/batches/<batch>/) | `recap add FILES/FOLDERS [--batch NAME / --no-batch]` | `Workspace.add_images` |
| Images: table, filter, Rescan | `recap status` | `Workspace.scan`, `Workspace.status` |
| Images: More > Ignore / Stop ignoring selected images; filter *ignored images* | `recap ignore PATH...`, `recap unignore PATH...`, `recap ignore` | `Workspace.ignore`, `unignore` (`.recapignore`) |
| Images: Run selected / out-of-date / all, Run this image | `recap run [-i IMG] [--out-of-date]` | `runner.run_workspace` |
| Images: Set batch..., More > Batches... (files move to the batch's folders) | `recap batch assign / remove / rename / delete / list` | `Workspace.assign_batch`, `rename_batch`, `delete_batch` (then `Workspace.organize`) |
| Settings: Save to workspace / batch / this image (all into recap.yaml) | `recap settings set [--batch / --image --reason]` | `Workspace.save_effective`, `update_level` |
| Settings: Clear this image's own settings | `recap settings clear --image IMG` | `Workspace.set_level_settings` |
| Settings: values and where they come from | `recap settings show IMG` | `Workspace.resolve` |
| Count: Count this image in Fiji instead | `recap count IMG` | `counting.count_in_fiji` |
| Count: click, Save count | (none: clicking needs the app) | `counting.save_count` |
| Fix outlines: Save corrected mask | (none: drawing needs the app) | `Workspace.save_mask` |
| Images: More > Import counts... | `recap import-counts IMG FILE` | `Workspace.import_count` |
| Images: More > Report (processed/reports/) | `recap report` | `Workspace.report` |
| Images: More > Fiji location... | `recap fiji show / set` | `Workspace.fiji`, `fiji.set_default` |
| Images: More > Your name for counts... | `recap count --by NAME --remember` | `counts.remember_counter_name` |
| Images: More > Edit settings file | edit `recap.yaml` | |
| Images: More > Export / Import settings file... (with the model: a .zip) | `recap settings export FILE [--with-model] / import FILE` | `transfer.export_settings`, `import_settings` |
| Images: Summary | `recap status` (one image) | `Workspace.image` |
| Tuning: Run tuning, Apply | `recap tune [--apply-to workspace / batch:NAME]` | `tune.tune`, `tune.apply_tuned` |
| Training: Train, Compare, Use selected / standard | `recap train`, `recap models compare`, `recap models use` | `train.train`, `train.compare_models`, `Workspace.use_model` |
| Training: Export selected version..., Import a model... | `recap models export VERSION FILE.zip`, `recap models import FILE [--use]` | `transfer.export_model`, `import_model` |

**What each tab shows.** Images and Fix outlines: the stains and recap's outlines (the
corrected ones if any), *excluded nuclei* on request. Count: the stains and your points only.
Settings: the stains and the live preview (VRC+ magenta, VRC− cyan), *recovered (DAPI + ICP8)*
and *preview excluded* on request. So there is only ever one set of outlines.

**Mouse.** Scroll zooms, dragging pans. Count: click = VRC−, Shift+click = VRC+, right-click
removes the nearest point (within about 12 screen pixels); a drag still pans. Fix outlines:
Draw new and Split use the drag (panning is off while they are chosen; switch to Delete or
Merge to pan), Delete and Merge use clicks.

**Keys.** Ctrl+1 to Ctrl+4 switch tabs; Ctrl+Z undoes the last point (Count) or edit (Fix
outlines).

**Asking first.** Another image, another workspace, leaving the Settings tab, or closing the
window with an unsaved count, unsaved outline corrections or unsaved settings asks first. A run
uses saved settings only.

## Code

| File | Role |
|---|---|
| `src/recap/gui/window.py` | recap's window: canvas, Show bar, top bar, the Count and Fix outlines tabs, which layers each tab shows, the mouse |
| `src/recap/gui/editing.py` | Counting points and outline edits (draw, delete, split, merge, undo), no Qt |
| `src/recap/napari.yaml` | Plugin manifest: recap, Tuning, Training (for `--napari` and napari's Plugins menu) |
| `src/recap/gui/main_panel.py` | The recap panel (the Images tab): workspace, table, runs, More; with `--napari` also the Manual / Guided switch and Manual mode |
| `src/recap/gui/guided.py` | The Settings tab (Guided mode): the settings steps, preview layers, saving to a level |
| `src/recap/gui/jobs.py` | Tuning and Training tabs |
| `src/recap/gui/dialogs.py` | Image summary, batches, Fiji location, making a folder a workspace, adding images into a batch |
| `src/recap/gui/common.py` | Shared state (`STATE`), background work, questions from a background thread, tables, remembered names and workspaces |
| `src/recap/gui/panels.py` | Index of the panels (what the manifest points at) |
| `src/recap/gui/app.py` | `recap gui`: recap's window, or with `--napari` the napari viewer with docked panels |
| `src/recap/gui/data.py`, `preview.py` | What an image view contains; Guided mode's preview (no Qt; segmentation reruns only for settings in `MODEL_STAGE_SETTINGS`) |
| `src/recap/gui/single_instance.py` | One app per user |
| `tests/test_window.py` | The editing logic; the window with Qt off-screen and no canvas |
| `tests/test_panels.py`, `tests/test_guided.py` | Panels with Qt off-screen and a stand-in viewer |

Rules: panels call the library and hold no logic (a new feature gets its library function and
command first, then its button); Qt-free code stays testable without a screen (CI has no OpenGL,
so tests use napari's ViewerModel without a canvas); napari is pinned to 0.10.x, so upgrade on purpose. Linux
without a GPU: `recap gui` sets `GALLIUM_DRIVER=softpipe`, because Mesa's llvmpipe crashes when it
compiles a shader after TensorFlow has loaded.

## Next steps

1. Guided mode for the settings it does not show yet (`fragments`, `qc`).
2. Per-nucleus scores in `recap report` with corrected masks as the reference.
3. Run TensorFlow in its own process, so the window never waits for it and Linux without a GPU
   can use the faster llvmpipe driver (DEF-006).
4. The assistant panel (deferred).

## napari plugins worth trying

These work in full napari (`recap gui --napari`, or napari itself), not in recap's window.
Install into a separate pixi environment (several bring PyTorch).

| Plugin | Use |
|---|---|
| micro-sam | Click-prompted segmentation; fastest way to draw or fix nuclei |
| napari-nninteractive | Promptable 3D segmentation; heavy for 2D, best with a GPU |
| cellpose-napari, stardist-napari | Run either model on the open image |
| napari-skimage-regionprops | Measurement tables for a mask |
| napari-clusters-plotter | Plot nucleus features, select groups |
| ndevio | Read CZI / LIF / ND2 directly |
