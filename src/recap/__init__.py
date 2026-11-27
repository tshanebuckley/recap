"""recap (Reconstruction of Cellular Architecture and Phenotypes).

Nuclear segmentation, counting and HSV-1 infection scoring (DAPI + ICP8).

Library use (no printing; see docs/architecture.md):

    from recap.workspace import open_workspace
    from recap.runner import run_workspace
    ws = open_workspace(Path("experiment"))             # recap.yaml + recap-data/recap.db
    run = run_workspace(ws)                             # what `recap run` and the app's Run all do
    result = process_file(ws.resolve(sha).cfg, path)    # one image with its own settings
"""

__version__ = "4.4.0"

_API = {
    "load_config": "recap.config",
    "PipelineConfig": "recap.config",
    "process_image": "recap.pipeline",
    "process_file": "recap.pipeline",
    "run_batch": "recap.pipeline",
    "Result": "recap.pipeline",
    "run_pipeline": "recap.runner",
    "RunResult": "recap.runner",
    "RecapError": "recap.errors",
    "ConfigError": "recap.errors",
    "Cancelled": "recap.errors",
}
__all__ = ["__version__", *_API]


def __getattr__(name):  # lazy: `recap --version` does not import numpy & co.
    if name in _API:
        import importlib

        return getattr(importlib.import_module(_API[name]), name)
    raise AttributeError(f"module 'recap' has no attribute {name!r}")
