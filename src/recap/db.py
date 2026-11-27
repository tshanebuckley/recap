"""The workspace database: SQLModel tables, and opening a database at the right version.

Every time recap opens a workspace (any command that uses one, and the app), open_database()
compares the recap version that last wrote the database with this program's version:

- same version (and schema up to date): nothing to do;
- older: copy the database to recap-data/backups/recap_v<old version>_<date>_<time>.db, then
  bring the schema up to date with the Alembic migrations in recap/migrations, and record the
  new version;
- newer: refuse, with a message to update recap (an older recap must not write a newer database).

Changing a table: edit the classes below, then `pixi run db-revision "what changed"` writes a
migration into src/recap/migrations/versions/; review it, and tests/test_db.py checks that the
migrations and these classes agree. SQLite cannot alter most columns in place, so migrations
run in Alembic's batch mode (copy the table, change it, swap it in).
"""

from __future__ import annotations

import datetime as dt
import logging
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from packaging.version import Version
from sqlalchemy import JSON, Column, Index, UniqueConstraint, inspect
from sqlalchemy.engine import Engine
from sqlmodel import Field, Session, SQLModel, create_engine, select

from recap.errors import RecapError

log = logging.getLogger(__name__)

MIGRATIONS = Path(__file__).with_name("migrations")
BASELINE = "0001"  # the schema recap 4.0.0 created without Alembic
VERSION_BEFORE_ALEMBIC = "4.0.0"


# ---- tables (as in recap 4.0.0) ----------------------------------------------------------------
class Meta(SQLModel, table=True):
    __tablename__ = "meta"
    key: str = Field(primary_key=True)
    value: str | None = None


class ImageRow(SQLModel, table=True):
    __tablename__ = "images"
    __table_args__ = (Index("images_path", "path"),)
    sha256: str = Field(primary_key=True)
    path: str | None = None  # relative to the workspace folder when inside it
    name: str | None = None
    width: int | None = None
    height: int | None = None
    channels: int | None = None
    dtype: str | None = None
    pixel_size: float | None = None
    size_bytes: int | None = None
    mtime: float | None = None
    seen_at: str | None = None


class RunRow(SQLModel, table=True):
    __tablename__ = "runs"
    run_id: str = Field(primary_key=True)
    out_dir: str | None = None
    stamp: str | None = None
    status: str | None = None
    config_path: str | None = None
    method: str | None = None
    model: str | None = None
    recap_version: str | None = None
    log_path: str | None = None


class ResultRow(SQLModel, table=True):
    __tablename__ = "results"
    run_id: str = Field(primary_key=True)
    sha256: str = Field(primary_key=True)
    image: str | None = None
    n_nuclei: int | None = None
    n_infected: int | None = None
    n_nuclei_low: int | None = None
    n_nuclei_high: int | None = None
    n_infected_low: int | None = None
    n_infected_high: int | None = None
    qc_flags: str | None = None
    labels_path: str | None = None
    excluded_path: str | None = None
    table_path: str | None = None
    settings_hash: str | None = None  # levels.settings_hash of the settings used (out-of-date check)


class CountRow(SQLModel, table=True):
    __tablename__ = "counts"
    __table_args__ = (UniqueConstraint("sha256", "counted_by", "counted_at", name="uq_counts_identity"),)
    id: int | None = Field(default=None, primary_key=True)
    sha256: str | None = None
    image: str | None = None
    image_name: str | None = None
    counted_by: str | None = None  # "" when unknown, so the unique constraint applies
    tool: str | None = None
    source: str | None = None
    counted_at: str | None = None
    nuclei: int | None = None
    infected: int | None = None
    origin: str | None = None
    points: list | None = Field(default=None, sa_column=Column(JSON))  # [[x, y, counter], ...]


class MaskRow(SQLModel, table=True):
    __tablename__ = "masks"
    id: int | None = Field(default=None, primary_key=True)
    sha256: str | None = None
    path: str | None = None
    from_run: str | None = None
    edited_by: str | None = None
    edited_at: str | None = None
    n_nuclei: int | None = None
    note: str | None = None


class ModelRow(SQLModel, table=True):
    __tablename__ = "models"
    __table_args__ = (UniqueConstraint("family", "version", name="uq_models_version"),)
    id: int | None = Field(default=None, primary_key=True)
    family: str | None = None
    version: int | None = None
    path: str | None = None
    parent: str | None = None
    created_at: str | None = None
    settings: dict | None = Field(default=None, sa_column=Column(JSON))
    train_images: list | None = Field(default=None, sa_column=Column(JSON))
    val_images: list | None = Field(default=None, sa_column=Column(JSON))
    metrics: dict | None = Field(default=None, sa_column=Column(JSON))
    note: str | None = None


