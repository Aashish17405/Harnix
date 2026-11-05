"""Hybrid retrieval as in the AIDLC flow: exact id lookup, vector/keyword search, graph traversal in Neo4j.

The Neo4j tests run against the real database (skipped when it is not reachable) under a random project name and
delete what they created, so the development graph is left alone."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from harnix.config import Settings
from harnix.graph.store import GraphStore, GraphUnavailable
from harnix.knowledge.context import _named_sources, build_bundle
from harnix.knowledge.embeddings import HashEmbedder
from harnix.knowledge.graphdb import KnowledgeGraph
from harnix.knowledge.ingest import Ingestor
from harnix.knowledge.repo import KnowledgeRepo
from harnix.knowledge.service import KnowledgeService
from harnix.persistence.store import Store

from .test_knowledge_intake import make_pack


def two_packs(store: Store, tmp_path: Path, project: str) -> KnowledgeRepo:
    repo = KnowledgeRepo(store)
    make_pack(tmp_path, "BRD-01-Alpha-Thing", "Alpha", "view the list")
    make_pack(tmp_path, "BRD-02-Beta-Thing", "Beta", "pin favourites")
    Ingestor(repo, HashEmbedder()).ingest_documents(project, tmp_path)
    return repo


def test_ids_named_in_the_task_are_looked_up_exactly_and_come_first(store: Store, tmp_path: Path):
    repo = two_packs(store, tmp_path, "p")
    svc = KnowledgeService(repo, HashEmbedder(), ["p"])
    ext = lambda ids: [repo.get_source(i).external_id for i in ids]  # type: ignore[union-attr]  # noqa: E731
    # US-001 exists in both backlogs: the namespace named in the same task picks one
    assert ext(_named_sources(svc, "p", "Implement US-001 of BRD-02")) == ["BRD-02/US-001", "BRD-02"]
    assert sorted(ext(_named_sources(svc, "p", "Implement US-001"))) == ["BRD-01/US-001", "BRD-02/US-001"]
    assert ext(_named_sources(svc, "p", "Implement BRD-01/US-002")) == ["BRD-01/US-002"]
    assert _named_sources(svc, "p", "Implement the thing from 2024-10") == []  # dates are not ids
    b = build_bundle(svc, "p", "Implement US-001 of BRD-02", [])
    first = b.content["slots"]["requirements"][0]
    assert first["external_id"] == "BRD-02/US-001" and "id" in first["found_by"]
    assert any(t["tool"] == "knowledge_get" for t in b.content["retrieval_trace"])


# ---- Neo4j ----------------------------------------------------------------------------------------------------------


@pytest.fixture
def kgraph(neo4j_settings: Settings):
    s = neo4j_settings
    store = GraphStore(s.neo4j_uri, s.neo4j_user, s.neo4j_password or "", s.neo4j_database, query_timeout=15)
    projects: list[str] = []
    yield KnowledgeGraph(store), projects
    for p in projects:
        store._write(
            lambda tx, p=p: tx.run("MATCH (n:Knowledge {project: $p}) DETACH DELETE n", p=p).consume()
        )  # noqa: SLF001
        store._write(
            lambda tx, p=p: tx.run("MATCH (n:KnowledgeProject {project: $p}) DELETE n", p=p).consume()
        )  # noqa: SLF001
    store.close()


def count(g: KnowledgeGraph, cypher: str, p: str) -> int:
    return g.store._read(lambda tx: tx.run(cypher, p=p).single()["c"])  # noqa: SLF001


def test_the_graph_is_mirrored_to_neo4j_and_traversed_there(store: Store, tmp_path: Path, kgraph):
    g, projects = kgraph
    p = f"test-{os.urandom(5).hex()}"
    projects.append(p)
    repo = two_packs(store, tmp_path, p)
    out = g.sync(repo, p)
    assert out["nodes"] == len(repo.sources(p)) and out["edges"] > 0
    # typed nodes and relationships, the same counts as the database
    assert count(g, "MATCH (n:Knowledge:Story {project: $p}) RETURN count(n) AS c", p) == 4
    assert (
        count(
            g, "MATCH (:Knowledge:Story {project: $p})-[r:DERIVES_FROM]->(:Feature) RETURN count(r) AS c", p
        )
        == 4
    )
    assert count(g, "MATCH ()-[r {project: $p}]->() RETURN count(r) AS c", p) == len(repo.edges(p))

    svc = KnowledgeService(repo, HashEmbedder(), [p], graph=g)
    rel = svc.relations(p, "BRD-02/US-001", depth=2)
    assert rel["via"] == "neo4j"
    names = {n["external_id"] for n in rel["nodes"]}
    assert {"BRD-02/US-001", "BRD-02/FEAT-001", "BRD-02/E-001"} <= names and not any(
        n.startswith("BRD-01") for n in names
    )
    sql = KnowledgeService(repo, HashEmbedder(), [p]).relations(p, "BRD-02/US-001", depth=2)
    assert {e["edge_id"] for e in rel["edges"]} == {
        e["edge_id"] for e in sql["edges"]
    }  # same answer as the table walk

    # a change in the database is picked up before the next traversal; nothing changed means no rebuild
    assert not g.ensure_synced(repo, p)
    Ingestor(repo, HashEmbedder()).ingest_text(p, kind="story", filename="user_story.md", scope="BRD-02", text="# S\n\n## US-009: New\n\n- Epic: E-001 | Feature: FEAT-001\n\nAs a user, I want more.\n")  # fmt: skip
    assert svc.relations(p, "BRD-02/US-009")["via"] == "neo4j"
    assert count(g, "MATCH (n:Knowledge:Story {project: $p}) RETURN count(n) AS c", p) == 5


def test_rejected_links_are_not_in_neo4j_and_a_down_graph_falls_back(store: Store, tmp_path: Path, kgraph):
    g, projects = kgraph
    p = f"test-{os.urandom(5).hex()}"
    projects.append(p)
    repo = two_packs(store, tmp_path, p)
    a, b = repo.resolve(p, "BRD-01/US-001"), repo.resolve(p, "BRD-02/US-001")
    e, _ = repo.upsert_edge(p, a, b, "relates_to", "proposed", "agent", "x")  # type: ignore[arg-type]
    repo.decide_edge(e.id, "rejected", "me")
    g.sync(repo, p)
    rejected = "MATCH (:Knowledge {external_id: 'BRD-01/US-001'})-[r:RELATES_TO {project: $p}]->(:Knowledge {external_id: 'BRD-02/US-001'}) RETURN count(r) AS c"  # fmt: skip
    assert count(g, rejected, p) == 0

    class Down:
        def ensure_synced(self, repo, project):  # noqa: ANN001, ANN201
            raise GraphUnavailable("cannot reach Neo4j")

    svc = KnowledgeService(repo, HashEmbedder(), [p], graph=Down())  # type: ignore[arg-type]
    rel = svc.relations(p, "BRD-02/US-001", depth=2)
    assert (
        rel["via"].startswith("database (Neo4j unavailable") and rel["edges"]
    )  # still answers from the table


def test_bundle_evaluation_and_project_metrics(store: Store, tmp_path: Path, settings: Settings):
    from fastapi.testclient import TestClient

    from harnix.api.app import create_app
    from harnix.knowledge.capture import metrics
    from harnix.knowledge.evaluate import evaluate_bundles

    from .test_knowledge import kset

    repo = two_packs(store, tmp_path, "p")
    svc = KnowledgeService(repo, HashEmbedder(), ["p"])
    out = evaluate_bundles(svc, "p", [{"query": "Implement US-002 of BRD-01", "expect": ["BRD-01/US-002"]}])
    assert out["target_in_bundle"] == 1.0 and out["mrr"] == 1.0 and out["misses"] == []
    assert out["same_backlog"] is not None and out["seconds_per_bundle"] >= 0
    m = metrics(repo, "p")
    assert (
        m["tasks"]["total"] == 0 and m["tasks"]["validation_success_rate"] is None and m["retrieval"] is None
    )

    with TestClient(create_app(kset(settings), create_schema=True)) as c:
        repo2 = KnowledgeRepo(c.app.state.forge.store)  # type: ignore[attr-defined]
        make_pack(tmp_path / "api", "BRD-01-Alpha-Thing", "Alpha", "view the list")
        Ingestor(repo2, HashEmbedder()).ingest_documents("q", tmp_path / "api")
        body = {"project": "q", "cases": [{"query": "Implement US-001 of BRD-01", "expect": ["BRD-01/US-001"]}], "bundles": True}  # fmt: skip
        r = c.post("/api/knowledge/eval", json=body).json()
        assert r["bundles"]["target_in_bundle"] == 1.0
        latest = c.get("/api/knowledge/metrics", params={"project": "q"}).json()["retrieval"]
        assert (
            latest["bundles"]["target_in_bundle"] == 1.0 and "hybrid" in latest
        )  # the latest evaluation is kept


def test_the_bundle_records_every_candidate_and_the_project_pipeline_is_reported(store: Store, tmp_path: Path, settings: Settings):  # fmt: skip
    from fastapi.testclient import TestClient

    from harnix.api.app import create_app

    from .test_knowledge import kset

    repo = two_packs(store, tmp_path, "p")
    b = build_bundle(KnowledgeService(repo, HashEmbedder(), ["p"]), "p", "Implement US-001 of BRD-02", [])
    pipe = b.content["pipeline"]
    assert pipe["named_ids"] == ["BRD-02", "BRD-02/US-001"] and pipe["seeds"][0] == "BRD-02/US-001"
    chosen = [c for c in pipe["candidates"] if c["decision"] == "chosen"]
    assert len(chosen) == sum(len(v) for v in b.content["slots"].values())
    assert chosen[0]["external_id"] == "BRD-02/US-001" and chosen[0]["bonus"]["named_id"] == 1.0
    twin = next(c for c in pipe["candidates"] if c["external_id"] == "BRD-01/US-001")
    assert (
        twin["decision"] == "dropped" and "another backlog" in twin["reason"]
    )  # BRD-01's US-001 is not the one named
    assert all(
        c["reason"] for c in pipe["candidates"] if c["decision"] != "chosen"
    )  # every exclusion says why
    assert set(pipe["timings_ms"]) == {
        "id_lookup_and_search",
        "graph_traversal",
        "rerank_filter_budget",
        "assemble",
    }

    with TestClient(create_app(kset(settings), create_schema=True)) as c:
        repo2 = KnowledgeRepo(c.app.state.forge.store)  # type: ignore[attr-defined]
        make_pack(tmp_path / "api", "BRD-01-Alpha-Thing", "Alpha", "view the list")
        Ingestor(repo2, HashEmbedder()).ingest_documents("q", tmp_path / "api")
        out = c.get("/api/knowledge/pipeline", params={"project": "q"}).json()
        assert (
            out["ingest"]["documents"]["story"] == 2 and out["ingest"]["last"]["action"] == "ingest.documents"
        )
        assert out["chunks"]["total"] == out["chunks"]["embedded"] > 0
        assert out["ontology"]["violations_total"] == 0 and out["graph"]["by_status"]["verified"] > 0
        assert out["neo4j"]["enabled"] is False and out["extraction"]["last"] is None


def test_context_graphs_are_listed_with_the_tasks_that_used_them(settings: Settings, tmp_path: Path):
    from fastapi.testclient import TestClient

    from harnix.api.app import create_app

    from .test_knowledge import kset

    with TestClient(create_app(kset(settings), create_schema=True)) as c:
        repo = KnowledgeRepo(c.app.state.forge.store)  # type: ignore[attr-defined]
        make_pack(tmp_path, "BRD-01-Alpha-Thing", "Alpha", "view the list")
        Ingestor(repo, HashEmbedder()).ingest_documents("q", tmp_path)
        made = c.post(
            "/api/knowledge/bundles", json={"project": "q", "instructions": "Implement US-001 of BRD-01"}
        ).json()
        repo.audit(
            "q", "task:t123", "bundle.built", "bundle", made["bundle_id"], {}
        )  # a task given the same context
        rows = c.get("/api/knowledge/bundles", params={"project": "q"}).json()["bundles"]
        assert len(rows) == 1 and rows[0]["bundle_id"] == made["bundle_id"]
        assert rows[0]["tasks"] == ["t123"] and rows[0]["previews"] == 1 and rows[0]["has_pipeline"] is True
        assert rows[0]["items"]["requirements"] >= 1 and "US-001" in rows[0]["query"]
        full = c.get(f"/api/knowledge/bundles/{made['bundle_id']}").json()
        assert full["slots"]["requirements"][0]["content"]  # the content the agent receives is there to show
