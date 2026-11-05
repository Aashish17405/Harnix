"""Tools that reach harness capabilities (skills, sub-agents, memory) through callbacks on the ToolContext.

The harness injects the callbacks; a tool never touches the runner, the database or the provider directly.
"""

from __future__ import annotations

from harnix.policies.paths import PathViolation
from harnix.tools.base import ToolContext, ToolResult
from harnix.tools.schemas import LoadSkillArgs, RememberArgs, SpawnAgentArgs


def load_skill(a: LoadSkillArgs, ctx: ToolContext) -> ToolResult:
    reg = ctx.skills
    if reg is None or not reg.skills:
        return ToolResult(False, error="No skills are available for this task")
    skill = reg.skills.get(a.id)
    if skill is None:
        listed = ", ".join(s.id for s in reg.listed()) or "none"
        return ToolResult(False, error=f"Unknown skill '{a.id}'. Available: {listed}")
    if a.file is None:
        return ToolResult(True, {"skill": skill.id, "source": skill.source, "content": reg.render(skill)})
    try:
        return ToolResult(True, {"skill": skill.id, "file": a.file, "content": reg.read_file(skill, a.file)})
    except (PathViolation, FileNotFoundError, ValueError, OSError) as e:
        return ToolResult(False, error=f"Cannot read '{a.file}' of skill {skill.id}: {e}")


def spawn_agent(a: SpawnAgentArgs, ctx: ToolContext) -> ToolResult:
    if ctx.spawn is None:
        return ToolResult(
            False, error="Sub-agents are not available here (a sub-agent cannot start another one)"
        )
    return ctx.spawn(a.agent, a.description, a.prompt, a.background)


def remember(a: RememberArgs, ctx: ToolContext) -> ToolResult:
    if ctx.remember is None:
        return ToolResult(False, error="Project memory is not available for this task")
    return ctx.remember(a.note)
