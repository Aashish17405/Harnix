"""The only path by which a model-proposed action can run: validate -> authorize -> (approve) -> execute."""

from __future__ import annotations

import time
from dataclasses import replace
from typing import Any

from pydantic import ValidationError

from harnix.mcp.manager import PREFIX as MCP_PREFIX
from harnix.mcp.manager import McpError, check_args
from harnix.models.base import INVALID_JSON_KEY, ToolUseBlock
from harnix.persistence.store import Store
from harnix.policies.approvals import ApprovalGate
from harnix.policies.budget import Budget
from harnix.policies.commands import CommandSyntaxError
from harnix.policies.paths import PathViolation
from harnix.policies.policy import PolicyEngine
from harnix.policies.redaction import Redactor
from harnix.tools.base import ToolContext, ToolResult
from harnix.tools.registry import TOOLS

EVENT_OUTPUT_LIMIT = 4000


class ToolExecutor:
    def __init__(
        self, *, store: Store, task_id: str, ctx: ToolContext, policy: PolicyEngine, gate: ApprovalGate,
        budget: Budget, redactor: Redactor, agent: str = "main", allowed: set[str] | None = None,
    ):  # fmt: skip
        self.store, self.task_id, self.ctx, self.policy = store, task_id, ctx, policy
        self.gate, self.budget, self.redactor = gate, budget, redactor
        self.agent = agent  # "main" or a sub-agent label: recorded on every event it emits
        self.allowed = allowed  # None = every registered tool; otherwise this agent's tool allowlist

    def for_agent(self, agent: str, allowed: set[str], ctx: ToolContext) -> ToolExecutor:
        """The same pipeline (policy, approvals, budget, redaction) for a sub-agent with its own tools and context."""
        return ToolExecutor(
            store=self.store, task_id=self.task_id, ctx=ctx, policy=self.policy, gate=self.gate,
            budget=self.budget, redactor=self.redactor, agent=agent, allowed=allowed,
        )  # fmt: skip

    def child_context(self, **changes: Any) -> ToolContext:
        return replace(self.ctx, **changes)

    def _event(self, type_: str, data: dict[str, Any]) -> None:
        if self.agent != "main":
            data = {**data, "agent": self.agent}
        self.store.add_event(self.task_id, type_, data)

    def _fail(self, call: ToolUseBlock, error: str, *, denied: bool = False) -> ToolResult:
        self.store.bump(self.task_id, tool_failures=1, **({"policy_denials": 1} if denied else {}))
        self._event("tool.result", {"call_id": call.id, "tool": call.name, "ok": False, "error": error})
        return ToolResult(False, error=error)

    def _execute_mcp(self, call: ToolUseBlock) -> ToolResult:
        """A tool of an MCP server: same pipeline (allowlist, validation, policy, approval, redaction), no workspace effect."""
        mcp = self.ctx.mcp
        t = mcp.tools.get(call.name) if mcp else None
        if mcp is None or t is None:
            return self._fail(call, f"Unknown tool '{call.name}'. Available: {', '.join(TOOLS)}")
        if self.allowed is not None and call.name not in self.allowed:
            return self._fail(call, f"Tool '{call.name}' is not available to this agent.", denied=True)
        if INVALID_JSON_KEY in call.input:
            return self._fail(call, "Invalid arguments: the tool arguments were not valid JSON; resend the call with a valid JSON object")  # fmt: skip
        problem = check_args(t.schema, call.input)
        if problem:
            return self._fail(call, f"Invalid arguments: {problem}")
        decision = self.policy.authorize_mcp(t.server, t.tool, mcp.configs[t.server].auto_approve)
        self._event("tool.authorization", {"call_id": call.id, "tool": call.name, "decision": decision.kind, "reason": decision.reason})  # fmt: skip
        if decision.kind == "deny":
            return self._fail(call, f"Denied by policy: {decision.reason}", denied=True)
        if decision.kind == "approve":
            outcome = self.gate.request(call.name, call.input, decision.reason)
            if not outcome.approved:
                return self._fail(call, f"Not approved: {outcome.reason}", denied=True)
        start = time.monotonic()
        try:
            ok, text, cut = mcp.call(call.name, call.input, self.ctx.timeout, self.ctx.max_output)
        except McpError as e:
            return self._fail(call, f"MCP server '{t.server}': {self.redactor.redact(str(e))}")
        except Exception as e:  # noqa: BLE001
            return self._fail(call, f"Tool error: {type(e).__name__}: {self.redactor.redact(str(e))}")
        text = self.redactor.redact(text)
        result = ToolResult(ok, {"server": t.server, "tool": t.tool, "output": text, "truncated": cut}, error=None if ok else text[:2000])  # fmt: skip
        if not ok:
            self.store.bump(self.task_id, tool_failures=1)
        self._event("tool.result", {
            "call_id": call.id, "tool": call.name, "ok": ok, "error": result.error,
            "duration_seconds": round(time.monotonic() - start, 3),
            "data": {"server": t.server, "tool": t.tool, "output": text[:EVENT_OUTPUT_LIMIT]},
        })  # fmt: skip
        return result

    def execute(self, call: ToolUseBlock) -> ToolResult:
        self._event("tool.proposed", {"call_id": call.id, "tool": call.name, "arguments": call.input})
        self.budget.add_tool_call()
        self.store.bump(self.task_id, tool_calls=1)

        if call.name.startswith(MCP_PREFIX) and call.name not in TOOLS:
            return self._execute_mcp(call)
        tool = TOOLS.get(call.name)
        if tool is None:
            return self._fail(call, f"Unknown tool '{call.name}'. Available: {', '.join(TOOLS)}")
        if self.allowed is not None and call.name not in self.allowed:
            return self._fail(
                call,
                f"Tool '{call.name}' is not available to this agent. Available: {', '.join(sorted(self.allowed))}",
                denied=True,
            )
        if INVALID_JSON_KEY in call.input:
            return self._fail(
                call,
                "Invalid arguments: the tool arguments were not valid JSON; resend the call with a valid JSON object",
            )
        try:
            args = tool.args_model.model_validate(call.input)
        except ValidationError as e:
            msg = "; ".join(f"{'.'.join(map(str, x['loc']))}: {x['msg']}" for x in e.errors())
            return self._fail(call, f"Invalid arguments: {msg}")

        decision = self.policy.authorize(call.name, args)
        self._event(
            "tool.authorization",
            {"call_id": call.id, "tool": call.name, "decision": decision.kind, "reason": decision.reason},
        )
        if decision.kind == "deny":
            return self._fail(call, f"Denied by policy: {decision.reason}", denied=True)
        if decision.kind == "approve":
            outcome = self.gate.request(call.name, args.model_dump(), decision.reason)
            if not outcome.approved:
                return self._fail(call, f"Not approved: {outcome.reason}", denied=True)

        start = time.monotonic()
        try:
            result = tool.run(args, self.ctx)  # always the validated args, never the raw model input
        except (PathViolation, CommandSyntaxError) as e:
            return self._fail(call, f"Denied: {e}", denied=True)
        except Exception as e:  # tool bugs/OS errors become structured failures, never crash the loop
            return self._fail(call, f"Tool error: {type(e).__name__}: {e}")
        if result.ok and call.name in ("write_file", "edit_file", "delete_file") and result.data.get("path"):
            self.ctx.touched.add(
                str(result.data["path"])
            )  # the graph describes the task start, not these files
        result.data = self.redactor.redact_obj(result.data)
        if result.error:
            result.error = self.redactor.redact(result.error)
        if not result.ok:
            self.store.bump(self.task_id, tool_failures=1)
        ev = {k: (v[:EVENT_OUTPUT_LIMIT] if isinstance(v, str) else v) for k, v in result.data.items()}
        self._event(
            "tool.result",
            {
                "call_id": call.id, "tool": call.name, "ok": result.ok, "error": result.error,
                "duration_seconds": round(time.monotonic() - start, 3), "data": ev,
            },
        )  # fmt: skip
        return result
