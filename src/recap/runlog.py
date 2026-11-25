"""Run logging helpers: log file setup, config diffs, package versions, YAML output."""

from __future__ import annotations

import logging
import sys
from importlib import metadata
from pathlib import Path
from typing import Any

import yaml

log = logging.getLogger(__name__)


# =============================================================================
# Logging helpers
# =============================================================================
def flatten(d: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in d.items():
        key = f"{prefix}{k}"
        if isinstance(v, dict):
            out.update(flatten(v, key + "."))
        else:
            out[key] = list(v) if isinstance(v, tuple) else v
    return out


def diff(before: dict[str, Any], after: dict[str, Any]) -> dict[str, dict[str, Any]]:
    a, b = flatten(before), flatten(after)
    return {k: {"from": a.get(k), "to": b.get(k)} for k in sorted(set(a) | set(b)) if a.get(k) != b.get(k)}


def package_versions(names: list[str]) -> dict[str, str | None]:
    out = {}
    for n in names:
        try:
            out[n] = metadata.version(n)
        except metadata.PackageNotFoundError:
            out[n] = None
    return out


def close_logging() -> None:
    """Detach and close the handlers added by setup_logging (closes the run log file)."""
    root = logging.getLogger("recap")
    for h in list(root.handlers):
        root.removeHandler(h)
        h.close()


def setup_logging(out_dir: Path, stamp: str, console: bool = True) -> Path:
    """Send the "recap" logger (all recap modules) to run_<stamp>.log and, optionally, stderr."""
    out_dir.mkdir(parents=True, exist_ok=True)
    log_file = out_dir / f"run_{stamp}.log"
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%Y-%m-%d %H:%M:%S")
    root = logging.getLogger("recap")
    close_logging()
    root.setLevel(logging.INFO)
    handlers = [logging.FileHandler(log_file, encoding="utf-8")] + (
        [logging.StreamHandler(sys.stderr)] if console else []
    )
    for h in handlers:
        h.setFormatter(fmt)
        root.addHandler(h)
    return log_file


def dump_yaml(path: Path, data: Any) -> None:
    path.write_text(yaml.safe_dump(data, sort_keys=False, default_flow_style=None), encoding="utf-8")
