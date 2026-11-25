"""Stand-in for Fiji running assets/fiji_count.groovy (same session-folder protocol).

Usage (as recap starts Fiji):  python fake_fiji.py --run <script> "session='<folder>'"
Answers come from $FAKE_FIJI_ANSWERS, one per request, separated by ';':
    import:x,y,c|x,y,c   points, then "Import into recap"
    skip                 "Skip image"
    cancel               "Stop counting"
    die:x,y,c|...        points saved, then Fiji is closed without answering
"""

import os
import re
import sys
import threading
import time
from pathlib import Path

session = Path(re.search(r"session='(.*)'", sys.argv[-1]).group(1))
answers = [a for a in os.environ.get("FAKE_FIJI_ANSWERS", "").split(";") if a]
alive = True


def heartbeat():
    while alive:
        (session / "alive").write_text(str(time.time()))
        time.sleep(0.2)


threading.Thread(target=heartbeat, daemon=True).start()


def write_points(f: Path, spec: str) -> None:
    rows = [p.split(",") for p in spec.split("|") if p]
    f.write_text("x,y,counter\n" + "".join(f"{x},{y},{c}\n" for x, y, c in rows))


while not (session / "stop").exists():
    pending = sorted(p for p in session.glob("request_*.properties") if not p.with_name(p.stem + ".status").exists())
    if not pending:
        time.sleep(0.1)
        continue
    req = pending[0]
    props = dict(line.split("=", 1) for line in req.read_text().splitlines() if "=" in line)
    assert Path(props["image"].replace("\\\\", "\\")).exists(), props["image"]
    kind, _, spec = (answers.pop(0) if answers else "skip").partition(":")
    points, status = req.with_name(req.stem + ".points.csv"), req.with_name(req.stem + ".status")
    if kind in ("import", "die", "cancel") and spec:
        write_points(points, spec)
    if kind == "die":
        alive = False
        os.remove(session / "alive")
        sys.exit(0)
    status.write_text(kind)
alive = False
