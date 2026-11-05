"""Knowledge base: ingestion, hybrid retrieval, relationships, MCP server, context bundles, task integration, verified
updates and access control. The model is the scripted mock; embeddings are the deterministic hash MOCK (real
OpenAI embeddings are covered separately, see docs/knowledge.md)."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from harnix.api.app import create_app
from harnix.config import Settings
from harnix.knowledge.context import build_bundle, render
from harnix.knowledge.embeddings import HashEmbedder
from harnix.knowledge.ingest import Ingestor
from harnix.knowledge.repo import KnowledgeRepo
from harnix.knowledge.service import KnowledgeService
from harnix.mcp.config import ServerConfig
from harnix.persistence.store import Store

from .conftest import step

DOCS = (
    Path(__file__).resolve().parents[2] / "testing_docs"
)  # the real documents: the only test data files; never written to
P = "real"


@pytest.fixture
def repo(store: Store) -> KnowledgeRepo:
    return KnowledgeRepo(store)


def load_all(repo: KnowledgeRepo, emb=None, project: str = P):  # noqa: ANN001
    """The real documents: a BRD, a domain note, a JSON backlog of epics and features and a JSON file of user stories."""
    ing = Ingestor(repo, emb or HashEmbedder(), "test")
    ing.ingest_documents(project, DOCS)
    return ing


# ---- ingestion -----------------------------------------------------------------------------------------------


def test_unknown_references_are_reported_not_invented(repo: KnowledgeRepo, tmp_path: Path):
    (tmp_path / "a.md").write_text("---\nid: A-1\nimplements: [NOPE-9]\n---\n# A\ntext\n")
    res = Ingestor(repo, HashEmbedder()).ingest_documents(P, tmp_path)
    assert res.unresolved == ["A-1 -implements-> NOPE-9"] and repo.edges(P) == []


# ---- retrieval ------------------------------------------------------------------------------------------------


# ---- context bundle -------------------------------------------------------------------------------------------


def test_bundle_is_insufficient_instead_of_inventing(repo: KnowledgeRepo):
    Ingestor(repo, HashEmbedder()).add(
        "empty", "doc", "NOTE-1", "Lunch", "# Lunch menu\nsandwiches on friday\n"
    )
    svc = KnowledgeService(repo, HashEmbedder(), ["empty"])
    b = build_bundle(svc, "empty", "Implement quantum teleportation of orders", [])
    assert b.status == "insufficient" and b.content["sufficiency"]["missing"]
    assert render(b.content).startswith("Context bundle") and "MISSING" in render(b.content)


# ---- the MCP server --------------------------------------------------------------------------------------------


def server_cfg(settings: Settings, projects: str, **extra: str) -> ServerConfig:
    env = {"FORGE_KNOWLEDGE_PROJECTS": projects, "FORGE_DATABASE_URL": settings.database_url, "FORGE_EMBEDDING_PROVIDER": "hash", "FORGE_KNOWLEDGE_VECTOR": settings.knowledge_vector, "FORGE_GRAPH_ENABLED": "false", "PYTHONPATH": str(Path(__file__).resolve().parents[1]), **extra}  # fmt: skip
    return ServerConfig(
        "knowledge", "stdio", command=sys.executable, args=["-m", "forge.knowledge.mcp_server"], env=env
    )


def test_mcp_server_refuses_to_start_without_a_project_list(settings: Settings):
    import subprocess

    env = {
        "FORGE_DATABASE_URL": settings.database_url,
        "PATH": "",
        "PYTHONPATH": str(Path(__file__).resolve().parents[1]),
    }
    done = subprocess.run([sys.executable, "-m", "forge.knowledge.mcp_server"], env={**env, "SYSTEMROOT": __import__("os").environ.get("SYSTEMROOT", "")}, capture_output=True, text=True, timeout=60)  # fmt: skip
    assert done.returncode == 2 and "FORGE_KNOWLEDGE_PROJECTS" in done.stderr


# ---- inside tasks ----------------------------------------------------------------------------------------------


def kset(settings: Settings, **kw: Any) -> Settings:
    return settings.model_copy(
        update={"embedding_provider": "hash", "mcp_config": str(settings.repo_roots[0] / "none.json"), **kw}
    )


# ---- HTTP API --------------------------------------------------------------------------------------------------


def client(settings: Settings, **kw: Any) -> TestClient:
    s = kset(settings, knowledge_roots=str(DOCS), **kw)
    return TestClient(create_app(s, create_schema=True))


def test_api_without_knowledge_roots_ingests_any_existing_folder(settings: Settings, tmp_path: Path):
    docs = tmp_path / "BRD-02-Anything"
    docs.mkdir()
    (docs / "BRD.md").write_text("# BRD\n\n## BF-1: Show the list\n\nThe user can see the list.\n")
    with TestClient(create_app(kset(settings, knowledge_roots=""), create_schema=True)) as c:
        ok = c.post("/api/knowledge/ingest", json={"project": P, "source": "documents", "path": str(docs)})
        assert ok.status_code == 200 and ok.json()["created"] >= 1, ok.text
        for source, path in (
            ("documents", tmp_path / "missing"),
            ("documents", docs / "BRD.md"),
            ("jira_mock", docs),
        ):
            assert c.post("/api/knowledge/ingest", json={"project": P, "source": source, "path": str(path)}).status_code == 422  # fmt: skip


def test_api_refuses_paths_outside_the_knowledge_roots(settings: Settings, tmp_path: Path):
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    with client(settings) as c:
        r = c.post("/api/knowledge/ingest", json={"project": P, "source": "documents", "path": str(outside)})
        assert r.status_code == 422
        f = tmp_path / "j.json"
        f.write_text("{}")
        assert (
            c.post(
                "/api/knowledge/ingest", json={"project": P, "source": "jira_mock", "path": str(f)}
            ).status_code
            == 422
        )
        assert (
            c.post(
                "/api/knowledge/ingest",
                json={"project": "bad name!", "source": "documents", "path": str(DOCS)},
            ).status_code
            == 422
        )


# ---- pgvector (PostgreSQL with the extension; skipped elsewhere) ----------------------------------------------


def test_pgvector_ranks_like_the_python_index(repo: KnowledgeRepo):
    from harnix.knowledge.pgvector import PgVectorIndex, PgVectorUnavailable, ensure
    from harnix.knowledge.service import PythonIndex

    try:
        ensure(repo)
    except PgVectorUnavailable as e:
        pytest.skip(str(e))
    load_all(repo)
    emb = HashEmbedder()
    py, pg = PythonIndex(repo), PgVectorIndex(repo)
    for q in ["accounts list across all entities", "favorite account limit", "transaction history view"]:
        v = emb.embed([q])[0]
        a, b = py.search(P, v, emb.name, None, 5), pg.search(P, v, emb.name, None, 5)
        assert [round(x[1], 4) for x in a] == [round(x[1], 4) for x in b], (a, b)  # same scores
        assert {x[0] for x in a[:2]} == {x[0] for x in b[:2]}, (a, b)  # same leaders; ties may swap
    only = pg.search(P, emb.embed(["virtual account list"])[0], emb.name, ["story"], 5)
    assert only and all(repo.get_source(repo.chunks_source(c)).kind == "story" for c, _ in only)  # type: ignore[union-attr]
    hits = KnowledgeService(repo, emb, [P], pg).search(P, "virtual account list")["hits"]
    assert hits and "vector" in hits[0]["matched_by"]


# ---- CLI --------------------------------------------------------------------------------------------------------


@pytest.fixture
def served(settings: Settings):
    import socket
    import threading
    import time

    import uvicorn

    from harnix.models.scripted import ScriptedProvider

    app = create_app(
        kset(settings, knowledge_roots=str(DOCS)),
        provider_factory=lambda: ScriptedProvider([step("never reached")]),
        create_schema=True,
    )
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    end = time.time() + 20
    while not server.started and time.time() < end:
        time.sleep(0.05)
    assert server.started
    yield f"http://127.0.0.1:{port}", app
    server.should_exit = True
    t.join(15)
