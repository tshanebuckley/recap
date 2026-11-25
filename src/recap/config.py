"""Settings: pydantic models for every YAML section, the commented template, and load_config."""

from __future__ import annotations

import glob
import hashlib
import logging
import re
from pathlib import Path
from typing import Any, Literal

import numpy as np
import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from recap.errors import ConfigError

log = logging.getLogger(__name__)


# =============================================================================
# Configuration (pydantic)
# =============================================================================
class StrictModel(BaseModel):
    """Unknown keys are errors, so typos in the YAML are caught instead of ignored."""

    model_config = ConfigDict(extra="forbid")


class InputConfig(StrictModel):
    images: list[str] = Field(
        default_factory=list,
        description="Image paths or glob patterns. In a workspace's recap.yaml, empty = every image in the folder.",
    )
    dapi_channel: int = Field(0, ge=0)
    vrc_channel: int = Field(1, ge=0)
    neuron_channel: int | None = Field(None, ge=0)
    use_workspace_settings: bool = Field(
        True,
        description="In a workspace: apply the batch and image settings stored in it.",
    )
    exclude_regions: list[tuple[int, int, int, int]] = Field(
        default_factory=list,
        description="[y0, y1, x0, x1] boxes set to 0 in all channels (burned-in timestamps, scale bars).",
    )

    @model_validator(mode="after")
    def _distinct_channels(self) -> InputConfig:
        chans = [c for c in (self.dapi_channel, self.vrc_channel, self.neuron_channel) if c is not None]
        if len(chans) != len(set(chans)):
            raise ValueError(f"channel indices must be distinct, got {chans}")
        for y0, y1, x0, x1 in self.exclude_regions:
            if not (0 <= y0 < y1 and 0 <= x0 < x1):
                raise ValueError(
                    f"exclude_regions entries must be [y0, y1, x0, x1] with y0<y1, x0<x1; got {[y0, y1, x0, x1]}"
                )
        return self

    def resolve_paths(self, base: Path) -> list[Path]:
        if not self.images:
            raise FileNotFoundError("input.images lists no images (only a workspace's recap.yaml may leave it empty)")
        paths: list[Path] = []
        for pattern in self.images:
            p = Path(pattern)
            full = str(p if p.is_absolute() else base / p)
            hits = sorted(glob.glob(full))
            if not hits:
                raise FileNotFoundError(f"no image matches '{pattern}' (looked for {full})")
            paths += [Path(h) for h in hits]
        return paths


class NormalizationConfig(StrictModel):
    dapi_percentiles: tuple[float, float] = (1.0, 99.8)
    vrc_range: tuple[float, float] | None = Field(
        None, description="Fixed VRC min/max (recommended). None = per-image percentiles."
    )
    vrc_weight: float = Field(1.0, gt=0)

    @model_validator(mode="after")
    def _ordered(self) -> NormalizationConfig:
        lo, hi = self.dapi_percentiles
        if not 0 <= lo < hi <= 100:
            raise ValueError("dapi_percentiles must satisfy 0 <= low < high <= 100")
        if self.vrc_range is not None and self.vrc_range[0] >= self.vrc_range[1]:
            raise ValueError("vrc_range must be [min, max] with min < max")
        return self


