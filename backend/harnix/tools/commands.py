"""Command tools. The policy layer has already classified/approved the argv before these run."""

from __future__ import annotations

from harnix.execution.sandbox import CommandResult
from harnix.policies.commands import parse_command
from harnix.tools.base import ToolContext, ToolResult, truncate
from harnix.tools.schemas import RunCommandArgs, RunTestsArgs


def _result(res: CommandResult, ctx: ToolContext, **extra) -> ToolResult:
    out, trunc = truncate(res.output, ctx.max_output)
    data = {
        "command": " ".join(res.argv), "exit_code": res.exit_code, "duration_seconds": round(res.duration, 2),
        "output": out, "truncated": trunc or res.truncated, **extra,
    }  # fmt: skip
    if res.timed_out:
        return ToolResult(False, data, error="Command timed out and was killed", mutated=True)
    if res.cancelled:
        return ToolResult(False, data, error="Command cancelled", mutated=True)
    if res.not_found:
        return ToolResult(False, data, error=res.error or "Executable not found")
    if res.error:
        return ToolResult(False, data, error=res.error)
    return ToolResult(True, data, mutated=True)  # commands may change files (formatters, builds)


def exec_command(command: str, workdir: str, timeout: int | None, ctx: ToolContext) -> ToolResult:
    argv = parse_command(command)
    wd = ctx.paths.resolve(workdir)
    if not wd.is_dir():
        return ToolResult(False, error=f"Not a directory: {workdir}")
    res = ctx.sandbox.run(
        argv, workspace=ctx.workspace, workdir=wd, timeout=min(timeout or ctx.timeout, ctx.timeout * 5),
        cancel=ctx.cancel, max_output=ctx.max_output * 2,
    )  # fmt: skip
    return _result(res, ctx)


def run_command(a: RunCommandArgs, ctx: ToolContext) -> ToolResult:
    return exec_command(a.command, a.workdir, a.timeout_seconds, ctx)


def run_tests(a: RunTestsArgs, ctx: ToolContext) -> ToolResult:
    cmd = a.command or ctx.project.commands.get("test")
    if not cmd:
        return ToolResult(False, error="No test command detected; pass `command` explicitly")
    r = exec_command(cmd, ".", None, ctx)
    if r.ok:
        code = r.data["exit_code"]
        r.data["passed"] = code == 0
        if code == 5 and "pytest" in cmd:
            r.data["note"] = "pytest collected no tests"
    return r
