# 5. Tuning

[← 4. Guided settings](04-guided-settings.md) · [Guide](README.md) · Next: [6. Train and run all →](06-train-and-run-all.md)

## US-17 Let recap find better settings

*As a lab member, after correcting a few masks, I let recap find better settings and use them.*

Tuning tries combinations of the settings that matter most (ICP8 weight, StarDist threshold,
fragment merging, focus / roundness / border filters) and scores each against your corrected
masks: how many real nuclei it finds and how few extra ones it adds (F1, from 0 to 1).

| In the app | In a terminal |
|---|---|
| Correct masks on at least 3 images (US-11) | |
| **Tuning** tab: **Trials** 30, **Run tuning** | `recap tune --trials 30` |
| *Use the best settings for:* the workspace or a batch, **Apply** | `recap tune --apply-to workspace` (or `batch:NAME`) |
| **Run out-of-date** in the recap tab | `recap run --out-of-date` |

The first trial is always your current settings, so "best" is never worse than what you have.
Every trial is kept (`recap-data/tuning/<date>/trials.csv`); `--seed` makes a search repeatable.

**Done when:** the best score is at least your current one, and after Apply and the run, recap's
counts are closer to the manual counts (check with the report, US-20).

**Watch out for:**
- Tuning is only as good as the corrected masks. Correct a few images from different batches.
- Each image's batch and image settings are applied on top of the values being tried, so tuning
  respects the per-batch choices you made in Guided mode.
- Applying to a batch stores only values that differ from recap.yaml.
- A big jump in F1 from a filter alone (for example a very strict minimum area) can mean the
  corrected masks left out small nuclei. Look at a few images in Guided mode before running all.
