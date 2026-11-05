"""Starting sub-agents (the `spawn_agent` tool's implementation).

A sub-agent is the same AgentLoop with a fresh context: it sees only the prompt it was given (plus the detected
project facts), uses the profile's tools through the SAME executor pipeline (validation, policy, approvals,
redaction), shares the task's budget and cancellation, runs at most `max_steps` steps, cannot start another
sub-agent (depth 1) and cannot write project memory. Its final text comes back to the parent as the tool result
(foreground) or as a later delivery (background, read-only agents only, see background.py); it is a report to
check, not verified evidence.
"""

from __future__ import annotations

import itertools
import threading
from collections.abc import Callable
from dataclasses import dataclass

from harnix.execution.project import ProjectInfo
from harnix.harness.agent import AgentLoop, AgentOutcome, ContextPolicy
from harnix.harness.agents import READ_ONLY_TOOLS, AgentProfile
from harnix.harness.background import BackgroundJobs
from harnix.harness.context import SUBAGENT_PROMPT, ContextBuilder
from harnix.models.base import ModelProvider
from harnix.persistence.store import Store
from harnix.policies.budget import Budget
from harnix.tools.base import ToolResult
from harnix.tools.executor import ToolExecutor
from harnix.tools.registry import OPTIONAL_TOOLS, specs

REPORT_LIMIT = 20_000


@dataclass
class SubagentDeps:
    provider: ModelProvider
    store: Store
    task_id: str
    executor: ToolExecutor  # the main agent's: its policy, gate, budget and redactor are reused
    budget: Budget
    cancel: threading.Event
    project: ProjectInfo
    top_level: list[str]
    agents: dict[str, AgentProfile]
    main_tools: set[str]  # tools the task has; a profile can only narrow them
    thinking: str | None  # the task's level, used when the profile does not set one
    skills_list: str | None
    max_steps: int
    max_tokens: int
    model_timeout: float
    result_limit: int
    context_policy: ContextPolicy
    background: BackgroundJobs | None = None
    max_total: int = 20


def _result(
    d: SubagentDeps, label: str, description: str, out: AgentOutcome, changed: bool, background: bool
) -> ToolResult:
    d.store.add_event(d.task_id, "subagent.finished", {
        "agent": label, "reason": out.reason, "steps": out.steps, "report_chars": len(out.final_text),
        "background": background, **({"error": out.error} if out.error else {}),
    })  # fmt: skip
    data = {"agent": label, "description": description, "steps": out.steps}
    if out.reason == "done":
        report = out.final_text
        if len(report) > REPORT_LIMIT:
            report = report[:REPORT_LIMIT] + "\n[report truncated]"
        return ToolResult(True, {**data, "report": report}, mutated=changed)
    return ToolResult(
        False, data, error=f"Sub-agent {label} stopped without a report ({out.reason}): {out.error or ''}".strip(),
        mutated=changed,
    )  # fmt: skip


def make_spawner(d: SubagentDeps) -> Callable[[str, str, str, bool], ToolResult]:
    counter = itertools.count(1)

    def spawn(agent_id: str, description: str, prompt: str, background: bool = False) -> ToolResult:
        profile = d.agents.get(agent_id)
        if profile is None:
            return ToolResult(
                False, error=f"Unknown agent '{agent_id}'. Available: {', '.join(sorted(d.agents))}"
            )
        allowed = profile.allowed(d.main_tools)
        if background:
            if d.background is None:
                return ToolResult(False, error="Background sub-agents are not available for this task")
            if not allowed <= READ_ONLY_TOOLS:
                extra = ", ".join(sorted(allowed - READ_ONLY_TOOLS))
                return ToolResult(
                    False,
                    error=f"background=true is only for read-only agents; '{agent_id}' can also use {extra}. "
                    "Run it in the foreground (background=false).",
                )
        n = next(counter)
        if n > d.max_total:
            return ToolResult(
                False, error=f"This task already started {d.max_total} sub-agents (FORGE_SUBAGENT_MAX_TOTAL)"
            )
        label = f"{agent_id}-{n}"
        cancel = d.background.stop_event if background and d.background is not None else d.cancel
        executor = d.executor.for_agent(
            label, allowed, d.executor.child_context(spawn=None, remember=None, cancel=cancel)
        )
        system = SUBAGENT_PROMPT + (f"\n\n{profile.prompt}" if profile.prompt else "")
        context = ContextBuilder(
            instructions=f"{description}\n\n{prompt}", criteria=[], project=d.project, baseline=[],
            top_level=d.top_level, system_prompt=system, graph_guidance="graph_query" in allowed,
            skills_list=d.skills_list if "load_skill" in allowed else None,
        )  # fmt: skip
        thinking = profile.thinking or d.thinking
        steps = min(profile.max_iterations or d.max_steps, d.max_steps)
        d.store.add_event(d.task_id, "subagent.started", {
            "agent": label, "profile": agent_id, "description": description, "thinking": thinking,
            "tools": sorted(allowed), "max_steps": steps, "background": background,
        })  # fmt: skip
        loop = AgentLoop(
            provider=d.provider, store=d.store, task_id=d.task_id, executor=executor, budget=d.budget,
            cancel=cancel, context=context, verify=None, max_recovery=0, max_tokens=d.max_tokens,
            model_timeout=d.model_timeout, result_limit=d.result_limit,
            tools=specs(optional=allowed & OPTIONAL_TOOLS, only=allowed), thinking=thinking, agent=label,
            max_steps=steps, context_policy=d.context_policy,
        )  # fmt: skip

        def run() -> ToolResult:
            touched_before = len(d.executor.ctx.touched)
            out = loop.run()
            changed = (
                len(d.executor.ctx.touched) > touched_before
            )  # its edits count as progress for the parent
            return _result(d, label, description, out, changed, background)

        if background and d.background is not None:
            try:
                d.background.start(label, description, run)
            except RuntimeError as e:
                return ToolResult(False, error=str(e))
            return ToolResult(True, {
                "agent": label, "status": "running",
                "note": "The sub-agent is working in the background. Its report is delivered to you automatically when it "
                "finishes. Do not poll or wait for it, and do not repeat its work; continue with other work.",
            })  # fmt: skip
        return run()

    return spawn
