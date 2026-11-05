"""Explicit task lifecycle: statuses and the only allowed transitions."""

from __future__ import annotations

from enum import StrEnum


class TaskStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    AWAITING_APPROVAL = "awaiting_approval"
    VERIFYING = "verifying"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


S = TaskStatus
TRANSITIONS: dict[TaskStatus, frozenset[TaskStatus]] = {
    S.QUEUED: frozenset({S.RUNNING, S.CANCELLED}),
    S.RUNNING: frozenset({S.AWAITING_APPROVAL, S.VERIFYING, S.FAILED, S.CANCELLED}),
    S.AWAITING_APPROVAL: frozenset({S.RUNNING, S.FAILED, S.CANCELLED}),
    S.VERIFYING: frozenset({S.RUNNING, S.COMPLETED, S.FAILED, S.CANCELLED}),
    S.COMPLETED: frozenset(),
    S.FAILED: frozenset(),
    S.CANCELLED: frozenset(),
}
TERMINAL = frozenset({S.COMPLETED, S.FAILED, S.CANCELLED})
ACTIVE = frozenset({S.RUNNING, S.AWAITING_APPROVAL, S.VERIFYING})


class InvalidTransition(Exception):
    def __init__(self, src: TaskStatus, dst: TaskStatus):
        super().__init__(f"Invalid task transition {src.value} -> {dst.value}")
        self.src, self.dst = src, dst


def check_transition(src: TaskStatus, dst: TaskStatus) -> None:
    if dst not in TRANSITIONS[src]:
        raise InvalidTransition(src, dst)


def allowed_sources(dst: TaskStatus) -> list[TaskStatus]:
    return [s for s, ds in TRANSITIONS.items() if dst in ds]