class SegmentationConfig(StrictModel):
    method: Literal["classical", "stardist", "cellpose"] = "classical"
    diameter: float = Field(40.0, gt=0, description="Typical nuclear diameter, px.")
    sigma: float = Field(2.0, ge=0)
    threshold_method: Literal["otsu", "li", "triangle"] = "otsu"
    close_radius: int | None = Field(None, ge=0, description="None = diameter/8.")
    min_area: int | None = Field(None, ge=1, description="None = area of a diameter/2 disk.")
    h: float | None = Field(None, gt=0, description="h-maxima height. None = diameter/8.")
    merge: bool = Field(True, description="Merge touching regions with weak borders. Use false with stardist.")
    fragment_merge: Literal["off", "shape", "shape_vrc"] = Field(
        "off",
        description="Rejoin small/irregular pieces to a neighbour: shape (+size) tests, "
        "or shape_vrc = also require ICP8 across the junction.",
    )

    @field_validator("fragment_merge", mode="before")
    @classmethod
    def _yaml_off(cls, v):
        return "off" if v is False else v  # YAML 1.1 reads a bare `off` as false

    merge_ratio: float = Field(0.8, ge=0, le=2, description="Higher merges less; >1 rarely merges.")
    stardist_model: str = Field(
        "2D_versatile_fluo",
        description="Pretrained name, or a model folder (e.g. recap-data/models/stardist/v2 from `recap train`).",
    )
    cellpose_model: str | None = Field(
        None, description="None = Cellpose-SAM (cpsam); or a model file from `recap train --method cellpose`."
    )
    stardist_scale: float = Field(1.0, gt=0, le=4, description="Image rescale before prediction.")
    prob_thresh: float | None = Field(None, gt=0, lt=1, description="None = model default.")
    nms_thresh: float | None = Field(None, gt=0, lt=1, description="None = model default.")

    def resolved(self) -> SegmentationConfig:
        """Fill derived defaults so the log shows the numbers actually used."""
        return SegmentationConfig.model_validate(
            {
                **self.model_dump(),
                "close_radius": self.close_radius if self.close_radius is not None else max(1, int(self.diameter / 8)),
                "min_area": self.min_area if self.min_area is not None else int(np.pi * (self.diameter / 4) ** 2),
                "h": self.h if self.h is not None else self.diameter / 8,
            }
        )


class MeasurementConfig(StrictModel):
    vrc_threshold: float = Field(0.3, ge=0, le=1, description="VRC+ pixel cut-off on normalised VRC.")
    infected_fraction: float = Field(0.05, ge=0, le=1, description="Min VRC+ area fraction to call infected.")
    neuron_ring_px: int = Field(6, ge=1)
    min_fragment_area: int = Field(10, ge=1)


class InteractiveConfig(BaseModel):
    """Removed in recap 4.0 (Fiji tuning windows). Still accepted so old settings files load;
    `fiji` is copied to validation.fiji. See deprecation_notes()."""

    model_config = ConfigDict(extra="allow")
    enabled: bool = False
    fiji: str | None = None


class OutputConfig(StrictModel):
    directory: Path = Path("results")
    save_labels: bool = True
    save_qc: bool = True


class ValidationConfig(StrictModel):
    """Manual spot-check counting of a random subset after segmentation."""

    enabled: bool = False
    sample_size: int = Field(5, ge=1, description="Images to count by hand.")
    sample_fraction: float | None = Field(
        None, gt=0, le=1, description="If set, overrides sample_size (rounded, at least 1)."
    )
    seed: int | None = Field(None, ge=0, description="None = random seed, recorded in the log.")
    exclude_tuning_image: bool = Field(True, description="Unused since 4.0 (no tuning image); kept so old files load.")
    count_infected: bool = Field(True, description="Mark infected nuclei with Multi-point counter 1.")
    fiji: str | None = Field(
        None,
        description="Fiji used for counting: None = find automatically (recap fiji set, RECAP_FIJI / FIJI_HOME, "
        "a launcher on PATH, common install folders), or a Fiji folder or launcher.",
    )
    counted_by: str | None = Field(None, description="Name recorded with new counts; None = your login name.")
    previous_counts: Path | None = Field(
        None, description="manual_counts_*.csv from an earlier run; used instead of random selection."
    )
    top_up: bool = Field(False, description="With previous_counts, randomly add images up to the sample size.")

    def target_size(self, n_available: int) -> int:
        n = max(1, round(self.sample_fraction * n_available)) if self.sample_fraction else self.sample_size
        return min(n, n_available)


