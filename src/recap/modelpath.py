"""Where a model named in the settings is: trained versions are stored as paths relative to the
workspace folder (recap-data/models/stardist/v2), so they are found from wherever recap was started.
Kept apart from recap.segment so that opening a workspace stays quick."""

from __future__ import annotations

from pathlib import Path

_MODEL_ROOTS: list[Path] = []  # workspaces opened in this process, most recent first


def use_model_root(root: Path) -> None:
    """Resolve model paths in settings (recap-data/models/stardist/v2) against this workspace's
    folder, not the folder the program was started in. Workspace() calls this."""
    root = Path(root).resolve()
    if root in _MODEL_ROOTS:
        _MODEL_ROOTS.remove(root)
    _MODEL_ROOTS.insert(0, root)


def model_path(name: str | None) -> Path | None:
    """A model setting as an existing folder or file, or None (a standard model's name)."""
    if not name:
        return None
    p = Path(name)
    if p.is_absolute():
        return p if p.exists() else None
    for base in (*_MODEL_ROOTS, Path.cwd()):  # the open workspace first
        if (base / p).exists():
            return (base / p).resolve()
    return None