class TuningRow(SQLModel, table=True):
    __tablename__ = "tuning"
    id: int | None = Field(default=None, primary_key=True)
    session: str | None = None
    created_at: str | None = None
    config_path: str | None = None
    params: dict | None = Field(default=None, sa_column=Column(JSON))
    scores: dict | None = Field(default=None, sa_column=Column(JSON))
    f1: float | None = None


class BatchRow(SQLModel, table=True):
    """A staining batch: images stained together, sharing settings."""

    __tablename__ = "batches"
    __table_args__ = (UniqueConstraint("name", name="uq_batches_name"),)
    id: int | None = Field(default=None, primary_key=True)
    name: str  # its settings and note: recap.yaml, batches: (recap 5.0)
    created_at: str | None = None


class ImageBatchRow(SQLModel, table=True):
    """Which batch an image belongs to (at most one)."""

    __tablename__ = "image_batches"
    sha256: str = Field(primary_key=True)
    batch_id: int = Field(foreign_key="batches.id")


# ---- versions and migrations -------------------------------------------------------------------
def alembic_config(connection=None):
    from alembic.config import Config

    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS))
    cfg.attributes["connection"] = connection
    return cfg


def head_revision() -> str:
    from alembic.script import ScriptDirectory

    return ScriptDirectory.from_config(alembic_config()).get_current_head()


def current_state(engine: Engine) -> tuple[str | None, str | None]:
    """(recap version that last wrote the database, Alembic revision); (None, None) for a new one.
    A database made by recap 4.0.0 (before Alembic) reports ("4.0.0", None)."""
    tables = set(inspect(engine).get_table_names())
    if "images" not in tables:
        return None, None
    with Session(engine) as s:
        row = s.get(Meta, "recap_version")
    revision = None
    if "alembic_version" in tables:
        with engine.connect() as c:
            revision = c.exec_driver_sql("SELECT version_num FROM alembic_version").scalar()
    return (row.value if row else VERSION_BEFORE_ALEMBIC), revision


def backup(db_path: Path, version: str) -> Path:
    """Copy the database (consistently, even if open elsewhere) to backups/recap_v<version>_<date>_<time>.db."""
    out_dir = db_path.parent / "backups"
    out_dir.mkdir(exist_ok=True)
    out = out_dir / f"recap_v{version}_{dt.datetime.now():%Y-%m-%d_%H%M%S}.db"
    src, dst = sqlite3.connect(db_path), sqlite3.connect(out)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    return out


@dataclass
class Upgrade:
    """What open_database did to an older database."""

    from_version: str
    to_version: str
    backup: Path

    def message(self) -> str:
        return (f"Workspace database upgraded from recap {self.from_version} to {self.to_version}; "
                f"the old one is saved as {self.backup}")  # fmt: skip


def open_database(db_path: Path, app_version: str) -> tuple[Engine, Upgrade | None]:
    """Open (or create) a workspace database, backing it up and upgrading it if it is older."""
    from alembic import command

    engine = create_engine(f"sqlite:///{db_path}", connect_args={"timeout": 30})
    db_version, revision = current_state(engine)
    head = head_revision()
    if db_version is not None and Version(db_version) > Version(app_version):
        raise RecapError(
            f"{db_path} was last used by recap {db_version}, newer than this recap ({app_version}). "
            "Update recap to open this workspace."
        )
    if db_version == app_version and revision == head:
        return engine, None
    saved = None
    if db_version is not None:
        saved = backup(db_path, db_version)
        log.info("workspace database from recap %s backed up to %s", db_version, saved)
    with engine.begin() as conn:
        cfg = alembic_config(conn)
        if db_version is not None and revision is None:
            command.stamp(cfg, BASELINE)  # recap 4.0.0 made these tables itself
        command.upgrade(cfg, "head")
    with Session(engine) as s:
        s.merge(Meta(key="recap_version", value=app_version))
        s.merge(Meta(key="updated_at", value=dt.datetime.now().isoformat(timespec="seconds")))
        s.commit()
    if saved is None:
        return engine, None
    log.info("workspace database upgraded from recap %s to %s", db_version, app_version)
    return engine, Upgrade(db_version, app_version, saved)


def meta(engine: Engine) -> dict[str, Any]:
    with Session(engine) as s:
        return {m.key: m.value for m in s.exec(select(Meta))}
