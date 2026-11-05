"""chat sessions: turns that share a conversation, workspace and checkpoints

Revision ID: 0005
Revises: 0004
"""

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "chat_sessions",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("repo_path", sa.Text(), nullable=False),
        sa.Column("workspace_path", sa.Text(), nullable=True),
        sa.Column("applied_ref", sa.String(length=64), nullable=True),
        sa.Column("archived", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_chat_sessions_updated_at", "chat_sessions", ["updated_at"])
    with op.batch_alter_table("tasks", schema=None) as batch_op:
        batch_op.add_column(sa.Column("session_id", sa.String(length=32), nullable=True))
        batch_op.add_column(sa.Column("turn", sa.Integer(), nullable=False, server_default="1"))
        batch_op.add_column(sa.Column("discarded", sa.Boolean(), nullable=False, server_default=sa.false()))
        batch_op.add_column(sa.Column("base_ref", sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column("checkpoint", sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column("messages", sa.JSON(), nullable=True))
        batch_op.create_index("ix_tasks_session_id", ["session_id"])
    # every task that existed before becomes the only turn of its own session, so history keeps working
    bind = op.get_bind()
    rows = bind.execute(sa.text("SELECT id, title, repo_path, workspace_path, created_at FROM tasks")).fetchall()
    for tid, title, repo_path, workspace_path, created_at in rows:
        sid = uuid.uuid4().hex[:16]
        bind.execute(
            sa.text(
                "INSERT INTO chat_sessions (id, title, repo_path, workspace_path, archived, created_at, updated_at) "
                "VALUES (:id, :title, :repo, :ws, :arch, :c, :c)"
            ),
            {"id": sid, "title": title, "repo": repo_path, "ws": workspace_path, "arch": False, "c": created_at},
        )
        bind.execute(sa.text("UPDATE tasks SET session_id = :sid WHERE id = :tid"), {"sid": sid, "tid": tid})


def downgrade() -> None:
    with op.batch_alter_table("tasks", schema=None) as batch_op:
        batch_op.drop_index("ix_tasks_session_id")
        batch_op.drop_column("messages")
        batch_op.drop_column("checkpoint")
        batch_op.drop_column("base_ref")
        batch_op.drop_column("discarded")
        batch_op.drop_column("turn")
        batch_op.drop_column("session_id")
    op.drop_index("ix_chat_sessions_updated_at", table_name="chat_sessions")
    op.drop_table("chat_sessions")
