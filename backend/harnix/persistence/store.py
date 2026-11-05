"""Persistence layer: the only module that talks to the database."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import timedelta
from typing import Any, cast

from sqlalchemy import CursorResult, Engine, func, select, update
from sqlalchemy.orm import Session, sessionmaker

from harnix.harness.states import ACTIVE, TERMINAL, InvalidTransition, TaskStatus, allowed_sources
from harnix.persistence.db import make_session_factory
from harnix.persistence.models import (
    Approval,
    Attachment,
    Base,
    ChatSession,
    Event,
    Memory,
    Task,
    VerificationResult,
    utcnow,
)
from harnix.policies.redaction import Redactor

COUNTERS = {
    "iterations", "model_calls", "tool_calls", "tool_failures", "policy_denials",
    "recovery_attempts", "input_tokens", "output_tokens",
}  # fmt: skip


class NotFound(Exception):
    pass


def _rows(result: Any) -> int:
    """Rows matched by an UPDATE (Session.execute is typed as a generic Result)."""
    return cast("CursorResult[Any]", result).rowcount


def new_id() -> str:
    return uuid.uuid4().hex[:16]


class Store:
    def __init__(self, engine: Engine, redactor: Redactor | None = None):
        self.engine = engine
        self.sessions: sessionmaker[Session] = make_session_factory(engine)
        self.redactor = redactor or Redactor()

    def create_schema(self) -> None:  # tests / quick local runs; production uses Alembic
        Base.metadata.create_all(self.engine)

    # ---- tasks -------------------------------------------------------------------------------
    def create_task(self, **fields: Any) -> Task:
        """A new turn. Without `session_id` it starts a new session; with one it continues that conversation."""
        session_id = fields.pop("session_id", None)
        if session_id is None:
            cs = self.create_chat_session(
                title=str(fields.get("title", "")), repo_path=str(fields.get("repo_path", ""))
            )
            session_id, turn = cs.id, 1
        else:
            with self.sessions() as s:
                last = s.scalar(select(func.max(Task.turn)).where(Task.session_id == session_id))
            turn = (last or 0) + 1
            self.touch_chat_session(session_id)
        task = Task(id=new_id(), status=TaskStatus.QUEUED.value, session_id=session_id, turn=turn, **fields)
        with self.sessions.begin() as s:
            s.add(task)
        self.add_event(task.id, "task.created", {"title": task.title, "repo_path": task.repo_path})
        return task

    # ---- sessions: a conversation of turns (each turn is a task) -------------------------------------
    def create_chat_session(self, *, title: str, repo_path: str) -> ChatSession:
        cs = ChatSession(id=new_id(), title=title[:200], repo_path=repo_path)
        with self.sessions.begin() as s:
            s.add(cs)
        return cs

    def get_chat_session(self, session_id: str) -> ChatSession:
        with self.sessions() as s:
            cs = s.get(ChatSession, session_id)
            if cs is None:
                raise NotFound(session_id)
            return cs

    def update_chat_session(self, session_id: str, **fields: Any) -> None:
        with self.sessions.begin() as s:
            s.execute(update(ChatSession).where(ChatSession.id == session_id).values(**fields))

    def touch_chat_session(self, session_id: str) -> None:
        self.update_chat_session(session_id, updated_at=utcnow())

    def list_chat_sessions(
        self, limit: int = 50, offset: int = 0, repo_path: str | None = None, archived: bool = False
    ) -> tuple[Sequence[ChatSession], int]:
        with self.sessions() as s:
            q = select(ChatSession).where(ChatSession.archived == archived)
            cq = select(func.count()).select_from(ChatSession).where(ChatSession.archived == archived)
            if repo_path:
                q, cq = (
                    q.where(ChatSession.repo_path == repo_path),
                    cq.where(ChatSession.repo_path == repo_path),
                )
            rows = s.scalars(
                q.order_by(ChatSession.updated_at.desc(), ChatSession.id).limit(limit).offset(offset)
            ).all()
            return rows, s.scalar(cq) or 0

    def session_turns(self, session_id: str, *, include_discarded: bool = True) -> list[Task]:
        with self.sessions() as s:
            q = select(Task).where(Task.session_id == session_id)
            if not include_discarded:
                q = q.where(Task.discarded.is_(False))
            return list(s.scalars(q.order_by(Task.turn, Task.created_at)).all())

    def session_summaries(self, session_ids: list[str]) -> dict[str, dict[str, Any]]:
        """Per session: number of live turns and the last live turn's status/outcome/prompt (one query)."""
        if not session_ids:
            return {}
        with self.sessions() as s:
            rows = s.execute(
                select(Task.session_id, Task.turn, Task.status, Task.outcome, Task.instructions)
                .where(Task.session_id.in_(session_ids), Task.discarded.is_(False))
                .order_by(Task.turn)
            ).all()
        out: dict[str, dict[str, Any]] = {}
        for sid, turn, status, outcome, instructions in rows:
            d = out.setdefault(str(sid), {"turns": 0})
            d.update(
                turns=d["turns"] + 1,
                last_turn=turn,
                last_status=status,
                last_outcome=outcome,
                last_prompt=str(instructions).split("\n")[0][:160],
            )
        return out

    def discard_turns(self, task_ids: list[str]) -> None:
        if task_ids:
            with self.sessions.begin() as s:
                s.execute(update(Task).where(Task.id.in_(task_ids)).values(discarded=True))

    def session_has_active_turn(self, session_id: str) -> bool:
        active = [x.value for x in ACTIVE] + [TaskStatus.QUEUED.value]
        with self.sessions() as s:
            return (
                s.scalar(
                    select(Task.id).where(Task.session_id == session_id, Task.status.in_(active)).limit(1)
                )
                is not None
            )

    def add_attachments(self, task_id: str, images: list[Any]) -> None:
        """Store validated images (forge.harness.images.ValidImage) for a task, numbered from 1."""
        with self.sessions.begin() as s:
            for n, im in enumerate(images, 1):
                s.add(
                    Attachment(
                        id=new_id(), task_id=task_id, idx=n, name=im.name, media_type=im.media_type,
                        size=len(im.data), sha256=im.sha256, width=im.width, height=im.height, data=im.data,
                    )
                )  # fmt: skip

    def attachment_info(self, task_id: str) -> list[dict[str, Any]]:
        """Metadata only (no bytes) of a task's images, in order."""
        with self.sessions() as s:
            rows = s.execute(
                select(Attachment.idx, Attachment.name, Attachment.media_type, Attachment.size, Attachment.width, Attachment.height)
                .where(Attachment.task_id == task_id)
                .order_by(Attachment.idx)
            ).all()  # fmt: skip
        return [
            {"index": i, "name": n, "media_type": t, "size": z, "width": w, "height": h}
            for i, n, t, z, w, h in rows
        ]

    def attachment_blobs(self, task_id: str) -> list[tuple[dict[str, Any], bytes]]:
        with self.sessions() as s:
            rows = s.execute(
                select(Attachment.idx, Attachment.name, Attachment.media_type, Attachment.data)
                .where(Attachment.task_id == task_id)
                .order_by(Attachment.idx)
            ).all()  # fmt: skip
        return [({"index": i, "name": n, "media_type": t}, bytes(d)) for i, n, t, d in rows]

    def attachment_blob(self, task_id: str, index: int) -> tuple[str, str, bytes] | None:
        with self.sessions() as s:
            row = s.execute(
                select(Attachment.name, Attachment.media_type, Attachment.data).where(
                    Attachment.task_id == task_id, Attachment.idx == index
                )
            ).first()
        return None if row is None else (row[0], row[1], bytes(row[2]))

    def get_task(self, task_id: str) -> Task:
        with self.sessions() as s:
            task = s.get(Task, task_id)
            if task is None:
                raise NotFound(task_id)
            return task

    def list_tasks(
        self, limit: int = 50, offset: int = 0, status: str | None = None, repo_path: str | None = None
    ) -> tuple[Sequence[Task], int]:
        with self.sessions() as s:
            q = select(Task)
            cq = select(func.count()).select_from(Task)
            if status:
                q, cq = q.where(Task.status == status), cq.where(Task.status == status)
            if repo_path:
                q, cq = q.where(Task.repo_path == repo_path), cq.where(Task.repo_path == repo_path)
            rows = s.scalars(q.order_by(Task.created_at.desc(), Task.id).limit(limit).offset(offset)).all()
            return rows, s.scalar(cq) or 0

    def transition(self, task_id: str, dst: TaskStatus, *, error: str | None = None, **fields: Any) -> Task:
        """Atomic compare-and-set transition; raises InvalidTransition if the current status disallows it."""
        sources = [x.value for x in allowed_sources(dst)]
        values: dict[str, Any] = {"status": dst.value, **fields}
        if error is not None:
            values["error"] = self.redactor.redact(error)
        if dst == TaskStatus.RUNNING:
            values.setdefault("started_at", func.coalesce(Task.started_at, utcnow()))
        if dst in TERMINAL:
            values["finished_at"] = utcnow()
        with self.sessions.begin() as s:
            prev = s.scalar(select(Task.status).where(Task.id == task_id))
            if prev is None:
                raise NotFound(task_id)
            res = s.execute(update(Task).where(Task.id == task_id, Task.status.in_(sources)).values(**values))
            if _rows(res) == 0:  # compare-and-set lost: status was not an allowed source
                raise InvalidTransition(TaskStatus(prev), dst)
        self.add_event(
            task_id, "task.state", {"from": prev, "to": dst.value, **({"error": error} if error else {})}
        )
        return self.get_task(task_id)

    def update_task(self, task_id: str, **fields: Any) -> None:
        with self.sessions.begin() as s:
            s.execute(update(Task).where(Task.id == task_id).values(**fields))

    def bump(self, task_id: str, **counters: int) -> None:
        assert set(counters) <= COUNTERS, counters
        with self.sessions.begin() as s:
            s.execute(
                update(Task)
                .where(Task.id == task_id)
                .values(**{k: getattr(Task, k) + v for k, v in counters.items()})
            )

    def request_cancel(self, task_id: str) -> None:
        self.update_task(task_id, cancel_requested=True)

    def orphaned_tasks(self, quiet_seconds: float = 0.0) -> list[str]:
        """Active tasks with no event for `quiet_seconds`. A task another live process runs (two backends on one
        database, or a --reload restart of one of them) keeps producing events, so it is not taken for dead."""
        cutoff = utcnow() - timedelta(seconds=quiet_seconds)
        last = select(Event.task_id, func.max(Event.ts).label("ts")).group_by(Event.task_id).subquery()
        with self.sessions() as s:
            q = (
                select(Task.id)
                .outerjoin(last, last.c.task_id == Task.id)
                .where(
                    Task.status.in_([x.value for x in ACTIVE]), (last.c.ts.is_(None)) | (last.c.ts < cutoff)
                )
            )
            return list(s.scalars(q))

    def usage_totals(self) -> tuple[int, int]:
        """Total (input, output) tokens recorded across every task."""
        with self.sessions() as s:
            row = s.execute(
                select(
                    func.coalesce(func.sum(Task.input_tokens), 0),
                    func.coalesce(func.sum(Task.output_tokens), 0),
                )
            ).one()
            return int(row[0]), int(row[1])

    def active_count(self) -> int:
        with self.sessions() as s:
            return (
                s.scalar(
                    select(func.count()).select_from(Task).where(Task.status.in_([x.value for x in ACTIVE]))
                )
                or 0
            )

    # ---- events ------------------------------------------------------------------------------
    def add_event(self, task_id: str, type_: str, data: dict[str, Any] | None = None) -> int:
        clean = self.redactor.redact_obj(data or {})
        with self.sessions.begin() as s:
            ev = Event(task_id=task_id, type=type_, data=clean)
            s.add(ev)
            s.flush()
            return ev.id

    def apply_states(self, task_ids: list[str]) -> dict[str, str]:
        """task id -> "applied" or "reverted" (the latest of the two events); tasks never applied are absent."""
        if not task_ids:
            return {}
        with self.sessions() as s:
            rows = s.execute(
                select(Event.task_id, Event.type)
                .where(Event.task_id.in_(task_ids), Event.type.in_(("task.applied", "task.reverted")))
                .order_by(Event.id)
            ).all()
        return {tid: ("applied" if t == "task.applied" else "reverted") for tid, t in rows}

    def has_event(self, task_id: str, type_: str) -> bool:
        with self.sessions() as s:
            return (
                s.scalar(select(Event.id).where(Event.task_id == task_id, Event.type == type_).limit(1))
                is not None
            )

    def events_after(self, task_id: str, after: int = 0, limit: int = 500) -> list[Event]:
        with self.sessions() as s:
            q = (
                select(Event)
                .where(Event.task_id == task_id, Event.id > after)
                .order_by(Event.id)
                .limit(limit)
            )
            return list(s.scalars(q))

    # ---- verification ------------------------------------------------------------------------
    def add_verification(self, task_id: str, **fields: Any) -> None:
        fields["output"] = self.redactor.redact(fields.get("output", ""))
        with self.sessions.begin() as s:
            s.add(VerificationResult(task_id=task_id, **fields))

    def verifications(self, task_id: str) -> list[VerificationResult]:
        with self.sessions() as s:
            q = (
                select(VerificationResult)
                .where(VerificationResult.task_id == task_id)
                .order_by(VerificationResult.id)
            )
            return list(s.scalars(q))

    # ---- approvals ---------------------------------------------------------------------------
    def create_approval(
        self, task_id: str, tool: str, arguments: dict[str, Any], action_hash: str, reason: str
    ) -> Approval:
        a = Approval(
            id=new_id(), task_id=task_id, tool=tool, action_hash=action_hash,
            arguments=self.redactor.redact_obj(arguments), reason=reason,
        )  # fmt: skip
        with self.sessions.begin() as s:
            s.add(a)
        return a

    def get_approval(self, approval_id: str) -> Approval:
        with self.sessions() as s:
            a = s.get(Approval, approval_id)
            if a is None:
                raise NotFound(approval_id)
            return a

    def approvals(self, task_id: str) -> list[Approval]:
        with self.sessions() as s:
            return list(
                s.scalars(select(Approval).where(Approval.task_id == task_id).order_by(Approval.created_at))
            )

    def decide_approval(self, approval_id: str, status: str, note: str | None = None) -> bool:
        """Only a pending approval can be decided, exactly once. Returns False if it was not pending."""
        with self.sessions.begin() as s:
            res = s.execute(
                update(Approval)
                .where(Approval.id == approval_id, Approval.status == "pending")
                .values(status=status, note=note, decided_at=utcnow())
            )
            return _rows(res) == 1

    # ---- project memory --------------------------------------------------------------------------
    def add_memory(self, scope: str, content: str, source: str, task_id: str | None = None) -> Memory:
        m = Memory(
            id=new_id(), scope=scope, content=self.redactor.redact(content), source=source, task_id=task_id
        )
        with self.sessions.begin() as s:
            s.add(m)
        return m

    def memories(self, scope: str, limit: int = 500) -> list[Memory]:
        """Oldest first, so notes read in the order they were written."""
        with self.sessions() as s:
            q = (
                select(Memory)
                .where(Memory.scope == scope)
                .order_by(Memory.created_at, Memory.id)
                .limit(limit)
            )
            return list(s.scalars(q))

    def delete_memory(self, scope: str, memory_id: str) -> bool:
        with self.sessions.begin() as s:
            m = s.get(Memory, memory_id)
            if m is None or m.scope != scope:
                return False
            s.delete(m)
            return True


def duration_seconds(task: Task) -> float | None:
    if not task.started_at:
        return None
    end = task.finished_at or utcnow()
    return (end - task.started_at) / timedelta(seconds=1)
