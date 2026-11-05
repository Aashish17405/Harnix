# MCP servers

Harnix can connect to [Model Context Protocol](https://modelcontextprotocol.io) servers and offer their tools to the agent, configured with the same `mcp.json` shape Claude Code uses (see [mcp.example.json](../mcp.example.json)).

## Configure

Put a file at `~/.forge/mcp.json` (or point `HARNIX_MCP_CONFIG` at one or more files, separated by `;` on Windows / `:` elsewhere; later files override earlier ones by server name):

```json
{"mcpServers": {
  "files":  {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "."], "autoApprove": ["list_directory"]},
  "remote": {"type": "http", "url": "https://example.com/mcp", "headers": {"Authorization": "Bearer ${TOKEN}"}}
}}
```

* stdio server: `command`, `args`, `env`, `cwd` (default: the repository). HTTP server: `type: "http"` (streamable HTTP), `url`, `headers`.
* `${VAR}` / `${VAR:-default}` are expanded from the backend's environment (a missing variable without default makes that entry invalid, and is reported).
* Harnix extras: `disabled: true` skips a server; `autoApprove: ["tool", ...]` lets those tools run without asking.
* Not supported: the legacy `sse` transport (an error says so), OAuth login for remote servers (send a token in `headers`), resources/prompts, and server-to-client requests (sampling, roots, elicitation are refused).

Servers start with each task and stop when it ends. The model sees their tools as `mcp__<server>__<tool>`. What happened is in the task's `mcp.servers` event (connected servers with tool counts, failures, config problems, skipped project servers).

## Safety

MCP servers run on your machine **outside the sandbox**, so each call asks for approval unless you listed the tool in `autoApprove`; a `.mcp.json` in a repository is ignored unless `HARNIX_MCP_TRUST_PROJECT=true`; the child process does not inherit the backend's environment. Details: [security.md](security.md#mcp-servers).

## Settings

`HARNIX_MCP_ENABLED`, `HARNIX_MCP_CONFIG`, `HARNIX_MCP_TRUST_PROJECT`, `HARNIX_MCP_START_TIMEOUT_SECONDS` (tool calls use `HARNIX_TOOL_TIMEOUT_SECONDS`).

## Limits

Tested with a fake server and a mocked HTTP transport, not with third-party servers. Servers are started per task (slow ones add start-up time). On Windows `npx` is resolved to `npx.cmd`. Sub-agents do not get MCP tools.
