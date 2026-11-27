"""StarDist (TensorFlow) in a helper process, so the app's own process never loads TensorFlow.

Why: in the app, TensorFlow loaded next to Qt and OpenGL caused three problems. On Linux without a
GPU, Mesa's fast llvmpipe renderer crashes once TensorFlow (which also carries LLVM) is loaded, so
recap had to force the slow softpipe renderer (seconds per redraw, DEF-006). The program could
stall while exiting after TensorFlow had run. And on Windows the window froze ("Not Responding",
0% CPU) right after the first segmentation of a session started in a background thread, the
pattern of a deadlock while TensorFlow's libraries load next to the window's thread.

How: the app calls `use_worker()` once; from then on `recap.segment.stardist_labels` sends the
image to `python -m recap.stardist_worker` (started on first use, kept running, restarted if it
stops) and gets the labels back. Training from the app runs there too (`Worker.call("train", ...)`),
with its progress messages passed back. The command line keeps running StarDist in its own process,
as before (no window, none of these problems).

Protocol: pickled frames on the worker's stdin / stdout, each a length (8 bytes) and the pickle. A
request is {"op": ..., ...}; the worker answers {"progress": text} frames, then {"ok": result} or
{"error": text, "traceback": text}. stdout is kept for the frames: everything printed (StarDist and
TensorFlow print) goes to stderr, which is the log file recap_stardist_worker.log in the temp folder.
"""

from __future__ import annotations

import logging
import os
import pickle
import struct
import subprocess
import sys
import tempfile
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

LOG_NAME = "recap_stardist_worker.log"


def _send(stream, obj: Any) -> None:
    data = pickle.dumps(obj, protocol=pickle.HIGHEST_PROTOCOL)
    stream.write(struct.pack("<Q", len(data)) + data)
    stream.flush()


def _recv(stream) -> Any:
    head = stream.read(8)
    if len(head) < 8:
        raise EOFError("the StarDist helper process stopped")
    (n,) = struct.unpack("<Q", head)
    data = stream.read(n)
    if len(data) < n:
        raise EOFError("the StarDist helper process stopped")
    return pickle.loads(data)  # noqa: S301 (our own child process, same program)


class WorkerError(RuntimeError):
    """The helper process stopped, or the work it was given failed."""


class Worker:
    """The client: one helper process, used by one request at a time."""

    def __init__(self) -> None:
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self.log_path = Path(tempfile.gettempdir()) / LOG_NAME

    def _start(self) -> subprocess.Popen:
        if self._proc is not None and self._proc.poll() is None:
            return self._proc
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
        err = self.log_path.open("ab")
        env = {**os.environ, "PYTHONUNBUFFERED": "1"}
        env.pop("GALLIUM_DRIVER", None)  # (the helper draws nothing)
        self._proc = subprocess.Popen(
            [sys.executable, "-m", "recap.stardist_worker"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=err, env=env, creationflags=flags,
        )  # fmt: skip
        err.close()
        log.info("StarDist helper process started (pid %s; log %s)", self._proc.pid, self.log_path)
        return self._proc

    def call(self, op: str, progress: Callable[[str], None] | None = None, **kwargs) -> Any:
        """Run op in the helper process and return its result (raises WorkerError if it failed)."""
        with self._lock:
            proc = self._start()
            try:
                _send(proc.stdin, {"op": op, **kwargs})
                while True:
                    msg = _recv(proc.stdout)
                    if "progress" in msg:
                        if progress is not None:
                            progress(msg["progress"])
                        continue
                    break
            except (EOFError, OSError, pickle.UnpicklingError) as e:
                self._kill()
                raise WorkerError(f"{e} (details: {self.log_path}); it is started again on the next try") from e
        if "error" in msg:
            log.debug("helper traceback:\n%s", msg.get("traceback"))
            raise WorkerError(msg["error"])
        return msg["ok"]

    def predict(self, img, model: str, scale: float, prob_thresh, nms_thresh):
        return self.call("predict", img=img, model=model, scale=scale, prob_thresh=prob_thresh,
                         nms_thresh=nms_thresh)  # fmt: skip

    def _kill(self) -> None:
        if self._proc is not None:
            try:
                self._proc.kill()
                self._proc.wait(timeout=5)
            except (OSError, subprocess.TimeoutExpired):
                pass
        self._proc = None

    def stop(self) -> None:
        """End the helper process (closing its input ends it; killed if it does not end)."""
        if self._proc is None:
            return
        try:
            self._proc.stdin.close()
            self._proc.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            self._kill()
        self._proc = None


_WORKER: Worker | None = None


def use_worker(on: bool = True) -> Worker | None:
    """From now on, run StarDist in the helper process (the app calls this at start)."""
    global _WORKER
    if on and _WORKER is None:
        _WORKER = Worker()
    elif not on and _WORKER is not None:
        _WORKER.stop()
        _WORKER = None
    return _WORKER


def worker() -> Worker | None:
    return _WORKER


# ---- the helper process ---------------------------------------------------------------------------------
def _predict(req: dict) -> Any:
    from csbdeep.utils import normalize

    from recap.segment import stardist_model

    model = stardist_model(req["model"])
    lab, _ = model.predict_instances(normalize(req["img"], 1, 99.8), scale=req["scale"],
                                     prob_thresh=req["prob_thresh"], nms_thresh=req["nms_thresh"])  # fmt: skip
    import numpy as np

    return lab.astype(np.int32)


def _train(req: dict, progress: Callable[[str], None]) -> Any:
    from recap.train import train
    from recap.workspace import Workspace

    res = train(Workspace(Path(req["workspace"])), req["family"], req.get("start"), req["epochs"],
                note=req.get("note"), progress=progress)  # fmt: skip
    return res


def serve(inp=None, out=None) -> None:
    """The helper's loop: answer requests until its input closes."""
    import traceback

    if inp is None:
        out = os.fdopen(os.dup(sys.stdout.fileno()), "wb")  # the frames' own copy of stdout
        os.dup2(sys.stderr.fileno(), sys.stdout.fileno())  # anything printed goes to the log instead
        sys.stdout = sys.stderr
        inp = sys.stdin.buffer
    logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(asctime)s worker %(message)s")
    while True:
        try:
            req = _recv(inp)
        except EOFError:
            return
        try:
            op = req.get("op")
            if op == "ping":
                result: Any = {"pid": os.getpid(), "tensorflow_loaded": "tensorflow" in sys.modules}
            elif op == "predict":
                result = _predict(req)
            elif op == "train":
                result = _train(req, lambda text: _send(out, {"progress": text}))
            elif op == "fail":  # (tests)
                raise ValueError(req.get("message", "failed on purpose"))
            elif op == "exit":  # (tests: the process stops without answering)
                os._exit(3)
            else:
                raise ValueError(f"unknown request {op!r}")
            _send(out, {"ok": result})
        except Exception as e:  # noqa: BLE001 (sent back to the app, which shows it)
            _send(out, {"error": f"{type(e).__name__}: {e}", "traceback": traceback.format_exc()})


if __name__ == "__main__":
    serve()
