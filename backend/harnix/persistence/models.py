"""SQLAlchemy models. Timestamps are naive UTC."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    repo_path: Mapped[str] = mapped_column(Text)
    instructions: Mapped[str] = mapped_column(Text)
    acceptance_criteria: Mapped[list[str]] = mapped_column(JSON, default=list)
    verification_commands: Mapped[list[str]] = mapped_column(JSON, default=list)
    # files the user pointed at: [{"path": ..., "start_line": ..., "end_line": ...}] (nullable: older rows)
    references: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON, nullable=True)
    thinking: Mapped[str | None] = mapped_column(String(8), nullable=True)  # low | medium | high | xhigh
    skills: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)  # skill ids preloaded for this task
    status: Mapped[str] = mapped_column(String(24), index=True)
    # verified | unverified | verification_failed (set when the task reaches a terminal state)
    outcome: Mapped[str | None] = mapped_column(String(32), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    parent_task_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    workspace_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    provider: Mapped[str | None] = mapped_column(String(64), nullable=True)
    model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # limits
    max_iterations: Mapped[int] = mapped_column(Integer)
    max_duration_seconds: Mapped[int] = mapped_column(Integer)
    # accounting
    iterations: Mapped[int] = mapped_column(Integer, default=0)
    model_calls: Mapped[int] = mapped_column(Integer, default=0)
    tool_calls: Mapped[int] = mapped_column(Integer, default=0)
    tool_failures: Mapped[int] = mapped_column(Integer, default=0)
    policy_denials: Mapped[int] = mapped_column(Integer, default=0)
    recovery_attempts: Mapped[int] = mapped_column(Integer, default=0)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    report: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    # the knowledge project whose sources this task may retrieve (None = no knowledge retrieval)
    knowledge_project: Mapped[str | None] = mapped_column(String(64), nullable=True)
    final_diff: Mapped[str | None] = mapped_column(Text, nullable=True)
    cancel_requested: Mapped[bool] = mapped_column(default=False)
    # a task is one turn of a session (a conversation that keeps its context, workspace and checkpoints)
    session_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    turn: Mapped[int] = mapped_column(Integer, default=1)
    discarded: Mapped[bool] = mapped_column(
        Boolean, default=False
    )  # rolled back: ignored when the session continues
    base_ref: Mapped[str | None] = mapped_column(String(64), nullable=True)  # snapshot this turn started from
    checkpoint: Mapped[str | None] = mapped_column(String(64), nullable=True)  # snapshot after this turn
    messages: Mapped[list[dict[str, Any]] | None] = mapped_column(
        JSON, nullable=True
    )  # the conversation after it
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Event(Base):
    __tablename__ = "events"
    __table_args__ = (Index("ix_events_task_id_id", "task_id", "id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"))
    ts: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    type: Mapped[str] = mapped_column(String(48))
    data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class VerificationResult(Base):
    __tablename__ = "verification_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), index=True)
    phase: Mapped[str] = mapped_column(String(16))  # baseline | final
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    name: Mapped[str] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(24))  # test | lint | typecheck | acceptance | criterion
    command: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(16))  # passed | failed | skipped | unavailable
    exit_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_seconds: Mapped[float] = mapped_column(default=0.0)
    output: Mapped[str] = mapped_column(Text, default="")
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Approval(Base):
    __tablename__ = "approvals"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), index=True)
    tool: Mapped[str] = mapped_column(String(48))
    arguments: Mapped[dict[str, Any]] = mapped_column(JSON)
    action_hash: Mapped[str] = mapped_column(String(64))
    reason: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending|approved|rejected|expired
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Memory(Base):
    """A project-memory note: survives tasks, scoped to one repository (`scope` = its repo key)."""

    __tablename__ = "memories"
    __table_args__ = (Index("ix_memories_scope_created", "scope", "created_at"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    scope: Mapped[str] = mapped_column(String(64))
    content: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(16))  # user | agent
    task_id: Mapped[str | None] = mapped_column(
        String(32), nullable=True
    )  # the task that wrote it (agent notes)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Attachment(Base):
    """An image the user attached to a task (validated raster bytes; the model sees it with the task)."""

    __tablename__ = "attachments"
    __table_args__ = (Index("ix_attachments_task_idx", "task_id", "idx"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    task_id: Mapped[str] = mapped_column(String(32))
    idx: Mapped[int] = mapped_column(Integer)  # 1-based position within the task
    name: Mapped[str] = mapped_column(String(120))
    media_type: Mapped[str] = mapped_column(String(32))
    size: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    data: Mapped[bytes] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ChatSession(Base):
    """A conversation: consecutive turns share one isolated workspace and one model context."""

    __tablename__ = "chat_sessions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    repo_path: Mapped[str] = mapped_column(Text)
    workspace_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    applied_ref: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )  # snapshot last applied to the repo
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class KnowledgeSource(Base):
    """One thing in the knowledge base (a BRD, requirement, story, ticket, design doc, code file or test)."""

    __tablename__ = "knowledge_sources"
    __table_args__ = (UniqueConstraint("project", "kind", "external_id", name="uq_knowledge_source_key"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    project: Mapped[str] = mapped_column(String(64), index=True)
    kind: Mapped[str] = mapped_column(String(16))  # brd requirement story ticket design code test doc
    external_id: Mapped[str] = mapped_column(String(300))  # REQ-1, PROJ-12, src/app.py ...
    title: Mapped[str] = mapped_column(String(300))
    uri: Mapped[str | None] = mapped_column(Text, nullable=True)  # where it came from (file, URL)
    origin: Mapped[str] = mapped_column(String(16), default="ingest")  # ingest | mock | agent
    current_version: Mapped[int] = mapped_column(Integer, default=0)
    # ok | partial (some chunks lack embeddings: re-ingest retries them) | failed
    ingestion_state: Mapped[str] = mapped_column(String(12), default="ok")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class KnowledgeVersion(Base):
    """An immutable revision of a source. A new hash makes a new version; bundles refer to versions."""

    __tablename__ = "knowledge_versions"
    __table_args__ = (UniqueConstraint("source_id", "version", name="uq_knowledge_version"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    source_id: Mapped[str] = mapped_column(String(32), index=True)
    version: Mapped[int] = mapped_column(Integer)
    content_hash: Mapped[str] = mapped_column(String(64))
    content: Mapped[str] = mapped_column(Text)
    meta: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class KnowledgeChunk(Base):
    """A retrievable piece of the CURRENT version of a source, with its embedding (JSON; pgvector copy optional)."""

    __tablename__ = "knowledge_chunks"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    source_id: Mapped[str] = mapped_column(String(32), index=True)
    version_id: Mapped[str] = mapped_column(String(32))
    project: Mapped[str] = mapped_column(String(64), index=True)
    ordinal: Mapped[int] = mapped_column(Integer)
    heading: Mapped[str] = mapped_column(String(300), default="")
    text: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float] | None] = mapped_column(JSON, nullable=True)
    embedding_model: Mapped[str | None] = mapped_column(String(80), nullable=True)


class KnowledgeEdge(Base):
    """A typed relationship between two sources. Only `verified` edges are authoritative."""

    __tablename__ = "knowledge_edges"
    __table_args__ = (UniqueConstraint("project", "src_id", "dst_id", "type", name="uq_knowledge_edge"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    project: Mapped[str] = mapped_column(String(64), index=True)
    src_id: Mapped[str] = mapped_column(String(32), index=True)
    dst_id: Mapped[str] = mapped_column(String(32), index=True)
    type: Mapped[str] = mapped_column(String(24))
    status: Mapped[str] = mapped_column(String(10), default="proposed")  # proposed | verified | rejected
    origin: Mapped[str] = mapped_column(String(10))  # source (declared in a document) | agent | human
    evidence: Mapped[str] = mapped_column(Text, default="")
    src_version: Mapped[int] = mapped_column(Integer, default=0)  # versions the edge was made against
    dst_version: Mapped[int] = mapped_column(Integer, default=0)
    task_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    decided_by: Mapped[str | None] = mapped_column(String(80), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class KnowledgeAudit(Base):
    """Append-only history of what was ingested, retrieved for a task, proposed, promoted or rejected."""

    __tablename__ = "knowledge_audit"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    project: Mapped[str] = mapped_column(String(64), index=True)
    actor: Mapped[str] = mapped_column(String(80))
    action: Mapped[str] = mapped_column(String(40))
    object_type: Mapped[str] = mapped_column(String(20))
    object_id: Mapped[str] = mapped_column(String(64))
    detail: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ContextBundle(Base):
    """The versioned context given to an agent for a task. bundle_id = hash of what it contains (reproducible)."""

    __tablename__ = "context_bundles"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)  # = bundle_id
    project: Mapped[str] = mapped_column(String(64), index=True)
    task_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(14))  # sufficient | insufficient
    content: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
