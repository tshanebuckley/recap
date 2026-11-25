"""Finding, saving and checking the Fiji installation recap opens for counting.

recap starts Fiji as its own program (see fijicount.py); it no longer runs Fiji inside Python.
Fiji brings its own Java, so recap needs none.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

log = logging.getLogger(__name__)

FIJI_ENV_VARS = ("RECAP_FIJI", "FIJI_HOME")
# Launcher names (shutil.which adds .exe on Windows). Fiji 2.16+ ships "fiji-<os>-<arch>" launchers,
# older releases "ImageJ-<os>". On macOS they live in Fiji.app/Contents/MacOS/.
FIJI_LAUNCHERS = (
    "fiji",
    "Fiji",
    "fiji-linux-x64",
    "ImageJ-linux64",
    "fiji-windows-x64",
    "ImageJ-win64",
    "fiji-macos-arm64",
    "fiji-macos-universal",
    "ImageJ-macosx",
    "ImageJ",
)
NOT_FOUND_HINT = (
    "Install Fiji (https://fiji.sc), then tell recap where it is:  recap fiji set /path/to/Fiji.app\n"
    "  (Windows: recap fiji set C:/Users/you/Fiji.app; Mac: recap fiji set /Applications/Fiji.app)\n"
    "  or set the environment variable RECAP_FIJI to that folder."
)


@dataclass
class FijiLocation:
    folder: Path | None  # the Fiji installation, or None if not found
    source: str  # how it was found
    checked: list[str] = field(default_factory=list)


def fiji_dir(candidate: Path) -> Path | None:
    """Fiji installation folder for a folder or launcher path, or None. A Fiji folder has jars/."""
    try:
        p = candidate.expanduser().resolve()
    except OSError:
        return None
    # a launcher sits in the Fiji folder, or in Fiji.app/Contents/MacOS/ on macOS
    for d in (p, p.parent, *p.parents[1:3]) if p.is_file() else (p,):
        if (d / "jars").is_dir():
            return d
    if p.is_file() and p.stat().st_size < 65536:  # wrapper script: look for a Fiji path inside
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return None
        for found in paths_in_script(text):
            q = Path(os.path.expandvars(found)).expanduser()
            for d in (q, *q.parents[:3]):  # also Fiji.app/Contents/MacOS/launcher
                try:
                    if (d / "jars").is_dir():
                        return d.resolve()
                except OSError:
                    continue
    return None


def paths_in_script(text: str) -> list[str]:
    """Absolute paths in a launcher wrapper: POSIX (/..., ~/...) and Windows (C:\\..., C:/...,
    %USERPROFILE%\\...), e.g. from a shell script or a .bat file."""
    return re.findall(r"""(?:^|[\s'"=@])((?:~|/|[A-Za-z]:[\\/]|%\w+%)[^\s'"]*)""", text, flags=re.M)


def launcher(folder: Path, platform: str | None = None) -> Path | None:
    """The program that starts this Fiji on this OS (newest launcher naming first)."""
    platform = platform or sys.platform
    if platform == "win32":
        names = ["fiji-windows-x64.exe", "ImageJ-win64.exe", "fiji.exe"]
    elif platform == "darwin":
        names = [f"Contents/MacOS/{n}" for n in ("fiji-macos-arm64", "fiji-macos-universal", "ImageJ-macosx", "fiji")]
    else:
        names = ["fiji-linux-x64", "ImageJ-linux64", "fiji", "ImageJ"]
    return next((folder / n for n in names if (folder / n).is_file()), None)


# ---- user settings ----------------------------------------------------------------------------
def settings_path() -> Path:
    """Per-user recap settings: $RECAP_CONFIG_DIR, else %APPDATA%\\recap on Windows, else
    $XDG_CONFIG_HOME/recap, else ~/.config/recap (Linux and macOS)."""
    base = os.environ.get("RECAP_CONFIG_DIR")
    if not base and sys.platform == "win32" and os.environ.get("APPDATA"):
        base = os.path.join(os.environ["APPDATA"], "recap")
    if not base:
        base = os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.join(Path.home(), ".config"), "recap")
    return Path(base) / "settings.yaml"


def load_settings() -> dict[str, Any]:
    p = settings_path()
    if not p.exists():
        return {}
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return {}
    return data if isinstance(data, dict) else {}


def save_settings(d: dict[str, Any]) -> Path:
    p = settings_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        "# recap user settings (written by recap: Fiji location, your name for counts, recent workspaces)\n"
        + yaml.safe_dump(d, sort_keys=False),
        encoding="utf-8",
    )
    return p


def set_default(path: str | Path, force: bool = False) -> tuple[Path, list[tuple[str, str]], bool]:
    """Check a Fiji folder (or a launcher inside it) and save it as this user's default unless a
    check fails (or force). Returns (folder, check results, saved). `recap fiji set` and the app's
    Fiji dialog both use this. Raises ValueError if it is not a Fiji installation."""
    import datetime as dt

    d = fiji_dir(Path(os.path.expandvars(str(path))).expanduser())
    if d is None:
        raise ValueError(f"{path} is not a Fiji installation (no jars/ folder found next to it).")
    results = check_fiji(d)
    if any(st == "fail" for st, _ in results) and not force:
        return d, results, False
    settings = load_settings()
    settings.update(fiji=str(d), fiji_set_at=dt.datetime.now().isoformat(timespec="seconds"))
    save_settings(settings)
    return d, results, True


