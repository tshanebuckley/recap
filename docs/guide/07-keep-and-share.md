# 7. Keep and share work

[← 6. Train and run all](06-train-and-run-all.md) · [Guide](README.md) · Next: [Testing and handoff →](08-testing-and-handoff.md)

## US-21 Bring in counts made with Fiji's Cell Counter

*As a lab member, I bring in counts I already made with Fiji's Cell Counter.*

| In the app | In a terminal |
|---|---|
| Select the image; More > **Import counts (Cell Counter XML / CSV)...** | `recap import-counts IMG markers.xml --by ben` |

- *Cell Counter* plugin: type 1 for VRC− nuclei, type 2 for VRC+, then *Save Markers* (XML).
- *Multi-point tool*: counter 0 for VRC−, counter 1 for VRC+, then Analyze > Measure, save the
  Results as CSV. If the image has a scale, add `--pixel-size` (units per pixel).
- Totals only: `recap import-counts IMG --nuclei 472 --vrc-pos 240`.

**Done when:** the totals match Cell Counter's and the count shows in the table. Counts with
points are worth more than totals: the report can then say which nuclei recap missed or merged.

## US-22 Move or copy a workspace

*As a lab member, I move or copy my workspace to another computer and keep everything.*

Copy the whole folder (`recap.yaml`, `raw/`, `processed/`, `recap-data/`), then **Open...** it on
the other computer. Paths are stored relative to the folder and images are recognised by their
content, so renamed images keep their history.

**Done when:** counts, corrected masks, batches, settings and model versions are all there.
Do not copy `recap-data/` alone: the settings live in `recap.yaml` and the images in `raw/`.
To share only the images, `raw/` is enough (recap never changes it); `processed/` can be made
again by running recap.

## US-32 Share settings and a trained model with another workspace

*As a lab member, I give my settings and a trained StarDist model to another workspace (mine or a
colleague's) without copying the images.*

| In the app | In a terminal |
|---|---|
| Images > More > **Export settings file...**; answer *Yes* to put the model in the same file | `recap settings export lab.zip --with-model` |
| In the other workspace: Images > More > **Import settings file...**, choose the `.zip` | `recap settings import lab.zip` |
| A model on its own: Training > **Export selected version...** / **Import a model...** | `recap models export v2 stardist_v2.zip`, `recap models import stardist_v2.zip --use` |

Exporting settings writes the whole `recap.yaml` with a line saying where it came from. Batch
settings go with it; single-image settings only if you ask (they name this workspace's files).
Importing makes the file's settings the workspace's (for every image); the old `recap.yaml` is
saved in `recap-data/settings_backups/`. The workspace keeps its own batch settings unless you
choose *Also take the file's batch settings* (`--with-batches`).

With the model in the same `.zip`, importing adds it as the next version of the other workspace
(whatever number that is there) and makes the imported settings use it. A settings file on its
own that names a trained model the other workspace does not have leaves that workspace's model as
it is, and says so. A model export alone is a `.zip` with the weights, thresholds and
configuration, and a note of where it came from and its scores; `recap models compare` scores it
on the other workspace's corrected masks. Any StarDist model works too: a folder or `.zip` with
`config.json` and `weights_best.h5`.

**Done when:** the other workspace's `recap.yaml` has your settings (the old one is in the backup
folder), the model shows in its Training tab with its note and is *in use*, and a run there gives
the same counts as yours on the same image.

**Watch out for:**
- Share settings and the model they use in one `.zip`: the model's version number usually differs
  in the other workspace, and the `.zip` takes care of that.
- Importing settings changes every image's settings: run the out-of-date images afterwards.
- Unpublished models are lab data: do not attach them to GitHub issues.

## US-23 Update recap without losing work

*As a lab member, my work survives updating recap.*

Opening a workspace with a newer recap first copies the database to
`recap-data/backups/recap_v<old version>_<date>_<time>.db`, then upgrades it, and says so. An
older recap refuses a workspace used by a newer one. A recap 4.2 folder (with `recap-data/` but
no `recap.yaml`) is opened with **New...** and *Copy a settings file...* (or
`recap init FOLDER --from old.yaml`); its workspace-wide settings move into recap.yaml. Opening
a 4.3 or 4.4 workspace with recap 5.0 moves its batch and image settings from the database into
recap.yaml (under `batches:` and `images:`); an old `input.images` list in recap.yaml is removed,
and the images it left out are written to `.recapignore`.

**Done when:** the upgrade message appears, the backup exists and nothing is lost (batch and
image settings are now at the end of recap.yaml). Results
from before 4.3 show as *out of date* (they have no record of their settings): **Run
out-of-date** once.

## US-24 Plain messages for mistakes

*As a lab member, when I make a mistake recap tells me plainly what to do.*

Try a wrong image path, Fiji not set, tuning with no corrected masks, a wrong channel number.

**Done when:** each gives a short message saying what to do, not an error dump. If one does
not, it is a defect (see [Testing and handoff](08-testing-and-handoff.md)).

## US-28 Terminal and app together

*As a lab member, what I do in the terminal shows in the app and the other way round.*

With the app open, run `recap settings set --batch NAME filters.min_area=50` and `recap count IMG`
in a terminal, then More > **Rescan folder** in the app. Then use **Set batch...** in the app and
`recap batch list` in the terminal.

**Done when:** each change shows on the other side. Commands work from any folder inside the
workspace (or give the folder: `recap status path/to/workspace`).

## US-25 Follow the README without help

*As a new lab member, I can follow the README from install to report without help.*

Follow the main [README](../../README.md) and these pages on a fresh machine and write down
anything unclear. Fixing the wording is as valuable as fixing code.
