"""Make the pictures in docs/images/ from the lab's example image.

    xvfb-run -a -s "-screen 0 1700x1100x24" \\
        pixi run python docs/images/make_screenshots.py PATH/TO/Experiment_Hippoc_org_day_79_Acute_VimentinR_ICP8_G.tif

(or without xvfb-run on a desktop: a recap window opens and closes by itself). The image is not
in the repository and is not copied into it: the script builds a demo workspace in a temporary
folder (the image, four quarter-size crops of it as more rows for the table, a staining batch,
an image's own settings, a run, a manual total, a corrected mask), opens the app on it, captures
each tab and marks its buttons with numbered callouts (the numbers are explained in the guide
pages that show the pictures), and draws the recovery figure for docs/pipeline.md.
Only the PNG files are written to docs/images/; the temporary folder is deleted.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import tifffile

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
BATCH = "stain 2026-10-03"
ORANGE = "#ff9f1c"


# ----------------------------------------------------------------------------- the workspace
def recap(*args: str, cwd: Path) -> None:
    subprocess.run([sys.executable, "-m", "recap", *args], cwd=cwd, check=True)


def build_workspace(image: Path, root: Path) -> None:
    """The image (day79_01.tif) and its four quarters, run with the example workspace's settings."""
    root.mkdir(parents=True)
    img = tifffile.imread(image)  # (Y, X, 3) RGB export
    shutil.copy(image, root / "day79_01.tif")
    h, w = img.shape[0] // 2, img.shape[1] // 2
    for i, (y, x) in enumerate(((0, 0), (0, w), (h, 0), (h, w)), 2):
        tifffile.imwrite(root / f"day79_{i:02d}.tif", np.ascontiguousarray(img[y : y + h, x : x + w]))
    shutil.copy(REPO / "examples" / "recap.yaml", root / "start.yaml")
    recap("init", ".", "--from", "start.yaml", cwd=root)
    (root / "start.yaml").unlink()
    recap("batch", "assign", BATCH, "day79_01.tif", "day79_02.tif", "day79_03.tif", cwd=root)
    recap("run", ".", cwd=root)
    recap("import-counts", "day79_01.tif", "--nuclei", "472", "--no-infection", "--by", "ana", cwd=root)
    from recap.workspace import open_workspace

    ws = open_workspace(root)
    first = next(r for r in ws.status() if r["name"] == "day79_01.tif")
    labels = tifffile.imread(ws.image_folder(first["sha256"]) / "day79_01_labels.tif")
    ws.save_mask(first["sha256"], labels, edited_by="ana")
    # after the run: an image's own settings (so it shows "own settings" and "out of date")
    recap("settings", "set", "normalization.vrc_weight=1.3", "--image", "day79_05.tif",
          "--reason", "weak DAPI in this corner", cwd=root)  # fmt: skip


