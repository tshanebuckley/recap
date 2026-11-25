"""settings fingerprint of each result (out-of-date check)

Revision ID: 0003
Revises: 0002
Created: 2026-11-24 20:30:00

The workspace-level settings that 4.2.0 kept in level_settings move into the workspace's
recap.yaml when the workspace is opened (Workspace._fold_workspace_level): a migration
cannot rely on that file existing yet.
"""

import sqlalchemy as sa
import sqlmodel  # noqa: F401
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("results", schema=None) as batch_op:
        batch_op.add_column(sa.Column("settings_hash", sqlmodel.sql.sqltypes.AutoString(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("results", schema=None) as batch_op:
        batch_op.drop_column("settings_hash")
