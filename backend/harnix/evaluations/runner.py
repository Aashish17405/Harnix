"""Evaluation harness: run known tasks end to end and score them against an independent ground truth.

Scripted (mock) runs are a harness self-test. They are labelled as such and must never be quoted as
LLM performance; only runs with a real provider measure a model.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from harnix.config import ConfigError, Settings
from harnix.execution.sandbox import make_sandbox
from harnix.execution.workspace import WorkspaceManager
from harnix.harness.runner import TaskRunner
from harnix.harness.states import TERMINAL, TaskStatus
from harnix.models.base import ModelProvider
from harnix.models.factory import provider_factory
from harnix.models.scripted import ScriptedProvider
from harnix.persistence.db import make_engine
from harnix.persistence.store import Store, duration_seconds
from harnix.policies.commands import parse_command
from harnix.policies.redaction import Redactor

CASES_DIR = Path(__file__).parent / "cases"


@dataclass
class Case:
    id: str
    description: str
    instructions: str
    repo_dir: Path
    ground_truth: str
    acceptance_criteria: list[str] = field(default_factory=list)
    verification_commands: list[str] = field(default_factory=list)
    max_iterations: int = 20
    scripted_steps: list[dict[str, Any]] = field(default_factory=list)
    selftest_expect: dict[str, Any] = field(default_factory=dict)
    selftest_only: bool = False


def load_cases(directory: Path = CASES_DIR, only: list[str] | None = None) -> list[Case]:
    cases = []
    for case_file in sorted(directory.glob("*/case.json")):
        raw = json.loads(case_file.read_text(encoding="utf-8"))
        if only and raw["id"] not in only:
            continue
        cases.append(Case(repo_dir=case_file.parent / "repo", **raw))
    return cases


def _ground_truth(sandbox, workspace: Path, command: str, timeout: float) -> bool:
    res = sandbox.run(
        parse_command(command),
        workspace=workspace,
        workdir=workspace,
        timeout=timeout,
        cancel=None,
        max_output=20000,
    )
    return res.exit_code == 0


def run_case(case: Case, settings: Settings, provider: ModelProvider, workroot: Path) -> dict[str, Any]:
    repos = workroot / "repos"
    repos.mkdir(parents=True, exist_ok=True)
    repo = repos / case.id
    shutil.copytree(case.repo_dir, repo)
    s = settings.model_copy(
        update={
            "database_url": f"sqlite:///{workroot / (case.id + '.db')}",
            "workspace_root": str(workroot / "ws"),
            "allowed_repo_roots": str(repos),
        }
    )
    redactor = Redactor(s.secret_values())
    store = Store(make_engine(s.database_url), redactor)
    store.create_schema()
    sandbox = make_sandbox(s)
    runner = TaskRunner(
        s, store, lambda: provider, sandbox, WorkspaceManager(s.workspace_root_path), redactor
    )
    task = store.create_task(
        title=case.id,
        repo_path=str(repo),
        instructions=case.instructions,
        acceptance_criteria=case.acceptance_criteria,
        verification_commands=case.verification_commands,
        max_iterations=min(case.max_iterations, s.max_iterations),
        max_duration_seconds=s.max_duration_seconds,
    )
    start = time.monotonic()
    result: dict[str, Any] = {"case": case.id, "description": case.description}
    try:
        runner.start(task.id)
        deadline = time.monotonic() + s.max_duration_seconds + 120
        while time.monotonic() < deadline:
            t = store.get_task(task.id)
            if TaskStatus(t.status) in TERMINAL:
                break
            time.sleep(0.2)
        else:
            runner.cancel(task.id)
        t = store.get_task(task.id)
        while TaskStatus(t.status) not in TERMINAL:
            time.sleep(0.2)
            t = store.get_task(task.id)
    finally:
        runner.shutdown()
    report = t.report or {}
    gt_pass = bool(t.workspace_path) and _ground_truth(
        sandbox, Path(t.workspace_path), case.ground_truth, s.tool_timeout_seconds * 3
    )
    claimed = bool(report.get("agent", {}).get("claimed_done"))
    provider_retries = sum(
        1
        for e in store.events_after(t.id, 0, 10000)
        if e.type == "retry.decision" and e.data.get("kind") == "provider"
    )
    result.update(
        status=t.status,
        outcome=t.outcome,
        error=t.error,
        ground_truth_pass=gt_pass,
        claimed_done=claimed,
        false_claim=claimed and not gt_pass,  # the agent said "done" but the independent check disagrees
        harness_false_accept=(t.outcome == "verified" and not gt_pass),  # would be a harness bug
        tool_calls=t.tool_calls,
        tool_failures=t.tool_failures,
        policy_denials=t.policy_denials,
        retries=t.recovery_attempts + provider_retries,
        iterations=t.iterations,
        input_tokens=t.input_tokens,
        output_tokens=t.output_tokens,
        estimated_cost_usd=report.get("usage", {}).get("estimated_cost_usd"),
        duration_seconds=round(duration_seconds(t) or time.monotonic() - start, 2),
    )
    return result


def selftest_mismatches(case: Case, r: dict[str, Any]) -> list[str]:
    exp, bad = case.selftest_expect, []
    for key in ("status", "outcome", "false_claim"):
        if key in exp and r[key] != exp[key]:
            bad.append(f"{key}: expected {exp[key]!r}, got {r[key]!r}")
    if r["policy_denials"] < exp.get("min_policy_denials", 0):
        bad.append(f"policy_denials: expected >= {exp['min_policy_denials']}, got {r['policy_denials']}")
    if r["harness_false_accept"]:
        bad.append("harness accepted a result that fails the ground truth")
    return bad


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(results) or 1
    cost = [r["estimated_cost_usd"] for r in results if r["estimated_cost_usd"] is not None]
    return {
        "cases": len(results),
        "completion_rate": sum(r["status"] == "completed" for r in results) / n,
        "verification_pass_rate": sum(r["outcome"] == "verified" for r in results) / n,
        "ground_truth_pass_rate": sum(r["ground_truth_pass"] for r in results) / n,
        "false_success_claims": sum(r["false_claim"] for r in results),
        "harness_false_accepts": sum(r["harness_false_accept"] for r in results),
        "tool_failures": sum(r["tool_failures"] for r in results),
        "retries": sum(r["retries"] for r in results),
        "policy_violation_attempts_blocked": sum(r["policy_denials"] for r in results),
        "mean_duration_seconds": round(sum(r["duration_seconds"] for r in results) / n, 2),
        "input_tokens": sum(r["input_tokens"] for r in results),
        "output_tokens": sum(r["output_tokens"] for r in results),
        "estimated_cost_usd": round(sum(cost), 4) if cost else None,
    }


def run_evaluation(
    settings: Settings,
    provider_kind: str,
    cases: list[Case],
    label: str = "",
    budget_usd: float | None = None,
) -> dict[str, Any]:
    """`provider_kind`: scripted (mock self-test) | anthropic | openai (real). `budget_usd` stops a real run
    before the next case once the cumulative estimated cost reaches it (needs FORGE_PRICE_* to be set)."""
    real = provider_kind != "scripted"
    if real:
        settings = settings.model_copy(update={"provider": provider_kind})
        problem = settings.provider_problem()
        if problem:
            raise ConfigError(
                f"Cannot run a real evaluation: {problem} Use --provider scripted for the harness self-test."
            )
        if budget_usd is not None and not settings.prices_known:
            raise ConfigError(
                "--budget-usd needs FORGE_PRICE_INPUT_PER_MTOK and FORGE_PRICE_OUTPUT_PER_MTOK to be set."
            )
        cases = [c for c in cases if not c.selftest_only]
    results, mismatches, skipped = [], {}, []
    spent = 0.0
    workroot = Path(tempfile.mkdtemp(prefix="forge-eval-"))
    try:
        for case in cases:
            if budget_usd is not None and spent >= budget_usd:
                skipped.append(case.id)
                continue
            if not real:
                provider: ModelProvider = ScriptedProvider(case.scripted_steps)
            else:
                provider = provider_factory(settings)()
            r = run_case(case, settings, provider, workroot)
            results.append(r)
            spent += r["estimated_cost_usd"] or 0.0
            if not real and (bad := selftest_mismatches(case, r)):
                mismatches[case.id] = bad
    finally:
        shutil.rmtree(workroot, ignore_errors=True)
    mock = not real
    return {
        "label": label or provider_kind,
        "provider": "scripted-mock" if mock else provider_kind,
        "model": None if mock else settings.model_name,
        "budget_usd": budget_usd,
        "skipped_for_budget": skipped,
        "valid_for_llm_claims": not mock,
        "notice": "SELF-TEST: scripted mock provider. Measures harness mechanics only, NOT LLM performance."
        if mock
        else None,
        "config": {
            "execution_mode": settings.execution_mode,
            "approval_policy": settings.approval_policy,
            "max_iterations": settings.max_iterations,
            "max_recovery_attempts": settings.max_recovery_attempts,
        },
        "summary": summarize(results),
        "results": results,
        "selftest_mismatches": mismatches if mock else None,
    }
