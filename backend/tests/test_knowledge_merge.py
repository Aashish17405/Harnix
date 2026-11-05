"""Reprocessing never duplicates: stable keys, MERGE in Neo4j, unique constraints, and a lost insert race in the database.

The Neo4j tests run against the real database (skipped when it is not reachable) under random project names."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy.exc import IntegrityError

from harnix.knowledge.capture import decide
from harnix.knowledge.embeddings import HashEmbedder
from harnix.knowledge.graphdb import RELS, SCHEMA, KnowledgeGraph, uid
from harnix.knowledge.ingest import Ingestor
from harnix.knowledge.repo import KnowledgeRepo, _once_more
from harnix.persistence.store import Store

from .test_knowledge_graph_tools import world
from .test_knowledge_graphdb import kgraph  # noqa: F401 - the fixture

P = "mg"


@pytest.fixture
def repo(store: Store) -> KnowledgeRepo:
    return KnowledgeRepo(store)


def shape(g: KnowledgeGraph, p: str) -> dict[str, int]:
    def run(tx):  # noqa: ANN001, ANN202
        n = tx.run(
            "MATCH (n:Knowledge {project: $p}) RETURN count(n) AS c, count(DISTINCT n.uid) AS d", p=p
        ).single()
        r = tx.run(
            "MATCH (:Knowledge {project: $p})-[x]->() RETURN count(x) AS c, count(DISTINCT x.edge_id) AS d",
            p=p,
        ).single()
        return {"nodes": n["c"], "node_keys": n["d"], "links": r["c"], "link_keys": r["d"]}

    return g.store._read(run)  # noqa: SLF001


# ---- Neo4j ------------------------------------------------------------------------------------------------------------


def test_syncing_again_keeps_the_same_nodes_and_links(repo: KnowledgeRepo, kgraph):  # noqa: F811
    g, projects = kgraph
    svc, p = world(repo, g, projects)
    g.sync(repo, p)
    first = shape(g, p)
    ids = g.store._read(
        lambda tx: sorted(
            r["i"] for r in tx.run("MATCH (n:Knowledge {project: $p}) RETURN elementId(n) AS i", p=p)
        )
    )  # noqa: SLF001
    for _ in range(3):
        g.sync(repo, p)
    assert (
        shape(g, p) == first
        and first["nodes"] == first["node_keys"]
        and first["links"] == first["link_keys"] > 0
    )
    again = g.store._read(
        lambda tx: sorted(
            r["i"] for r in tx.run("MATCH (n:Knowledge {project: $p}) RETURN elementId(n) AS i", p=p)
        )
    )  # noqa: SLF001
    assert again == ids  # the nodes were merged in place, not deleted and created again


def test_two_syncs_at_once_make_no_copies(repo: KnowledgeRepo, kgraph):  # noqa: F811
    g, projects = kgraph
    svc, p = world(repo, g, projects)
    with ThreadPoolExecutor(4) as pool:
        for fut in [pool.submit(g.sync, repo, p) for _ in range(4)]:
            fut.result()  # none of them fails on a key conflict
    s = shape(g, p)
    assert s["nodes"] == s["node_keys"] == len(repo.sources(p, limit=1000)) and s["links"] == s["link_keys"]


def test_what_the_database_no_longer_holds_leaves_the_mirror(repo: KnowledgeRepo, kgraph):  # noqa: F811
    g, projects = kgraph
    svc, p = world(repo, g, projects)
    g.sync(repo, p)
    before = shape(g, p)
    (guess,) = [e for e in repo.edges(p, limit=1000) if e.type == "depends_on" and e.status == "proposed"]
    assert decide(repo, guess.id, False, "reviewer", None) == "rejected"
    repo.delete_sources(p, ("concept",), origin="extracted")
    g.sync(repo, p)
    after = shape(g, p)
    assert after["links"] < before["links"] and after["nodes"] == before["nodes"] - 1
    assert (
        g.store._read(
            lambda tx: tx.run("MATCH ()-[x {edge_id: $i}]->() RETURN count(x) AS c", i=guess.id).single()["c"]
        )
        == 0
    )  # noqa: SLF001


def test_the_database_refuses_a_second_copy(repo: KnowledgeRepo, kgraph):  # noqa: F811
    from harnix.graph.store import GraphUnavailable

    g, projects = kgraph
    svc, p = world(repo, g, projects)
    g.sync(repo, p)
    by = {s.external_id: s for s in repo.sources(p, limit=1000)}
    edge = next(e for e in repo.edges(p, limit=1000) if e.type == "depends_on" and e.status == "verified")
    with pytest.raises(GraphUnavailable):  # a node with an existing uid
        g.store._write(
            lambda tx: tx.run(
                "CREATE (:Knowledge {uid: $u, project: $p})", u=uid(p, by["US-001"].id), p=p
            ).consume()
        )  # noqa: SLF001
    with pytest.raises(GraphUnavailable):  # a link with an existing edge_id
        g.store._write(
            lambda tx: tx.run(
                "MATCH (a:Knowledge {uid: $a}), (b:Knowledge {uid: $b}) CREATE (a)-[:DEPENDS_ON {edge_id: $i}]->(b)",
                a=uid(p, by["US-004"].id),
                b=uid(p, by["US-005"].id),
                i=edge.id,
            ).consume()
        )  # noqa: SLF001
    assert len(SCHEMA) == 3 + len(RELS)  # one uniqueness constraint per link type, plus the node ones
    assert shape(g, p)["nodes"] == shape(g, p)["node_keys"]


# ---- the database: a lost insert race -----------------------------------------------------------------------------


def test_a_lost_insert_race_is_retried_once_and_then_reported():
    calls = []

    def flaky() -> str:
        calls.append(1)
        if len(calls) == 1:
            raise IntegrityError("insert", {}, Exception("duplicate key"))
        return "found the winner's row"

    assert _once_more(flaky)() == "found the winner's row" and len(calls) == 2
    broken = _once_more(lambda: (_ for _ in ()).throw(IntegrityError("insert", {}, Exception("always"))))
    with pytest.raises(IntegrityError):  # a second conflict is a real problem, not a race
        broken()


def test_reprocessing_the_same_document_at_once_leaves_one_row_of_each(repo: KnowledgeRepo):
    ing = Ingestor(repo, HashEmbedder(), "test")
    ing.ingest_text(
        P,
        kind="story",
        filename="user_story.md",
        text="# S\n\n## US-001: A\n\nAs a user I want a.\n\n## US-002: B\n\nAs a user I want b.\n",
    )
    a, b = repo.resolve(P, "US-001"), repo.resolve(P, "US-002")
    assert a is not None and b is not None

    def again(i: int):  # noqa: ANN202
        repo.upsert_edge(P, a, b, "depends_on", "proposed", "extracted", f"run {i}")
        return Ingestor(repo, HashEmbedder(), "test").ingest_text(P, kind="story", filename="user_story.md", text="# S\n\n## US-001: A\n\nAs a user I want a.\n\n## US-002: B\n\nAs a user I want b.\n")  # fmt: skip

    with ThreadPoolExecutor(6) as pool:
        for fut in [pool.submit(again, i) for i in range(6)]:
            fut.result()
    assert len([e for e in repo.edges(P, limit=100) if e.type == "depends_on"]) == 1
    assert sorted(s.external_id for s in repo.sources(P, limit=100)) == ["US-001", "US-002"]
    assert repo.version(a.id).version == 1  # type: ignore[union-attr]  # reprocessing identical text made no new version
