"""The knowledge MCP tools over HTTP (`/api/knowledge/mcp`): the protocol, every tool, refusals, project scoping
under the access list, and the client settings (no secrets). The stdio transport is covered in test_knowledge.py.
Embeddings are the deterministic hash mock; Neo4j is off here (knowledge_query against Neo4j: test_knowledge_query)."""

from __future__ import annotations

import json
from typing import Any

from fastapi.testclient import TestClient

from harnix.config import Settings
from harnix.knowledge import tools

from .test_knowledge import DOCS, P, client

URL = f"/api/knowledge/mcp?project={P}"
# a server scoped to one project, without a graph database, offers neither the graph tools nor a project list
OFFERED = tools.NAMES - tools.GRAPH_TOOLS - {"knowledge_projects"}


def load(c: TestClient, headers: dict[str, str] | None = None) -> None:
    r = c.post("/api/knowledge/ingest", headers=headers or {}, json={"project": P, "source": "documents", "path": str(DOCS)})  # fmt: skip
    assert r.status_code == 200, r.text


def rpc(
    c: TestClient, method: str, params: dict[str, Any] | None = None, url: str = URL, **kw: Any
) -> dict[str, Any]:
    r = c.post(url, json={"jsonrpc": "2.0", "id": 7, "method": method, "params": params or {}}, **kw)
    assert r.status_code == 200, r.text
    return r.json()


def tool(c: TestClient, name: str, url: str = URL, **args: Any) -> tuple[bool, Any]:
    res = rpc(c, "tools/call", {"name": name, "arguments": args}, url)["result"]
    text = res["content"][0]["text"]
    return res["isError"], (text if res["isError"] else json.loads(text))


def test_http_speaks_the_protocol(settings: Settings):
    with client(settings) as c:
        init = rpc(c, "initialize", {"protocolVersion": "2025-06-18"})
        assert init["id"] == 7 and init["result"]["serverInfo"]["name"] == "forge-knowledge"
        assert "knowledge_context" in init["result"]["instructions"]  # tells any agent where to start
        assert {t["name"] for t in rpc(c, "tools/list")["result"]["tools"]} == OFFERED
        assert rpc(c, "ping")["result"] == {}
        assert rpc(c, "resources/list")["error"]["code"] == -32601
        note = c.post(URL, json={"jsonrpc": "2.0", "method": "notifications/initialized"})
        assert note.status_code == 202 and note.content == b""  # notifications get no reply
        batch = c.post(URL, json=[{"jsonrpc": "2.0", "id": 1, "method": "ping"}, {"jsonrpc": "2.0", "method": "notifications/initialized"}, "junk"])  # fmt: skip
        assert [m.get("id") for m in batch.json()] == [1, None] and batch.json()[1]["error"]["code"] == -32600
        assert c.get(URL).status_code == 405  # no server-sent stream: JSON responses only


def test_client_settings_hold_no_secrets(settings: Settings):
    with client(settings, api_token="sekrit-token-123") as c:
        H = {"Authorization": "Bearer sekrit-token-123"}
        cfg = c.get("/api/knowledge/mcp/config", params={"project": P}, headers=H).json()
        dump = json.dumps(cfg)
        assert "sekrit-token-123" not in dump and cfg["auth_required"] is True
        assert cfg["url"].endswith(f"/api/knowledge/mcp?project={P}") and set(cfg["tools"]) == OFFERED
        names = {x["client"] for x in cfg["clients"]}
        assert {"Claude Code", "Cursor", "Kiro"} <= names
        kiro = next(x for x in cfg["clients"] if x["client"] == "Kiro")["config"]["mcpServers"][
            "forge-knowledge"
        ]
        assert kiro["args"] == ["-m", "forge.knowledge.mcp_server"] and kiro["env"] == {
            "FORGE_KNOWLEDGE_PROJECTS": P
        }
        assert set(kiro["autoApprove"]) == OFFERED  # all read-only


def test_a_short_id_finds_its_one_backlog_item_and_an_ambiguous_one_names_the_choices(store):
    from harnix.knowledge.embeddings import HashEmbedder
    from harnix.knowledge.ingest import Ingestor
    from harnix.knowledge.repo import KnowledgeRepo
    from harnix.knowledge.service import KnowledgeService

    repo = KnowledgeRepo(store)
    ing = Ingestor(repo, HashEmbedder())
    for ext in ("BRD-02/US-003", "BRD-03/US-003", "BRD-03/US-004"):
        ing.add("va", "story", ext, ext, f"# {ext}\n\nSome story text for {ext}.\n")
    svc = KnowledgeService(repo, HashEmbedder(), ["va"])
    assert (
        json.loads(tools.call_text(svc, "knowledge_get", {"ref": "US-004"})[1])["external_id"]
        == "BRD-03/US-004"
    )
    ok, text = tools.call_text(svc, "knowledge_trace", {"ref": "US-003"})
    assert not ok and "ambiguous" in text and "BRD-02/US-003, BRD-03/US-003" in text
    assert tools.call_text(svc, "knowledge_trace", {"ref": "BRD-03/US-003"})[0]
    assert "No source" in tools.call_text(svc, "knowledge_get", {"ref": "US-999"})[1]
