"""Counting nuclei by hand in Fiji, with the counts coming straight back into recap.

recap starts Fiji once, as its own program, with assets/fiji_count.groovy. The two talk through a
session folder (see the script's header): recap asks for an image to be counted, Fiji shows it
with the Multi-point tool and a "Done counting" panel, then asks "Import these counts into
recap?". Points are saved every second, so if Fiji is closed mid-count recap still has them and
asks (ask_import) whether to keep them.

FijiCounter is a counter for the spot check: counter(image_path, rgb, k, n) returns
("done", points, nuclei, infected) | ("skip",) | ("cancel",).
"""

from __future__ import annotations

import datetime as dt
import logging
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

import numpy as np
import tifffile

from recap.errors import RecapError
from recap.fiji import NOT_FOUND_HINT, launcher

log = logging.getLogger(__name__)

STALE_SECONDS = 15  # no heartbeat for this long while Fiji's process is gone = Fiji was closed


@dataclass
class FijiCount:
    status: str  # import | skip | cancel | closed (Fiji closed before a decision)
    points: list[tuple[float, float, int]]

    @property
    def nuclei(self) -> int:
        return len(self.points)

    @property
    def infected(self) -> int:
        return sum(1 for *_, c in self.points if c == 1)

    def summary(self, count_infected: bool = True) -> str:
        if not count_infected:
            return f"{self.nuclei} nuclei"
        return f"{self.nuclei} nuclei (VRC-: {self.nuclei - self.infected}, VRC+: {self.infected})"


def read_points(path: Path) -> list[tuple[float, float, int]]:
    if not path.is_file():
        return []
    rows = path.read_text(encoding="utf-8").splitlines()[1:]
    return [(float(x), float(y), int(c)) for x, y, c in (r.split(",") for r in rows if r.strip())]


class FijiSession:
    """One running Fiji that counts images on request. Use as a context manager, or call close()."""

    def __init__(self, fiji_folder: Path | None, session_dir: Path, command: list[str] | None = None):
        self.fiji_folder = fiji_folder  # checked when Fiji is first needed, not before
        self.command = command  # tests replace Fiji with a stand-in program here
        self.dir = Path(session_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.proc: subprocess.Popen | None = None
        self._k = 0

    def _start(self) -> None:
        if self.command is None:
            if self.fiji_folder is None:
                raise RecapError("Counting needs Fiji, which was not found. " + NOT_FOUND_HINT)
            exe = launcher(self.fiji_folder)
            if exe is None:
                raise RecapError(f"No Fiji launcher for this system in {self.fiji_folder}")
            self.command = [str(exe)]
        script = self.dir / "fiji_count.groovy"
        script.write_bytes((resources.files("recap") / "assets" / "fiji_count.groovy").read_bytes())
        (self.dir / "stop").unlink(missing_ok=True)
        arg = f"session='{self.dir.resolve().as_posix()}'"  # forward slashes work on Windows too
        log.info("starting Fiji: %s", " ".join(self.command))
        self.proc = subprocess.Popen([*self.command, "--run", script.resolve().as_posix(), arg])

    def _fiji_gone(self) -> bool:
        alive = self.dir / "alive"
        stale = not alive.exists() or time.time() - alive.stat().st_mtime > STALE_SECONDS
        exited = self.proc is None or self.proc.poll() is not None
        return exited and stale  # a Fiji that handed us to an already-open Fiji exits early but stays alive

    def count(
        self, image: Path, title: str, position: str = "", count_infected: bool = True, poll: float = 0.5
    ) -> FijiCount:
        """Show one image in Fiji and wait for the person's decision."""
        if self.proc is None:
            self._start()
        self._k += 1
        base = self.dir / f"request_{self._k:04d}"
        for suffix in (".status", ".points.csv"):
            Path(f"{base}{suffix}").unlink(missing_ok=True)
        props = {
            "image": Path(image).resolve().as_posix(),
            "title": title,
            "position": position or title,
            "count_infected": "true" if count_infected else "false",
        }
        text = "".join(f"{k}={_escape(v)}\n" for k, v in props.items())
        Path(f"{base}.properties").write_text(text, encoding="utf-8")
        status_file, points_file = Path(f"{base}.status"), Path(f"{base}.points.csv")
        started = time.time()
        while True:
            status = status_file.read_text(encoding="utf-8").split("\n", 1)[0].strip() if status_file.exists() else ""
            if status:  # empty = Fiji is still writing it
                return FijiCount(status, read_points(points_file))
            if time.time() - started > 5 and self._fiji_gone():
                self.proc = None  # restart on the next request
                return FijiCount("closed", read_points(points_file))
            time.sleep(poll)

    def close(self) -> None:
        if self.proc is not None:  # only a Fiji we started needs telling
            (self.dir / "stop").write_text("stop", encoding="utf-8")

    def __enter__(self) -> FijiSession:
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def _escape(v: str) -> str:
    """Java .properties value: backslashes and line breaks escaped."""
    return v.replace("\\", "\\\\").replace("\n", "\\n")


class FijiCounter:
    """Spot-check counter that counts in Fiji. ask_import(summary) decides about counts left in a
    Fiji that was closed before "Import" (e.g. a terminal yes/no question); None = keep them."""

    tool = "fiji"

    def __init__(
        self,
        session: FijiSession,
        count_infected: bool = True,
        ask_import: Callable[[str], bool] | None = None,
    ):
        self.session, self.count_infected, self.ask_import = session, count_infected, ask_import

    def __call__(self, image_path: Path, rgb: np.ndarray, k: int, n: int):
        view = self.session.dir / f"{Path(image_path).stem}_for_counting.tif"
        tifffile.imwrite(view, rgb, photometric="rgb")  # the normalised DAPI/VRC view, same pixel grid
        res = self.session.count(view, Path(image_path).name, f"image {k} of {n}", self.count_infected)
        return answer(res, self.count_infected, self.ask_import)


def answer(res: FijiCount, count_infected: bool, ask_import: Callable[[str], bool] | None):
    """Turn a Fiji result into a spot-check answer, asking about counts from a closed Fiji."""
    if res.status == "closed" and res.points:
        keep = ask_import(f"Fiji was closed with {res.summary(count_infected)} not imported. Import them?") \
            if ask_import else True  # fmt: skip
        res = FijiCount("import" if keep else "skip", res.points)
    if res.status == "import":
        return ("done", res.points, res.nuclei, res.infected if count_infected else None)
    if res.status in ("cancel", "closed"):
        return ("cancel",)
    return ("skip",)


def session_dir(base: Path) -> Path:
    return Path(base) / f"session_{dt.datetime.now().strftime('%Y%m%d_%H%M%S')}"
