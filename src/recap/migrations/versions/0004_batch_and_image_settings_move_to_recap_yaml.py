"""batch and image settings move to recap.yaml

Revision ID: 0004
Revises: 0003
Created: 2026-11-25 10:30:00

recap 5.0 keeps every level of the settings in the workspace's recap.yaml (batches: and images:
sections), and a batch's note with them. Before this migration runs, Workspace copies what
level_settings and batches.note hold into recap.yaml (Workspace._fold_database_levels), and the
database is backed up as for every upgrade. The database keeps which image is in which batch.
"""

import sqlalchemy as sa
import sqlmodel  # noqa: F401
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_table("level_settings")
    with op.batch_alter_table("batches", schema=None) as batch_op:
        batch_op.drop_column("note")


def downgrade() -> None:
    with op.batch_alter_table("batches", schema=None) as batch_op:
        batch_op.add_column(sa.Column("note", sqlmodel.sql.sqltypes.AutoString(), nullable=True))
    op.create_table(
        "level_settings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("level", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("target", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("overrides", sa.JSON(), nullable=True),
        sa.Column("reason", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("updated_by", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("updated_at", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("level", "target", name="uq_level_settings_target"),
    )
