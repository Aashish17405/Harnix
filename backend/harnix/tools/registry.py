"""The registered toolset. The agent can only invoke tools listed here."""

from __future__ import annotations

from harnix.models.base import ToolSpec
from harnix.tools import agentic, commands, files, graph, terminal
from harnix.tools.base import Tool
from harnix.tools.schemas import (
    DeleteFileArgs,
    EditFileArgs,
    GitDiffArgs,
    GitStatusArgs,
    GraphQueryArgs,
    ListFilesArgs,
    LoadSkillArgs,
    ReadFileArgs,
    RememberArgs,
    RunCommandArgs,
    RunTestsArgs,
    SearchTextArgs,
    SpawnAgentArgs,
    TerminalOutputArgs,
    TerminalRunArgs,
    TerminalStartArgs,
    TerminalStopArgs,
    WriteFileArgs,
)

TOOLS: dict[str, Tool] = {
    t.name: t
    for t in [
        Tool("list_files", "List files and directories in the workspace.", ListFilesArgs, files.list_files),
        Tool("read_file", "Read a text file from the workspace (line-windowed).", ReadFileArgs, files.read_file),
        Tool("search_text", "Search repository text (git grep); returns file:line:text matches.", SearchTextArgs, files.search_text),
        Tool("git_status", "Show files changed since the task started.", GitStatusArgs, files.git_status),
        Tool("git_diff", "Show the diff of changes since the task started.", GitDiffArgs, files.git_diff),
        Tool("write_file", "Create or overwrite a file with complete content.", WriteFileArgs, files.write_file),
        Tool("edit_file", "Replace exact text in a file (must match exactly once unless replace_all).", EditFileArgs, files.edit_file),
        Tool("delete_file", "Delete a file. Requires human approval.", DeleteFileArgs, files.delete_file),
        Tool(
            "run_command",
            "Run one command without a shell. Permitted validation commands run directly; others require human approval.",
            RunCommandArgs, commands.run_command,
        ),
        Tool("run_tests", "Run the project's tests (detected command by default).", RunTestsArgs, commands.run_tests),
        Tool(
            "graph_query",
            "Query the repository's knowledge graph (static analysis as of the task start): find symbols, callers/callees, "
            "class hierarchy, a file's imports and importers, which files depend on a file, which tests cover it.",
            GraphQueryArgs, graph.graph_query,
        ),
        Tool(
            "load_skill",
            "Load a skill's instructions (or one of its resource files) when the task matches its description in <available_skills>.",
            LoadSkillArgs, agentic.load_skill,
        ),
        Tool(
            "spawn_agent",
            "Delegate a self-contained piece of work to a sub-agent from <available_agents>. It starts with a fresh context "
            "(only your prompt), shares this workspace and budget, and returns its final report.",
            SpawnAgentArgs, agentic.spawn_agent,
        ),
        Tool(
            "remember",
            "Save one durable fact about this repository for future tasks (shown to later runs as an unverified agent "
            "note). May require human approval. Never store secrets or task-specific details.",
            RememberArgs, agentic.remember,
        ),
        Tool(
            "terminal_run",
            "Run one command on the user's real machine (host terminal: no Docker, network available) in the workspace copy. "
            "For commands that finish. For a server or anything that keeps running use terminal_start.",
            TerminalRunArgs, terminal.terminal_run,
        ),
        Tool(
            "terminal_start",
            "Start a long-running process on the user's machine (e.g. a dev server) that keeps running after this task. "
            "Pass wait_for_url (http://localhost:PORT/...) so the result says whether it is really reachable; never tell "
            "the user a server is up unless reachable is true. Runs in the workspace copy (your edits, not yet applied).",
            TerminalStartArgs, terminal.terminal_start,
        ),
        Tool("terminal_output", "Recent output and status of a background process you started.", TerminalOutputArgs, terminal.terminal_output),
        Tool("terminal_stop", "Stop a background process you started.", TerminalStopArgs, terminal.terminal_stop),
    ]
}  # fmt: skip

# offered to the model only when the task has the resource they need (a graph, skills, sub-agents, memory)
TERMINAL_TOOLS = frozenset({"terminal_run", "terminal_start", "terminal_output", "terminal_stop"})
OPTIONAL_TOOLS = frozenset({"graph_query", "load_skill", "spawn_agent", "remember"}) | TERMINAL_TOOLS


def specs(
    *, with_graph: bool = True, optional: set[str] | None = None, only: set[str] | None = None
) -> list[ToolSpec]:
    """Tool specs for one agent: the core tools plus the enabled optional ones; `only` restricts further.

    `optional=None` keeps the earlier behaviour (graph_query when `with_graph`).
    """
    enabled = set(optional) if optional is not None else ({"graph_query"} if with_graph else set())
    return [
        t.spec()
        for t in TOOLS.values()
        if (t.name not in OPTIONAL_TOOLS or t.name in enabled) and (only is None or t.name in only)
    ]
