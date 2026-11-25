"""Desktop icon that starts the recap app (`recap gui`), for Linux, Windows and macOS.

Every icon recap creates is recorded in the user settings and carries a marker, so re-running
`recap desktop-icon` replaces old icons and never touches files recap did not make.

- Linux:   recap.desktop in the applications menu folder and on the desktop
- Windows: recap.lnk on the desktop and in the Start menu (created with PowerShell)
- macOS:   recap.app on the desktop (a minimal app bundle)

The icon runs the bundle's `recap` launcher when installed from a bundle (packaging/), `pixi run
... recap gui` when created from a pixi environment (so Qt and the rest are set up exactly as in
`pixi run`), else `<this python> -m recap gui`.
"""

from __future__ import annotations

import logging
import os
import plistlib
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

from recap.errors import RecapError
from recap.fiji import load_settings, save_settings, settings_path

log = logging.getLogger(__name__)

NAME = "recap"
MARKER = "X-Recap-Desktop-Icon"  # in .desktop files and Info.plist; Windows shortcuts are recorded only
DESCRIPTION = "recap: Reconstruction of Cellular Architecture and Phenotypes"


@dataclass
class Launch:
    argv: list[str]
    cwd: Path


def launch_command() -> Launch:
    """How the icon starts the app: through the installed launcher (bundle installs set
    RECAP_LAUNCHER), through pixi in a pixi environment, else with this Python."""
    launcher = os.environ.get("RECAP_LAUNCHER")
    if launcher and Path(launcher).is_file():
        return Launch([launcher, "gui"], Path(launcher).parent)
    manifest = os.environ.get("PIXI_PROJECT_MANIFEST") or os.environ.get("PIXI_WORKSPACE_MANIFEST")
    pixi = os.environ.get("PIXI_EXE") or shutil.which("pixi")
    if manifest and pixi:
        env = os.environ.get("PIXI_ENVIRONMENT_NAME", "default")
        argv = [pixi, "run", "--manifest-path", manifest, "--environment", env, "--frozen", "recap", "gui"]
        return Launch(argv, Path(manifest).parent)
    return Launch([sys.executable, "-m", "recap", "gui"], Path.cwd())