class FragmentConfig(StrictModel):
    """Thresholds for segmentation.fragment_merge."""

    candidate_area_ratio: float = Field(0.5, gt=0, le=1, description="Candidate if area < ratio x median nucleus.")
    candidate_circularity: float = Field(0.65, gt=0, le=1, description="...or circularity below this.")
    gap_px: int = Field(2, ge=0, le=6, description="Max gap bridged between pieces.")
    min_solidity: float = Field(0.93, gt=0, le=1, description="Merged object must be at least this convex.")
    max_fit_error: float = Field(0.10, gt=0, description="Ellipse-fit error of the merged outline.")
    area_percentiles: tuple[float, float] = Field((1.0, 99.0), description="Merged area range vs non-candidates.")
    max_iterations: int = Field(3, ge=1, le=10)

    @model_validator(mode="after")
    def pct(self) -> FragmentConfig:
        lo, hi = self.area_percentiles
        if not 0 <= lo < hi <= 100:
            raise ValueError("fragments.area_percentiles must satisfy 0 <= low < high <= 100")
        return self


class FilterConfig(StrictModel):
    """Final per-nucleus filters, applied after segmentation. None = filter off."""

    min_area: int | None = Field(None, ge=1, description="px; on top of segmentation.min_area.")
    max_area: int | None = Field(None, ge=1, description="px; infected nuclei swell, so keep this generous.")
    min_circularity: float | None = Field(None, gt=0, le=1, description="4*pi*area/perimeter^2.")
    min_focus: float | None = Field(
        None, gt=0, description="Relative focus score; 1.0 = median nucleus of the same image."
    )
    exclude_border: bool = Field(False, description="Drop nuclei touching the image edge or an excluded region.")
    border_px: int = Field(2, ge=0)
    border_max_missing: float | None = Field(
        None,
        ge=0,
        le=1,
        description="With exclude_border: keep border nuclei whose estimated missing "
        "(clipped) fraction is at most this. None = exclude every border nucleus.",
    )
    border_max_fit_error: float = Field(0.10, gt=0, description="Shape: max ellipse-fit error of the uncut outline.")
    border_area_percentiles: tuple[float, float] = Field(
        (1.0, 99.0), description="Size: predicted full area must lie within these percentiles of non-border nuclei."
    )

    @model_validator(mode="after")
    def _border_pct(self) -> FilterConfig:
        lo, hi = self.border_area_percentiles
        if not 0 <= lo < hi <= 100:
            raise ValueError("border_area_percentiles must satisfy 0 <= low < high <= 100")
        return self

    @model_validator(mode="after")
    def _areas(self) -> FilterConfig:
        if self.min_area is not None and self.max_area is not None and self.min_area >= self.max_area:
            raise ValueError("filters.min_area must be smaller than filters.max_area")
        return self


class QCConfig(StrictModel):
    """Per-image warnings and count ranges written to the batch summary."""

    saturation_max_fraction: float = Field(
        0.005, ge=0, le=1, description="Flag a channel with more pixels at its maximum value than this fraction."
    )
    min_nuclei: int = Field(20, ge=0, description="Flag images with fewer counted nuclei.")
    max_excluded_fraction: float = Field(
        0.30, ge=0, le=1, description="Flag if filters drop more than this fraction of nuclei."
    )
    max_out_of_focus_fraction: float = Field(0.25, ge=0, le=1)
    blurry_vs_batch: float = Field(
        0.6,
        gt=0,
        le=1,
        description="Flag an image whose median edge sharpness is "
        "below this fraction of the batch median (needs >= 3 images).",
    )
    vrc_background_max: float = Field(
        0.3, ge=0, le=1, description="Flag if the median normalised ICP8 outside nuclei exceeds this."
    )
    focus_delta: float = Field(0.10, ge=0, description="Count range: min_focus +/- this.")
    circularity_delta: float = Field(0.05, ge=0, description="Count range: min_circularity +/- this.")
    border_missing_delta: float = Field(0.05, ge=0, description="Count range: border_max_missing +/- this.")
    infected_fraction_factor: float = Field(2.0, ge=1, description="Count range: infected_fraction x / this.")


