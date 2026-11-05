"""knowledge base: sources, versions, chunks, edges, audit, context bundles; tasks.knowledge_project

Revision ID: 0006
Revises: 0005
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "knowledge_sources",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("project", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("external_id", sa.String(length=300), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("uri", sa.Text(), nullable=True),
        sa.Column("origin", sa.String(length=16), nullable=False),
        sa.Column("current_version", sa.Integer(), nullable=False),
        sa.Column("ingestion_state", sa.String(length=12), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project", "kind", "external_id", name="uq_knowledge_source_key"),
    )
    op.create_index("ix_knowledge_sources_project", "knowledge_sources", ["project"])
    op.create_table(
        "knowledge_versions",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("source_id", sa.String(length=32), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_id", "version", name="uq_knowledge_version"),
    )
    op.create_index("ix_knowledge_versions_source_id", "knowledge_versions", ["source_id"])
    op.create_table(
        "knowledge_chunks",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("source_id", sa.String(length=32), nullable=False),
        sa.Column("version_id", sa.String(length=32), nullable=False),
        sa.Column("project", sa.String(length=64), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("heading", sa.String(length=300), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("embedding", sa.JSON(), nullable=True),
        sa.Column("embedding_model", sa.String(length=80), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_knowledge_chunks_source_id", "knowledge_chunks", ["source_id"])
    op.create_index("ix_knowledge_chunks_project", "knowledge_chunks", ["project"])
    op.create_table(
        "knowledge_edges",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("project", sa.String(length=64), nullable=False),
        sa.Column("src_id", sa.String(length=32), nullable=False),
        sa.Column("dst_id", sa.String(length=32), nullable=False),
        sa.Column("type", sa.String(length=24), nullable=False),
        sa.Column("status", sa.String(length=10), nullable=False),
        sa.Column("origin", sa.String(length=10), nullable=False),
        sa.Column("evidence", sa.Text(), nullable=False),
        sa.Column("src_version", sa.Integer(), nullable=False),
        sa.Column("dst_version", sa.Integer(), nullable=False),
        sa.Column("task_id", sa.String(length=32), nullable=True),
        sa.Column("decided_by", sa.String(length=80), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("decided_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project", "src_id", "dst_id", "type", name="uq_knowledge_edge"),
    )
    op.create_index("ix_knowledge_edges_project", "knowledge_edges", ["project"])
    op.create_index("ix_knowledge_edges_src_id", "knowledge_edges", ["src_id"])
    op.create_index("ix_knowledge_edges_dst_id", "knowledge_edges", ["dst_id"])
    op.create_table(
        "knowledge_audit",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("project", sa.String(length=64), nullable=False),
        sa.Column("actor", sa.String(length=80), nullable=False),
        sa.Column("action", sa.String(length=40), nullable=False),
        sa.Column("object_type", sa.String(length=20), nullable=False),
        sa.Column("object_id", sa.String(length=64), nullable=False),
        sa.Column("detail", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_knowledge_audit_project", "knowledge_audit", ["project"])
    op.create_table(
        "context_bundles",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("project", sa.String(length=64), nullable=False),
        sa.Column("task_id", sa.String(length=32), nullable=True),
        sa.Column("status", sa.String(length=14), nullable=False),
        sa.Column("content", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_context_bundles_project", "context_bundles", ["project"])
    op.create_index("ix_context_bundles_task_id", "context_bundles", ["task_id"])
    with op.batch_alter_table("tasks", schema=None) as batch_op:
        batch_op.add_column(sa.Column("knowledge_project", sa.String(length=64), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("tasks", schema=None) as batch_op:
        batch_op.drop_column("knowledge_project")
    for table, indexes in [
        ("context_bundles", ["ix_context_bundles_task_id", "ix_context_bundles_project"]),
        ("knowledge_audit", ["ix_knowledge_audit_project"]),
        ("knowledge_edges", ["ix_knowledge_edges_dst_id", "ix_knowledge_edges_src_id", "ix_knowledge_edges_project"]),
        ("knowledge_chunks", ["ix_knowledge_chunks_project", "ix_knowledge_chunks_source_id"]),
        ("knowledge_versions", ["ix_knowledge_versions_source_id"]),
        ("knowledge_sources", ["ix_knowledge_sources_project"]),
    ]:
        for ix in indexes:
            op.drop_index(ix, table_name=table)
        op.drop_table(table)
