"""Editing a workspace's recap.yaml without losing its comments (ruamel.yaml round trip).

Used for the workspace level of the settings: Guided mode's "Save to workspace", `recap settings
set` without --batch / --image, applying tuned settings, and choosing a trained model version.
"""

from __future__ import annotations

from io import StringIO
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq

from recap.config import PipelineConfig
from recap.errors import ConfigError


def _yaml() -> YAML:
    y = YAML()  # round-trip: comments, key order and quoting are kept
    y.preserve_quotes = True
    y.width = 120
    y.indent(mapping=2, sequence=4, offset=2)
    return y


def _plain(v: Any) -> Any:
    if isinstance(v, (list, tuple)):
        seq = CommentedSeq([_plain(x) for x in v])
        seq.fa.set_flow_style()  # [3, 25], as in the template
        return seq
    return v


def set_values(path: Path, values: dict[str, Any]) -> None:
    """Set dotted keys ({"filters.min_area": 300}) in a settings file, keeping everything else.
    The result is validated before it is written."""
    y = _yaml()
    text = path.read_text(encoding="utf-8")
    data = y.load(text) or CommentedMap()
    for key, value in values.items():
        section, name = key.split(".", 1)
        if data.get(section) is None:
            data[section] = CommentedMap()
        data[section][name] = _plain(value)
    buf = StringIO()
    y.dump(data, buf)
    import yaml

    try:
        PipelineConfig.model_validate(yaml.safe_load(buf.getvalue()) or {})
    except ValueError as e:
        raise ConfigError(f"These values would make {path.name} invalid:\n{e}") from e
    path.write_text(buf.getvalue(), encoding="utf-8")
