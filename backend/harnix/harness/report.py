"""Final structured report (JSON) and its Markdown rendering."""

from __future__ import annotations

from typing import Any

from harnix.config import Settings
from harnix.execution.project import ProjectInfo
from harnix.harness.agent import AgentOutcome
from harnix.persistence.models import Task
from harnix.persistence.store import duration_seconds
from harnix.verification.engine import CheckResult, Verdict


def estimated_cost(task: Task, s: Settings) -> float | None:
    if s.price_input_per_mtok is None or s.price_output_per_mtok is None:
        return None
    return round(
        task.input_tokens / 1e6 * s.price_input_per_mtok + task.output_tokens / 1e6 * s.price_output_per_mtok,
        6,
    )


def build_report(
    *, task: Task, settings: Settings, project: ProjectInfo, changed_files: list[dict], baseline: list[CheckResult],
    outcome: AgentOutcome, verdict: Verdict | None, final_checks: list[CheckResult], provider_name: str,
    sandbox_name: str, status: str, error: str | None,
) -> dict[str, Any]:  # fmt: skip
    limitations = list(verdict.limitations) if verdict else []
    if verdict is None:
        limitations.append(
            "Independent verification did not run to completion; nothing about this result is verified"
        )
    if sandbox_name == "local":
        limitations.append("Executed in local mode: commands ran on the host without container isolation")
    if provider_name == "scripted-mock":
        limitations.append("Mock provider: this run exercised harness mechanics, not a real model")
    crit = [c for c in final_checks if c.kind == "criterion"]
    if crit:
        limitations.append(
            f"{len(crit)} free-text acceptance criteria were not machine-verified and need human review"
        )
    claimed = outcome.claimed_done
    return {
        "task_id": task.id,
        "title": task.title,
        "status": status,
        "outcome": task.outcome,
        "error": error,
        "provider": {"name": provider_name, "model": task.model},
        "execution_mode": sandbox_name,
        "agent": {
            "finish_reason": outcome.reason,
            "claimed_done": claimed,
            "final_message": outcome.final_text,  # the model's own claim; informational only
        },
        "independent_verification": verdict.as_dict() if verdict else None,
        "checks": {"baseline": [c.as_dict() for c in baseline], "final": [c.as_dict() for c in final_checks]},
        "false_success_claim": bool(claimed and verdict and verdict.outcome == "verification_failed"),
        "changed_files": changed_files,
        "project": {"types": project.kinds, "commands": project.commands},
        "usage": {
            "iterations": task.iterations, "model_calls": task.model_calls, "tool_calls": task.tool_calls,
            "tool_failures": task.tool_failures, "policy_denials": task.policy_denials,
            "recovery_attempts": task.recovery_attempts, "input_tokens": task.input_tokens,
            "output_tokens": task.output_tokens, "estimated_cost_usd": estimated_cost(task, settings),
            "duration_seconds": duration_seconds(task),
        },
        "known_limitations": limitations,
    }  # fmt: skip


def render_markdown(r: dict[str, Any]) -> str:
    v = r.get("independent_verification") or {}
    lines = [
        f"# Forge report: {r['title']}",
        f"- Task: `{r['task_id']}`  Status: **{r['status']}**  Outcome: **{r.get('outcome') or 'n/a'}**",
        f"- Provider: {r['provider']['name']} ({r['provider'].get('model')})  Execution: {r['execution_mode']}",
        f"- Agent finish reason: {r['agent']['finish_reason']}; claimed done: {r['agent']['claimed_done']}"
        + ("  **FALSE SUCCESS CLAIM**" if r["false_success_claim"] else ""),
    ]
    if r.get("error"):
        lines.append(f"- Error: {r['error']}")
    lines += ["", "## Independent verification", f"Verdict: **{v.get('outcome', 'not run')}**"]
    for phase in ("baseline", "final"):
        lines.append(f"\n### {phase.capitalize()} checks")
        for c in r["checks"][phase]:
            lines.append(
                f"- [{c['status']}] {c['kind']}: `{c['command'] or c['name']}`"
                + (f" - {c['note']}" if c.get("note") else "")
            )
    lines += ["", "## Changed files"]
    lines += [
        f"- {f['status']} {f['path']} (+{f['additions']}/-{f['deletions']})" for f in r["changed_files"]
    ] or ["- none"]
    u = r["usage"]
    lines += [
        "", "## Usage",
        f"- iterations {u['iterations']}, model calls {u['model_calls']}, tool calls {u['tool_calls']} "
        f"({u['tool_failures']} failed, {u['policy_denials']} denied), recoveries {u['recovery_attempts']}",
        f"- tokens in/out: {u['input_tokens']}/{u['output_tokens']}; estimated cost: {u['estimated_cost_usd']}; duration: {u['duration_seconds']}s",
        "", "## Known limitations", *[f"- {x}" for x in r["known_limitations"]], "",
        "## Agent's final message (unverified claim)", r["agent"]["final_message"] or "(none)",
    ]  # fmt: skip
    return "\n".join(lines)
