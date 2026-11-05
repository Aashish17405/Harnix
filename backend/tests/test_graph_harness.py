"""The knowledge graph inside real task runs and the HTTP API (real Neo4j; the model is scripted).

The scripted provider is a mock: these tests prove the wiring (context, tool, events, degradation), not that a real
model does better with the graph.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from harnix.api.app import create_app
from harnix.config import Settings
from harnix.graph.manager import GraphManager, repo_id_for
from harnix.graph.store import GraphStore
from harnix.models.base import Message, ModelResponse, ToolSpec
from harnix.models.scripted import ScriptedProvider
from harnix.persistence.store import Store

from .conftest import call, make_runner, new_task, step, wait_for, wait_terminal

FIX = call("edit_file", path="mathutil.py", old_text="a - b", new_text="a + b")


class Spy(ScriptedProvider):
    """Records what the model would have been sent."""

    def __init__(self, steps: list[Any]):
        super().__init__(steps)
        self.seen: list[dict[str, Any]] = []

    def complete(
        self, *, system: str, messages: list[Message], tools: list[ToolSpec], max_tokens: int, timeout: float,
        thinking: str | None = None,
    ) -> ModelResponse:  # fmt: skip
        first = messages[0].content[0].text  # type: ignore[union-attr]
        self.seen.append({"system": system, "first": first, "tools": [t.name for t in tools]})
        return super().complete(
            system=system,
            messages=messages,
            tools=tools,
            max_tokens=max_tokens,
            timeout=timeout,
            thinking=thinking,
        )


def graph_settings(settings: Settings, neo: Settings) -> Settings:
    return settings.model_copy(
        update={
            "graph_enabled": True,
            "neo4j_uri": neo.neo4j_uri,
            "neo4j_user": neo.neo4j_user,
            "neo4j_password": neo.neo4j_password,
            "neo4j_database": neo.neo4j_database,
        }  # fmt: skip
    )


@pytest.fixture
def manager(settings: Settings, neo4j_settings: Settings, repo: Path):
    m = GraphManager(graph_settings(settings, neo4j_settings))
    yield m
    m.drop(repo)
    m.close()


def events(store: Store, task_id: str) -> list[Any]:
    return store.events_after(task_id, 0, 5000)


def of(evs: list[Any], type_: str) -> list[dict[str, Any]]:
    return [e.data for e in evs if e.type == type_]


def run(settings: Settings, store: Store, repo: Path, steps: list[Any], graph: GraphManager | None):
    spy = Spy(steps)
    runner = make_runner(settings, store, spy, graph)
    task = new_task(store, repo, settings)
    runner.start(task.id)
    return spy, task, wait_terminal(store, task.id)


# ---- a real run with the graph ---------------------------------------------------------------------------


def test_graph_context_and_tool_are_wired_into_the_loop(
    settings: Settings, store: Store, repo: Path, manager: GraphManager
):
    steps = [
        step("", call("graph_query", op="file_info", target="mathutil.py")),
        step("", FIX),
        step("", call("graph_query", op="callers", target="add")),
        step("fixed it"),
    ]
    spy, task, done = run(manager.settings, store, repo, steps, manager)
    assert done.status == "completed" and done.outcome == "verified"
    evs = events(store, task.id)
    order = [e.type for e in evs]
    assert (
        order.index("workspace.created")
        < order.index("graph.indexed")
        < order.index("graph.context")
        < order.index("model.request")
    )

    idx = of(evs, "graph.indexed")[0]
    assert (
        idx["files"] == 3 and idx["files_parsed"] == 3 and idx["complete"] is True and idx["symbols"] == 2
    )  # add + test_add
    ctx = of(evs, "graph.context")[0]
    assert (
        ctx["seed_tests"] == 1 and ctx["chars"] > 100
    )  # the test failing at baseline was found from the traceback

    first = spy.seen[0]["first"]
    assert '<knowledge_graph trust="untrusted">' in first
    assert "test_mathutil.py (a test that fails before your changes) imports: mathutil.py" in first
    assert "graph_query" in spy.seen[0]["system"] and "graph_query" in spy.seen[0]["tools"]
    assert first.index("<knowledge_graph") < first.index(
        "<repository_instructions"
    )  # before untrusted repo text

    results = {e["call_id"]: e for e in of(evs, "tool.result")}
    q = [r for r in results.values() if r["tool"] == "graph_query"]
    assert len(q) == 2 and all(r["ok"] for r in q)
    info = q[0]["data"]
    assert info["found"] and [d["path"] for d in info["imported_by"]] == ["test_mathutil.py"]
    assert [s["qualname"] for s in info["symbols"]] == ["add"]
    after_edit = q[1]["data"]["snapshot"]
    assert (
        "mathutil.py" in after_edit["note"]
    )  # the agent edited it: the graph says it no longer reflects that file
    assert [r["caller"] for r in q[1]["data"]["results"]] == ["test_add"]


def test_secrets_never_reach_the_graph_or_the_model_context(
    settings: Settings, store: Store, repo: Path, manager: GraphManager
):
    spy, task, done = run(manager.settings, store, repo, [step("done")], manager)
    assert done.status in ("completed", "failed")
    blob = repr(events(store, task.id)) + spy.seen[0]["first"]
    assert "supersecretvalue123" not in blob and "API_KEY" not in blob
    rid = repo_id_for(repo)
    assert manager.store.symbol_name_counts(rid, ["API_KEY"]) == {}
    assert not manager.store.existing_paths(
        rid, [".env"]
    )  # the file was never copied into the workspace, so never indexed
    assert manager.store.existing_paths(rid, ["mathutil.py", "AGENTS.md"]) == {"mathutil.py", "AGENTS.md"}


def test_second_task_on_the_same_repository_is_incremental(
    settings: Settings, store: Store, repo: Path, manager: GraphManager
):
    _, t1, _ = run(manager.settings, store, repo, [step("done")], manager)
    first = of(events(store, t1.id), "graph.indexed")[0]
    _, t2, _ = run(manager.settings, store, repo, [step("done")], manager)
    second = of(events(store, t2.id), "graph.indexed")[0]
    assert first["files_parsed"] == 3 and first["version"] == 1
    assert (
        second["files_parsed"] == 0
        and second["files_unchanged"] == 3
        and second["version"] == 1
        and not second["changed"]
    )


# ---- degradation: the graph must never fail a task -------------------------------------------------------------


def test_task_completes_without_the_graph_when_neo4j_is_down(settings: Settings, store: Store, repo: Path):
    dead = settings.model_copy(
        update={"graph_enabled": True, "neo4j_password": "x", "neo4j_uri": "bolt://127.0.0.1:1"}
    )
    m = GraphManager(dead, store=GraphStore(dead.neo4j_uri, "neo4j", "x", query_timeout=2))
    try:
        spy, task, done = run(settings, store, repo, [step("", FIX), step("fixed")], m)
    finally:
        m.close()
    assert done.status == "completed" and done.outcome == "verified"
    evs = events(store, task.id)
    reason = of(evs, "graph.unavailable")[0]["reason"]
    assert "cannot reach Neo4j" in reason  # said out loud, with the fix, not silently skipped
    assert not of(evs, "graph.indexed") and not of(evs, "graph.context")
    assert "knowledge_graph" not in spy.seen[0]["first"] and "graph_query" not in spy.seen[0]["tools"]
    assert "graph_query" not in spy.seen[0]["system"]


def test_an_unexpected_graph_bug_is_contained(
    settings: Settings, store: Store, repo: Path, manager: GraphManager, monkeypatch: pytest.MonkeyPatch
):
    def boom(*_a: Any, **_k: Any):
        raise RuntimeError("bug in the indexer")

    monkeypatch.setattr(manager, "index_for_task", boom)
    spy, task, done = run(manager.settings, store, repo, [step("", FIX), step("fixed")], manager)
    assert done.status == "completed" and done.outcome == "verified"
    reason = of(events(store, task.id), "graph.unavailable")[0]["reason"]
    assert reason == "internal error: RuntimeError"  # the exception text is not leaked into events
    assert "graph_query" not in spy.seen[0]["tools"]


def test_model_calling_graph_query_when_there_is_no_graph_gets_a_structured_error(
    settings: Settings, store: Store, repo: Path
):
    steps = [step("", call("graph_query", op="callers", target="add")), step("", FIX), step("fixed")]
    spy, task, done = run(settings, store, repo, steps, None)
    assert done.status == "completed" and "graph_query" not in spy.seen[0]["tools"]
    r = [e for e in of(events(store, task.id), "tool.result") if e["tool"] == "graph_query"][0]
    assert r["ok"] is False and "not available" in r["error"]


# ---- HTTP API ----------------------------------------------------------------------------------------------------


def client(settings: Settings, **kw: Any) -> TestClient:
    return TestClient(
        create_app(settings, provider_factory=lambda: ScriptedProvider([]), create_schema=True, **kw)
    )


def test_api_when_the_graph_is_disabled(settings: Settings, repo: Path):
    with client(settings) as c:
        g = c.get("/api/health").json()["graph"]
        assert g == {"enabled": False, "available": False, "detail": "FORGE_GRAPH_ENABLED is false"}
        r = c.get("/api/graph/status", params={"repo_path": str(repo)})
        assert r.status_code == 503 and r.json()["error"]["code"] == "not_configured"
        assert "FORGE_GRAPH_ENABLED=true" in r.json()["error"]["message"]
        assert c.post("/api/graph/index", json={"repo_path": str(repo)}).status_code == 503
        assert c.delete("/api/graph", params={"repo_path": str(repo)}).status_code == 503


def test_api_preindex_status_and_drop(
    settings: Settings, neo4j_settings: Settings, repo: Path, tmp_path: Path
):
    s = graph_settings(settings, neo4j_settings)
    with client(s) as c:
        h = c.get("/api/health").json()["graph"]
        assert h["enabled"] and h["available"] and "connected" in h["detail"]
        assert neo4j_settings.neo4j_password not in str(h)  # health never echoes the credential
        st = c.get("/api/graph/status", params={"repo_path": str(repo)}).json()
        assert st["indexed"] is False and st["repo_id"] == repo_id_for(repo)

        r = c.post("/api/graph/index", json={"repo_path": str(repo)})
        assert r.status_code == 202 and r.json()["repo_id"] == repo_id_for(repo)
        done = wait_for(
            lambda: (
                (j := c.get("/api/graph/status", params={"repo_path": str(repo)}).json().get("job"))
                and j["state"] != "running"
                and j
            )
        )
        assert (
            done["state"] == "done" and done["report"]["complete"] and done["report"]["files"] == 3
        )  # .env is skipped
        st = c.get("/api/graph/status", params={"repo_path": str(repo)}).json()
        assert (
            st["indexed"]
            and st["graph"]["files"] == 3
            and st["graph"]["complete"] is True
            and st["graph"]["symbols"] == 2
        )

        outside = tmp_path / "elsewhere"
        outside.mkdir()
        bad = c.get("/api/graph/status", params={"repo_path": str(outside)})
        assert bad.status_code == 422 and "authorized roots" in bad.json()["error"]["message"]
        assert c.post("/api/graph/index", json={"repo_path": str(outside)}).status_code == 422

        deleted = c.delete("/api/graph", params={"repo_path": str(repo)}).json()["deleted_nodes"]
        assert deleted == 6  # the repo node, 3 files, 2 symbols (no external imports in this project)
        assert c.get("/api/graph/status", params={"repo_path": str(repo)}).json()["indexed"] is False


def test_api_graph_endpoints_report_an_unreachable_database(settings: Settings, repo: Path):
    dead = settings.model_copy(
        update={"graph_enabled": True, "neo4j_password": "x", "neo4j_uri": "bolt://127.0.0.1:1"}
    )
    with client(dead) as c:
        h = c.get("/api/health").json()
        assert (
            h["status"] == "ok" and h["graph"]["enabled"] and not h["graph"]["available"]
        )  # the backend itself stays up
        assert "cannot reach Neo4j" in h["graph"]["detail"]
        r = c.post("/api/graph/index", json={"repo_path": str(repo)})
        assert r.status_code == 503 and "not reachable" in r.json()["error"]["message"]
        assert c.get("/api/graph/status", params={"repo_path": str(repo)}).json()["available"] is False
