"""A tiny stdio MCP server for tests: tools `echo`, `fail`, `env` (reports whether a variable is visible), `big`."""

import json
import os
import sys

TOOLS = [
    {
        "name": "echo",
        "description": "Echo text",
        "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
    },
    {"name": "fail", "description": "Always an error result", "inputSchema": {"type": "object"}},
    {
        "name": "env",
        "description": "Is a variable set?",
        "inputSchema": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]},
    },
    {"name": "big", "description": "Lots of text", "inputSchema": {"type": "object"}},
    {"name": "bad.name with spaces", "description": "odd name", "inputSchema": {"type": "object"}},
]


def send(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


print("banner that is not json", flush=True)
for line in sys.stdin:
    msg = json.loads(line)
    if "id" not in msg:
        continue
    m, p, i = msg["method"], msg.get("params", {}), msg["id"]
    if m == "initialize":
        send(
            {
                "jsonrpc": "2.0",
                "id": i,
                "result": {
                    "protocolVersion": p["protocolVersion"],
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "fake", "version": "1"},
                },
            }
        )
    elif m == "tools/list":
        page = {"tools": TOOLS[:3], "nextCursor": "c2"} if not p.get("cursor") else {"tools": TOOLS[3:]}
        send({"jsonrpc": "2.0", "id": i, "result": page})
    elif m == "tools/call":
        n, a = p["name"], p["arguments"]
        if n == "echo":
            send(
                {
                    "jsonrpc": "2.0",
                    "id": i,
                    "result": {
                        "content": [
                            {"type": "text", "text": "echo: " + a["text"]},
                            {"type": "image", "data": "AAAA", "mimeType": "image/png"},
                        ]
                    },
                }
            )
        elif n == "fail":
            send(
                {
                    "jsonrpc": "2.0",
                    "id": i,
                    "result": {"isError": True, "content": [{"type": "text", "text": "it broke"}]},
                }
            )
        elif n == "env":
            send(
                {
                    "jsonrpc": "2.0",
                    "id": i,
                    "result": {
                        "content": [{"type": "text", "text": "set" if os.environ.get(a["name"]) else "unset"}]
                    },
                }
            )
        elif n == "big":
            send({"jsonrpc": "2.0", "id": i, "result": {"content": [{"type": "text", "text": "x" * 100000}]}})
        else:
            send({"jsonrpc": "2.0", "id": i, "result": {"content": [{"type": "text", "text": "ok " + n}]}})
    else:
        send({"jsonrpc": "2.0", "id": i, "error": {"code": -32601, "message": "unknown method"}})
