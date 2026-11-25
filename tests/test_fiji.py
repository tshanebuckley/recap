"""Finding Fiji, and counting in Fiji (with a stand-in Fiji program, tests/fake_fiji.py)."""

import sys
from pathlib import Path

import pytest
import yaml

from helpers import BASE, make_ws, run_cli
from recap import fiji as fj
from recap import fijicount

FAKE_FIJI = [sys.executable, str(Path(__file__).with_name("fake_fiji.py"))]


@pytest.fixture
def fake_fiji(tmp_path, monkeypatch):
    """A fake Fiji install (with launchers for every OS), and no real Fiji or saved setting visible."""
    app = tmp_path / "apps" / "Fiji.app"
    for d in ("jars", "plugins", "java/linux-x64", "Contents/MacOS"):
        (app / d).mkdir(parents=True)
    (app / "jars" / "ij-1.54p.jar").write_text("", encoding="utf-8")
    for name in ("fiji-linux-x64", "fiji-windows-x64.exe", "Contents/MacOS/fiji-macos-arm64"):
        (app / name).write_text("#!/bin/sh\n", encoding="utf-8")
        (app / name).chmod(0o755)
    empty_bin = tmp_path / "bin"
    empty_bin.mkdir()
    monkeypatch.setenv("PATH", str(empty_bin))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))  # Path.home() on Windows
    monkeypatch.setenv("RECAP_CONFIG_DIR", str(tmp_path / "settings"))
    monkeypatch.setattr(fj, "install_folders", lambda platform=None: [tmp_path / "home" / "Fiji.app"])
    for var in fj.FIJI_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    return app, app / "fiji-linux-x64", empty_bin


# ----------------------------------------------------------------- finding Fiji
def test_fiji_from_setting_env_and_path(fake_fiji, tmp_path, monkeypatch):
    app, launcher, bin_ = fake_fiji
    assert fj.resolve_fiji(str(app)).folder == app.resolve()
    assert fj.resolve_fiji(str(launcher)).folder == app.resolve()  # a launcher file
    assert fj.resolve_fiji("apps/Fiji.app", base=tmp_path).folder == app.resolve()  # relative to the config
    for bad in (str(tmp_path / "nope"), str(tmp_path / "bin"), "C:/nowhere/Fiji.app", "sc.fiji:fiji"):
        with pytest.raises(ValueError):
            fj.resolve_fiji(bad)
    assert fj.resolve_fiji(None).folder is None  # nothing found (no download any more)
    monkeypatch.setenv("RECAP_FIJI", str(app))
    loc = fj.resolve_fiji(None)
    assert loc.folder == app.resolve() and "RECAP_FIJI" in loc.source


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need extra rights on Windows")
def test_fiji_from_symlinked_launcher_on_path(fake_fiji):
    app, launcher, bin_ = fake_fiji
    (bin_ / "fiji").symlink_to(launcher)
    loc = fj.resolve_fiji(None)
    assert loc.folder == app.resolve() and "PATH" in loc.source


def test_fiji_from_wrapper_script_on_path(fake_fiji):
    app, _, bin_ = fake_fiji
    if sys.platform == "win32":
        wrapper = bin_ / "fiji.bat"
        wrapper.write_text(f'@echo off\r\n"{app}\\fiji-windows-x64.exe" %*\r\n', encoding="utf-8")
    else:
        wrapper = bin_ / "fiji"
        wrapper.write_text(f'#!/bin/sh\nexec "{app}/ImageJ-linux64" "$@"\n', encoding="utf-8")
        wrapper.chmod(0o755)
    assert fj.resolve_fiji(None).folder == app.resolve()


def test_paths_in_wrapper_scripts():
    bat = '@echo off\r\n"C:\\Users\\pi\\Fiji.app\\fiji-windows-x64.exe" %*\r\n'
    assert fj.paths_in_script(bat) == ["C:\\Users\\pi\\Fiji.app\\fiji-windows-x64.exe"]
    assert fj.paths_in_script("start C:/Fiji.app/ImageJ-win64.exe") == ["C:/Fiji.app/ImageJ-win64.exe"]
    assert fj.paths_in_script('exec "/opt/Fiji.app/fiji-linux-x64" "$@"') == ["/opt/Fiji.app/fiji-linux-x64"]


