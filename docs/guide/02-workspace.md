# 2. Set up the workspace

[← 1. Get started](01-get-started.md) · [Guide](README.md) · Next: [3. First pass →](03-first-pass.md)

## US-05 Open your folder of images

*As a lab member, I open my folder of images and see where each image stands.*

| In the app | In a terminal |
|---|---|
| **New...** (or **Open...**), choose the folder with your images | `recap init path/to/images --from my_settings.yaml` |
| If it has no settings yet: **Copy a settings file...** (recommended) or **Start from defaults** | `recap status path/to/images` |

recap creates `recap.yaml` (every setting of the workspace) and `recap-data/`, moves every image
in the folder (subfolders included) into `raw/unbatched/`, and lists them. recap's own outputs
(label images, `recap-data/`) are skipped; to leave out other images, see US-30; where everything
goes, see US-31. Next time the app opens this workspace directly, and
the **Workspace** list remembers recent ones.

**Done when:** one row per image, state *not run*.

**Choosing the settings to start from:** copy a settings file that already worked on similar
images (for the day-79 organoids: `examples/recap.yaml`). Starting from defaults means checking
the channel numbers yourself: the Settings tab (US-13) shows the result immediately, or open
Images > More > **Edit settings file (recap.yaml)**. Channel numbers start at 0; for RGB exports red = 0,
green = 1, blue = 2.

**Watch out for:**
- One folder = one workspace = one recap.yaml. Do not keep several settings files for the same
  images; to compare a different method, see [stage 6](06-train-and-run-all.md#comparing-two-methods).
- A folder used with recap 4.2 opens the same way (New... and copy its old settings file);
  its workspace-wide settings move into recap.yaml. See US-23.
- Keep workspaces on a local disk. Synced folders (OneDrive, network drives) may lock the
  database (technical check W-05).

## US-29 Add images to the workspace, into a batch

*As a lab member, I add new image files or a folder of images to my workspace and choose the
batch they belong to.*

| In the app | In a terminal |
|---|---|
| **Add images** > *Image files...* (one or several) or *A folder of images...* | `recap add new_image.tif` or `recap add path/to/folder` |
| Choose the batch: an existing one, **New batch...** (name, optional note) or *No batch*; **Add** | it asks the same; or `--batch "stain 2026-11-25"`, `--no-batch` |

A folder is searched with its subfolders for TIFF images; recap's own outputs (label images,
`recap-data/`) are skipped. Images from outside the workspace are **copied** into
`raw/batches/<batch>/` (`raw/unbatched/` with no batch); your originals are not touched. Images
already inside the workspace folder are moved there. An image whose content is already in the
workspace is not copied a second time, but it still goes into the batch you chose (and moves to
its folder). The message says how many were added, copied and already there.

**Done when:** the new images are in the table with the batch you chose, state *not run*; run
them with **Run out-of-date** (or select them, **Run selected**).

**Watch out for:**
- A new staining day is a new batch. Adding its images to an older batch gives them that
  batch's settings, which may not suit them.
- Copies take disk space; for very large folders, move the images into the workspace folder
  yourself, More > **Rescan folder** (they go to `raw/unbatched/`), then **Set batch...**.
- In a script, `recap add` needs `--batch` or `--no-batch` (it cannot ask).

## US-30 Leave images out of the workspace

*As a lab member, I leave some images out of the workspace (and bring them back), without
deleting them.*

| In the app | In a terminal |
|---|---|
| Select images in the table; More > **Ignore selected images** | `recap ignore data/day79_03.tif` (or a folder: `recap ignore old_exports/`) |
| Filter above the table: **ignored images (N)**; select them, More > **Stop ignoring selected images** | `recap unignore data/day79_03.tif`; `recap ignore` alone lists what is ignored |

recap writes them to `.recapignore` in the workspace folder, one path per line, like a
`.gitignore`. You can edit it by hand for patterns: `*_bad.tif` (a name anywhere), `old/` (a
folder), `!old/keep.tif` (bring one back). Ignored images are not run, counted in the spot check,
tuned or trained on, and are not in reports; their counts and corrected masks are kept for when
you bring them back.

**Done when:** the ignored images leave the table (they are under *ignored images*), **Run all**
skips them, and stopping ignoring brings them back with their history.

**Watch out for:** ignoring is for images that should not be analysed at all (wrong stain, test
exports, duplicates). An image that is valid but weakly stained gets its own settings instead
(US-15).

## US-31 Find an image and everything made from it, by batch

*As a lab member, I find each image's raw file and everything recap made from it in folders named
after its staining batch, and they follow the image when its batch changes.*

The workspace folder has two mirrored trees, one folder per batch in each:

```
raw/batches/stain 2026-10-03/day79_01.tif                    the image as acquired (never changed)
processed/batches/stain 2026-10-03/day79_01/                 everything made from it:
    day79_01_labels.tif  day79_01_nuclei.csv  day79_01_qc.png  day79_01_corrected_1.tif  ...
    previous/                                                the run before the latest
    previous_runs/20261120_141500.zip                        each earlier run, zipped
raw/unbatched/ and processed/unbatched/                      images in no batch yet
processed/runs/<date>/                                       each run's summary and run log
processed/reports/<date>/                                    reports
```

There is nothing to do: recap moves files to match. Putting images in a batch (or another one,
or none), renaming or deleting a batch moves their raw files and their folders in `processed/`;
images added go straight into `raw/`; an image you drop anywhere else in the workspace folder is
moved into `raw/unbatched/` at the next **Rescan folder** (or any run). Ignored images (US-30) are
left where they are. Every move is listed in `recap-data/moves.csv`, and commands still accept
an image's old path.

A workspace from recap 5.0 or older is reorganized this way the first time recap 5.1 opens it:
images from anywhere in the folder, outputs from the old `results/` folder, corrected masks from
`recap-data/masks/`.

**Done when:** after **Set batch...**, the image's raw file and its folder in `processed/` are in
the new batch's folders; renaming the batch renames both folders; the image still opens with its
outlines and corrected masks, and its counts and state are unchanged.

**Watch out for:**
- Do not rename or move files inside `raw/` and `processed/` by hand while the app is open; use
  **Set batch...** and More > **Batches...**. (A file moved by hand is put back at the next scan.)
- A file open in another program (Fiji, Excel) cannot be moved on Windows: recap leaves it and
  moves it next time.
- Keep the files you make yourself (notes, spreadsheets) outside `raw/` and `processed/`.

## US-06 Group images stained together into a batch

*As a lab member, I group the images stained together into a batch so they share settings.*

| In the app | In a terminal |
|---|---|
| Ctrl/Shift-click the images of one staining, **Set batch...**, type a name | `recap batch assign "stain 2026-10-03" data/day79_*.tif` |
| More > **Batches...**: see each batch's images and settings, **Rename...**, **Delete** | `recap batch list`, `recap batch rename OLD NEW`, `recap batch delete NAME` |
| Set batch with an empty name takes images out of their batch | `recap batch remove IMAGE...` |

An image is in at most one batch. Deleting a batch removes its settings; its images stay, in
no batch.

**Done when:** the batch column shows the name; a renamed batch keeps its settings.

**Why batches matter:** staining strength varies between staining days more than between
images of one day. Settings fixed per batch (especially the ICP8 range, from an infected
control of that batch) keep counts comparable within a batch, and let the model later be trained
on every batch with settings that suit it.
