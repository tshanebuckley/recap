"""Desktop app stub and desktop icons. The napari window itself is not opened here (CI machines may
lack OpenGL); its parts are: what it loads, the one-instance guard, and the icons per OS."""

import os
import plistlib
import sys

import numpy as np
import pytest

from helpers import make_ws, run_cli
from recap import desktop


# ----------------------------------------------------------------- what the app opens
def test_view_from_workspace(synth_dir):
    from recap.gui.data import view_from_workspace
    from recap.io import file_sha256
    from recap.workspace import Workspace

    ws = Workspace(make_ws(synth_dir))
    ws.scan()
    sha = file_sha256(synth_dir / "data" / "img1.tif")
    view = view_from_workspace(ws, sha)  # before a run: image only, with a hint
    assert view.labels is None and "Run selected" in view.messages[0]
    assert set(view.channels) == {"VRC (ICP8)", "DAPI"}  # named by the workspace's settings
    assert run_cli("run", synth_dir, "-i", synth_dir / "data" / "img1.tif").exit_code == 0
    view = view_from_workspace(ws, sha)
    assert view.labels is not None and view.labels.max() == 4  # 4 synthetic nuclei
    assert view.labels.shape == view.channels["DAPI"][0].shape and not view.messages


def test_example_workspace_is_found():
    from recap.gui.data import example_workspace

    ex = example_workspace()
    assert ex is not None and (ex / "recap.yaml").is_file()
    from recap.config import load_config

    assert load_config(ex / "recap.yaml")[1].input.images == []  # every image in the folder


def test_add_layers_order_and_editing():
    pytest.importorskip("qtpy.QtCore")  # recap.gui.app imports the Qt-based single-instance guard
    from types import SimpleNamespace

    from recap.gui.app import add_layers
    from recap.gui.data import ImageView

    calls = []

    class Viewer:
        layers = SimpleNamespace(selection=SimpleNamespace(active=None))

        def add_image(self, data, **kw):
            calls.append(("image", kw["name"]))

        def add_labels(self, data, **kw):
            calls.append(("labels", kw["name"], kw.get("visible", True)))
            return SimpleNamespace(contour=0)

    z = np.zeros((4, 4))
    v = ImageView(None, {"VRC (ICP8)": (z, "green"), "DAPI": (z, "blue")}, z.astype(int), z.astype(int), None)
    viewer = Viewer()
    add_layers(viewer, v)
    assert calls == [
        ("image", "VRC (ICP8)"),
        ("image", "DAPI"),
        ("labels", "excluded nuclei", False),
        ("labels", "nuclei (program)", True),
    ]
    assert viewer.layers.selection.active.contour == 2  # mask selected and drawn as outlines


# ----------------------------------------------------------------- one running app
def test_single_instance(tmp_path):
    """A second launch (a separate process, as when the icon is double-clicked again) does not
    start and asks the running app to come forward."""
    import subprocess

    qtcore = pytest.importorskip("qtpy.QtCore")
    pytest.importorskip("qtpy.QtNetwork")
    from recap.gui.single_instance import SingleInstance

    app = qtcore.QCoreApplication.instance() or qtcore.QCoreApplication([])
    key = f"recap-test-{os.getpid()}"
    activated = []
    first = SingleInstance(key, on_activate=lambda: activated.append(1))
    assert first.acquire()
    first.listen()
    code = (
        "import sys; from recap.gui.single_instance import SingleInstance; "
        f"sys.exit(0 if not SingleInstance({key!r}).acquire() else 3)"
    )
    second = subprocess.Popen([sys.executable, "-c", code])
    deadline = qtcore.QDeadlineTimer(15000)
    while (second.poll() is None or not activated) and not deadline.hasExpired():
        app.processEvents(qtcore.QEventLoop.ProcessEventsFlag.AllEvents, 50)
    assert second.wait(5) == 0  # the second launch saw the running app and stopped
    assert activated  # and the running app was asked to come forward
    first.release()
    third = SingleInstance(key)
    assert third.acquire()  # free again after the first app exits
    third.release()


# ----------------------------------------------------------------- desktop icons
@pytest.fixture
def icon_home(tmp_path, monkeypatch):
    monkeypatch.setenv("RECAP_CONFIG_DIR", str(tmp_path / "settings"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "share"))
    (tmp_path / "Desktop").mkdir()
    monkeypatch.setenv("XDG_DESKTOP_DIR", str(tmp_path / "Desktop"))
    monkeypatch.setattr(desktop.shutil, "which", lambda name: None)  # no gio / xdg-user-dir / pixi
    for var in ("PIXI_PROJECT_MANIFEST", "PIXI_WORKSPACE_MANIFEST", "PIXI_EXE"):
        monkeypatch.delenv(var, raising=False)
    return tmp_path