# ----------------------------------------------------------------------------- pictures
def recovery_figure(image: Path, labels_file: Path, out: Path) -> None:
    """DAPI alone, ICP8, and the combined image the model sees, on the region with the most
    nuclei (as found by recap) that are dim in DAPI but clear in ICP8; those are circled."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from skimage import measure

    from recap.config import load_config
    from recap.io import load_channels
    from recap.segment import combine

    _, cfg, _ = load_config(REPO / "examples" / "recap.yaml")
    img = load_channels(image, cfg.input.exclude_regions)
    dapi_n, vrc_n, comb = combine(img[cfg.input.dapi_channel], img[cfg.input.vrc_channel], cfg.normalization)
    labels = tifffile.imread(labels_file)
    weak = [
        (r.centroid, r.equivalent_diameter_area / 2)
        for r in measure.regionprops(labels, np.stack([dapi_n, vrc_n], -1))
        if r.intensity_mean[0] < 0.3 and r.intensity_mean[1] > 2 * r.intensity_mean[0]
    ]
    half, best = 130, None
    for (cy, cx), _ in weak:  # the window (centred on one of them) holding the most of them
        cy, cx = int(np.clip(cy, half, dapi_n.shape[0] - half)), int(np.clip(cx, half, dapi_n.shape[1] - half))
        n = sum(abs(y - cy) < half - 10 and abs(x - cx) < half - 10 for (y, x), _ in weak)
        if best is None or n > best[0]:
            best = (n, cy, cx)
    _, cy, cx = best
    sl = (slice(cy - half, cy + half), slice(cx - half, cx + half))
    fig, axes = plt.subplots(1, 3, figsize=(10.5, 4.3), dpi=110)
    panels = (
        (dapi_n, "DAPI (scaled)"),
        (vrc_n, "ICP8 (scaled with the ICP8 range)"),
        (comb, "combined = max(DAPI, weight × ICP8)"),
    )
    for ax, (a, title) in zip(axes, panels):
        ax.imshow(a[sl], cmap="gray", vmin=0, vmax=1)
        for (y, x), r in weak:
            if abs(y - cy) < half and abs(x - cx) < half:
                ax.add_patch(plt.Circle((x - cx + half, y - cy + half), r + 5, fill=False, color=ORANGE, lw=1.6))
        ax.set_title(title, fontsize=10)
        ax.axis("off")
    fig.suptitle("Recovery: circled nuclei are dim in DAPI but clear in ICP8, "
                 "so they are whole in the combined image the model sees", fontsize=11)  # fmt: skip
    fig.tight_layout(rect=(0, 0, 1, 0.93))
    fig.savefig(out, facecolor="white")
    plt.close(fig)
    print("wrote", out.name)


class Shooter:
    """Captures the recap window (or one of its dialogs) and draws numbered callouts on it."""

    def __init__(self, win, out: Path):
        self.win, self.out = win, out

    def wait(self, ms: int = 600) -> None:
        from qtpy.QtTest import QTest

        QTest.qWait(ms)

    def button(self, text: str, within=None):
        from qtpy.QtWidgets import QAbstractButton

        for b in (within or self.win).findChildren(QAbstractButton):
            if b.text().replace("&", "") == text and b.isVisible():
                return b
        raise LookupError(f"no visible button {text!r}")

    def shoot(self, name: str, marks: list, region=None, top=None, trim: bool = False) -> None:
        """marks, in callout order 1, 2, ...: a widget, a tuple of widgets (one box around them),
        or (widget(s), side) with side "left" (default), "right" or "above" for the number.
        region: the widget to picture (default: top, the whole window); trim: cut the picture
        below the lowest box."""
        from qtpy.QtCore import QPoint, QRect, Qt
        from qtpy.QtGui import QColor, QFont, QPainter, QPen, QPixmap

        top = top or self.win
        base = region or top
        self.wait()
        origin = base.mapToGlobal(QPoint(0, 0))
        shot = base.screen().grabWindow(0, origin.x(), origin.y(), base.width(), base.height())
        pad = 30  # a margin, so the numbers can sit outside the boxes
        pix = QPixmap(shot.width() + 2 * pad, shot.height() + 2 * pad)
        pix.fill(QColor("white"))
        p = QPainter(pix)
        p.drawPixmap(pad, pad, shot)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        font = QFont()
        font.setBold(True)
        font.setPixelSize(15)
        p.setFont(font)
        lowest = 0
        for n, mark in enumerate(marks, 1):
            side = "left"
            if isinstance(mark, tuple) and mark and isinstance(mark[-1], str):
                mark, side = mark[0], mark[-1]
            rect = QRect()
            for w in mark if isinstance(mark, tuple) else (mark,):
                at = w.mapToGlobal(QPoint(0, 0)) - origin
                rect = rect.united(QRect(at, w.size()))
            rect = rect.translated(pad, pad).adjusted(-3, -3, 3, 3)
            p.setPen(QPen(QColor(ORANGE), 3))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(rect, 6, 6)
            lowest = max(lowest, rect.bottom())
            r = 13
            c = {
                "left": QPoint(rect.left() - r - 2, rect.center().y()),
                "right": QPoint(rect.right() + r + 2, rect.center().y()),
                "above": QPoint(rect.left() + r, rect.top() - r - 2),
            }[side]
            p.setPen(QPen(QColor("white"), 2))
            p.setBrush(QColor(ORANGE))
            p.drawEllipse(c, r, r)
            p.setPen(QColor("black"))
            p.drawText(QRect(c.x() - r, c.y() - r, 2 * r, 2 * r), Qt.AlignmentFlag.AlignCenter, str(n))
        p.end()
        if trim:
            pix = pix.copy(0, 0, pix.width(), min(pix.height(), lowest + pad))
        pix.save(str(self.out / name))
        print("wrote", name)


def capture_all(root: Path, out: Path) -> None:
    import pandas as pd
    from qtpy.QtWidgets import QMessageBox

    from recap.gui import common as C
    from recap.gui.window import open_window, start

    QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Discard)  # no prompts
    app, win = open_window(root)
    win.resize(1500, 930)
    win.move(0, 0)
    start(win, root)
    s = Shooter(win, out)
    s.wait(2500)
    rows = {r["name"]: r["sha256"] for r in C.STATE.workspace.status()}

    def label(text, within):
        from qtpy.QtWidgets import QLabel

        return next(w for w in within.findChildren(QLabel) if w.text() == text and w.isVisible())

    def look(sha, zoom=None, center=None):
        win.images.open_image(sha)
        s.wait(1500)
        if zoom:
            win.viewer.camera.zoom = zoom
        if center:
            win.viewer.camera.center = center
        s.wait(800)

    # 1. the window: top bar, tabs, Show bar
    win.tabs.setCurrentIndex(0)
    look(rows["day79_01.tif"])
    s.shoot("window.png", [
        s.button("Add images"), (s.button("Tuning and training"), "above"),
        (s.button("◀"), win.image_box, s.button("▶")), win.tabs.tabBar(), win.view_bar,
    ])  # fmt: skip
    # 2. the Images tab, wider so every column shows, with an out-of-date image open (the banner shows)
    win.split.setSizes([560, 940])
    look(rows["day79_05.tif"])
    p = win.images
    s.shoot("images_tab.png", [
        (label("Workspace:", p), p.ws_combo, s.button("Open...", p), s.button("New...", p)), p.filter, p.table,
        (s.button("Run selected", p), s.button("Run out-of-date", p), s.button("Run all", p)),
        (s.button("Open image", p), s.button("Set batch...", p), s.button("Summary", p)),
        (s.button("More", p), "right"), p.banner,
    ], region=win.split.widget(1), trim=True)  # fmt: skip
    win.split.setSizes([1000, 500])
    # 3. Count: some clicks on recap's nuclei, zoomed in
    look(rows["day79_01.tif"], zoom=2.0, center=(520, 700))
    win.tabs.setCurrentIndex(1)
    s.wait(800)
    nuc = pd.read_csv(next((root / "processed").rglob("day79_01_nuclei.csv")))
    for _, r in nuc[(abs(nuc.y - 520) < 140) & (abs(nuc.x - 700) < 230)].head(30).iterrows():
        win.count.click(r.y, r.x, shift=bool(r.infected))
    c = win.count
    s.shoot("count_tab.png", [
        c.totals, (s.button("Undo", c), s.button("Clear", c), s.button("Save count", c)),
        s.button("Count this image in Fiji instead", c),
    ])  # fmt: skip
    c.edit.points, c.saved_points = [], []
    c.redraw()
    # 4. Fix outlines
    win.tabs.setCurrentIndex(2)
    s.wait(800)
    o = win.outlines
    s.shoot("outlines_tab.png", [
        tuple(o.buttons.values()), o.how, (s.button("Undo", o), s.button("Save corrected mask", o)),
    ])  # fmt: skip
    # 5. Settings (Guided), with the preview
    win.tabs.setCurrentIndex(3)
    s.wait(8000)  # the preview's first segmentation
    g = win.guided
    s.shoot("settings_tab.png", [
        g.scroll, g.readout, (label("Save to:", g), g.level, s.button("Save", g), s.button("Undo changes", g)),
    ])  # fmt: skip
    # 6. the Tuning and training window: both tabs
    from qtpy.QtWidgets import QTabWidget

    d = win.show_jobs()
    d.move(40, 40)
    s.wait(1500)
    tabs = d.findChild(QTabWidget)
    tune = tabs.widget(0)
    s.shoot("tuning_window.png", [
        tabs.tabBar(), (label("Trials", d), tune.trials), s.button("Run tuning", d),
        (label("Use the best settings for:", d), tune.target, s.button("Apply", d)),
    ], top=d)  # fmt: skip
    tabs.setCurrentIndex(1)
    s.wait(1200)
    s.shoot("training_window.png", [
        s.button("Train new version", d),
        (s.button("Compare all versions", d), s.button("Use selected version", d), s.button("Use standard model", d)),
        (s.button("Export selected version...", d), s.button("Import a model...", d)),
    ], top=d)  # fmt: skip
    d.close()
    win.close()
    app.quit()


def main() -> None:
    if len(sys.argv) != 2 or not Path(sys.argv[1]).is_file():
        sys.exit(__doc__)
    image = Path(sys.argv[1]).resolve()
    tmp = Path(tempfile.mkdtemp(prefix="recap-docs-"))
    os.environ["RECAP_CONFIG_DIR"] = str(tmp / "user-settings")  # leave your own recap settings alone
    os.environ["USER"] = os.environ["LOGNAME"] = os.environ["USERNAME"] = "ana"  # the name shown in the app
    root = tmp / "my-experiment"
    try:
        build_workspace(image, root)
        recovery_figure(image, next((root / "processed").rglob("day79_01_labels.tif")), HERE / "recovery.png")
        capture_all(root, HERE)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)  # the copies of the image go with it


if __name__ == "__main__":
    main()
