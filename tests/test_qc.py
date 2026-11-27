"""Per-image QC flags and count ranges in the batch summary."""

from pathlib import Path

import numpy as np
import tifffile
from scipy import ndimage as ndi

from conftest import make_synthetic
from helpers import BASE, run_cli, summary


def test_flags_and_ranges_on_synthetic(write_config, synth_dir):
    cfg = write_config(
        BASE + "filters: {min_circularity: 0.8, min_focus: 0.5}\n"
        "qc: {max_excluded_fraction: 0.2}\noutput: {directory: q}\n"
    )
    run_cli("run", cfg)
    s = summary(synth_dir).iloc[0]
    flags = str(s.qc_flags).split(";")
    assert "few_nuclei" in flags  # 3-4 nuclei < 20
    assert "high_exclusion" in flags  # 1 of 4 dropped = 25% > 20%
    assert s.n_nuclei_low <= s.n_nuclei <= s.n_nuclei_high
    assert s.n_infected_low <= s.n_infected <= s.n_infected_high


def test_vrc_range_not_fixed_flag(write_config, synth_dir):
    cfg = write_config("input: {images: [data/img1.tif]}\noutput: {directory: q2}\n")
    run_cli("run", cfg)
    assert "vrc_range_not_fixed" in str(summary(synth_dir).iloc[0].qc_flags)


def test_saturation_and_blurry_vs_batch(write_config, synth_dir):
    d = synth_dir / "batch"
    d.mkdir()
    for i in range(3):
        tifffile.imwrite(d / f"sharp{i}.tif", make_synthetic(seed=10 + i))
    blurred = make_synthetic(seed=20)
    blurred = np.stack([ndi.gaussian_filter(c, 4) for c in blurred]).astype(np.float32)
    tifffile.imwrite(d / "blurred.tif", blurred)
    sat = make_synthetic(seed=30)
    sat[0] = np.minimum(sat[0] * 8, 4095)  # 12-bit DAPI clipped at its ceiling
    tifffile.imwrite(d / "saturated.tif", sat)
    cfg = write_config(
        "input: {images: [batch/*.tif]}\nnormalization: {vrc_range: [150, 3000]}\noutput: {directory: q3}\n"
    )
    run_cli("run", cfg)
    s = summary(synth_dir)
    s.index = s.image.map(lambda x: Path(x).name)
    assert "blurry_vs_batch" in str(s.loc["blurred.tif"].qc_flags)
    assert "blurry_vs_batch" not in str(s.loc["sharp0.tif"].qc_flags)
    assert "saturated_dapi" in str(s.loc["saturated.tif"].qc_flags)
    assert s.loc["saturated.tif"].saturated_dapi_fraction > 0.005
