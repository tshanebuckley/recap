# 2. Set up the workspace

[← 1. Get started](01-get-started.md) · [Guide](README.md) · Next: [3. First pass →](03-first-pass.md)

## US-05 Open your folder of images

*As a lab member, I open my folder of images and see where each image stands.*

| In the app | In a terminal |
|---|---|
| **New...** (or **Open...**), choose the folder with your images | `recap init path/to/images --from my_settings.yaml` |
| If it has no settings yet: **Copy a settings file...** (recommended) or **Start from defaults** | `recap status path/to/images` |

recap creates `recap.yaml` (the settings for every image in the folder, `input.images: []`)
and `recap-data/`, then lists the images. Images can be in subfolders; recap's own outputs
(label images, `recap-data/`) are ignored. Next time the app opens this workspace directly, and
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
`recap-data/`) are skipped. Images from outside the workspace are **copied** into a subfolder
named after the batch (`--to NAME` chooses another); your originals are not touched. Images
already inside the workspace folder stay where they are. An image whose content is already in
the workspace is not copied a second time, but it still goes into the batch you chose. The
message says how many were added, copied and already there.

**Done when:** the new images are in the table with the batch you chose, state *not run*; run
them with **Run out-of-date** (or select them, **Run selected**).

**Watch out for:**
- A new staining day is a new batch. Adding its images to an older batch gives them that
  batch's settings, which may not suit them.
- Copies take disk space; for very large folders, put the images into the workspace folder
  yourself, More > **Rescan folder**, then **Set batch...**.
- In a script, `recap add` needs `--batch` or `--no-batch` (it cannot ask).

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
