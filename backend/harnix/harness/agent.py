"""The bounded agent loop. Pure orchestration: every side effect goes through injected collaborators.

The same loop runs the main agent and sub-agents. A sub-agent (`verify=None`) ends on its first final answer and
reports it; only the main agent's answer is independently verified.
"""

from __future__ import annotations

import hashlib
import json
import threading
from collections.abc import Callable
from dataclasses import dataclass

from harnix.harness import compaction
from harnix.harness.background import BackgroundJobs, deliver, render_delivery
from harnix.harness.context import ContextBuilder, verification_feedback
from harnix.harness.states import InvalidTransition, TaskStatus
from harnix.models.base import (
    Message,
    ModelProvider,
    ModelResponse,
    ProviderError,
    TextBlock,
    ToolResultBlock,
    ToolSpec,
)
from harnix.persistence.store import Store
from harnix.policies.budget import Budget, BudgetExceeded
from harnix.tools.executor import ToolExecutor
from harnix.tools.registry import specs
from harnix.verification.engine import CheckResult, Verdict

MAX_INVALID_RESPONSES = 3
MAX_PROVIDER_RETRIES = 2
NUDGE_AT, ABORT_AT = 3, 5  # identical failing calls with no workspace change in between
SUMMARY_MAX_TOKENS = 6000


@dataclass
class AgentOutcome:
    reason: str  # done | verification_failed | cancelled | budget_<kind> | provider_error | invalid_responses | repeated_failure | max_steps
    final_text: str = ""
    claimed_done: bool = False
    verdict: Verdict | None = None
    final_checks: list[CheckResult] | None = None
    error: str | None = None
    steps: int = 0


@dataclass(frozen=True)
class ContextPolicy:
    """When to prune and compact (see compaction.py). Disabled = the history is sent as it is."""

    enabled: bool = True
    threshold: int = 170_000  # estimated tokens at which the conversation is compacted
    keep: int = 15_000  # tokens of recent conversation kept verbatim beside the summary
    protect: int = 40_000  # newest tool output never pruned
    minimum: int = 20_000  # prune only if at least this much is reclaimed


VerifyFn = Callable[[int], tuple[list[CheckResult], Verdict]]


