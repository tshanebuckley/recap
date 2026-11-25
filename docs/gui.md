# The recap desktop app (napari)

```bash
pixi run recap gui [FOLDER]                # napari with recap's panels (default: the last workspace)
pixi run recap desktop-icon                # icon that runs the line above
xvfb-run -a pixi run recap gui             # Linux without a screen (testing)
```

One **recap** panel (the workspace, its images, running recap, Manual and Guided modes) and two
tabs, **Tuning** and **Training**. How to use them, step by step: the [user guide](guide/README.md). Every button calls the same library function as the command
beside it, on the same `recap.yaml` and `recap-data/recap.db`:

| In the app | Command | Library |
|---|---|---|
| Open... / New... (folder without recap.yaml) | `recap init FOLDER [--from FILE]` | `workspace.init_workspace` |
| Image table, filter, Rescan | `recap status` | `Workspace.scan`, `Workspace.status` |
| Run selected / out-of-date / all, Run this image | `recap run [-i IMG] [--out-of-date]` | `runner.run_workspace` |
| Set batch..., More > Batches... | `recap batch assign / remove / rename / delete / list` | `Workspace.assign_batch`, `rename_batch`, `delete_batch` |
| Guided mode: Save to workspace / batch / this image | `recap settings set [--batch / --image --reason]` | `Workspace.save_effective`, `update_level` |
| Guided mode: Clear this image's own settings | `recap settings clear --image IMG` | `Workspace.set_level_settings` |
| Guided mode: values and where they come from | `recap settings show IMG` | `Workspace.resolve` |
| Manual mode: Count in Fiji | `recap count IMG` | `counting.count_in_fiji` |
| Manual mode: Count here, Save count | (none: clicking needs the app) | `counting.save_count` |
| Manual mode: Save corrected mask | (none: painting needs the app) | `Workspace.save_mask` |
| More > Import counts... | `recap import-counts IMG FILE` | `Workspace.import_count` |
| More > Report | `recap report` | `Workspace.report` |
| More > Fiji location... | `recap fiji show / set` | `Workspace.fiji`, `fiji.set_default` |
| More > Your name for counts... | `recap count --by NAME --remember` | `counts.remember_counter_name` |
| More > Edit settings file | edit `recap.yaml` | |
| Summary | `recap status` (one image) | `Workspace.image` |
| Tuning: Run tuning, Apply | `recap tune [--apply-to workspace / batch:NAME]` | `tune.tune`, `tune.apply_tuned` |
| Training: Train, Compare, Use selected / standard | `recap train`, `recap models compare`, `recap models use` | `train.train`, `train.compare_models`, `Workspace.use_model` |

Manual mode shows recap's outlines (to correct) and counts; Guided mode hides them and shows its
preview, so there is only ever one set of outlines. Leaving Guided mode, or opening another image,
with unsaved changes asks first. A run uses saved settings only.

## Code

| File | Role |
|---|---|
| `src/recap/napari.yaml` | Plugin manifest: recap, Tuning, Training |
| `src/recap/gui/main_panel.py` | The recap panel: workspace, table, runs, mode switch, Manual mode |
| `src/recap/gui/guided.py` | Guided mode: the settings steps, preview layers, saving to a level |
| `src/recap/gui/jobs.py` | Tuning and Training tabs |
| `src/recap/gui/dialogs.py` | Image summary, batches, Fiji location, making a folder a workspace |
| `src/recap/gui/common.py` | Shared state (`STATE`), background work, questions from a background thread, tables, remembered names and workspaces |
| `src/recap/gui/panels.py` | Index of the panels (what the manifest points at) |
| `src/recap/gui/app.py` | `recap gui`: viewer, docked panels, image layers |
| `src/recap/gui/data.py`, `preview.py` | What an image view contains; Guided mode's preview (no Qt; segmentation reruns only for settings in `MODEL_STAGE_SETTINGS`) |
| `src/recap/gui/single_instance.py` | One app per user |
| `tests/test_panels.py`, `tests/test_guided.py` | Panels with Qt off-screen and a stand-in viewer |

Rules: panels call the library and hold no logic (a new feature gets its library function and
command first, then its button); Qt-free code stays testable without a screen (CI has no OpenGL,
so tests never open a napari window); napari is pinned to 0.10.x, so upgrade on purpose. Linux
without a GPU: `recap gui` sets `GALLIUM_DRIVER=softpipe`, because Mesa's llvmpipe crashes when it
compiles a shader after TensorFlow has loaded.

## Next steps

1. Guided mode for the settings it does not show yet (`fragments`, `qc`).
2. Per-nucleus scores in `recap report` with corrected masks as the reference.
3. The assistant panel (deferred).

## napari plugins worth trying

Install into a separate pixi environment (several bring PyTorch).

| Plugin | Use |
|---|---|
| micro-sam | Click-prompted segmentation; fastest way to draw or fix nuclei |
| napari-nninteractive | Promptable 3D segmentation; heavy for 2D, best with a GPU |
| cellpose-napari, stardist-napari | Run either model on the open image |
| napari-skimage-regionprops | Measurement tables for a mask |
| napari-clusters-plotter | Plot nucleus features, select groups |
| ndevio | Read CZI / LIF / ND2 directly |
