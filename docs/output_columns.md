# Output columns

Every CSV that recap (Reconstruction of Cellular Architecture and Phenotypes) writes, column by column. `tests/test_docs.py` fails if a file gains or
loses a column without this page being updated.

Where the files are (in a workspace; see the README's *Workspaces*): per image in
`processed/batches/<batch>/<image>/` (or `processed/unbatched/<image>/`): `<image>_nuclei.csv`,
`<image>_excluded.csv`, `<image>_merges.csv`; per run in `processed/runs/<date>/`:
`all_nuclei_<stamp>.csv`, `image_summary_<stamp>.csv`, `spot_check_<stamp>.csv`,
`manual_counts_<stamp>.csv`; `recap report` in `processed/reports/<date>/`; `recap compare` where
`--out` says. A comparison run (`recap run --settings`) writes all of its files into
`processed/comparisons/<name>/`.

Conventions

- **infected = VRC+**: a nucleus with detectable viral replication compartments (ICP8 above
  threshold over at least `measurement.infected_fraction` of its area). It does not capture
  immediate-early or latent infection. Some files say `infected`, others `vrc_pos`; they mean the same.
- Coordinates and areas are in **pixels**; `y` runs down from the top edge, `x` to the right.
- Intensities called *raw* are in the units of the image file; *normalised* values are 0–1 after
  the `normalization` settings.
- Empty cells mean "not applicable" or "not recorded" (e.g. clipping columns for nuclei away
  from the border, point-based columns when only totals were counted).
- Columns marked *(optional)* appear only when the setting named in brackets is used.

## `<image>_nuclei.csv`

One row per counted nucleus (kept by all filters). Written by `recap run`.

| column | meaning |
|---|---|
| `image` | Image file name. |
| `label` | Nucleus id: its pixel value in `<image>_labels.tif`. |
| `y` | Centroid row (px). |
| `x` | Centroid column (px). |
| `area` | Area (px). |
| `solidity` | Area / area of the convex hull (1 = convex). |
| `dapi_integrated` | Sum of raw DAPI in the nucleus. |
| `dapi_mean` | Mean raw DAPI in the nucleus. |
| `vrc_integrated` | Sum of raw VRC (ICP8) in the nucleus. |
| `vrc_area_fraction` | Fraction of nucleus pixels with normalised VRC above `measurement.vrc_threshold`. |
| `n_chromatin_fragments` | Separate bright DAPI regions inside the nucleus (marginalised chromatin gives several). |
| `infected` | True if `vrc_area_fraction` >= `measurement.infected_fraction` (VRC+). |
| `neuron_ring_mean` | *(optional: `input.neuron_channel`)* Mean of that channel in a ring `neuron_ring_px` wide around the nucleus. |
| `circularity` | 4π·area / perimeter² (1 = circle). Filter: `filters.min_circularity`. |
| `focus_edge` | Mean intensity gradient along the nucleus edge in the combined image (absolute; higher = sharper). |
| `focus_detail` | Fine internal detail (band-passed signal) inside the nucleus (absolute). |
| `touches_border` | True if the nucleus touches the image edge or an `exclude_regions` box. |
| `focus_score` | Mean of `focus_edge` and `focus_detail`, each divided by its median in this image (1.0 = typical nucleus). Filter: `filters.min_focus`. |
| `clip_missing` | Border nuclei: estimated fraction cut off (mean of the two estimates below). Filter: `filters.border_max_missing`. |
| `clip_missing_ellipse` | Estimate from an ellipse fitted to the uncut part of the outline. |
| `clip_missing_chord` | Estimate from the cut-edge length and the visible area. |
| `clip_fit_error` | How well the uncut outline fits an ellipse (median relative deviation; 0 = perfect). |
| `clip_pred_area` | Predicted full area of the nucleus (px). |
| `clip_chord_px` | Length of the cut edge (px). |

## `<image>_excluded.csv`

Nuclei removed by the final filters: the same columns as `<image>_nuclei.csv` (labels refer to
`<image>_excluded_labels.tif`), plus:

| column | meaning |
|---|---|
| `exclusion` | Reason(s), semicolon-separated: `border`, `too_small`, `too_large`, `irregular`, `out_of_focus`. |

## `all_nuclei_<stamp>.csv`

All `<image>_nuclei.csv` files of the run in one table (same columns).

## `<image>_merges.csv`

Written only when `segmentation.fragment_merge` joined pieces. Labels refer to the segmentation
*before* merging, so they do not appear in the saved label images.

| column | meaning |
|---|---|
| `iteration` | Merge round (a nucleus split in three needs two). |
| `piece` | Label of the small or irregular piece. |
| `into` | Label of the neighbour it was joined to. |
| `piece_area` | Area of the piece (px). |
| `into_area` | Area of the neighbour (px). |
| `merged_area` | Area after merging (px). |
| `merged_solidity` | Solidity of the merged nucleus. |
| `merged_fit_error` | Ellipse-fit error of the merged outline. |
| `vrc_bridge` | Median normalised VRC across the junction (used by mode `shape_vrc`). |
| `y` | Centroid row of the merged nucleus (px). |
| `x` | Centroid column of the merged nucleus (px). |
| `image` | Image file name. |

## `image_summary_<stamp>.csv`

One row per image. Written by `recap run`.

| column | meaning |
|---|---|
| `image` | Image path as given in the settings. |
| `n_nuclei` | Counted nuclei. |
| `n_infected` | VRC+ nuclei. |
| `n_uninfected` | VRC− nuclei. |
| `pct_infected` | VRC+ as % of counted nuclei. |
| `n_nuclei_low` | Lowest count when each filter cutoff is moved down/up by the `qc` deltas (one at a time). |
| `n_nuclei_high` | Highest count over the same variations. |
| `n_infected_low` | Lowest VRC+ count over the variations (including `infected_fraction` ÷/× `qc.infected_fraction_factor`). |
| `n_infected_high` | Highest VRC+ count over the variations. |
| `qc_flags` | Warnings, semicolon-separated (empty = none): `saturated_dapi`, `saturated_vrc`, `few_nuclei`, `high_exclusion`, `many_out_of_focus`, `high_vrc_background`, `vrc_range_not_fixed`, `blurry_vs_batch`. Thresholds in the `qc` settings. |
| `n_small_bodies` | Objects below `segmentation.min_area`, removed before the filters (mostly apoptotic or pyknotic bodies). |
| `n_fragment_merges` | Pieces joined by `segmentation.fragment_merge`. |
| `n_filtered` | Nuclei removed by the final filters (any reason). |
| `n_excluded_border` | Removed as cut off by the image edge or an excluded region. |
| `n_excluded_too_small` | Removed by `filters.min_area`. |
| `n_excluded_too_large` | Removed by `filters.max_area`. |
| `n_excluded_irregular` | Removed by `filters.min_circularity`. |
| `n_excluded_out_of_focus` | Removed by `filters.min_focus`. A nucleus failing several filters counts under each. |
| `saturated_dapi_fraction` | Fraction of DAPI pixels at the channel's ceiling (255, 4095 or 65535); 0 if the channel never reaches one. |
| `saturated_vrc_fraction` | Same for VRC. |
| `vrc_background` | Median normalised VRC outside nuclei. |
| `settings` | Where the image's settings came from: `workspace` (its recap.yaml), plus `batch:<name>` and `image` for each level that changed something; `settings file` for a settings file run outside a workspace. |
| `overridden` | Settings changed for this image's batch or for the image itself (`;`-separated); empty if none. |
| `settings_reason` | Why the image has its own settings (required when they are saved). |
| `median_edge_sharpness` | Median `focus_edge` over all nuclei (kept and excluded); compared across the batch for `blurry_vs_batch`. |

## `manual_counts*.csv`

Manual counts, from the spot check (`manual_counts_<stamp>.csv`) or `recap import-counts`. One
row per image and counter.

| column | meaning |
|---|---|
| `image` | Image path when counted or imported. |
| `image_name` | Image file name. |
| `sha256` | Hash of the image file: how counts are matched to images (names may change). |
| `nuclei` | Nuclei counted. |
| `infected` | VRC+ counted (empty = infection not recorded). |
| `points` | Clicked positions as JSON `[[x, y, c], ...]` in px; `c` = 1 VRC+, 0 VRC−. Empty for typed totals. |
| `source` | `new` (counted in the spot check), `previous` (reused) or `imported`. |
| `counted_at` | Date and time. |
| `counted_by` | Counter's name (`--by`), for comparing counters. |
| `origin` | File the counts were imported from, or `typed`. |
| `tool` | How the count was made: `fiji` (recap count / spot check), `napari`, `cell-counter`, `multipoint-csv`, `typed`. |

## `spot_check_<stamp>.csv`

Program vs manual for each spot-checked image. Point-based columns need clicked points.

| column | meaning |
|---|---|
| `image_name` | Image file name. |
| `source` | Where the manual count came from (see `manual_counts`). |
| `manual_nuclei` | Manual count. |
| `program_nuclei` | Program count. |
| `difference` | Program − manual. |
| `pct_difference` | Difference as % of the manual count. |
| `manual_infected` | Manual VRC+. |
| `program_infected` | Program VRC+. |
| `manual_uninfected` | Manual VRC−. |
| `program_uninfected` | Program VRC−. |
| `manual_pct_infected` | Manual % VRC+. |
| `program_pct_infected` | Program % VRC+. |
| `matched` | Program nuclei containing exactly one manual point. |
| `missed` | Manual points on background or on an excluded nucleus (program missed it). |
| `merged` | Program nuclei containing two or more points (nuclei fused). |
| `extra` | Program nuclei with no point (fragment or false positive). |
| `both_infected` | Matched nuclei VRC+ in both. |
| `both_uninfected` | Matched nuclei VRC− in both. |
| `manual_inf_program_uninf` | Manual VRC+, program VRC− (program too strict). |
| `manual_uninf_program_inf` | Manual VRC−, program VRC+ (program too lenient). |
| `infection_agreement` | Fraction of matched nuclei with the same call. |

## `comparison.csv` (`recap compare`)

`<A>` and `<B>` are the `--names` given (default `A`, `B`).

| column | meaning |
|---|---|
| `image` | Image file stem. |
| `<A>_nuclei` | Count in run A. |
| `<B>_nuclei` | Count in run B. |
| `<A>_vrc_pos` | VRC+ in run A. |
| `<B>_vrc_pos` | VRC+ in run B. |
| `<A>_pct_vrc_pos` | % VRC+ in run A. |
| `<B>_pct_vrc_pos` | % VRC+ in run B. |
| `matched` | Nuclei found by both (one-to-one, overlap IoU >= `--iou`). |
| `only_<A>` | Nuclei found only by A (missed, split or merged by B). |
| `only_<B>` | Nuclei found only by B. |
| `mean_iou_matched` | Mean overlap of matched nuclei (1 = identical outlines). |
| `infection_agreement` | Fraction of matched nuclei with the same VRC+ call. |

## `recap report` files

### `program_counts.csv`

| column | meaning |
|---|---|
| `image` | Image file name. |
| `method` | Run name (`--name`). |
| `n_nuclei` | As in `image_summary`. |
| `n_infected` | As in `image_summary`. |
| `pct_infected` | As in `image_summary`. |
| `n_nuclei_low` | As in `image_summary`. |
| `n_nuclei_high` | As in `image_summary`. |
| `n_infected_low` | As in `image_summary`. |
| `n_infected_high` | As in `image_summary`. |
| `qc_flags` | As in `image_summary`. |

### `vs_manual.csv`

One row per image, method and counter. Same comparison columns as `spot_check` (from
`manual_nuclei` to `infection_agreement`), plus:

| column | meaning |
|---|---|
| `image` | Image file name. |
| `method` | Run name. |
| `counted_by` | Counter (empty if not given). |
| `manual_within_range` | True if the manual count lies within the program's `n_nuclei_low`–`n_nuclei_high`. |

### `method_agreement.csv`

| column | meaning |
|---|---|
| `image` | Image file name. |
| `method_a` | First run. |
| `method_b` | Second run. |
| `n_a` | Nuclei in run A. |
| `n_b` | Nuclei in run B. |
| `matched` | Nuclei found by both (overlap IoU >= `--iou`). |
| `only_a` | Found only by A. |
| `only_b` | Found only by B. |
| `mean_iou_matched` | Mean overlap of matched nuclei. |
| `infection_agreement` | Fraction of matched nuclei with the same VRC+ call. |

### `inter_rater.csv`

Images counted by more than one person.

| column | meaning |
|---|---|
| `image` | Image file name. |
| `counter_a` | First counter. |
| `counter_b` | Second counter. |
| `nuclei_a` | Count by A. |
| `nuclei_b` | Count by B. |
| `difference` | B − A. |
| `pct_difference` | Difference as % of A. |
| `vrc_pos_a` | VRC+ by A. |
| `vrc_pos_b` | VRC+ by B. |

### `summary.csv`

One row per method, over all image × counter comparisons.

| column | meaning |
|---|---|
| `method` | Run name. |
| `n_comparisons` | Image × counter pairs compared. |
| `n_images` | Distinct images. |
| `manual_total` | Sum of manual counts. |
| `program_total` | Sum of program counts. |
| `total_pct_difference` | (program_total − manual_total) as % of manual_total. |
| `mean_difference` | Mean per-image (program − manual): the bias. |
| `sd_difference` | SD of the per-image differences. |
| `loa_low` | Lower 95% limit of agreement (mean − 1.96 SD). |
| `loa_high` | Upper 95% limit of agreement (mean + 1.96 SD). |
| `mean_abs_pct_difference` | Mean absolute per-image % difference. |
| `max_abs_pct_difference` | Largest absolute per-image % difference. |
| `manual_pct_vrc_pos` | Pooled manual % VRC+ (images where infection was recorded). |
| `program_pct_vrc_pos` | Pooled program % VRC+ on the same images. |
| `pct_vrc_pos_difference` | Program − manual, percentage points. |
| `images_with_points` | Comparisons with clicked points. |
| `matched` | Total matched nuclei (with points). |
| `missed` | Total missed. |
| `merged` | Total merged. |
| `extra` | Total extra. |
| `both_vrc_pos` | Matched nuclei VRC+ in both. |
| `both_vrc_neg` | Matched nuclei VRC− in both. |
| `manual_pos_program_neg` | Manual VRC+, program VRC−. |
| `manual_neg_program_pos` | Manual VRC−, program VRC+. |
| `infection_agreement` | Fraction of matched nuclei with the same call. |
| `images_manual_within_range` | Comparisons where the manual count lies in the program's count range. |
