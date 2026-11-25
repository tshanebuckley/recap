# 3. First pass and manual counts

[← 2. Workspace](02-workspace.md) · [Guide](README.md) · Next: [4. Guided settings →](04-guided-settings.md)

The first pass gives recap's outlines and counts for every image, and the lab's own counts and
corrections for a few. Those few are what everything later is checked against.

## US-07 Get recap's first outlines and counts

*As a lab member, I get recap's first outlines and counts for all my images.*

| In the app | In a terminal |
|---|---|
| **Run all** (or select images and **Run selected**); **Cancel** stops after the current image | `recap run` (all), `recap run -i IMG`, `recap run --out-of-date` |

Each image is run with its own settings (recap.yaml → its batch → itself). Outputs go to
`results/` in the workspace (`output.directory`): tables per image, label images, QC overlays,
`image_summary_*.csv` and a run log. Columns are explained in
[`docs/output_columns.md`](../output_columns.md).

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

Double-click an image (or **Open image**). DAPI is blue, ICP8 green, the third channel red,
each scaled to its own range. In **Manual** mode you see recap's outlines; toggle
*excluded nuclei* in napari's layer list (left) to see what the filters removed.

**Done when:** the stains are readable and there is one set of outlines on the nuclei.

**napari basics:** scroll to zoom, drag to pan, the eye icons in the layer list show and hide
layers, Ctrl+Z undoes edits.

## US-09 Count nuclei by hand in Fiji

*As a lab member, I count nuclei by hand in Fiji and the count is stored in recap under my name.*

| In the app | In a terminal |
|---|---|
| Select an image; Manual: **Count in Fiji** | `recap count IMG... --by ana --remember` |

The first time, recap asks your name (tick *Remember on this computer*; change it later in
More > **Your name for counts...**). Fiji opens the image in recap's adjusted view (DAPI blue,
ICP8 green, the same contrast recap uses) with the Multi-point tool:

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

Open an image; Manual: **Count here**, then click nuclei. Press **VRC+** before clicking
infected nuclei and **VRC-** to go back; the pressed button shows what clicks mark. To remove a
point, choose napari's select tool (arrow, in the layer controls), click the point and press
Backspace; then press VRC- or VRC+ to go on clicking. **Save count** stores it under your name.

**Done when:** the saved totals match what you clicked.

## US-11 Correct recap's outlines

*As a lab member, I correct recap's outlines on an image and save the corrected mask.*

Open an image; Manual > *Correct outlines*:

| Problem | What to do |
|---|---|
| recap missed a nucleus | **New nucleus**, then paint it (adjust **Brush size**) |
| two nuclei merged into one | **Pick** the merged one, **Erase** the joint, then **New nucleus** and paint the other half |
| an extra object (debris, a fragment) | **Erase** it |
| a hole inside an outline | **Pick** the nucleus, then **Fill** |

**Save corrected mask** keeps it (earlier versions are kept too). Corrected masks are the
reference for tuning and training, so a few careful ones are worth more than many rough ones.

**Done when:** the table's *corrected* column counts it and the image reopens with your
corrections.

**Watch out for:** correct the whole image, not just the obvious mistakes: anything left wrong
teaches tuning and training the wrong thing. Aim for at least 3 corrected images before tuning
and 5 before training, from different batches.

## US-12 One image's history

*As a lab member, I see one image's history: recap's counts, manual counts, masks.*

Select an image, **Summary**: file details, recap's result for every run, every manual count,
corrected masks, and plots (nucleus areas, VRC+ fractions, recap vs manual counts).

**Done when:** the summary matches what you did to that image.
