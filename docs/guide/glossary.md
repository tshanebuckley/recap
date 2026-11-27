# Glossary

[← Guide](README.md)

| Word | Meaning |
|---|---|
| **workspace** | A folder of images with one settings file (`recap.yaml`, all levels of the settings), an optional `.recapignore` (images left out) and recap's folder `recap-data/` (database `recap.db`, corrected masks, model versions, tuning, reports, backups). |
| **raw/, processed/** | The workspace's two mirrored trees: `raw/batches/<batch>/` holds the images as acquired, `processed/batches/<batch>/<image>/` everything recap made from each (outputs, corrected masks); `unbatched/` in both for images in no batch; `processed/runs/`, `comparisons/`, `reports/` for runs and reports. |
| **previous/, previous_runs/** | In an image's folder under `processed/`: the outputs of the run before the latest, and each earlier run's outputs zipped as `<date_time>.zip`. Corrected masks are never moved there. |
| **ignored image** | An image listed in `.recapignore`: kept in the folder and in the database, but not run, counted, tuned, trained on or reported. `recap ignore` / `unignore`, or Ignore in the app. |
| **recap.yaml** | The workspace's settings, for every image. The workspace level of the settings. |
| **batch** (staining batch) | Images stained together. A batch can change some settings for its images. |
| **own settings** | Settings saved for one image only, with a reason. |
| **level** | Where a setting comes from: workspace → batch → image; the most specific wins. |
| **state** | Whether recap's count for an image is *up to date* with its current settings and model, *out of date*, *not run*, or *unknown* (invalid recap.yaml). |
| **run** | recap processing images: each image's outputs in its folder under `processed/`, the run's summary and log in `processed/runs/<date>/`, recorded in the workspace. |
| **spot check** | Before a full run, counting a random sample by hand to see how recap compares (`validation.enabled`). |
| **recovery** | Combining DAPI and ICP8 into the image the model sees, so infected nuclei with weak DAPI are still found. |
| **segmentation** | Finding the nuclei's outlines (StarDist by default). |
| **fragments** | Pieces of one nucleus that the model split; recap can rejoin them. |
| **filters** | Rules that drop outlines that are not whole, in-focus nuclei (focus, roundness, area, border). |
| **VRC+ / VRC−** | Nuclei with / without viral replication compartments (ICP8), i.e. infected / not infected. |
| **manual count** | Nuclei counted by a person, in Fiji or in the app, stored with their name. |
| **corrected mask** | recap's outlines for an image after a person fixed them; the reference for tuning and training. |
| **tuning** | Trying settings and scoring them against corrected masks. |
| **F1** | Tuning's score, 0 to 1: high when recap finds the real nuclei and adds few extra ones. |
| **model version** | A StarDist (or Cellpose) model trained on the workspace's corrected masks: `v1`, `v2`, ... |
| **Count / Fix outlines / Settings tabs** | The ways to work on an image in the app: count by hand, correct recap's outlines, or adjust settings with a live preview (Guided mode). With `recap gui --napari` the first two are *Manual mode* and the third is *Guided mode*. |
| **adjusted view** | The image as recap sees it (DAPI blue, ICP8 green, normalised), used for counting in Fiji. |
| **QC flags** | Warnings per image (saturation, blur, few nuclei...) in the last column of the table and the image summary. |
