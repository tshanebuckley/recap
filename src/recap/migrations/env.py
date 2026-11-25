"""Alembic environment: recap runs migrations itself (recap.db.open_database), on its own connection."""

from alembic import context
from sqlmodel import SQLModel

import recap.db  # noqa: F401  (registers the tables)

connection = context.config.attributes["connection"]
context.configure(
    connection=connection,
    target_metadata=SQLModel.metadata,
    render_as_batch=True,  # SQLite: change tables by copy-and-swap
    compare_type=True,
)
with context.begin_transaction():
    context.run_migrations()
