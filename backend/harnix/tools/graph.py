"""Knowledge-graph tool. Read-only, fixed operations; the model supplies values, never query text."""

from __future__ import annotations

from harnix.graph.store import GraphUnavailable
from harnix.tools.base import ToolContext, ToolResult
from harnix.tools.schemas import GraphQueryArgs


def graph_query(a: GraphQueryArgs, ctx: ToolContext) -> ToolResult:
    if ctx.graph is None:
        return ToolResult(
            False, error="The knowledge graph is not available for this task; use the file tools instead"
        )
    try:
        return ToolResult(True, ctx.graph.op(a.op, a.target, a.limit, a.depth))
    except ValueError as e:
        return ToolResult(False, error=str(e))
    except GraphUnavailable as e:
        return ToolResult(False, error=f"Knowledge graph unavailable: {e}")
