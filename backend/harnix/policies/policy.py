"""Authorization of proposed tool calls. Pure function of (tool, validated args, settings, context).

Outcomes: allow, deny (hard refusal), approve (needs a human decision bound to these exact arguments).
Model output and repository text can never alter these rules.
"""

from __future__ import annotations

from pydantic import BaseModel

from harnix.config import Settings
from harnix.policies.commands import CommandSyntaxError, Decision, classify_command, parse_command
from harnix.policies.paths import PathViolation, WorkspacePaths
from harnix.tools.schemas import (
    DeleteFileArgs,
    EditFileArgs,
    GitDiffArgs,
    ListFilesArgs,
    ReadFileArgs,
    RunCommandArgs,
    RunTestsArgs,
    SearchTextArgs,
    TerminalRunArgs,
    TerminalStartArgs,
    WriteFileArgs,
)

READ_ONLY = {"list_files", "read_file", "search_text", "git_status", "git_diff", "graph_query", "load_skill"}
WRITES = {"write_file", "edit_file"}
KNOWN_TOOLS = READ_ONLY | WRITES | {
    "delete_file", "run_command", "run_tests", "spawn_agent", "remember",
    "terminal_run", "terminal_start", "terminal_output", "terminal_stop",
}  # fmt: skip


class PolicyEngine:
    def __init__(self, settings: Settings, paths: WorkspacePaths, trusted_commands: list[str] | None = None):
        self.settings = settings
        self.paths = paths
        self.trusted = {
            c.strip() for c in (trusted_commands or [])
        }  # harness-detected / task-specified checks

    def authorize(self, tool: str, args: BaseModel) -> Decision:
        d = self._decide(tool, args)
        if d.kind == "approve" and self.settings.approval_policy == "deny_sensitive":
            return Decision(
                "deny", f"{d.reason} (approval_policy=deny_sensitive: sensitive actions are refused)"
            )
        return d

    def authorize_mcp(self, server: str, tool: str, auto_approve: frozenset[str]) -> Decision:
        """MCP tools act outside the workspace (the server is a program running as the operator), so each call asks
        a human unless the operator listed the tool in that server's autoApprove. Server hints never lower this."""
        if tool in auto_approve:
            return Decision(
                "allow", f"'{tool}' is in autoApprove for MCP server '{server}' (set by the operator)"
            )
        d = Decision(
            "approve", f"Calls the external MCP server '{server}', which can act outside the workspace"
        )
        if self.settings.approval_policy == "deny_sensitive":
            return Decision(
                "deny", f"{d.reason} (approval_policy=deny_sensitive: sensitive actions are refused)"
            )
        return d

    def _path(self, raw: str):
        return self.paths.resolve(raw)

    def _decide(self, tool: str, args: BaseModel) -> Decision:
        if tool not in KNOWN_TOOLS:
            return Decision("deny", f"Unknown tool: {tool}")
        try:
            if isinstance(
                args,
                ListFilesArgs | ReadFileArgs | SearchTextArgs | WriteFileArgs | EditFileArgs | DeleteFileArgs,
            ):
                p = self._path(args.path)
                if tool == "read_file" and self.paths.is_secret(p):
                    return Decision("deny", "Reading secret/credential files is not permitted")
                if tool in WRITES | {"delete_file"} and self.paths.is_protected(p):
                    return Decision("deny", "Path is protected (.git/.forge)")
            if isinstance(args, GitDiffArgs) and args.path:
                self._path(args.path)
            if (
                isinstance(args, WriteFileArgs)
                and len(args.content.encode("utf-8")) > self.settings.max_write_bytes
            ):
                return Decision("deny", f"Content exceeds {self.settings.max_write_bytes} bytes")
            if isinstance(args, RunCommandArgs):
                self._path(args.workdir)
                return self._command(args.command)
            if isinstance(args, TerminalRunArgs):
                self._path(args.workdir)
                return self._host(self._command(args.command))
            if isinstance(args, TerminalStartArgs):
                self._path(args.workdir)
                return self._start(args.command)
            if isinstance(args, RunTestsArgs):
                return (
                    self._command(args.command)
                    if args.command
                    else Decision("allow", "detected project test command")
                )
        except PathViolation as e:
            return Decision("deny", str(e))

        if tool in READ_ONLY:
            return Decision("allow", "read-only operation inside the workspace")
        if tool in WRITES:
            if self.settings.approval_policy == "strict":
                return Decision(
                    "approve", "approval_policy=strict requires approval for every file modification"
                )
            return Decision("allow", "file change inside the workspace")
        if tool == "delete_file":
            return Decision("approve", "Deleting files is destructive")
        if tool in ("terminal_output", "terminal_stop"):
            return Decision("allow", "only touches background processes this conversation started")
        if tool == "spawn_agent":  # the sub-agent's own actions go through this same policy, one by one
            return Decision("allow", "sub-agent: each of its actions is authorized separately")
        if tool == "remember":
            mode = self.settings.memory_write
            if mode == "deny":
                return Decision("deny", "FORGE_MEMORY_WRITE=deny: the agent may not write project memory")
            if mode == "approve":
                return Decision("approve", "Saving a note that persists into future tasks on this repository")
            return Decision("allow", "FORGE_MEMORY_WRITE=allow")
        return Decision("deny", f"Unknown tool: {tool}")

    def _host(self, d: Decision) -> Decision:
        """The same classification as run_command, but the command runs on the user's machine, outside Docker."""
        if d.kind == "deny":
            return d
        return Decision(d.kind, d.reason + " (host terminal: runs on your machine, not in the sandbox)")

    def _start(self, command: str) -> Decision:
        try:
            argv = parse_command(command)
        except CommandSyntaxError as e:
            return Decision("deny", str(e))
        d = classify_command(argv)
        if d.kind == "deny":
            return d
        return Decision(
            "approve",
            "Starts a process on your machine that keeps running after the task, outside the sandbox",
        )

    def _command(self, command: str) -> Decision:
        try:
            argv = parse_command(command)
        except CommandSyntaxError as e:
            return Decision("deny", str(e))
        if command.strip() in self.trusted:
            return Decision("allow", "matches a harness-detected or task-specified verification command")
        return classify_command(argv)