def test_launch_command_prefers_pixi(monkeypatch, tmp_path):
    monkeypatch.delenv("PIXI_WORKSPACE_MANIFEST", raising=False)
    monkeypatch.setenv("PIXI_PROJECT_MANIFEST", str(tmp_path / "pixi.toml"))
    monkeypatch.setenv("PIXI_EXE", "/usr/bin/pixi")
    monkeypatch.setenv("PIXI_ENVIRONMENT_NAME", "cellpose")
    launch = desktop.launch_command()
    assert launch.argv[:2] == ["/usr/bin/pixi", "run"] and launch.argv[-2:] == ["recap", "gui"]
    assert "cellpose" in launch.argv and "--frozen" in launch.argv and launch.cwd == tmp_path
    monkeypatch.delenv("PIXI_PROJECT_MANIFEST")
    assert desktop.launch_command().argv == [sys.executable, "-m", "recap", "gui"]


def test_desktop_exec_quoting():
    argv = ["/opt/my env/python", "-m", "recap", 'say "hi"', "100%"]
    assert desktop._desktop_exec(argv) == '"/opt/my env/python" -m recap "say \\"hi\\"" 100%%'


@pytest.mark.skipif(sys.platform == "win32", reason="Linux icon files; Windows is tested below")
def test_linux_icon_replaces_old_and_spares_foreign(icon_home):
    foreign = icon_home / "Desktop" / "recap.desktop"
    foreign.write_text("[Desktop Entry]\nName=someone else's\n", encoding="utf-8")
    removed, created = desktop.install_icon("linux")
    assert removed == [] and len(created) == 2  # applications menu + desktop
    entry = created[0].read_text(encoding="utf-8")
    assert desktop.MARKER in entry and "-m recap gui" in entry and "Terminal=false" in entry
    assert (icon_home / "settings" / "recap.png").is_file()
    removed, created2 = desktop.install_icon("linux")  # again: old ones replaced, still exactly two
    assert sorted(removed) == sorted(created) and sorted(created2) == sorted(created)
    assert sorted(desktop.load_settings()["desktop_icons"]) == sorted(map(str, created))
    res = run_cli("desktop-icon", "--remove")
    assert res.exit_code == 0 and not any(p.exists() for p in created)
    stranger = icon_home / "share" / "applications" / "recap.desktop"
    stranger.write_text("[Desktop Entry]\nName=not recap's\n", encoding="utf-8")
    desktop.remove_icons(desktop.usual_places("linux"))
    assert stranger.exists()  # no marker: never deleted


def test_macos_app_bundle(icon_home):
    (app,) = desktop.install_macos(desktop.launch_command(), desktop=icon_home / "Desktop")
    with open(app / "Contents" / "Info.plist", "rb") as f:
        info = plistlib.load(f)
    assert info["CFBundleExecutable"] == "recap" and info[desktop.MARKER] is True
    script = (app / "Contents" / "MacOS" / "recap").read_text(encoding="utf-8")
    assert script.startswith("#!/bin/bash") and "recap gui" in script
    assert (app / "Contents" / "Resources" / "recap.icns").stat().st_size > 0
    assert desktop._is_ours(app)
    desktop._record([app])
    assert desktop.remove_icons() == [app] and not app.exists()


def test_windows_shortcut_script(icon_home, monkeypatch):
    seen = {}

    class Done:
        returncode, stderr = 0, ""
        stdout = "C:\\Users\\pi\\Desktop\\recap.lnk\nC:\\Users\\pi\\Start Menu\\Programs\\recap.lnk\n"

    def fake_run(cmd, **kw):
        seen["cmd"] = cmd
        return Done()

    monkeypatch.setattr(desktop.subprocess, "run", fake_run)
    monkeypatch.setattr(
        desktop, "launch_command", lambda: desktop.Launch(["C:\\pixi\\pixi.exe", "run", "a b"], icon_home)
    )
    created = desktop.install_windows(desktop.launch_command())
    script = seen["cmd"][-1]
    assert seen["cmd"][0] == "powershell" and "WScript.Shell" in script and "WindowStyle = 7" in script
    assert "$s.TargetPath = 'C:\\pixi\\pixi.exe'" in script and "'run \"a b\"'" in script
    assert "recap.ico" in script and (icon_home / "settings" / "recap.ico").is_file()
    assert len(created) == 2 and all(str(p).endswith("\\recap.lnk") for p in created)