# ---- checking -------------------------------------------------------------------------------
def check_fiji(folder: Path) -> list[tuple[str, str]]:
    """Static checks of a Fiji installation. Returns (status, message) with status ok | warn | fail."""
    out: list[tuple[str, str]] = []
    d = Path(folder)
    if not d.is_dir():
        return [("fail", f"{d} is not a folder")]
    out.append(("ok", f"folder exists: {d}"))
    out.append(("ok", "jars/ folder present") if (d / "jars").is_dir() else ("fail", "no jars/ folder"))
    ij = sorted((d / "jars").glob("ij-[0-9]*.jar"))
    out.append(("ok", f"ImageJ {ij[-1].stem[3:]}") if ij else ("warn", "no jars/ij-*.jar (ImageJ1) found"))
    exe = launcher(d)
    out.append(
        ("ok", f"launcher: {exe.relative_to(d)}") if exe else ("fail", "no launcher for this OS: recap cannot start it")
    )
    java = [p for p in (d / "java").glob("*") if p.is_dir()] if (d / "java").is_dir() else []
    out.append(
        ("ok", "bundled Java present")
        if java
        else ("warn", "no bundled Java (java/ folder): Fiji will use the system Java, which needs version 21")
    )
    out.append(("ok", "plugins/ folder present") if (d / "plugins").is_dir() else ("warn", "no plugins/ folder"))
    return out


# ---- finding --------------------------------------------------------------------------------
def install_folders(platform: str | None = None) -> list[Path]:
    """Folders where people commonly unpack Fiji, for this OS (or the given sys.platform value)."""
    platform = platform or sys.platform
    home = Path.home()
    names = ("Fiji.app", "Fiji")  # Fiji 2.16+ unpacks to "Fiji" on Linux/Windows; "Fiji.app" before
    personal = [home / n for n in names] + [home / sub / n for sub in ("Desktop", "Downloads") for n in names]
    if platform == "win32":
        env = [os.environ.get(v) for v in ("LOCALAPPDATA", "ProgramFiles", "ProgramFiles(x86)")]
        return (
            personal
            + [home / "Documents" / n for n in names]
            + [Path(e) / n for e in env if e for n in names]
            + [Path("C:/") / n for n in names]
        )
    if platform == "darwin":
        return [Path("/Applications/Fiji.app"), home / "Applications/Fiji.app", *personal]
    return personal + [
        home / "Applications/Fiji.app",
        home / "fiji/Fiji.app",
        home / ".local/share/Fiji.app",
        Path("/opt/Fiji.app"),
        Path("/opt/Fiji"),
        Path("/opt/fiji/Fiji.app"),
        Path("/usr/local/Fiji.app"),
    ]


def resolve_fiji(setting: str | None, base: Path = Path(".")) -> FijiLocation:
    """Find Fiji. Order: the setting (validation.fiji), $RECAP_FIJI / $FIJI_HOME, the saved setting
    (recap fiji set), Fiji launchers on PATH, common install folders for this OS. Returns a
    location with folder=None if nothing was found; raises ValueError if the setting is wrong.
    Shell aliases are invisible to programs, so an alias is honoured only via one of these."""
    checked: list[str] = []
    if setting:
        p = Path(os.path.expandvars(setting)).expanduser()
        p = p if p.is_absolute() else base / p
        if not p.exists():
            raise ValueError(f"fiji = {setting!r}: path not found ({p})")
        d = fiji_dir(p)
        if d is None:
            raise ValueError(f"fiji = {setting!r}: {p} is not a Fiji installation (no jars/ folder found)")
        return FijiLocation(d, f"setting validation.fiji ({setting})", [str(p)])
    for var in FIJI_ENV_VARS:
        val = os.environ.get(var)
        if val:
            checked.append(f"${var}={val}")
            d = fiji_dir(Path(val))
            if d:
                return FijiLocation(d, f"environment variable {var}", checked)
    saved = load_settings().get("fiji")
    if saved:
        d = fiji_dir(Path(saved))
        checked.append(f"saved setting: {saved}" + ("" if d else "  (no longer a valid Fiji: skipped)"))
        if d:
            return FijiLocation(d, f"saved setting (recap fiji set; {settings_path()})", checked)
    for name in FIJI_LAUNCHERS:
        exe = shutil.which(name)
        if exe:
            checked.append(f"PATH: {name} -> {os.path.realpath(exe)}")
            d = fiji_dir(Path(exe))
            if d:
                return FijiLocation(d, f"launcher on PATH ({name} -> {os.path.realpath(exe)})", checked)
    for c in install_folders():
        checked.append(str(c))
        d = fiji_dir(c)
        if d:
            return FijiLocation(d, f"install folder {c}", checked)
    return FijiLocation(None, "not found", checked)
