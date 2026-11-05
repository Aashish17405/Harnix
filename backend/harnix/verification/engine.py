"""Independent verification. Runs deterministic checks itself; the model's claims are never evidence."""

from __future__ import annotations

import re
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path

from harnix.execution.project import ProjectInfo
from harnix.execution.sandbox import Sandbox
from harnix.policies.commands import CommandSyntaxError, parse_command

BLOCKING_KINDS = {"test", "acceptance"}
MISSING_TOOL = re.compile(r"No module named '?(pytest|ruff|pyright|mypy|flake8|black)\b")


@dataclass
class Check:
    name: str
    kind: str  # test | lint | typecheck | acceptance | criterion
    command: str | None = None
    note: str | None = None  # why it cannot run, if no command


@dataclass
class CheckResult:
    name: str
    kind: str
    command: str | None
    status: str  # passed | failed | skipped | unavailable
    exit_code: int | None = None
    duration_seconds: float = 0.0
    output: str = ""
    note: str | None = None

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class Verdict:
    outcome: str  # verified | unverified | verification_failed
    blocking: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return asdict(self)


def plan_checks(project: ProjectInfo, verification_commands: list[str], criteria: list[str]) -> list[Check]:
    checks: list[Check] = []
    seen: set[str] = set()

    def add(name: str, kind: str, cmd: str) -> None:
        if cmd not in seen:
            seen.add(cmd)
            checks.append(Check(name, kind, cmd))

    for cmd in verification_commands:
        add(cmd, "acceptance", cmd)
    for kind in ("test", "lint", "typecheck"):
        if kind in project.commands:
            add(project.commands[kind], kind, project.commands[kind])
    if not any(c.kind in BLOCKING_KINDS for c in checks):
        checks.append(
            Check("tests", "test", None, "No test command detected and no verification command supplied")
        )
    for text in criteria:
        checks.append(
            Check(
                text[:120],
                "criterion",
                None,
                "Free-text criterion; not machine-checkable (needs human review)",
            )
        )
    return checks


SYNTAX_SUFFIXES = {
    ".py": "python -m py_compile",
    ".js": "node --check",
    ".mjs": "node --check",
    ".cjs": "node --check",
}


def plan_final_checks(
    base: list[Check], project_now: ProjectInfo, changed: list[str], baseline_had_test: bool
) -> list[Check]:
    """The checks for the final verification: the ones planned at the start, plus what only exists now.

    - Tests the task itself created (the repository had none when it started) are run as `agent_test`: they are real
      executions, but the agent wrote both sides, so they cannot make the result "verified" on their own.
    - A syntax/compile check of every changed source file (`syntax`): catches broken code even when no tests exist.
    """
    checks = [
        c for c in base if not (c.command is None and c.kind == "test")
    ]  # the "no test command" placeholder
    seen = {c.command for c in checks}
    test_cmd = project_now.commands.get("test")
    if test_cmd and test_cmd not in seen:
        checks.append(Check(test_cmd, "agent_test" if not baseline_had_test else "test", test_cmd))
        seen.add(test_cmd)
    elif not test_cmd and not any(c.kind in BLOCKING_KINDS for c in checks):
        checks.append(
            Check("tests", "test", None, "No test command detected and no verification command supplied")
        )
    by_tool: dict[str, list[str]] = {}
    for p in changed:
        tool = SYNTAX_SUFFIXES.get(Path(p).suffix.lower())
        if tool and not any(ch in p for ch in " \"'"):  # paths with spaces/quotes: skip rather than mis-parse
            by_tool.setdefault(tool, []).append(p)
    for tool, files in by_tool.items():
        for chunk in (files[i : i + 20] for i in range(0, len(files), 20)):
            cmd = f"{tool} {' '.join(chunk)}"
            if cmd not in seen:
                checks.append(Check(f"syntax of {len(chunk)} changed file(s)", "syntax", cmd))
                seen.add(cmd)
    # criteria stay in the list
    return checks


class VerificationEngine:
    def __init__(self, sandbox: Sandbox, *, timeout: float, max_output: int):
        self.sandbox, self.timeout, self.max_output = sandbox, timeout, max_output

    def run(
        self, checks: list[Check], workspace: Path, cancel: threading.Event | None = None
    ) -> list[CheckResult]:
        out: list[CheckResult] = []
        for c in checks:
            if c.command is None:
                out.append(CheckResult(c.name, c.kind, None, "unavailable", note=c.note))
            elif cancel is not None and cancel.is_set():
                out.append(
                    CheckResult(c.name, c.kind, c.command, "skipped", note="cancelled before this check ran")
                )
            else:
                out.append(self._run_one(c, workspace, cancel))
        return out

    def skipped(self, checks: list[Check], reason: str) -> list[CheckResult]:
        return [
            CheckResult(
                c.name,
                c.kind,
                c.command,
                "skipped" if c.command else "unavailable",
                note=reason if c.command else c.note,
            )
            for c in checks
        ]

    def _run_one(self, c: Check, workspace: Path, cancel: threading.Event | None) -> CheckResult:
        assert c.command
        try:
            argv = parse_command(c.command)
        except CommandSyntaxError as e:
            return CheckResult(c.name, c.kind, c.command, "unavailable", note=str(e))
        res = self.sandbox.run(
            argv,
            workspace=workspace,
            workdir=workspace,
            timeout=self.timeout,
            cancel=cancel,
            max_output=self.max_output,
        )

        def result(status: str, note: str | None = None) -> CheckResult:
            return CheckResult(
                c.name, c.kind, c.command, status, res.exit_code, round(res.duration, 2), res.output, note
            )

        if res.cancelled:
            return result("skipped", "cancelled")
        if res.not_found:
            return result("unavailable", res.error or "executable not found")
        if res.timed_out:
            return result("failed", f"timed out after {self.timeout:.0f}s")
        if res.exit_code == 0:
            return result("passed")
        if "pytest" in c.command and res.exit_code == 5:
            return result("unavailable", "pytest collected no tests")
        m = MISSING_TOOL.search(res.output)
        if m:
            return result("unavailable", f"tool '{m.group(1)}' is not installed in the execution environment")
        return result("failed")


def aggregate(baseline: list[CheckResult], final: list[CheckResult]) -> Verdict:
    """Combine final results with the pre-task baseline into an honest verdict."""
    base_status = {r.command: r.status for r in baseline if r.command}
    v = Verdict("unverified")
    passed_blocking = False
    for r in final:
        if r.status == "failed":
            pre_existing = base_status.get(r.command or "") == "failed"
            if r.kind in BLOCKING_KINDS or not pre_existing:
                v.blocking.append(r.name)
                v.reasons.append(
                    f"{r.kind} check failed: {r.name}"
                    + (" (also failed before the task)" if pre_existing else "")
                )
            else:
                v.limitations.append(
                    f"{r.kind} check '{r.name}' was already failing before the task; not counted as a regression"
                )
        elif r.status == "passed" and r.kind in BLOCKING_KINDS:
            passed_blocking = True
        elif r.status in ("unavailable", "skipped"):
            v.limitations.append(f"{r.kind} check '{r.name}' {r.status}: {r.note}")
    if v.blocking:
        v.outcome = "verification_failed"
    elif passed_blocking:
        v.outcome = "verified"
    else:
        extra = [r for r in final if r.status == "passed"]
        msg = "No test or acceptance check passed, so the result cannot be called verified"
        if extra:
            what = ", ".join(
                "agent-written tests" if r.kind == "agent_test" else f"{r.kind} check" for r in extra
            )
            msg += f". These did run and pass: {what}. They are not independent evidence of correctness"
        v.reasons.append(msg)
    return v