def icon_file(suffix: str) -> Path:
    """The app icon as .png / .ico / .icns, written next to the user settings (a stable location)."""
    out = settings_path().parent / f"recap{suffix}"
    out.parent.mkdir(parents=True, exist_ok=True)
    src = resources.files("recap") / "assets" / "recap.png"
    if suffix == ".png":
        out.write_bytes(src.read_bytes())
        return out
    from PIL import Image

    with resources.as_file(src) as p, Image.open(p) as im:
        if suffix == ".ico":
            im.save(out, sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
        else:
            im.save(out)
    return out


# ---- removing old icons ---------------------------------------------------------------------
def _is_ours(p: Path) -> bool:
    try:
        if p.suffix == ".desktop" and p.is_file():
            return MARKER in p.read_text(encoding="utf-8", errors="ignore")
        if p.suffix == ".app" and (p / "Contents" / "Info.plist").is_file():
            with open(p / "Contents" / "Info.plist", "rb") as f:
                return bool(plistlib.load(f).get(MARKER))
    except (OSError, plistlib.InvalidFileException):
        return False
    return False


def remove_icons(candidates: list[Path] | None = None) -> list[Path]:
    """Delete the icons recap recorded, plus marked recap icons in the usual places. Returns what was removed."""
    settings = load_settings()
    recorded = [Path(p) for p in settings.get("desktop_icons", [])]
    removed = []
    for p in dict.fromkeys(recorded + [c for c in candidates or [] if _is_ours(c)]):
        if not (p.exists() or p.is_symlink()):
            continue
        if p.suffix == ".app" and p.is_dir():
            if _is_ours(p):
                shutil.rmtree(p)
                removed.append(p)
        elif p.suffix in (".desktop", ".lnk"):
            p.unlink()
            removed.append(p)
    if "desktop_icons" in settings:
        settings.pop("desktop_icons")
        save_settings(settings)
    return removed


def _record(paths: list[Path]) -> None:
    settings = load_settings()
    settings["desktop_icons"] = [str(p) for p in paths]
    save_settings(settings)


# ---- Linux --------------------------------------------------------------------------------
def linux_dirs() -> tuple[Path, Path | None]:
    """(applications menu folder, desktop folder or None)."""
    data = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    desktop = os.environ.get("XDG_DESKTOP_DIR")
    if not desktop and shutil.which("xdg-user-dir"):
        try:
            desktop = subprocess.run(
                ["xdg-user-dir", "DESKTOP"], capture_output=True, text=True, timeout=10
            ).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            desktop = None
    d = Path(desktop) if desktop else Path.home() / "Desktop"
    return data / "applications", (d if d.is_dir() and d != Path.home() else None)


def _desktop_exec(argv: list[str]) -> str:
    """Quote arguments for the Exec= key (freedesktop Desktop Entry spec)."""

    def q(a: str) -> str:
        if a and not any(c in a for c in " \t\n\"'\\><~|&;$*?#()`"):
            return a
        return '"' + "".join("\\" + c if c in '"`$\\' else c for c in a) + '"'

    return " ".join(q(a) for a in argv).replace("%", "%%")


def desktop_entry(launch: Launch, icon: Path) -> str:
    return (
        "[Desktop Entry]\n"
        "Type=Application\n"
        f"Name={NAME}\n"
        f"Comment={DESCRIPTION}\n"
        f"Exec={_desktop_exec(launch.argv)}\n"
        f"Path={launch.cwd}\n"
        f"Icon={icon}\n"
        "Terminal=false\n"
        "Categories=Science;Biology;Graphics;\n"
        f"{MARKER}=true\n"
    )


def install_linux(launch: Launch) -> list[Path]:
    apps, desktop = linux_dirs()
    text = desktop_entry(launch, icon_file(".png"))
    created = []
    for d in [apps] + ([desktop] if desktop else []):
        d.mkdir(parents=True, exist_ok=True)
        p = d / f"{NAME}.desktop"
        p.write_text(text, encoding="utf-8")
        p.chmod(0o755)
        created.append(p)
        if d == desktop and shutil.which("gio"):  # GNOME: allow launching without "Untrusted" prompt
            subprocess.run(["gio", "set", str(p), "metadata::trusted", "true"], capture_output=True, timeout=10)
    return created


# ---- macOS --------------------------------------------------------------------------------
def install_macos(launch: Launch, desktop: Path | None = None) -> list[Path]:
    desktop = desktop or Path.home() / "Desktop"
    app = desktop / f"{NAME}.app"
    contents = app / "Contents"
    (contents / "MacOS").mkdir(parents=True, exist_ok=True)
    (contents / "Resources").mkdir(parents=True, exist_ok=True)
    shutil.copy(icon_file(".icns"), contents / "Resources" / "recap.icns")
    info = {
        "CFBundleName": NAME,
        "CFBundleDisplayName": NAME,
        "CFBundleIdentifier": "org.recap.desktop",
        "CFBundleExecutable": NAME,
        "CFBundleIconFile": "recap.icns",
        "CFBundlePackageType": "APPL",
        "CFBundleShortVersionString": _version(),
        "LSMinimumSystemVersion": "11.0",
        "NSHighResolutionCapable": True,
        MARKER: True,
    }
    with open(contents / "Info.plist", "wb") as f:
        plistlib.dump(info, f)
    script = contents / "MacOS" / NAME
    script.write_text(
        "#!/bin/bash\n"
        "# Started by Finder with a minimal environment; pixi sets up everything recap needs.\n"
        f"cd {shlex.quote(str(launch.cwd))} || exit 1\n"
        f"exec {shlex.join(launch.argv)}\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    return [app]


# ---- Windows ------------------------------------------------------------------------------
def _ps(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


def windows_script(launch: Launch, icon: Path) -> str:
    """PowerShell that creates recap.lnk on the desktop and in the Start menu and prints their paths.
    The desktop folder is asked from Windows, so OneDrive-redirected desktops work."""
    target, args = launch.argv[0], subprocess.list2cmdline(launch.argv[1:])
    return f"""$ErrorActionPreference = 'Stop'
$shell = New-Object -ComObject WScript.Shell
foreach ($dir in @([Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('Programs'))) {{
    $path = Join-Path $dir '{NAME}.lnk'
    $s = $shell.CreateShortcut($path)
    $s.TargetPath = {_ps(target)}
    $s.Arguments = {_ps(args)}
    $s.WorkingDirectory = {_ps(str(launch.cwd))}
    $s.IconLocation = {_ps(str(icon))}
    $s.Description = {_ps(DESCRIPTION)}
    $s.WindowStyle = 7
    $s.Save()
    Write-Output $path
}}
"""


def install_windows(launch: Launch) -> list[Path]:
    script = windows_script(launch, icon_file(".ico"))
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError) as e:
        raise RecapError(f"Could not run PowerShell to create the shortcut: {e}") from e
    if out.returncode != 0:
        raise RecapError(f"Creating the shortcut failed:\n{out.stderr.strip()}")
    return [Path(line.strip()) for line in out.stdout.splitlines() if line.strip().endswith(".lnk")]


# ---- entry points ---------------------------------------------------------------------------
def _version() -> str:
    from recap import __version__

    return __version__


def usual_places(platform: str) -> list[Path]:
    """Where a recap icon from an older install may sit (removed only if it carries the marker)."""
    if platform == "darwin":
        return [Path.home() / "Desktop" / f"{NAME}.app", Path.home() / "Applications" / f"{NAME}.app"]
    if platform.startswith("linux"):
        apps, desktop = linux_dirs()
        return [apps / f"{NAME}.desktop"] + ([desktop / f"{NAME}.desktop"] if desktop else [])
    return []


def install_icon(platform: str | None = None) -> tuple[list[Path], list[Path]]:
    """Remove recap's old icons, then create new ones. Returns (removed, created)."""
    platform = platform or sys.platform
    removed = remove_icons(usual_places(platform))
    launch = launch_command()
    if platform == "win32":
        created = install_windows(launch)
    elif platform == "darwin":
        created = install_macos(launch)
    elif platform.startswith("linux"):
        created = install_linux(launch)
    else:
        raise RecapError(f"Desktop icons are not supported on {platform}")
    _record(created)
    log.info("desktop icon: removed %s, created %s", removed, created)
    return removed, created
