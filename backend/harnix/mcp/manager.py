"""The MCP servers of one task: started when the task starts, closed when it ends, tools named mcp__<server>__<tool>."""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass
from typing import Any

from harnix.mcp.client import Client, McpError, connect
from harnix.mcp.config import ServerConfig
from harnix.models.base import ToolSpec

log = logging.getLogger(__name__)
PREFIX = "mcp__"
MAX_NAME = 64  # the shortest limit among the providers
_BAD = re.compile(r"[^A-Za-z0-9_-]")


@dataclass
class McpTool:
    name: str  # what the model sees
    server: str
    tool: str  # the server's own name
    description: str
    schema: dict[str, Any]


def tool_name(server: str, tool: str) -> str:
    n = f"{PREFIX}{server}__{_BAD.sub('_', tool)}"
    if len(n) > MAX_NAME:
        n = n[: MAX_NAME - 9] + "_" + hashlib.sha256(f"{server}/{tool}".encode()).hexdigest()[:8]
    return n


def check_args(schema: dict[str, Any], args: dict[str, Any]) -> str | None:
    """A light check of the model's arguments against the server's JSON Schema (the server validates fully)."""
    raw_props = schema.get("properties")
    props: dict[str, Any] = raw_props if isinstance(raw_props, dict) else {}
    missing = [k for k in schema.get("required", []) if isinstance(k, str) and k not in args]
    if missing:
        return f"missing required argument(s): {', '.join(missing)}"
    kinds: dict[str, Any] = {"string": str, "boolean": bool, "array": list, "object": dict}
    for k, v in args.items():
        spec = props.get(k)
        t = spec.get("type") if isinstance(spec, dict) else None
        if t in ("number", "integer"):
            ok = (
                isinstance(v, int | float)
                and not isinstance(v, bool)
                and (t == "number" or float(v).is_integer())
            )
        elif t in kinds:
            ok = isinstance(v, kinds[t])
        else:
            ok = True
        if not ok:
            return f"argument '{k}' must be of type {t}"
    return None


def result_text(r: dict[str, Any], limit: int) -> tuple[str, bool]:
    """Flatten a tools/call result to text. Images and binary blobs are described, never inlined."""
    parts: list[str] = []
    for c in r.get("content") or []:
        if not isinstance(c, dict):
            continue
        t = c.get("type")
        if t == "text" and isinstance(c.get("text"), str):
            parts.append(c["text"])
        elif t in ("image", "audio"):
            parts.append(f"[{t} content ({c.get('mimeType', '?')}) not shown]")
        elif t == "resource" and isinstance(c.get("resource"), dict):
            res = c["resource"]
            parts.append(
                res["text"]
                if isinstance(res.get("text"), str)
                else f"[binary resource {res.get('uri', '')} not shown]"
            )
        elif t == "resource_link":
            parts.append(f"[resource: {c.get('uri', '')}]")
    if not parts and r.get("structuredContent") is not None:
        parts.append(json.dumps(r["structuredContent"], ensure_ascii=False, default=str))
    text = "\n".join(parts)
    return (text[:limit] + "\n...[truncated]", True) if len(text) > limit else (text, False)


class McpManager:
    def __init__(self) -> None:
        self.clients: dict[str, Client] = {}
        self.configs: dict[str, ServerConfig] = {}
        self.tools: dict[str, McpTool] = {}
        self.failed: dict[str, str] = {}

    def start(self, servers: list[ServerConfig], timeout: float, cwd: str | None = None) -> None:
        for cfg in servers:
            try:
                c = connect(cfg, timeout, cwd)
            except Exception as e:  # noqa: BLE001 - a broken server must not fail the task
                self.failed[cfg.name] = f"{type(e).__name__}: {e}"
                continue
            try:
                listed = c.list_tools()
            except Exception as e:  # noqa: BLE001
                c.close()
                self.failed[cfg.name] = f"tools/list failed: {e}"
                continue
            self.clients[cfg.name] = c
            self.configs[cfg.name] = cfg
            for t in listed:
                name = tool_name(cfg.name, t["name"])
                if name in self.tools:
                    continue
                raw_schema = t.get("inputSchema")
                schema = {
                    "type": "object",
                    **{
                        k: v
                        for k, v in (raw_schema if isinstance(raw_schema, dict) else {}).items()
                        if k != "$schema"
                    },
                }
                desc = str(t.get("description") or t.get("title") or t["name"])
                self.tools[name] = McpTool(
                    name, cfg.name, t["name"], f"[MCP server {cfg.name}] {desc}", schema
                )

    def secrets(self) -> list[str]:
        """Values from the configured env/headers (and the token part of a header such as 'Bearer x')."""
        out: list[str] = []
        for c in self.configs.values():
            for v in [*c.env.values(), *c.headers.values()]:
                out += [v, v.split()[-1]]
        return out

    def specs(self) -> list[ToolSpec]:
        return [ToolSpec(t.name, t.description, t.schema) for t in self.tools.values()]

    def call(self, name: str, args: dict[str, Any], timeout: float, limit: int) -> tuple[bool, str, bool]:
        """(ok, text, truncated). Transport/protocol failures raise McpError."""
        t = self.tools[name]
        r = self.clients[t.server].call_tool(t.tool, args, timeout)
        text, cut = result_text(r, limit)
        return r.get("isError") is not True, text, cut

    def close(self) -> None:
        for c in self.clients.values():
            try:
                c.close()
            except Exception:  # noqa: BLE001
                log.exception("closing an MCP server failed")
        self.clients.clear()


__all__ = ["McpError", "McpManager", "McpTool", "check_args", "result_text", "tool_name"]
