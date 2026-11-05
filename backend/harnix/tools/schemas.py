"""Strict argument schemas for every tool (extra fields are rejected)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ListFilesArgs(_Args):
    path: str = Field(".", description="Directory relative to the workspace root")
    recursive: bool = False
    max_entries: int = Field(200, ge=1, le=1000)


class ReadFileArgs(_Args):
    path: str = Field(description="File path relative to the workspace root")
    start_line: int = Field(1, ge=1)
    max_lines: int = Field(400, ge=1, le=2000)


class SearchTextArgs(_Args):
    pattern: str = Field(min_length=1, max_length=200)
    path: str = "."
    regex: bool = Field(False, description="Treat pattern as an extended regular expression")
    case_sensitive: bool = False
    max_results: int = Field(50, ge=1, le=200)


class GitStatusArgs(_Args):
    pass


class GitDiffArgs(_Args):
    path: str | None = Field(None, description="Limit the diff to this path")


class WriteFileArgs(_Args):
    path: str
    content: str = Field(description="Complete new file content (creates or overwrites)")


class EditFileArgs(_Args):
    path: str
    old_text: str = Field(
        min_length=1, description="Exact text to replace; must match exactly once unless replace_all"
    )
    new_text: str
    replace_all: bool = False


class DeleteFileArgs(_Args):
    path: str


class RunCommandArgs(_Args):
    command: str = Field(
        min_length=1, max_length=1000, description="Single command; no shell syntax (pipes, &&, redirects)"
    )
    workdir: str = Field(".", description="Working directory relative to the workspace root")
    timeout_seconds: int | None = Field(None, ge=1, le=600)


class GraphQueryArgs(_Args):
    op: Literal[
        "search_symbols",
        "find_symbol",
        "callers",
        "callees",
        "hierarchy",
        "file_info",
        "dependents",
        "tests_for",
    ] = Field(
        description="search_symbols: names containing target | find_symbol: definitions of a symbol | callers / callees "
        "of a function or Class.method | hierarchy: base classes and subclasses | file_info: symbols, imports and "
        "importers of a file | dependents: files that import a file (transitively, depth 1-3) | tests_for: test files "
        "that import a file"
    )
    target: str = Field(
        min_length=1,
        max_length=200,
        description="A symbol name (or Class.method) or a repo-relative file path",
    )
    limit: int | None = Field(None, ge=1, le=50)
    depth: int = Field(2, ge=1, le=3, description="Only for dependents")


class RunTestsArgs(_Args):
    command: str | None = Field(
        None, description="Test command; defaults to the detected project test command"
    )


class LoadSkillArgs(_Args):
    id: str = Field(
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$",
        description="A skill id from <available_skills>",
    )
    file: str | None = Field(
        None, max_length=300, description="Optional: a resource file of the skill, relative to its directory"
    )


class SpawnAgentArgs(_Args):
    agent: str = Field(min_length=1, max_length=64, description="An agent id from <available_agents>")
    description: str = Field(
        min_length=1, max_length=80, description="A short 3-5 word label for the sub-task"
    )
    prompt: str = Field(
        min_length=1,
        max_length=20_000,
        description="The complete task for the sub-agent. It sees nothing else: include all paths, facts and constraints it needs",
    )
    background: bool = Field(
        False,
        description="Read-only agents only (e.g. explore): run it in parallel and return immediately; its report is delivered "
        "to you automatically later. Do not poll or wait for it, and do not repeat its work",
    )


class RememberArgs(_Args):
    note: str = Field(
        min_length=1,
        max_length=500,
        description="One durable fact about this repository worth knowing in future tasks (a convention, a gotcha, "
        "where something lives). Never secrets",
    )


class TerminalRunArgs(_Args):
    command: str = Field(
        min_length=1,
        max_length=1000,
        description="One command run on the host machine (no shell syntax: pipes, &&, redirects)",
    )
    workdir: str = Field(".", description="Working directory relative to the workspace root")
    timeout_seconds: int | None = Field(None, ge=1, le=600)


class TerminalStartArgs(_Args):
    command: str = Field(
        min_length=1,
        max_length=1000,
        description="One command that keeps running (a dev server), run on the host machine",
    )
    workdir: str = Field(".", description="Working directory relative to the workspace root")
    wait_for_url: str | None = Field(
        None,
        max_length=300,
        description="A http://localhost:PORT/... URL to poll until it answers; the result says whether it did",
    )
    wait_seconds: int = Field(15, ge=1, le=60)


class TerminalOutputArgs(_Args):
    process_id: str = Field(min_length=1, max_length=20)
    tail_chars: int = Field(4000, ge=100, le=20000)


class TerminalStopArgs(_Args):
    process_id: str = Field(min_length=1, max_length=20)
