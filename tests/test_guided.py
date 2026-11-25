"""Guided mode's live preview (no Qt). The page itself is tested in test_panels.py."""

import numpy as np

from helpers import BASE
from recap.config import load_config
from recap.gui.preview import PreviewSession, split_labels
from recap.io import load_channels


def _session(write_config, synth_dir):
    _, cfg, _ = load_config(write_config(BASE))
    img = load_channels(synth_dir / "data" / "img1.tif")
    return cfg, PreviewSession(img[cfg.input.dapi_channel], img[cfg.input.vrc_channel])


def test_preview_reruns_only_what_changed(write_config, synth_dir):
    cfg, s = _session(write_config, synth_dir)
    first = s.update(cfg)
    assert first.resegmented and first.counts["nuclei"] == 4 and s.n_segmentations == 1
    d = cfg.model_dump()
    d["filters"]["min_area"] = 100000
    stricter = s.update(type(cfg).model_validate(d))  # a filter: no new segmentation
    assert not stricter.resegmented and stricter.counts["nuclei"] == 0 and s.n_segmentations == 1
    assert stricter.counts["excluded"] == {"too_small": 4}
    d["normalization"]["vrc_weight"] = 0.5  # recovery: segment again
    assert s.update(type(cfg).model_validate(d)).resegmented and s.n_segmentations == 2
    pos, neg, exc = split_labels(first.result)
    assert len(np.unique(pos[pos > 0])) == first.counts["vrc_pos"] and not (pos & neg).any()
    assert len(np.unique(pos[pos > 0])) + len(np.unique(neg[neg > 0])) == 4
