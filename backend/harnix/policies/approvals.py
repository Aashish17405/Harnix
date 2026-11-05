"""Human-approval gate. An approval authorizes exactly one (tool, arguments) action for one task."""

from __future__ import annotations

import hashlib
import json
import threading
import time
from dataclasses import dataclass
from typing import Any

from harnix.harness.states import InvalidTransition, TaskStatus
from harnix.persistence.store import Store
from harnix.policies.budget import Budget


def action_hash(task_id: str, tool: str, args: dict[str, Any]) -> str:
    canon = json.dumps(
        {"task": task_id, "tool": tool, "args": args}, sort_keys=True, separators=(",", ":"), default=str
    )
    return hashlib.sha256(canon.encode()).hexdigest()


@dataclass
class ApprovalOutcome:
    approved: bool
    reason: str


class ApprovalGate:
    def __init__(
        self,
        store: Store,
        task_id: str,
        *,
        timeout: float,
        cancel: threading.Event,
        budget: Budget,
        poll: float = 0.25,
    ):
        self.store, self.task_id, self.timeout = store, task_id, timeout
        self.cancel, self.budget, self.poll = cancel, budget, poll

    def request(self, tool: str, args: dict[str, Any], reason: str) -> ApprovalOutcome:
        h = action_hash(self.task_id, tool, args)
        a = self.store.create_approval(self.task_id, tool, args, h, reason)
        self.store.add_event(
            self.task_id,
            "approval.requested",
            {"approval_id": a.id, "tool": tool, "arguments": args, "reason": reason},
        )
        try:
            self.store.transition(self.task_id, TaskStatus.AWAITING_APPROVAL)
        except InvalidTransition:  # e.g. already cancelled
            self.store.decide_approval(a.id, "rejected", "task no longer running")
            return ApprovalOutcome(False, "task is no longer running")
        start = time.monotonic()
        outcome = ApprovalOutcome(False, "approval timed out")
        while True:
            cur = self.store.get_approval(a.id)
            if cur.status == "approved":
                # the decision is bound to the hash recorded when the request was created
                ok = cur.action_hash == h
                outcome = ApprovalOutcome(
                    ok, "approved by human" if ok else "approval does not match the proposed action"
                )
                break
            if cur.status in ("rejected", "expired"):
                outcome = ApprovalOutcome(False, f"rejected by human{': ' + cur.note if cur.note else ''}")
                break
            if self.cancel.is_set():
                self.store.decide_approval(a.id, "rejected", "task cancelled")
                outcome = ApprovalOutcome(False, "task cancelled while awaiting approval")
                break
            if time.monotonic() - start > self.timeout:
                self.store.decide_approval(a.id, "expired", "no decision before timeout")
                outcome = ApprovalOutcome(False, "approval timed out")
                break
            self.cancel.wait(self.poll)
        self.budget.pause(time.monotonic() - start)
        self.store.add_event(
            self.task_id,
            "approval.decided",
            {"approval_id": a.id, "approved": outcome.approved, "reason": outcome.reason},
        )
        try:
            self.store.transition(self.task_id, TaskStatus.RUNNING)
        except InvalidTransition:
            pass
        return outcome