class PipelineConfig(StrictModel):
    input: InputConfig
    normalization: NormalizationConfig = Field(default_factory=NormalizationConfig)
    segmentation: SegmentationConfig = Field(default_factory=SegmentationConfig)
    measurement: MeasurementConfig = Field(default_factory=MeasurementConfig)
    fragments: FragmentConfig = Field(default_factory=FragmentConfig)
    filters: FilterConfig = Field(default_factory=FilterConfig)
    qc: QCConfig = Field(default_factory=QCConfig)
    validation: ValidationConfig = Field(default_factory=ValidationConfig)
    output: OutputConfig = Field(default_factory=OutputConfig)
    interactive: InteractiveConfig | None = Field(None, exclude=True)  # removed in 4.0; see deprecation_notes

    @model_validator(mode="after")
    def _carry_over_fiji(self) -> PipelineConfig:
        if self.interactive is not None and self.interactive.fiji and not self.validation.fiji:
            self.validation.fiji = self.interactive.fiji
        return self


def deprecation_notes(cfg: PipelineConfig) -> list[str]:
    """Messages about settings that no longer have an effect (shown by validate and run)."""
    notes = []
    if cfg.interactive is not None:
        notes.append(
            "The `interactive` section was removed in recap 4.0 (tuning in Fiji windows) and is ignored"
            + (" (interactive.enabled: true has no effect)" if cfg.interactive.enabled else "")
            + ". Tune with `recap tune` or the Tuning panel in `recap gui`."
            + (" interactive.fiji is used as validation.fiji." if cfg.interactive.fiji else "")
        )
    return notes


