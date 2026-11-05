"""MCP servers from mcp.json: parsing, trust, the stdio and HTTP clients, and real task runs through the executor.

The server is a small fake (tests/fake_mcp_server.py) speaking the real protocol; the model is the scripted mock.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest

from harnix.config import Settings
from harnix.mcp.client import HttpClient, McpError, connect
from harnix.mcp.config import McpConfigError, ServerConfig, discover, expand, load_file, parse_server
from harnix.mcp.manager import McpManager, check_args, tool_name
from harnix.persistence.store import Store

from .conftest import call, make_runner, new_task, step, wait_for, wait_terminal
from .test_agentic_harness import FIX, Router, of

FAKE = str(Path(__file__).with_name("fake_mcp_server.py"))


def write_cfg(tmp_path: Path, servers: dict[str, Any], name: str = "mcp.json") -> Path:
    p = tmp_path / name
    p.write_text(json.dumps({"mcpServers": servers}))
    return p


def fake(**extra: Any) -> dict[str, Any]:
    return {"command": sys.executable, "args": [FAKE], **extra}


# ---- configuration -----------------------------------------------------------------------------------------


def test_expand_env_with_default_and_missing(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MCP_T", "abc")
    monkeypatch.delenv("MCP_NOPE", raising=False)
    assert expand("x-${MCP_T}-${MCP_NOPE:-dflt}") == "x-abc-dflt"
    with pytest.raises(McpConfigError, match="MCP_NOPE"):
        expand("${MCP_NOPE}")


@pytest.mark.parametrize(
    "raw,msg",
    [
        ({}, "either 'command'"),
        ({"command": ""}, "command"),
        ({"command": "x", "args": "no"}, "args"),
        ({"type": "sse", "url": "http://x"}, "sse"),
        ({"type": "http", "url": "ftp://x"}, "url"),
        ({"command": "x", "autoApprove": "all"}, "autoApprove"),
        ({"command": "x", "env": {"A": 1}}, "env"),
    ],
)
def test_bad_entries_are_refused_with_a_reason(raw: dict[str, Any], msg: str):
    with pytest.raises(McpConfigError, match=msg):
        parse_server("s", raw, "f")


def test_good_entries_disabled_and_describe_hides_secrets(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MCP_TOK", "tok-123456789")
    p = write_cfg(tmp_path, {
        "a": {"command": "npx", "args": ["-y", "pkg"], "env": {"K": "${MCP_TOK}"}},
        "b": {"type": "http", "url": "https://h.example/mcp?key=secret", "headers": {"Authorization": "Bearer ${MCP_TOK}"}},
        "off": {"command": "x", "disabled": True},
        "bad name!": {"command": "x"},
        "broken": {"command": 5},
    })  # fmt: skip
    servers, problems = load_file(p)
    assert [s.name for s in servers] == ["a", "b"] and len(problems) == 2
    shown = json.dumps([s.describe() for s in servers])
    assert "tok-123456789" not in shown and "secret" not in shown and "https://h.example/mcp" in shown


def test_project_file_is_untrusted_unless_enabled(tmp_path: Path):
    user = write_cfg(tmp_path, {"u": fake()}, "user.json")
    proj = write_cfg(tmp_path, {"p": fake()}, ".mcp.json")
    servers, _, skipped = discover([user], proj, trust_project=False)
    assert [s.name for s in servers] == ["u"] and skipped == ["p"]
    servers, _, skipped = discover([user], proj, trust_project=True)
    assert sorted(s.name for s in servers) == ["p", "u"] and skipped == []


def test_tool_names_are_valid_and_unique_when_long():
    assert tool_name("fs", "read file") == "mcp__fs__read_file"
    a, b = tool_name("s", "x" * 80), tool_name("s", "x" * 80 + "y")
    assert len(a) <= 64 and a != b


def test_check_args():
    schema = {"properties": {"n": {"type": "integer"}, "s": {"type": "string"}}, "required": ["s"]}
    assert check_args(schema, {"s": "a", "n": 3}) is None
    assert "missing" in (check_args(schema, {"n": 3}) or "")
    assert "'n'" in (check_args(schema, {"s": "a", "n": "3"}) or "")
    assert "'n'" in (check_args(schema, {"s": "a", "n": True}) or "")


# ---- stdio client ------------------------------------------------------------------------------------------


def stdio_cfg(**extra: Any) -> ServerConfig:
    return ServerConfig("fake", "stdio", command=sys.executable, args=[FAKE], **extra)


def test_stdio_handshake_pagination_call_and_environment_isolation(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-should-not-leak-1234567890")
    monkeypatch.setenv("FROM_CONFIG", "no")
    c = connect(stdio_cfg(env={"FROM_CONFIG": "yes"}), 20)
    try:
        assert c.server_info["name"] == "fake"
        names = [t["name"] for t in c.list_tools()]  # two pages, and a banner line on stdout was ignored
        assert names == ["echo", "fail", "env", "big", "bad.name with spaces"]
        r = c.call_tool("echo", {"text": "hi"}, 20)
        assert r["content"][0]["text"] == "echo: hi"
        seen = lambda n: c.call_tool("env", {"name": n}, 20)["content"][0]["text"]  # noqa: E731
        assert seen("FROM_CONFIG") == "set" and seen("ANTHROPIC_API_KEY") == "unset"
    finally:
        c.close()
    assert c.proc.poll() is not None  # type: ignore[attr-defined]


def test_stdio_errors_are_reported_not_hung():
    with pytest.raises(McpError, match="cannot start"):
        connect(ServerConfig("x", "stdio", command="definitely-not-a-program-xyz"), 5)
    with pytest.raises(McpError):  # exits immediately without answering
        connect(ServerConfig("x", "stdio", command=sys.executable, args=["-c", "import sys; sys.exit(3)"]), 5)
    c = connect(stdio_cfg(), 20)
    try:
        with pytest.raises(McpError, match="unknown method"):
            c._request("nope/nope", {}, 5)
    finally:
        c.close()


# ---- HTTP client -------------------------------------------------------------------------------------------


def test_http_json_and_sse_responses_and_session_header():
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        if req.method == "DELETE":
            return httpx.Response(200)
        body = json.loads(req.content)
        if "id" not in body:
            return httpx.Response(202)
        if body["method"] == "initialize":
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": {"serverInfo": {"name": "h"}}}, headers={"Mcp-Session-Id": "sess-1"})  # fmt: skip
        if body["method"] == "tools/list":
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": {"tools": [{"name": "t", "inputSchema": {"type": "object"}}]}})  # fmt: skip
        msg = {
            "jsonrpc": "2.0",
            "id": body["id"],
            "result": {"content": [{"type": "text", "text": "from sse"}]},
        }
        sse = "event: message\ndata: " + json.dumps({"jsonrpc": "2.0", "method": "notifications/progress"}) + "\n\ndata: " + json.dumps(msg) + "\n\n"  # fmt: skip
        return httpx.Response(200, text=sse, headers={"content-type": "text/event-stream"})

    cfg = ServerConfig("h", "http", url="https://h.example/mcp", headers={"Authorization": "Bearer abc"})
    c = HttpClient(cfg, 5, transport=httpx.MockTransport(handler))
    c.initialize()
    assert [t["name"] for t in c.list_tools()] == ["t"]
    assert c.call_tool("t", {}, 5)["content"][0]["text"] == "from sse"
    assert all(r.headers.get("authorization") == "Bearer abc" for r in seen)
    assert seen[-1].headers.get("mcp-session-id") == "sess-1"
    c.close()
    assert seen[-1].method == "DELETE"


def test_http_failure_status_is_an_error():
    c = HttpClient(
        ServerConfig("h", "http", url="https://h.example/mcp"),
        5,
        transport=httpx.MockTransport(lambda r: httpx.Response(401)),
    )
    with pytest.raises(McpError, match="401"):
        c.initialize()


# ---- manager -----------------------------------------------------------------------------------------------


def test_manager_survives_a_broken_server_and_truncates():
    m = McpManager()
    m.start([ServerConfig("bad", "stdio", command="definitely-not-a-program-xyz"), stdio_cfg()], 20)
    try:
        assert (
            list(m.failed) == ["bad"]
            and "mcp__fake__echo" in m.tools
            and "mcp__fake__bad_name_with_spaces" in m.tools
        )
        ok, text, cut = m.call("mcp__fake__big", {}, 20, 1000)
        assert ok and cut and len(text) < 1100
        ok, text, _ = m.call("mcp__fake__echo", {"text": "a"}, 20, 1000)
        assert ok and "echo: a" in text and "image content" in text and "AAAA" not in text
        ok, text, _ = m.call("mcp__fake__fail", {}, 20, 1000)
        assert not ok and text == "it broke"
    finally:
        m.close()


# ---- inside a task -----------------------------------------------------------------------------------------


def mcp_settings(settings: Settings, tmp_path: Path, **server_extra: Any) -> Settings:
    return settings.model_copy(
        update={"mcp_config": str(write_cfg(tmp_path, {"fake": fake(**server_extra)}))}
    )


def run_task(settings: Settings, store: Store, repo: Path, steps: list[Any], wait: bool = True):
    provider = Router(steps)
    runner = make_runner(settings, store, provider)
    task = new_task(store, repo, settings)
    runner.start(task.id)
    return provider, task, (wait_terminal(store, task.id) if wait else None)


def pending(store: Store, task_id: str):
    return wait_for(lambda: [a for a in store.approvals(task_id) if a.status == "pending"] or None)[0]


def test_mcp_tool_needs_approval_then_runs(settings: Settings, store: Store, repo: Path, tmp_path: Path):
    s = mcp_settings(settings, tmp_path)
    p, task, _ = run_task(
        s,
        store,
        repo,
        [step("", call("mcp__fake__echo", text="hello")), step("", FIX), step("done")],
        wait=False,
    )
    a = pending(store, task.id)
    assert a.tool == "mcp__fake__echo" and "MCP server 'fake'" in a.reason
    store.decide_approval(a.id, "approved")
    done = wait_terminal(store, task.id)
    assert done.status == "completed"
    res = [r for r in of(store, task.id, "tool.result") if r["tool"] == "mcp__fake__echo"][0]
    assert res["ok"] and "echo: hello" in res["data"]["output"]
    assert "mcp__fake__echo" in [t for c in p.seen for t in c["tools"]]
    ev = of(store, task.id, "mcp.servers")[0]
    assert ev["connected"][0]["name"] == "fake" and ev["connected"][0]["tools"] == 5 and ev["failed"] == {}


def test_rejected_mcp_call_never_reaches_the_server(
    settings: Settings, store: Store, repo: Path, tmp_path: Path
):
    s = mcp_settings(settings, tmp_path)
    _, task, _ = run_task(
        s, store, repo, [step("", call("mcp__fake__echo", text="x")), step("", FIX), step("done")], wait=False
    )
    store.decide_approval(pending(store, task.id).id, "rejected", "no")
    wait_terminal(store, task.id)
    res = [r for r in of(store, task.id, "tool.result") if r["tool"] == "mcp__fake__echo"][0]
    assert not res["ok"] and "Not approved" in res["error"]


def test_auto_approve_runs_without_asking_and_errors_are_failures(
    settings: Settings, store: Store, repo: Path, tmp_path: Path
):
    s = mcp_settings(settings, tmp_path, autoApprove=["echo", "fail"])
    _, task, done = run_task(
        s,
        store,
        repo,
        [
            step("", call("mcp__fake__echo", text="x"), call("mcp__fake__fail"), call("mcp__fake__echo")),
            step("", FIX),
            step("done"),
        ],
    )
    assert store.approvals(task.id) == []
    results = {
        (r["tool"], r["ok"]): r for r in of(store, task.id, "tool.result") if r["tool"].startswith("mcp__")
    }
    assert ("mcp__fake__echo", True) in results
    assert results[("mcp__fake__fail", False)]["error"] == "it broke"
    bad = [r for r in of(store, task.id, "tool.result") if r["tool"] == "mcp__fake__echo" and not r["ok"]]
    assert "missing required argument" in bad[0]["error"]
    assert done.tool_failures >= 2


def test_deny_sensitive_refuses_mcp_calls(settings: Settings, store: Store, repo: Path, tmp_path: Path):
    s = mcp_settings(settings, tmp_path).model_copy(update={"approval_policy": "deny_sensitive"})
    _, task, done = run_task(
        s, store, repo, [step("", call("mcp__fake__echo", text="x")), step("", FIX), step("done")]
    )
    res = [r for r in of(store, task.id, "tool.result") if r["tool"] == "mcp__fake__echo"][0]
    assert not res["ok"] and "Denied by policy" in res["error"] and done.policy_denials == 1


def test_unknown_mcp_tool_and_disabled(settings: Settings, store: Store, repo: Path, tmp_path: Path):
    s = mcp_settings(settings, tmp_path)
    _, task, _ = run_task(s, store, repo, [step("", call("mcp__fake__nope")), step("", FIX), step("done")])
    assert (
        "Unknown tool"
        in [r for r in of(store, task.id, "tool.result") if r["tool"] == "mcp__fake__nope"][0]["error"]
    )
    off = s.model_copy(update={"mcp_enabled": False})
    p, task2, _ = run_task(off, store, repo, [step("", FIX), step("done")])
    assert of(store, task2.id, "mcp.servers") == [] and not any(
        t.startswith("mcp__") for c in p.seen for t in c["tools"]
    )


def test_repository_mcp_json_is_ignored_unless_trusted(
    settings: Settings, store: Store, repo: Path, tmp_path: Path
):
    write_cfg(repo, {"evil": fake()}, ".mcp.json")
    s = settings.model_copy(update={"mcp_config": str(tmp_path / "none.json")})
    p, task, _ = run_task(s, store, repo, [step("", FIX), step("done")])
    ev = of(store, task.id, "mcp.servers")[0]
    assert ev["connected"] == [] and ev["skipped_project_servers"] == ["evil"]
    assert not any(t.startswith("mcp__") for c in p.seen for t in c["tools"])
    t = s.model_copy(update={"mcp_trust_project": True})
    p2, task2, _ = run_task(t, store, repo, [step("", FIX), step("done")])
    assert of(store, task2.id, "mcp.servers")[0]["connected"][0]["name"] == "evil"


def test_a_server_that_cannot_start_does_not_fail_the_task(
    settings: Settings, store: Store, repo: Path, tmp_path: Path
):
    cfg = write_cfg(tmp_path, {"dead": {"command": "definitely-not-a-program-xyz"}})
    s = settings.model_copy(update={"mcp_config": str(cfg)})
    _, task, done = run_task(s, store, repo, [step("", FIX), step("done")])
    assert done.status == "completed"
    assert "dead" in of(store, task.id, "mcp.servers")[0]["failed"]


def test_server_env_values_are_redacted_from_results(
    settings: Settings, store: Store, repo: Path, tmp_path: Path
):
    s = mcp_settings(settings, tmp_path, autoApprove=["echo"], env={"MCP_SECRET": "plainsecretvalue"})
    _, task, _ = run_task(
        s,
        store,
        repo,
        [step("", call("mcp__fake__echo", text="leak plainsecretvalue")), step("", FIX), step("done")],
    )
    res = [r for r in of(store, task.id, "tool.result") if r["tool"] == "mcp__fake__echo"][0]
    assert "plainsecretvalue" not in json.dumps(res) and "[REDACTED]" in res["data"]["output"]
