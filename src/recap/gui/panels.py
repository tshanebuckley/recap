"""recap's napari panels (declared in recap/napari.yaml, docked by `recap gui`).

recap     main_panel.py: the workspace (open or create one), its images and their status, running
          recap, Manual mode (count, correct outlines) and Guided mode (guided.py: settings step
          by step with a live preview).
Tuning    jobs.py: try settings against the corrected masks and apply the best.
Training  jobs.py: train model versions on the corrected masks, compare them, choose one.

The panels only call the library (the same functions as the commands); slow work runs in a
background thread (common.run_in_background) so the window stays responsive. State shared
between panels lives in common.STATE.
"""

from recap.gui.common import STATE, SharedState, run_in_background
from recap.gui.guided import GuidedPage
from recap.gui.jobs import TrainingPanel, TuningPanel
from recap.gui.main_panel import ManualPage, RecapPanel

__all__ = [
    "STATE", "GuidedPage", "ManualPage", "RecapPanel", "SharedState", "TrainingPanel", "TuningPanel",
    "run_in_background",
]  # fmt: skip
