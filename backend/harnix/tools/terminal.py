"""Host terminal tools (offered only with FORGE_TERMINAL=host). Policy has already classified/approved the argv."""

from __future__ import annotations

from harnix.execution.terminal import run_foreground
from harnix.policies.commands import parse_command
from harnix.tools.base import ToolContext, ToolResult, truncate
from harnix.tools.commands import _result
from harnix.tools.schemas import TerminalOutputArgs, TerminalRunArgs, TerminalStartArgs, TerminalStopArgs


def terminal_run(a: TerminalRunArgs, ctx: ToolContext) -> ToolResult:
    wd = ctx.paths.resolve(a.workdir)
    if not wd.is_dir():
        return ToolResult(False, error=f"Not a directory: {a.workdir}")
    res = run_foreground(
        parse_command(a.command), workdir=wd, timeout=min(a.timeout_seconds or ctx.timeout, ctx.timeout * 5),
        cancel=ctx.cancel, max_output=ctx.max_output * 2, secrets=ctx.secrets,
    )  # fmt: skip
    return _result(res, ctx, where="host")


def terminal_start(a: TerminalStartArgs, ctx: ToolContext) -> ToolResult:
    pm = ctx.processes
    if pm is None:
        return ToolResult(False, error="The host terminal is not enabled (FORGE_TERMINAL=host)")
    wd = ctx.paths.resolve(a.workdir)
    if not wd.is_dir():
        return ToolResult(False, error=f"Not a directory: {a.workdir}")
    try:
        mp = pm.start(
            parse_command(a.command), cwd=wd, owner=ctx.owner, task_id=ctx.task_id, secrets=ctx.secrets
        )
    except (FileNotFoundError, RuntimeError, OSError) as e:
        return ToolResult(False, error=str(e))
    reachable, status, note = pm.wait_ready(mp, a.wait_for_url, float(a.wait_seconds))
    data = {
        "process_id": mp.id, "pid": mp.proc.pid, "running": mp.running, "exit_code": mp.proc.poll(),
        "url": a.wait_for_url, "reachable": reachable, "http_status": status, "note": note,
        "output": truncate(mp.output(3000), ctx.max_output)[0],
        "keeps_running": "It keeps running after this task until it is stopped (terminal_stop, forge stop) or the backend exits.",
    }  # fmt: skip
    ok = mp.running and reachable is not False
    return ToolResult(ok, data, error=None if ok else (note or "the process is not running"), mutated=True)


def terminal_output(a: TerminalOutputArgs, ctx: ToolContext) -> ToolResult:
    mp = ctx.processes.get(a.process_id, ctx.owner) if ctx.processes else None
    if mp is None:
        return ToolResult(False, error=f"No background process '{a.process_id}' from this conversation")
    return ToolResult(True, {**mp.info(), "output": truncate(mp.output(a.tail_chars), ctx.max_output)[0]})


def terminal_stop(a: TerminalStopArgs, ctx: ToolContext) -> ToolResult:
    if ctx.processes is None or not ctx.processes.stop(a.process_id, ctx.owner):
        return ToolResult(False, error=f"No background process '{a.process_id}' from this conversation")
    return ToolResult(True, {"process_id": a.process_id, "stopped": True})
