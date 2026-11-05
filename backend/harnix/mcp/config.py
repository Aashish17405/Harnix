"""mcp.json: the same shape Claude Code uses, so an existing file works unchanged.

    {"mcpServers": {
        "files":  {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "."], "env": {"K": "${K}"}},
        "remote": {"type": "http", "url": "https://example.com/mcp", "headers": {"Authorization": "Bearer ${TOKEN}"}}
    }}

`${VAR}` and `${VAR:-default}` are expanded from the environment of the backend. Extra keys: `disabled` (skip the
server), `autoApprove` (tool names that run without asking; everything else asks a human), `cwd`.

Trust: the operator's files (FORGE_MCP_CONFIG, default ~/.forge/mcp.json) are trusted. A `.mcp.json` inside the
repository is repository text, i.e. untrusted, and starting a server is running a program, so it is ignored unless
FORGE_MCP_TRUST_PROJECT=true.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_VAR = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")
NAME_OK = re.compile(r"^[A-Za-z0-9_-]{1,32}$")


@dataclass
class ServerConfig:
    name: str
    transport: str  # "stdio" | "http"
    command: str = ""
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    cwd: str | None = None
    url: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    auto_approve: frozenset[str] = frozenset()
    source: str = ""  # the file it came from (shown to the user; never contains secrets)

    def describe(self) -> dict[str, Any]:
        """What may be shown or logged: no env values, no header values, no URL query."""
        d: dict[str, Any] = {"name": self.name, "transport": self.transport, "source": self.source}
        if self.transport == "stdio":
            d["command"] = self.command
        else:
            d["url"] = self.url.split("?", 1)[0]
        d["auto_approve"] = sorted(self.auto_approve)
        return d


class McpConfigError(Exception):
    pass


def expand(value: str, env: dict[str, str] | None = None) -> str:
    source: Mapping[str, str] = os.environ if env is None else env

    def sub(m: re.Match[str]) -> str:
        v = source.get(m.group(1))
        if v is None:
            if m.group(2) is None:
                raise McpConfigError(f"environment variable {m.group(1)} is not set (used in mcp.json)")
            return m.group(2)
        return v

    return _VAR.sub(sub, value)


def _strs(v: Any, what: str) -> dict[str, str]:
    if not isinstance(v, dict) or not all(isinstance(k, str) and isinstance(x, str) for k, x in v.items()):
        raise McpConfigError(f"{what} must be an object of strings")
    return v


def parse_server(name: str, raw: Any, source: str) -> ServerConfig | None:
    """One entry; None when it is disabled. Raises McpConfigError with an actionable message."""
    if not NAME_OK.match(name):
        raise McpConfigError(f"server name {name!r}: use 1-32 letters, digits, '_' or '-'")
    if not isinstance(raw, dict):
        raise McpConfigError(f"server {name!r}: must be an object")
    if raw.get("disabled") is True:
        return None
    kind = raw.get("type") or ("stdio" if "command" in raw else "http" if "url" in raw else None)
    auto = raw.get("autoApprove", [])
    if not isinstance(auto, list) or not all(isinstance(x, str) for x in auto):
        raise McpConfigError(f"server {name!r}: autoApprove must be a list of tool names")
    try:
        if kind == "stdio":
            cmd = raw.get("command")
            if not isinstance(cmd, str) or not cmd.strip():
                raise McpConfigError(f"server {name!r}: 'command' is required for a stdio server")
            args = raw.get("args", [])
            if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
                raise McpConfigError(f"server {name!r}: args must be a list of strings")
            env = {k: expand(v) for k, v in _strs(raw.get("env", {}), f"server {name!r}: env").items()}
            cwd = expand(raw["cwd"]) if isinstance(raw.get("cwd"), str) else None
            return ServerConfig(
                name, "stdio", command=expand(cmd), args=[expand(a) for a in args], env=env, cwd=cwd,
                auto_approve=frozenset(auto), source=source,
            )  # fmt: skip
        if kind in ("http", "streamable-http"):
            url = expand(str(raw.get("url", "")))
            if not re.match(r"^https?://", url):
                raise McpConfigError(f"server {name!r}: 'url' must start with http:// or https://")
            headers = {
                k: expand(v) for k, v in _strs(raw.get("headers", {}), f"server {name!r}: headers").items()
            }
            return ServerConfig(
                name, "http", url=url, headers=headers, auto_approve=frozenset(auto), source=source
            )
    except McpConfigError as e:
        raise McpConfigError(str(e)) from None
    if kind == "sse":
        raise McpConfigError(f"server {name!r}: the legacy 'sse' transport is not supported; use type 'http' (streamable HTTP) or stdio")  # fmt: skip
    raise McpConfigError(f"server {name!r}: give either 'command' (stdio) or 'url' (type 'http')")


def load_file(path: Path) -> tuple[list[ServerConfig], list[str]]:
    """Servers and problems of one file (a bad entry never hides the good ones)."""
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return [], []
    except (OSError, ValueError) as e:
        return [], [f"{path}: cannot read ({type(e).__name__}: {e})"]
    servers = data.get("mcpServers") if isinstance(data, dict) else None
    if not isinstance(servers, dict):
        return [], [f"{path}: expected an object with a 'mcpServers' key"]
    out: list[ServerConfig] = []
    problems: list[str] = []
    for name, raw in servers.items():
        try:
            s = parse_server(str(name), raw, str(path))
        except McpConfigError as e:
            problems.append(f"{path}: {e}")
            continue
        if s is not None:
            out.append(s)
    return out, problems


def discover(
    config_paths: list[Path], project_file: Path | None, trust_project: bool
) -> tuple[list[ServerConfig], list[str], list[str]]:
    """(servers, problems, skipped_project_servers). Later files override earlier ones by server name."""
    merged: dict[str, ServerConfig] = {}
    problems: list[str] = []
    for p in config_paths:
        servers, probs = load_file(p)
        problems += probs
        merged.update({s.name: s for s in servers})
    skipped: list[str] = []
    if project_file is not None and project_file.is_file():
        servers, probs = load_file(project_file)
        if trust_project:
            problems += probs
            merged.update({s.name: s for s in servers})
        else:
            skipped = [s.name for s in servers]
    return list(merged.values()), problems, skipped
