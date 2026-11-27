"""A workspace's .recapignore: the images recap leaves out.

A workspace is every image in its folder (and subfolders) except those listed in `.recapignore`,
a text file in the workspace folder. Its lines work like a .gitignore:

    # comments start with #
    data/day79_03.tif        # one image (paths relative to the workspace, / between folders)
    old_exports/             # a whole folder
    *_bad.tif                # a pattern without / matches a file or folder name anywhere
    !old_exports/keep.tif    # ! brings an image back; the last matching line wins

`recap ignore` / `recap unignore` and the app's Ignore / Stop ignoring write it; it can also be
edited by hand. Ignored images keep what recap knows about them (runs, counts, masks) but are not
run, counted, tuned or trained on, and do not appear in reports.
"""

from __future__ import annotations

import fnmatch
import os
from pathlib import Path, PurePosixPath

IGNORE_NAME = ".recapignore"
HEADER = (
    "# Images recap leaves out of this workspace (one path or pattern per line, relative to this folder).\n"
    "# folder/ = a whole folder; a name without / matches anywhere; !path brings an image back.\n"
)


def _norm(s: str) -> str:
    return s.casefold() if os.name == "nt" else s


def _matches(rule: str, rel: str) -> bool:
    """Does one rule (without its !) match a path relative to the workspace?"""
    path = PurePosixPath(rel)
    rule = rule.strip()
    folder = rule.endswith("/")
    rule = rule.strip("/")
    if not rule:
        return False
    rule, parts = _norm(rule), [_norm(p) for p in path.parts]
    if "/" in rule:  # anchored at the workspace folder
        candidates = ["/".join(parts[:i]) for i in range(1, len(parts) + (0 if folder else 1))]
        return any(fnmatch.fnmatchcase(c, rule) for c in candidates)
    names = parts[:-1] if folder else parts  # a folder rule matches the folders above the file
    return any(fnmatch.fnmatchcase(n, rule) for n in names)


class IgnoreList:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.path = self.root / IGNORE_NAME

    def lines(self) -> list[str]:
        if not self.path.is_file():
            return []
        return self.path.read_text(encoding="utf-8").splitlines()

    def rules(self) -> list[str]:
        """The rules in order: comments, blank lines and end-of-line comments removed."""
        out = []
        for line in self.lines():
            line = line.split(" #", 1)[0].strip() if not line.lstrip().startswith("#") else ""
            if line:
                out.append(line.replace("\\", "/"))
        return out

    def is_ignored(self, rel: str, rules: list[str] | None = None) -> bool:
        ignored = False
        for rule in self.rules() if rules is None else rules:
            negate = rule.startswith("!")
            if _matches(rule[1:] if negate else rule, rel):
                ignored = not negate
        return ignored

    def filter(self, rels: list[str]) -> list[str]:
        """The paths that are not ignored."""
        rules = self.rules()
        return [r for r in rels if not self.is_ignored(r, rules)]

    def _write(self, lines: list[str]) -> None:
        text = "\n".join(lines).rstrip("\n") + "\n"
        if not text.startswith("#"):
            text = HEADER + text
        self.path.write_text(text, encoding="utf-8")

    def ignore(self, rels: list[str]) -> list[str]:
        """Leave these paths out (files or folders ending in /). Returns those newly ignored."""
        lines, added = self.lines(), []
        for rel in rels:
            rel = rel.replace("\\", "/")
            if self.is_ignored(rel.rstrip("/") + ("/x" if rel.endswith("/") else "")):
                continue
            undo = f"!{rel}"
            if undo in [ln.strip() for ln in lines]:  # it was brought back before: just drop that line
                lines = [ln for ln in lines if ln.strip() != undo]
            else:
                lines.append(rel)
            added.append(rel)
            self._write(lines)
        return added

    def unignore(self, rels: list[str]) -> list[str]:
        """Bring these paths back. A line naming the path is removed; if a pattern or folder still
        covers it, a !path line is added. Returns those brought back."""
        lines, back = self.lines(), []
        for rel in rels:
            rel = rel.replace("\\", "/")
            if not self.is_ignored(rel):
                continue
            lines = [ln for ln in lines if ln.split(" #", 1)[0].strip() != rel]
            self._write(lines)
            if self.is_ignored(rel):
                lines.append(f"!{rel}")
                self._write(lines)
            back.append(rel)
        return back
