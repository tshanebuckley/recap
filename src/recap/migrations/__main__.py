"""Write a new migration from changes to the tables in recap/db.py.

    pixi run db-revision "add a notes column to masks"

Builds a scratch database at the current schema, compares it with the classes in recap/db.py and
writes the difference to versions/. Review the file before committing it.
"""

import sys
import tempfile
from pathlib import Path

from alembic import command
from sqlmodel import create_engine

from recap.db import alembic_config


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit('usage: python -m recap.migrations "what changed"')
    with tempfile.TemporaryDirectory() as d:
        engine = create_engine(f"sqlite:///{Path(d) / 'scratch.db'}")
        with engine.begin() as conn:
            cfg = alembic_config(conn)
            command.upgrade(cfg, "head")
            n = len(list((Path(cfg.get_main_option("script_location")) / "versions").glob("[0-9]*.py")))
            command.revision(cfg, message=sys.argv[1], autogenerate=True, rev_id=f"{n + 1:04d}")


if __name__ == "__main__":
    main()
