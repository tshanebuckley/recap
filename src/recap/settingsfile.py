"""Editing a workspace's recap.yaml without losing its comments (ruamel.yaml round trip).

Every level of the settings is written here: the workspace's own sections (Settings tab "Save to
workspace", `recap settings set`, applying tuned settings, choosing a model version) and the
batches: and images: sections (Save to batch / this image, `recap settings set --batch / --image`,
renaming or deleting a batch). See recap.levels for the layout.
"""

from __future__ import annotations

from io import StringIO
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq

from recap.config import LEVEL_SECTIONS, PipelineConfig, base_settings
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


def _load(path: Path) -> tuple[YAML, CommentedMap]:
    y = _yaml()
    return y, (y.load(path.read_text(encoding="utf-8")) or CommentedMap())


def _write(path: Path, y: YAML, data: CommentedMap) -> None:
    """Validate the workspace's own settings, then write (the levels are checked by the caller)."""
    import yaml

    buf = StringIO()
    y.dump(data, buf)
    try:
        PipelineConfig.model_validate(base_settings(yaml.safe_load(buf.getvalue()) or {}))
    except ValueError as e:
        raise ConfigError(f"These values would make {path.name} invalid:\n{e}") from e
    path.write_text(buf.getvalue(), encoding="utf-8")


def _place_last(data: CommentedMap) -> None:
    """batches: then images: at the end of the file, after the workspace's own sections."""
    for section in LEVEL_SECTIONS:
        if section in data:
            data.move_to_end(section)


def set_values(path: Path, values: dict[str, Any]) -> None:
    """Set dotted keys ({"filters.min_area": 300}) in a settings file, keeping everything else.
    The result is validated before it is written."""
    y, data = _load(path)
    for key, value in values.items():
        section, name = key.split(".", 1)
        if data.get(section) is None:
            data[section] = CommentedMap()
        data[section][name] = _plain(value)
    _place_last(data)
    _write(path, y, data)


def set_entry(path: Path, section: str, name: str, info: dict[str, Any], values: dict[str, dict[str, Any]]) -> None:
    """Write one batch's or image's entry: its info (note / reason, by, date) and its settings
    (nested: {"filters": {"min_area": 300}}). An entry with neither is removed, and so is an
    empty section. Comments elsewhere in the file are kept."""
    assert section in LEVEL_SECTIONS
    y, data = _load(path)
    info = {k: v for k, v in info.items() if v not in (None, "")}
    if info or values:
        if data.get(section) is None:
            data[section] = CommentedMap()
        entry = CommentedMap()
        for k, v in info.items():
            entry[k] = v
        for sec, vals in values.items():
            entry[sec] = CommentedMap((k, _plain(v)) for k, v in vals.items())
        data[section][name] = entry
    elif data.get(section) and name in data[section]:
        del data[section][name]
    if section in data and not data[section]:
        del data[section]
    _place_last(data)
    _write(path, y, data)


def rename_entry(path: Path, section: str, old: str, new: str) -> None:
    """Rename a batch's or image's entry, keeping its place in the file."""
    y, data = _load(path)
    entries = data.get(section)
    if not entries or old not in entries:
        return
    keys = list(entries)
    items = [(new if k == old else k, entries[k]) for k in keys]
    for k in keys:
        del entries[k]
    for k, v in items:
        entries[k] = v
    _write(path, y, data)


def remove_keys(path: Path, keys: list[str], sections: tuple[str, ...] = ()) -> list[str]:
    """Remove dotted keys ("input.images") and whole top-level sections; returns what was there."""
    y, data = _load(path)
    gone = []
    for key in keys:
        section, name = key.split(".", 1)
        if isinstance(data.get(section), dict) and name in data[section]:
            del data[section][name]
            gone.append(key)
    for section in sections:
        if section in data:
            del data[section]
            gone.append(section)
    if gone:
        _write(path, y, data)
    return gone


def read(path: Path) -> dict[str, Any]:
    """The whole file as plain data (with batches: and images:)."""
    import yaml

    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