TEMPLATE = """\
# recap (Reconstruction of Cellular Architecture and Phenotypes) configuration
input:
  images:                 # paths or globs, relative to this file (a workspace's recap.yaml: [] = all its images)
    - data/*.tif
  dapi_channel: 0         # 0-based channel indices
  vrc_channel: 1          # ICP8 / ICP4 / click-labelled viral DNA
  neuron_channel: null    # e.g. 2 for MAP2 or bIII-tubulin; null to skip
  exclude_regions: []     # [y0, y1, x0, x1] boxes blanked before analysis (timestamps, scale bars)
  use_workspace_settings: true  # in a workspace: apply its batch and image settings

normalization:
  dapi_percentiles: [1.0, 99.8]
  vrc_range: null         # [min, max] from an infected control; strongly recommended
  vrc_weight: 1.0

segmentation:
  method: classical       # classical | stardist | cellpose  (stardist for dense tissue)
  diameter: 40            # typical nuclear diameter, px
  sigma: 2.0
  threshold_method: otsu  # otsu | li | triangle
  close_radius: null      # null = diameter/8
  min_area: null          # null = area of a disk of diameter/2
  h: null                 # h-maxima height, null = diameter/8
  merge: true             # set false for stardist / cellpose
  fragment_merge: "off"   # "off" | shape | shape_vrc : rejoin pieces of split nuclei
  merge_ratio: 0.8        # higher = fewer merges
  stardist_model: 2D_versatile_fluo   # or a trained version: recap-data/models/stardist/v2
  cellpose_model: null    # null = Cellpose-SAM; or a trained model file
  stardist_scale: 1.0     # <1 if nuclei are larger than ~25 px across
  prob_thresh: null       # null = model default
  nms_thresh: null        # null = model default

measurement:
  vrc_threshold: 0.3
  infected_fraction: 0.05
  neuron_ring_px: 6
  min_fragment_area: 10

fragments:                # thresholds for segmentation.fragment_merge
  candidate_area_ratio: 0.5   # piece if area < 0.5 x median nucleus ...
  candidate_circularity: 0.65 # ... or circularity below this
  gap_px: 2                   # max gap bridged between pieces
  min_solidity: 0.93          # merged object must be this convex (merged neighbour pairs rarely are)
  max_fit_error: 0.10         # merged outline must fit an ellipse
  area_percentiles: [1, 99]   # merged area within the normal range of nuclei
  max_iterations: 3

filters:                  # final per-nucleus filters; null = off
  min_area: null          # px, on top of segmentation.min_area
  max_area: null          # px; infected nuclei swell, keep generous (e.g. 4x a typical nucleus)
  min_circularity: null   # 0-1; ~0.65 drops crescents and clipped pieces, keeps elongated nuclei
  min_focus: null         # relative to the image's median nucleus; ~0.65 drops dim, blurred nuclei
  exclude_border: false   # drop nuclei touching the image edge or an exclude_regions box
  border_px: 2
  border_max_missing: null    # e.g. 0.15: keep border nuclei estimated <=15% clipped (null = drop all)
  border_max_fit_error: 0.10  # shape check for kept border nuclei
  border_area_percentiles: [1, 99]   # size check: predicted full area vs non-border nuclei

qc:                       # per-image warnings (qc_flags) and count ranges in the batch summary
  saturation_max_fraction: 0.005  # flag a channel with more pixels at its maximum value
  min_nuclei: 20                  # flag images with fewer counted nuclei
  max_excluded_fraction: 0.30     # flag if filters drop more than 30% of nuclei
  max_out_of_focus_fraction: 0.25
  blurry_vs_batch: 0.6            # flag images < 0.6 x the batch median sharpness (>= 3 images)
  vrc_background_max: 0.3         # flag high ICP8 background (normalised, outside nuclei)
  focus_delta: 0.10               # count range: each cutoff moved down and up by these amounts
  circularity_delta: 0.05
  border_missing_delta: 0.05
  infected_fraction_factor: 2.0   # infected_fraction divided and multiplied by this

validation:               # manual spot check after segmentation, counted in Fiji
  enabled: false
  sample_size: 5          # images to count by hand
  sample_fraction: null   # e.g. 0.1 = 10% of images; overrides sample_size
  seed: null              # null = random; the seed used is written to the run log
  count_infected: true    # Multi-point counter 0 = uninfected, counter 1 = infected
  fiji: null              # null = find automatically (recap fiji set, RECAP_FIJI, ~/Fiji.app,
                          # /Applications/Fiji.app, C:/Fiji.app ...); or a Fiji folder (forward slashes)
  counted_by: null        # name stored with new counts; null = your login name
  previous_counts: null   # e.g. results/manual_counts_20261113_101500.csv
  top_up: false           # with previous_counts, add random images up to sample_size

output:
  directory: results
  save_labels: true
  save_qc: true
"""


def workspace_template() -> str:
    """The settings file of a new workspace (recap.yaml): every image in the folder."""
    lines = TEMPLATE.splitlines(keepends=True)
    i = next(k for k, ln in enumerate(lines) if ln.startswith("  images:"))
    lines[i : i + 2] = ["  images: []              # [] = every image in this folder; or globs relative to it\n"]
    out = "".join(lines).replace(
        "# recap (Reconstruction of Cellular Architecture and Phenotypes) configuration\n",
        "# recap workspace settings: every image in this folder. Batches and single images can change\n"
        "# some of these (recap settings set, or Guided mode in the app); this file is the default.\n",
    )
    return out


def load_config(path: Path) -> tuple[dict[str, Any], PipelineConfig, str]:
    text = path.read_text(encoding="utf-8")
    try:
        raw = yaml.safe_load(text) or {}
    except yaml.YAMLError as e:
        hint = ""
        if re.search(r'"[^"\n]*\\', text):
            hint = (
                "\nHint: backslashes inside double quotes are escape characters in YAML. Write Windows "
                "paths with forward slashes (C:/Users/me/data/*.tif), in single quotes, or unquoted."
            )
        raise ConfigError(f"{path} is not valid YAML:\n{e}{hint}") from e
    cfg = PipelineConfig.model_validate(raw)
    return raw, cfg, hashlib.sha256(text.encode()).hexdigest()
