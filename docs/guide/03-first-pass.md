# 3. First pass and manual counts

[← 2. Workspace](02-workspace.md) · [Guide](README.md) · Next: [4. Guided settings →](04-guided-settings.md)

The first pass gives recap's outlines and counts for every image, and the lab's own counts and
corrections for a few. Those few are what everything later is checked against.

## US-07 Get recap's first outlines and counts

*As a lab member, I get recap's first outlines and counts for all my images.*

| In the app | In a terminal |
|---|---|
| **Run all** (or select images and **Run selected**); **Cancel** stops after the current image | `recap run` (all), `recap run -i IMG`, `recap run --out-of-date` |

Each image is run with its own settings (recap.yaml → its batch → itself). Each image's tables,
label images and QC overlay go to its own folder, `processed/batches/<batch>/<image>/` (or
`processed/unbatched/<image>/`), next to its corrected masks; the run's `image_summary_*.csv`,
`all_nuclei_*.csv` and run log go to `processed/runs/<date>/` (see US-31). Columns are explained
in [`docs/output_columns.md`](../output_columns.md).

**Done when:** counts appear and the state is *up to date*. The example image gives
**472 nuclei, 248 VRC+**; on Windows or Mac, write down any difference.

**Spot check:** with `validation.enabled: true` in recap.yaml, **Run all** (and `recap run`
without `-i`) first asks you to count a random sample by hand in Fiji, reusing counts already
in the workspace, then shows how recap compares before running everything. Runs of selected
images skip it.

**Watch out for:** the first StarDist run downloads the model (about 5 MB) unless it came with
the installer. QC flags in the last column (for example `saturated_vrc`, `blurry_vs_batch`)
are worth a look before trusting an image's count.

## US-08 Look at an image

*As a lab member, I open an image and clearly see the stains and recap's outlines.*

Double-click an image in the **Images** tab (or **Open image**, or ◀ ▶ in the top bar). DAPI
is blue, ICP8 green, the third channel (the marker) red, each scaled to its own range. The
**Show** bar under the image turns each stain on or off and sets the brightness; the channel
number is in each name, so a wrong channel in the settings shows at once. The Images tab shows
recap's outlines; tick *excluded nuclei* to see what the filters removed.

**Done when:** the stains are readable and there is one set of outlines on the nuclei.

**Basics:** scroll to zoom, drag to pan. Ctrl+1 to Ctrl+4 switch between the Images, Count,
Fix outlines and Settings tabs.

## US-09 Count nuclei by hand in Fiji

*As a lab member, I count nuclei by hand in Fiji and the count is stored in recap under my name.*

| In the app | In a terminal |
|---|---|
| Open the image; **Count** tab: **Count this image in Fiji instead** | `recap count IMG... --by ana --remember` |

The first time, recap asks your name (tick *Remember on this computer*; change it later in
Images > More > **Your name for counts...**). Fiji opens the image in recap's adjusted view (DAPI
blue, ICP8 green, the same contrast recap uses) with the Multi-point tool:

- click every nucleus; **counter 0 = VRC−**, **counter 1 = VRC+**;
- to switch counters, double-click the Multi-point tool and change *Counter*;
- press **Done counting** in the small recap window: Fiji shows the totals and asks whether to
  import them.

Points are saved every second. If Fiji closes before you import, recap asks whether to keep
the points. `recap count --original` shows the image file as it is instead of recap's view.

**Done when:** the table shows your count and name, and the app stayed usable while Fiji was
open.

**Watch out for:** count the same field you would report, including border nuclei the way the
protocol says. Two people counting the same image is useful: the report compares counters
(US-20).

## US-10 Count by clicking in the app

*As a lab member, I count by clicking in the app instead of Fiji.*

Open an image and choose the **Count** tab. recap's outlines are hidden so they do not guide
your eye.

| To | Do |
|---|---|
| mark a VRC− nucleus | click it (blue point) |
| mark a VRC+ nucleus | Shift+click it (orange point) |
| remove a point | right-click it |
| undo the last change | **Undo** or Ctrl+Z |
| move around | scroll to zoom, drag to pan (a drag never adds a point) |

The totals update as you click. **Save count** stores it under your name; **Continue my last
count** brings back your saved points on that image to carry on.

**Done when:** the saved totals match what you clicked, and the table shows the count.

**Watch out for:** opening another image or closing the window with an unsaved count asks
first.

## US-11 Correct recap's outlines

*As a lab member, I correct recap's outlines on an image and save the corrected mask.*

Open an image and choose the **Fix outlines** tab. Pick a tool, then:

| Problem | Tool | What to do |
|---|---|---|
| recap missed a nucleus | **Draw new** | drag around it; when you let go the outline is filled (only where there is no nucleus yet) |
| an extra object (debris, a fragment that is not a nucleus) | **Delete** | click it |
| two nuclei joined into one | **Split** | drag a line across the join, from outside to outside |
| one nucleus in two pieces | **Merge** | click one piece, then the other |

**Undo** (or Ctrl+Z) goes back one edit at a time. While Draw new or Split is chosen, dragging
draws instead of panning; choose Delete or Merge to pan again. **Save corrected mask** keeps it
(as `<image>_corrected_<N>.tif` in the image's folder under `processed/`; earlier versions are
kept too). Corrected masks are the reference for tuning and training, so
a few careful ones are worth more than many rough ones.

**Done when:** the table's *corrected* column counts it and the image reopens with your
corrections.

**Watch out for:** correct the whole image, not just the obvious mistakes: anything left wrong
teaches tuning and training the wrong thing. Aim for at least 3 corrected images before tuning
and 5 before training, from different batches. A nucleus whose outline is the wrong shape:
Delete it, then Draw new.

## US-12 One image's history

*As a lab member, I see one image's history: recap's counts, manual counts, masks.*

Select an image in the Images tab, **Summary**: file details, recap's result for every run, every manual count,
corrected masks, and plots (nucleus areas, VRC+ fractions, recap vs manual counts).

**Done when:** the summary matches what you did to that image.
