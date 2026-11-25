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

Copy the whole folder (images, `recap.yaml`, `recap-data/`, `results/`), then **Open...** it on
the other computer. Paths are stored relative to the folder and images are recognised by their
content, so renamed images keep their history.

**Done when:** counts, corrected masks, batches, settings and model versions are all there.
Do not copy `recap-data/` alone: the settings live in `recap.yaml`.

## US-23 Update recap without losing work

*As a lab member, my work survives updating recap.*

Opening a workspace with a newer recap first copies the database to
`recap-data/backups/recap_v<old version>_<date>_<time>.db`, then upgrades it, and says so. An
older recap refuses a workspace used by a newer one. A recap 4.2 folder (with `recap-data/` but
no `recap.yaml`) is opened with **New...** and *Copy a settings file...* (or
`recap init FOLDER --from old.yaml`); its workspace-wide settings move into recap.yaml.

**Done when:** the upgrade message appears, the backup exists and nothing is lost. Results
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
