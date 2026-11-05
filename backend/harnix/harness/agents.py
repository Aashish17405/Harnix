"""Sub-agent profiles (modelled on opencode's `explore` / `general` subagents).

A sub-agent is a fresh-context child loop the main agent starts with the `spawn_agent` tool: it receives only the
prompt it is given, works in the same isolated workspace under the same policy, approvals and budget, and returns
its final text. Profiles decide which tools it gets and how hard it thinks.

Custom profiles: FORGE_AGENTS_DIR/<id>.md with frontmatter `description`, `tools` (a subset of the registered tools),
`thinking` (low | medium | high | xhigh) and `max_iterations`; the body is appended to the sub-agent system prompt.
They are operator configuration, never read from the target repository.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from harnix.harness.skills import ID_RE, parse_frontmatter
from harnix.models.base import THINKING_LEVELS

# a sub-agent can never start another one (depth 1) or write project memory (that is the main agent's call)
NEVER_FOR_SUBAGENTS = frozenset({"spawn_agent", "remember"})
READ_ONLY_TOOLS = frozenset(
    {"list_files", "read_file", "search_text", "git_status", "git_diff", "graph_query", "load_skill"}
)

EXPLORE_PROMPT = """You are a code exploration specialist working for another agent.
- Your role is EXCLUSIVELY to search and analyze: you cannot change files or run commands.
- Adapt how thorough you are to what the request asks for; stop as soon as you can answer.
- Answer with what you found: exact file paths (repository-relative), line numbers, symbol names, and short quotes
  where they matter. Say clearly what you could not find."""

GENERAL_PROMPT = """You are a general-purpose agent working on one well-defined piece of a larger task for another agent.
- Do exactly the part you were given; do not expand the scope.
- End with a concise report: what you did, which files you changed, what you verified, and anything left open."""


@dataclass(frozen=True)
class AgentProfile:
    id: str
    description: str
    prompt: str
    tools: frozenset[str] | None  # None = every tool the task has (minus NEVER_FOR_SUBAGENTS)
    thinking: str | None = None  # None = the task's level
    max_iterations: int | None = None
    source: str = "builtin"

    def allowed(self, task_tools: set[str]) -> set[str]:
        base = set(task_tools) if self.tools is None else set(self.tools) & set(task_tools)
        return base - NEVER_FOR_SUBAGENTS


BUILTIN = {
    "explore": AgentProfile(
        "explore",
        "Fast, read-only agent for exploring the codebase: finding files, searching code, answering questions about how "
        "something works. Say how thorough it should be (quick / medium / very thorough).",
        EXPLORE_PROMPT, READ_ONLY_TOOLS, thinking="low",
    ),
    "general": AgentProfile(
        "general",
        "General-purpose agent for a self-contained multi-step piece of work (can edit files and run checks). Use it to "
        "keep a long side-investigation or a separable change out of your own context.",
        GENERAL_PROMPT, None,
    ),
}  # fmt: skip


def load_agents(directory: Path | None, known_tools: set[str]) -> tuple[dict[str, AgentProfile], list[str]]:
    """Built-in profiles plus custom ones from `directory`. Returns (profiles, problems)."""
    agents = dict(BUILTIN)
    problems: list[str] = []
    if directory is None or not directory.is_dir():
        return agents, problems
    for path in sorted(directory.glob("*.md")):
        if path.is_symlink():
            continue
        meta, body = parse_frontmatter(path.read_text(encoding="utf-8", errors="replace")[:64_000])
        aid = path.stem
        if not ID_RE.match(aid):
            problems.append(f"{path.name}: invalid agent id")
            continue
        tools_raw = meta.get("tools")
        tools: frozenset[str] | None = None
        if tools_raw:
            names = {
                t.strip() for t in (tools_raw if isinstance(tools_raw, list) else str(tools_raw).split(","))
            }
            unknown = names - known_tools
            if unknown:
                problems.append(f"{path.name}: unknown tools {sorted(unknown)}")
                continue
            tools = frozenset(names)
        thinking = str(meta.get("thinking") or "") or None
        if thinking and thinking not in THINKING_LEVELS:
            problems.append(f"{path.name}: thinking must be one of {', '.join(THINKING_LEVELS)}")
            continue
        try:
            max_it = int(str(meta["max_iterations"])) if meta.get("max_iterations") else None
        except ValueError:
            problems.append(f"{path.name}: max_iterations must be a number")
            continue
        description = " ".join(str(meta.get("description") or "").split())[:500]
        if not description:
            problems.append(f"{path.name}: a description is required (the main agent chooses agents by it)")
            continue
        if aid in BUILTIN:
            problems.append(f"{path.name}: overrides the built-in '{aid}' agent")
        agents[aid] = AgentProfile(aid, description, body.strip(), tools, thinking, max_it, "custom")
    return agents, problems


def render_agents(agents: dict[str, AgentProfile]) -> str:
    lines = [
        "You can delegate a self-contained piece of work to a sub-agent with the spawn_agent tool. A sub-agent starts",
        "with a fresh context and sees ONLY the prompt you give it, so include every detail it needs. It shares your",
        "workspace and budget and returns its final report. Use it to keep long searches or side tasks out of your",
        "own context; do simple lookups yourself. Read-only agents (like explore) can also run with background=true: they",
        "work in parallel while you continue, and their reports are delivered to you automatically; do not poll for them.",
        "<available_agents>",
        *[
            f'  <agent id="{a.id}">{a.description}</agent>'
            for a in sorted(agents.values(), key=lambda a: a.id)
        ],
        "</available_agents>",
    ]
    return "\n".join(lines)
