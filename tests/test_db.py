"""The workspace database: version check, backup before upgrading, migrations matching the tables."""

import re
import sqlite3

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlmodel import SQLModel

from recap import __version__, db
from recap.errors import RecapError
from recap.workspace import Workspace

# The schema recap 4.0.0 created with plain SQL, before Alembic.
SCHEMA_4_0_0 = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE images (sha256 TEXT PRIMARY KEY, path TEXT, name TEXT, width INTEGER, height INTEGER,
    channels INTEGER, dtype TEXT, pixel_size REAL, size_bytes INTEGER, mtime REAL, seen_at TEXT);
CREATE INDEX images_path ON images(path);
CREATE TABLE runs (run_id TEXT PRIMARY KEY, out_dir TEXT, stamp TEXT, status TEXT, config_path TEXT,
    method TEXT, model TEXT, recap_version TEXT, log_path TEXT);
CREATE TABLE results (run_id TEXT, sha256 TEXT, image TEXT, n_nuclei INTEGER, n_infected INTEGER,
    n_nuclei_low INTEGER, n_nuclei_high INTEGER, n_infected_low INTEGER, n_infected_high INTEGER,
    qc_flags TEXT, labels_path TEXT, excluded_path TEXT, table_path TEXT, PRIMARY KEY (run_id, sha256));
CREATE TABLE counts (id INTEGER PRIMARY KEY, sha256 TEXT, image TEXT, image_name TEXT, counted_by TEXT,
    tool TEXT, source TEXT, counted_at TEXT, nuclei INTEGER, infected INTEGER, origin TEXT, points TEXT,
    UNIQUE (sha256, counted_by, counted_at));
CREATE TABLE masks (id INTEGER PRIMARY KEY, sha256 TEXT, path TEXT, from_run TEXT, edited_by TEXT,
    edited_at TEXT, n_nuclei INTEGER, note TEXT);
CREATE TABLE models (id INTEGER PRIMARY KEY, family TEXT, version INTEGER, path TEXT, parent TEXT,
    created_at TEXT, settings TEXT, train_images TEXT, val_images TEXT, metrics TEXT, note TEXT,
    UNIQUE (family, version));
CREATE TABLE tuning (id INTEGER PRIMARY KEY, session TEXT, created_at TEXT, config_path TEXT, params TEXT,
    scores TEXT, f1 REAL);
INSERT INTO meta VALUES ('schema_version', '1');
INSERT INTO counts
    (sha256, image, image_name, counted_by, tool, source, counted_at, nuclei, infected, points)
    VALUES ('abc', '/data/a.tif', 'a.tif', 'ana', 'fiji', 'new', '2026-11-23T10:00:00', 2, 1,
    '[[1.0, 2.0, 0], [3.0, 4.0, 1]]');
INSERT INTO models (family, version, path, parent, metrics, train_images, val_images, settings)
    VALUES ('stardist', 1, 'recap-data/models/stardist/v1', '2D_versatile_fluo', '{"new": {"f1": 0.9}}',
    '["abc"]', '[]', '{}');
"""


def _version(path):
    con = sqlite3.connect(path)
    try:
        return dict(con.execute("SELECT key, value FROM meta").fetchall()).get("recap_version")
    finally:
        con.close()


def _backups(ws_root):
    d = ws_root / "recap-data" / "backups"
    return sorted(d.glob("*.db")) if d.exists() else []


def test_new_workspace_is_at_head(tmp_path):
    ws = Workspace(tmp_path)
    assert _version(ws.db_path) == __version__ and not _backups(tmp_path)
    assert db.current_state(ws.engine) == (__version__, db.head_revision())


def test_tables_and_migrations_agree(tmp_path):
    """Fails when a table in recap/db.py changed without a migration (pixi run db-revision "...")."""
    ws = Workspace(tmp_path)
    with ws.engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn, opts={"compare_type": True}), SQLModel.metadata)
    assert diff == []


def test_upgrade_from_4_0_0_backs_up_first(tmp_path):
    data = tmp_path / "recap-data"
    data.mkdir()
    con = sqlite3.connect(data / "recap.db")
    con.executescript(SCHEMA_4_0_0)
    con.commit()
    con.close()
    from helpers import run_cli

    res = run_cli("status", tmp_path)  # the CLI says what it did
    assert res.exit_code == 0 and "upgraded from recap 4.0.0" in res.output
    ws = Workspace(tmp_path)
    (b,) = _backups(tmp_path)
    assert re.fullmatch(r"recap_v4\.0\.0_\d{4}-\d{2}-\d{2}_\d{6}\.db", b.name)
    old = sqlite3.connect(b)
    assert old.execute("SELECT COUNT(*) FROM counts").fetchone() == (1,)  # the backup holds the old data
    assert old.execute("SELECT name FROM sqlite_master WHERE name = 'alembic_version'").fetchone() is None
    old.close()
    assert _version(ws.db_path) == __version__
    (c,) = ws.counts()  # data written by 4.0.0 still reads, JSON included
    assert c.points == [(1.0, 2.0, 0), (3.0, 4.0, 1)] and c.counted_by == "ana"
    assert ws.models()[0]["metrics"] == {"new": {"f1": 0.9}}
    Workspace(tmp_path)  # same version again: no second backup
    assert len(_backups(tmp_path)) == 1


def test_older_version_upgrades_and_newer_is_refused(tmp_path):
    ws = Workspace(tmp_path)
    con = sqlite3.connect(ws.db_path)
    con.execute("UPDATE meta SET value = '4.0.5' WHERE key = 'recap_version'")
    con.commit()
    con.close()
    Workspace(tmp_path)
    assert [b.name.split("_")[1] for b in _backups(tmp_path)] == ["v4.0.5"]
    assert _version(ws.db_path) == __version__
    con = sqlite3.connect(ws.db_path)
    con.execute("UPDATE meta SET value = '99.0.0' WHERE key = 'recap_version'")
    con.commit()
    con.close()
    with pytest.raises(RecapError, match="newer than this recap"):
        Workspace(tmp_path)
    assert _version(ws.db_path) == "99.0.0"  # untouched