def test_launcher_and_install_folders_per_os(fake_fiji, monkeypatch, tmp_path):
    app, _, _ = fake_fiji
    assert fj.launcher(app, "linux").name == "fiji-linux-x64"
    assert fj.launcher(app, "win32").name == "fiji-windows-x64.exe"
    assert fj.launcher(app, "darwin").relative_to(app).as_posix() == "Contents/MacOS/fiji-macos-arm64"
    assert fj.fiji_dir(app / "Contents" / "MacOS" / "fiji-macos-arm64") == app.resolve()
    monkeypatch.undo()  # the real install_folders
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    win = [p.as_posix() for p in fj.install_folders("win32")]
    assert "C:/Fiji.app" in win and (tmp_path / "local" / "Fiji").as_posix() in win
    assert fj.install_folders("darwin")[0].as_posix() == "/Applications/Fiji.app"


def test_settings_path_per_os(monkeypatch, tmp_path):
    monkeypatch.delenv("RECAP_CONFIG_DIR", raising=False)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
    monkeypatch.setattr(fj.sys, "platform", "win32")
    assert fj.settings_path() == tmp_path / "Roaming" / "recap" / "settings.yaml"
    monkeypatch.setattr(fj.sys, "platform", "darwin")
    assert fj.settings_path() == fj.Path.home() / ".config" / "recap" / "settings.yaml"


def test_fiji_commands(fake_fiji, tmp_path, write_config, synth_dir):
    app, launcher, _ = fake_fiji
    res = run_cli("fiji")
    assert res.exit_code == 1 and "not found" in res.output and "recap fiji set" in res.output
    res = run_cli("fiji", "set", str(launcher))  # a launcher, e.g. an alias target
    assert res.exit_code == 0 and "Saved" in res.output and "ImageJ 1.54p" in res.output
    assert yaml.safe_load(fj.settings_path().read_text(encoding="utf-8"))["fiji"] == str(app.resolve())
    res = run_cli("fiji", "check")
    assert res.exit_code == 0 and "Result: OK" in res.output and "bundled Java present" in res.output
    assert "saved setting" in run_cli("fiji", "show").output
    assert run_cli("fiji", "set", str(tmp_path / "bin")).exit_code == 1  # not a Fiji
    assert run_cli("fiji", "unset").exit_code == 0 and fj.resolve_fiji(None).folder is None
    make_ws(synth_dir, "validation: {fiji: /no/such/Fiji.app}\n")
    res = run_cli("fiji", "show", "-w", synth_dir)
    assert res.exit_code == 1 and "path not found" in res.output


def test_old_interactive_section_still_loads(write_config, synth_dir, fake_fiji):
    app, _, _ = fake_fiji
    cfg = write_config(
        BASE + f"interactive: {{enabled: true, fiji: {app.as_posix()}, java_memory: 4g, tune_on: 0}}\n"
        "output: {directory: old}\n",
        "old.yaml",
    )
    res = run_cli("validate", cfg)
    assert res.exit_code == 0 and "removed in recap 4.0" in res.output
    assert f"fiji: {app.as_posix()}" in res.output  # carried over to validation.fiji
    assert run_cli("run", cfg).exit_code == 0  # runs, ignoring the old tuning step


# ----------------------------------------------------------------- counting in Fiji (stand-in)
@pytest.fixture
def session(tmp_path, monkeypatch):
    monkeypatch.setattr(fijicount, "STALE_SECONDS", 1)
    s = fijicount.FijiSession(None, tmp_path / "session", command=FAKE_FIJI)
    yield s
    s.close()


def _image(tmp_path):
    import numpy as np
    import tifffile

    p = tmp_path / "view.tif"
    tifffile.imwrite(p, np.zeros((20, 20, 3), np.uint8), photometric="rgb")
    return p