class AgentLoop:
    def __init__(
        self, *, provider: ModelProvider, store: Store, task_id: str, executor: ToolExecutor, budget: Budget,
        cancel: threading.Event, context: ContextBuilder, verify: VerifyFn | None, max_recovery: int,
        max_tokens: int, model_timeout: float, result_limit: int, backoff: float = 1.0,
        tools: list[ToolSpec] | None = None, thinking: str | None = None, agent: str = "main",
        max_steps: int | None = None, context_policy: ContextPolicy | None = None,
        background: BackgroundJobs | None = None, prior: list[Message] | None = None, turn: int = 1,
        changed_files: list[str] | None = None,
    ):  # fmt: skip
        self.provider, self.store, self.task_id, self.executor = provider, store, task_id, executor
        self.budget, self.cancel, self.context, self.verify = budget, cancel, context, verify
        self.max_recovery, self.max_tokens, self.model_timeout = max_recovery, max_tokens, model_timeout
        self.result_limit, self.backoff = result_limit, backoff
        self.tools = tools if tools is not None else specs()  # the tools this agent may use
        self.thinking, self.agent, self.max_steps = thinking, agent, max_steps
        self.policy = context_policy or ContextPolicy(enabled=False)
        self.history: list[Message] = []  # kept on the loop so callers (and tests) can inspect it
        self.background = background  # read-only sub-agents running in parallel (main agent only)
        self.prior, self.turn, self.changed_files = (
            prior or [],
            turn,
            changed_files or [],
        )  # earlier turns of a session
        self._pruned: dict[str, str] = {}  # originals of pruned tool output, for the next summary
        self._compacted_at: int | None = None  # estimate right after the last compaction
        self._gave_up = False  # compaction stopped helping; said so once

    # ---- helpers -----------------------------------------------------------------------------
    def _event(self, type_: str, data: dict) -> None:
        if self.agent != "main":
            data = {**data, "agent": self.agent}
        self.store.add_event(self.task_id, type_, data)

    def _timeout(self) -> float:
        return min(self.model_timeout, max(5.0, self.budget.remaining_seconds()))

    def _account(self, resp: ModelResponse) -> None:
        self.budget.add_usage(resp.usage.input_tokens, resp.usage.output_tokens)
        self.store.bump(
            self.task_id, input_tokens=resp.usage.input_tokens, output_tokens=resp.usage.output_tokens
        )

    def _call_model(self, history: list[Message]) -> ModelResponse:
        attempt = 0
        compacted_for_overflow = False
        while True:
            self.budget.check_time()
            self.store.bump(self.task_id, model_calls=1)
            try:
                return self.provider.complete(
                    system=self.context.system(), messages=self.context.build(history), tools=self.tools,
                    max_tokens=self.max_tokens, timeout=self._timeout(), thinking=self.thinking,
                )  # fmt: skip
            except ProviderError as e:
                if e.kind == "context_overflow" and self.policy.enabled and not compacted_for_overflow:
                    compacted_for_overflow = True
                    if self._compact(history, reason="overflow"):
                        continue  # the provider measured the request as too long: compact once and retry
                    raise
                if not e.retryable or attempt >= MAX_PROVIDER_RETRIES:
                    raise
                attempt += 1
                delay = self.backoff * 2 ** (attempt - 1)
                self._event(
                    "retry.decision",
                    {"kind": "provider", "attempt": attempt, "delay_seconds": delay, "error": str(e)},
                )
                if self.cancel.wait(delay):
                    raise

    # ---- background sub-agents ---------------------------------------------------------------
    def _deliver(self, history: list[Message], *, wait: bool) -> bool:
        """Put the reports of finished background sub-agents into the conversation. True if any were delivered."""
        if self.background is None:
            return False
        jobs = self.background.collect(wait=wait)
        if not jobs:
            return False
        deliver(history, render_delivery(jobs))
        self._event("subagent.delivered", {"agents": [j.label for j in jobs]})
        return True

    # ---- context management ------------------------------------------------------------------
    def _manage_context(self, history: list[Message]) -> None:
        """Prune, then compact, the stored history when the estimate grows (append-only between these steps)."""
        if not self.policy.enabled:
            return
        system = self.context.system()
        est = compaction.estimate(history, system, self.tools)
        if est >= self.policy.threshold // 2:
            pr = compaction.prune(history, protect=self.policy.protect, minimum=self.policy.minimum)
            if pr is not None:
                history[:] = pr.history
                self._pruned.update(pr.originals)
                after = compaction.estimate(history, system, self.tools)
                self._event(
                    "context.pruned",
                    {
                        "results_pruned": pr.pruned,
                        "tokens_reclaimed": pr.reclaimed,
                        "tokens_before": est,
                        "tokens_after": after,
                    },
                )
                est = after
        if est < self.policy.threshold:
            return
        # Compacting again right away cannot help when the fixed part of the request (system prompt, tool schemas,
        # task, checkpoint) is most of the threshold: every turn would pay a summary call. Wait for real growth.
        if self._compacted_at is not None and est - self._compacted_at < max(
            self.policy.keep, self.policy.threshold // 10
        ):
            if not self._gave_up:
                self._gave_up = True
                self._event("context.compaction_deferred", {
                    "tokens": est, "after_last_compaction": self._compacted_at,
                    "reason": "the context has not grown enough since the last compaction for another one to help",
                })  # fmt: skip
            return
        self._compact(history, reason="threshold", estimate=est)

    def _summarize(self, system: str, prompt: str) -> str:
        self.budget.check_time()
        self.store.bump(self.task_id, model_calls=1)
        resp = self.provider.complete(
            system=system, messages=[Message("user", [TextBlock(prompt)])], tools=[],
            max_tokens=SUMMARY_MAX_TOKENS, timeout=self._timeout(), thinking="low" if self.thinking else None,
        )  # fmt: skip
        self._account(resp)
        return resp.text

    def _compact(self, history: list[Message], *, reason: str, estimate: int | None = None) -> bool:
        before = (
            estimate
            if estimate is not None
            else compaction.estimate(history, self.context.system(), self.tools)
        )
        # The provider measured the request as too long although our estimate fit: the estimate ran low, so keep
        # much less verbatim this time (opencode likewise aims well below the rejected size after an overflow).
        keep = min(self.policy.keep, max(before // 4, 1000)) if reason == "overflow" else self.policy.keep
        try:
            res = compaction.compact(history, keep, self._summarize, self._pruned)
        except (ProviderError, ValueError) as e:
            self._event(
                "context.compaction_failed", {"reason": reason, "error": str(e)[:300], "tokens": before}
            )
            return False
        if res is None:
            self._event(
                "context.compaction_failed",
                {"reason": reason, "error": "nothing older than the kept tail", "tokens": before},
            )
            return False
        history[:] = res.history
        after = compaction.estimate(history, self.context.system(), self.tools)
        self._compacted_at, self._gave_up = after, False
        live = {b.tool_use_id for m in history for b in m.content if isinstance(b, ToolResultBlock)}
        self._pruned = {k: v for k, v in self._pruned.items() if k in live}  # summarized: no longer needed
        self._event("context.compacted", {
            "reason": reason, "tokens_before": before, "tokens_after": after,
            "summarized_messages": res.summarized_messages, "kept_messages": res.kept_messages,
            "summary": res.summary[:4000],
        })  # fmt: skip
        return True

    # ---- main loop ---------------------------------------------------------------------------
    def run(self) -> AgentOutcome:
        history = self.history
        # a follow-up turn continues the saved conversation: the model sees everything said and done before
        history[:] = (
            [*self.prior, self.context.followup_message(self.turn, self.changed_files)]
            if self.prior
            else [self.context.initial_message()]
        )
        invalid = recovery = nudges = steps = 0
        fail_counts: dict[str, int] = {}

        while True:
            if self.cancel.is_set():
                return AgentOutcome("cancelled", steps=steps)
            if self.max_steps is not None and steps >= self.max_steps:
                return AgentOutcome(
                    "max_steps", error=f"stopped after {steps} steps (the sub-agent's limit)", steps=steps
                )
            try:
                it = self.budget.next_iteration()
            except BudgetExceeded as e:
                return AgentOutcome(f"budget_{e.kind}", error=str(e), steps=steps)
            steps += 1
            self.store.update_task(self.task_id, iterations=it)
            self._deliver(history, wait=False)
            self._manage_context(history)
            self._event("model.request", {"iteration": it, "messages": len(history)})
            try:
                resp = self._call_model(history)
            except BudgetExceeded as e:
                return AgentOutcome(f"budget_{e.kind}", error=str(e), steps=steps)
            except ProviderError as e:
                if self.cancel.is_set():
                    return AgentOutcome("cancelled", steps=steps)
                self._event("error", {"where": "provider", "kind": e.kind, "message": str(e)})
                return AgentOutcome("provider_error", error=str(e), steps=steps)

            self._account(resp)
            event = {
                "iteration": it, "text": resp.text[:2000], "stop_reason": resp.stop_reason,
                "tool_calls": [{"id": c.id, "name": c.name} for c in resp.tool_calls],
                "usage": {"input": resp.usage.input_tokens, "output": resp.usage.output_tokens,
                          "reasoning": resp.usage.reasoning_tokens},
            }  # fmt: skip
            if resp.thinking:
                event["thinking"] = resp.thinking  # requested vs applied, with the reason when they differ
            self._event("model.response", event)
            history.append(resp.message or Message("assistant", [TextBlock(resp.text), *resp.tool_calls]))

            if resp.tool_calls:
                invalid = 0
                results: list[ToolResultBlock] = []
                for call in resp.tool_calls:
                    if self.cancel.is_set():
                        return AgentOutcome("cancelled", steps=steps)
                    try:
                        r = self.executor.execute(call)
                    except BudgetExceeded as e:
                        return AgentOutcome(f"budget_{e.kind}", error=str(e), steps=steps)
                    results.append(ToolResultBlock(call.id, r.to_text(self.result_limit), is_error=not r.ok))
                    sig = hashlib.sha256(
                        f"{call.name}{json.dumps(call.input, sort_keys=True, default=str)}".encode()
                    ).hexdigest()
                    if r.mutated and r.ok and call.name not in ("run_command", "run_tests"):
                        fail_counts.clear()  # workspace changed: earlier failures are no longer comparable
                    if not r.ok or r.data.get("exit_code") not in (0, None):
                        fail_counts[sig] = fail_counts.get(sig, 0) + 1
                history.append(Message("user", list(results)))
                worst = max(fail_counts.values(), default=0)
                if worst >= ABORT_AT:
                    self._event(
                        "error", {"where": "agent", "message": "repeated identical failing tool call"}
                    )
                    return AgentOutcome(
                        "repeated_failure",
                        error=f"The same failing tool call was repeated {worst} times without progress",
                        steps=steps,
                    )
                if worst >= NUDGE_AT and nudges < 2:
                    nudges += 1
                    self._event("retry.decision", {"kind": "repeated_failure_nudge", "count": worst})
                    history.append(Message("user", [TextBlock(
                        "Harness notice: you have repeated the same failing call several times without changing anything. Try a different approach.")]))  # fmt: skip
                continue

            # A final answer is not final while background sub-agents still run (or were not delivered yet):
            # wait for them, hand their reports to the model, and let it finish with them in view.
            if self.background is not None and (self.background.running() or self.background.undelivered()):
                self._event("subagent.waiting", {"running": self.background.running()})
                self._deliver(history, wait=True)
                continue

            # no tool calls -> either a final answer or an unusable response
            if not resp.text.strip() or resp.stop_reason == "max_tokens":
                invalid += 1
                self._event(
                    "error",
                    {
                        "where": "model_response",
                        "message": "empty or truncated response",
                        "stop_reason": resp.stop_reason,
                    },
                )
                if invalid >= MAX_INVALID_RESPONSES:
                    return AgentOutcome(
                        "invalid_responses", error="Model returned unusable responses repeatedly", steps=steps
                    )
                history.append(Message("user", [TextBlock(
                    "Your last response was empty or cut off. Use a tool, or give your final plain-text summary.")]))  # fmt: skip
                continue

            invalid = 0
            if self.verify is None:  # a sub-agent: its report goes back to the agent that started it
                return AgentOutcome("done", resp.text, True, steps=steps)
            checks, verdict = self.verify(recovery)
            if self.cancel.is_set():
                return AgentOutcome("cancelled", steps=steps)
            if verdict.outcome != "verification_failed":
                return AgentOutcome("done", resp.text, True, verdict, checks, steps=steps)
            if recovery >= self.max_recovery:
                return AgentOutcome(
                    "verification_failed",
                    resp.text,
                    True,
                    verdict,
                    checks,
                    error="; ".join(verdict.reasons),
                    steps=steps,
                )
            recovery += 1
            self.store.bump(self.task_id, recovery_attempts=1)
            self._event(
                "retry.decision",
                {
                    "kind": "verification_recovery",
                    "attempt": recovery,
                    "of": self.max_recovery,
                    "reasons": verdict.reasons,
                },
            )
            try:
                self.store.transition(self.task_id, TaskStatus.RUNNING)
            except InvalidTransition:
                return AgentOutcome("cancelled", steps=steps)
            fail_counts.clear()
            history.append(
                Message("user", [TextBlock(verification_feedback(checks, verdict.reasons, recovery))])
            )
