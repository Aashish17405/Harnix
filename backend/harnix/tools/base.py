"""Tool plumbing shared by all tools."""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

from harnix.execution.project import ProjectInfo
from harnix.execution.sandbox import Sandbox
from harnix.execution.terminal import ProcessManager
from harnix.graph.query import RepoGraph
from harnix.harness.skills import SkillRegistry
from harnix.mcp.manager import McpManager
from harnix.models.base import ToolSpec
from harnix.policies.paths import WorkspacePaths


@dataclass
class ToolContext:
    paths: WorkspacePaths
    sandbox: Sandbox
    project: ProjectInfo
    timeout: int
    max_output: int
    max_read: int
    max_write: int
    cancel: threading.Event | None = None
    extra_commands: list[str] = field(default_factory=list)  # task-specified verification commands
    graph: RepoGraph | None = None  # knowledge graph view of this repository, when available
    touched: set[str] = field(
        default_factory=set
    )  # files the agent changed (the graph describes the task start)
    skills: SkillRegistry | None = None  # skills this task may load
    spawn: Callable[[str, str, str, bool], ToolResult] | None = (
        None  # start a sub-agent (None inside sub-agents)
    )
    remember: Callable[[str], ToolResult] | None = None  # save a project-memory note
    mcp: McpManager | None = None  # tools of the MCP servers connected for this task
    processes: ProcessManager | None = None  # host terminal (FORGE_TERMINAL=host): background processes
    owner: str = ""  # session (or task) id that owns the processes it starts
    task_id: str = ""
    secrets: list[str] = field(default_factory=list)  # values kept out of the host terminal environment

    @property
    def workspace(self):
        return self.paths.root


@dataclass
class ToolResult:
    ok: bool
    data: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    mutated: bool = False  # True when the workspace changed (resets repeated-failure tracking)

    def to_text(self, limit: int) -> str:
        payload = {"ok": self.ok, **self.data}
        if self.error:
            payload["error"] = self.error
        text = json.dumps(payload, ensure_ascii=False, default=str)
        if len(text) > limit:
            text = text[:limit] + '..."[result truncated]'
        return text


def truncate(text: str, limit: int) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    half = limit // 2
    return text[:half] + "\n...[truncated]...\n" + text[-half:], True


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    args_model: type[BaseModel]
    run: Callable[[Any, ToolContext], ToolResult]

    def spec(self) -> ToolSpec:
        schema = self.args_model.model_json_schema()
        schema.pop("title", None)
        return ToolSpec(self.name, self.description, schema)
