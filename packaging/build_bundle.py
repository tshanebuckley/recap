"""Build an offline install bundle of recap for one system.

    pixi run bundle linux-64 | win-64 | osx-arm64       (or: python packaging/build_bundle.py <platform>)

Makes dist/recap-<version>-<platform>.zip holding:
    recap-env.sh                   Linux / Mac: the whole environment (pixi-pack, self-extracting):
                                   Python, TensorFlow/StarDist, napari, Qt and recap itself (a wheel)
    recap-env.tar, pixi-unpack.exe Windows: the same environment as a plain pack and its unpacker
                                   (a self-extracting .ps1 this size is more than PowerShell can load)
    stardist-models/               the pretrained StarDist model, so the first run works offline
    install script(s)              unpack, copy the model, make the `recap` launcher and desktop icon
    README.txt
Everything except Fiji. Linux and Mac bundles can be built from any system; the Windows bundle
is built on Windows (it ships the Windows pixi-unpack.exe). Each installs only on its own system.
"""

import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HERE = Path(__file__).resolve().parent
PLATFORMS = {"linux-64": "sh", "osx-arm64": "sh", "win-64": "tar"}
PIXI_PACK = "0.7.12"  # pixi-pack and pixi-unpack must be the same version


def run(cmd: list[str], **kw) -> None:
    print("+", " ".join(map(str, cmd)), flush=True)
    subprocess.run(cmd, check=True, **kw)


def pixi_unpack_exe() -> Path:
    """pixi-unpack.exe (conda-forge, the same version as pixi-pack) for the Windows bundle."""
    if sys.platform != "win32":
        raise SystemExit("The win-64 bundle must be built on Windows (it ships pixi-unpack.exe).")
    code = "import shutil; print(shutil.which('pixi-unpack'))"
    res = subprocess.run(["pixi", "exec", "--spec", f"pixi-unpack=={PIXI_PACK}", "--spec", "python", "--",
                          "python", "-c", code], capture_output=True, text=True, check=True)  # fmt: skip
    exe = Path(res.stdout.strip().splitlines()[-1])
    if not exe.is_file():
        raise SystemExit(f"pixi-unpack not found ({res.stdout!r})")
    return exe


def version() -> str:
    for line in (ROOT / "src" / "recap" / "__init__.py").read_text(encoding="utf-8").splitlines():
        if line.startswith("__version__"):
            return line.split('"')[1]
    raise SystemExit("no __version__")


def stardist_model(dest: Path) -> None:
    """Copy the pretrained model from the Keras cache, downloading it first if needed."""
    code = "from stardist.models import StarDist2D; StarDist2D.from_pretrained('2D_versatile_fluo')"
    run([sys.executable, "-c", code])
    src = Path.home() / ".keras" / "models" / "StarDist2D" / "2D_versatile_fluo"
    shutil.copytree(src, dest / "StarDist2D" / "2D_versatile_fluo")


def main() -> None:
    platform = sys.argv[1] if len(sys.argv) > 1 else "linux-64"
    if platform not in PLATFORMS:
        raise SystemExit(f"platform must be one of {', '.join(PLATFORMS)}")
    v = version()
    dist = ROOT / "dist"
    name = f"recap-{v}-{platform}"
    out = dist / name
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    wheels = dist / "wheel"
    shutil.rmtree(wheels, ignore_errors=True)
    run([sys.executable, "-m", "pip", "wheel", str(ROOT), "--no-deps", "-q", "-w", str(wheels)])
    wheel = next(wheels.glob("recap-*.whl"))
    pixi_pack = ["pixi", "exec", "--spec", f"pixi-pack=={PIXI_PACK}", "pixi-pack"]
    env_file = out / f"recap-env.{PLATFORMS[platform]}"
    windows = platform == "win-64"
    if windows:
        shutil.copy2(pixi_unpack_exe(), out / "pixi-unpack.exe")
    # recap is installed editable in the lock (not a wheel): skip that entry and inject the built wheel
    run([*pixi_pack, "-e", "default", "-p", platform, *([] if windows else ["--create-executable"]),
         "--ignore-pypi-non-wheel", "--inject", str(wheel), "-o", str(env_file), str(ROOT / "pixi.toml")])  # fmt: skip
    stardist_model(out / "stardist-models")
    files = ["README.txt"] + (["install.ps1", "Install recap.bat"] if platform == "win-64" else ["install.sh"])
    if platform == "osx-arm64":
        files.append("Install recap.command")
    for f in files:
        text = (HERE / f).read_text(encoding="utf-8").replace("@VERSION@", v)
        newline = "\r\n" if f.endswith((".bat", ".ps1")) else "\n"
        (out / f).write_text(text, encoding="utf-8", newline=newline)
        if f.endswith((".sh", ".command")):
            (out / f).chmod(0o755)
    archive = dist / f"{name}.zip"
    archive.unlink(missing_ok=True)
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_STORED, allowZip64=True) as z:  # the pack is compressed already
        for p in sorted(out.rglob("*")):
            info = zipfile.ZipInfo.from_file(p, Path(name) / p.relative_to(out))
            if p.is_file():
                info.external_attr = (0o755 if p.suffix in (".sh", ".command") else 0o644) << 16
                info.compress_type = zipfile.ZIP_STORED
                with open(p, "rb") as src, z.open(info, "w", force_zip64=True) as dst:
                    shutil.copyfileobj(src, dst, 16 * 2**20)
    size = archive.stat().st_size / 2**30
    print(f"\n{archive}  ({size:.2f} GiB)")
    if size >= 2:
        print("WARNING: over 2 GiB: too large for one GitHub release file", file=sys.stderr)


if __name__ == "__main__":
    main()
