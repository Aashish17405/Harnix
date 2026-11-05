"""thinking level, preloaded skills, project memory

Revision ID: 0003
Revises: 0002
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("tasks", schema=None) as batch_op:
        batch_op.add_column(sa.Column("thinking", sa.String(length=8), nullable=True))
        batch_op.add_column(sa.Column("skills", sa.JSON(), nullable=True))
    op.create_table(
        "memories",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("scope", sa.String(length=64), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("task_id", sa.String(length=32), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_memories_scope_created", "memories", ["scope", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_memories_scope_created", table_name="memories")
    op.drop_table("memories")
    with op.batch_alter_table("tasks", schema=None) as batch_op:
        batch_op.drop_column("skills")
        batch_op.drop_column("thinking")