def test_fiji_session_import_skip_cancel(session, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_FIJI_ANSWERS", "import:10,12,0|30.5,40,1;skip;cancel:1,1,0")
    img = _image(tmp_path)
    first = session.count(img, "a.tif", poll=0.05)
    assert first.status == "import" and first.points == [(10.0, 12.0, 0), (30.5, 40.0, 1)]
    assert (first.nuclei, first.infected) == (2, 1)
    assert session.count(img, "b.tif", poll=0.05).status == "skip"
    assert session.count(img, "c.tif", poll=0.05).status == "cancel"


def test_fiji_closed_mid_count_asks_to_keep_points(session, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_FIJI_ANSWERS", "die:5,5,0|6,6,1|7,7,1")
    res = session.count(_image(tmp_path), "a.tif", poll=0.05)
    assert res.status == "closed" and res.nuclei == 3
    asked = []
    ans = fijicount.answer(res, True, lambda q: asked.append(q) or True)
    assert ans == ("done", res.points, 3, 2) and "VRC+: 2" in asked[0]
    assert fijicount.answer(res, True, lambda q: False) == ("skip",)


def _start_fake(real_start):
    def start(self):
        self.command = list(FAKE_FIJI)
        real_start(self)

    return start


def test_spot_check_counts_in_fiji(write_config, synth_dir, monkeypatch):
    """`recap run` with a spot check: counted in (stand-in) Fiji, decision typed in the terminal."""
    app = synth_dir / "Fiji.app"
    (app / "jars").mkdir(parents=True)
    monkeypatch.setattr(fijicount.FijiSession, "_start", _start_fake(fijicount.FijiSession._start))
    monkeypatch.setenv("FAKE_FIJI_ANSWERS", "skip;import:50,50,0|200,100,1|250,20,0;import:60,150,0;import:1,1,0")
    cfg = write_config(
        "input: {images: [data/*.tif]}\nnormalization: {vrc_range: [150, 3000]}\n"
        f"validation: {{enabled: true, sample_size: 3, seed: 7, fiji: {app.as_posix()}, counted_by: ana}}\n"
        "output: {directory: sc}\n"
    )
    res = run_cli("run", cfg, input="s\n")
    assert res.exit_code == 0, res.output
    import pandas as pd

    counts = pd.read_csv(next((synth_dir / "sc").glob("manual_counts_*.csv")))
    assert len(counts) == 3 and set(counts.tool) == {"fiji"} and set(counts.counted_by) == {"ana"}


def test_count_command_stores_in_workspace(synth_dir, monkeypatch):
    calls = []

    def fake_count(self, image, title, position="", count_infected=True, poll=0.5):
        calls.append(Path(image).name)
        return fijicount.FijiCount("import", [(1.0, 2.0, 0), (3.0, 4.0, 1)])

    monkeypatch.setattr(fijicount.FijiSession, "count", fake_count)
    monkeypatch.setattr(fj, "resolve_fiji", lambda s, b=None: fj.FijiLocation(None, "test"))
    imgs = [synth_dir / "data" / "img1.tif", synth_dir / "data" / "img2.tif"]
    assert run_cli("count", *imgs).exit_code == 1  # no workspace yet
    make_ws(synth_dir)
    res = run_cli("count", *imgs, "--by", "ana", "--remember")
    assert res.exit_code == 0 and "2 count(s) added" in res.output and "Counter 0 = VRC-" in res.output
    assert calls == ["img1_for_counting.tif", "img2_for_counting.tif"]  # recap's adjusted view
    from recap.counts import default_counter_name
    from recap.workspace import Workspace

    assert default_counter_name() == "ana"  # remembered
    run_cli("count", imgs[0], "--original")
    assert calls[-1] == "img1.tif"
    counts = Workspace(synth_dir).counts()  # (the same image by the same person in the same second: kept once)
    assert {(c.nuclei, c.infected, c.counted_by, c.tool) for c in counts} == {(2, 1, "ana", "fiji")}
    assert len(counts) >= 2
