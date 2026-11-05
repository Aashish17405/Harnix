"""Querying the knowledge graph like a graph database browser: read-only, project-scoped, bounded. The guard is tested
without Neo4j; the queries themselves run against the real Neo4j (skipped when it is not reachable)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from harnix.config import Settings
from harnix.graph.store import GraphStore, GraphUnavailable
from harnix.knowledge.embeddings import HashEmbedder
from harnix.knowledge.graphdb import KnowledgeGraph
from harnix.knowledge.ingest import Ingestor
from harnix.knowledge.query import QueryRefused, check_cypher, nl_to_cypher, run_query, schema_text
from harnix.knowledge.repo import KnowledgeRepo
from harnix.models.scripted import ScriptedProvider
from harnix.persistence.store import Store

from .test_knowledge_intake import make_pack

OK = "MATCH (n:Knowledge {project: $project}) RETURN n LIMIT 5"


@pytest.mark.parametrize(
    "cypher,why",
    [
        ("LOAD CSV FROM 'file:///etc/passwd' AS l RETURN l, $project", "LOAD CSV"),
        ("CALL dbms.listConfig() YIELD name RETURN name, $project", "dbms.listConfig"),
        ("CALL apoc.load.json('http://x') YIELD value RETURN value, $project", "apoc.load.json"),
        ("USE system\nSHOW USERS", "USE"),
        ("CREATE USER x SET PASSWORD 'y'", "administration"),
        ("MATCH (n) RETURN count(n)", "$project"),  # unscoped: would count every project
        ("", "Write a query"),
    ],
)
def test_the_guard_refuses_what_a_browser_here_must_not_run(cypher: str, why: str):
    with pytest.raises(QueryRefused, match=why.replace("$", r"\$").replace("(", r"\(")):
        check_cypher(cypher)


def test_the_guard_allows_reads_and_ignores_keywords_inside_strings():
    check_cypher(OK)
    check_cypher("MATCH (n:Knowledge {project: $project}) WHERE n.title CONTAINS 'load csv' RETURN n")
    check_cypher("CALL db.labels()")  # schema introspection is fine


def test_a_question_becomes_checked_cypher_and_a_bad_answer_is_refused():
    answer = (
        '{"cypher": "MATCH (s:Story {project: $project}) RETURN s LIMIT 10", "explanation": "the stories"}'
    )
    out = nl_to_cypher(ScriptedProvider([{"text": answer}]), "list the stories")
    assert out["cypher"].startswith("MATCH (s:Story") and out["explanation"] == "the stories"
    evil = '{"cypher": "LOAD CSV FROM \'file:///x\' AS l RETURN l, $project"}'
    with pytest.raises(QueryRefused):  # a model's query is untrusted like any other
        nl_to_cypher(ScriptedProvider([{"text": evil}]), "anything")
    assert ":Story" in schema_text() and ":DERIVES_FROM" in schema_text()


# ---- against Neo4j ----------------------------------------------------------------------------------------------------


@pytest.fixture
def kg(neo4j_settings: Settings):
    s = neo4j_settings
    store = GraphStore(s.neo4j_uri, s.neo4j_user, s.neo4j_password or "", s.neo4j_database, query_timeout=15)
    made: list[str] = []
    yield KnowledgeGraph(store), made
    for p in made:
        store._write(
            lambda tx, p=p: tx.run("MATCH (n:Knowledge {project: $p}) DETACH DELETE n", p=p).consume()
        )  # noqa: SLF001
        store._write(
            lambda tx, p=p: tx.run("MATCH (n:KnowledgeProject {project: $p}) DELETE n", p=p).consume()
        )  # noqa: SLF001
    store.close()


def test_queries_run_read_only_and_only_see_their_project(store: Store, tmp_path: Path, kg):
    g, made = kg
    a, b = f"qa-{os.urandom(4).hex()}", f"qb-{os.urandom(4).hex()}"
    made += [a, b]
    repo = KnowledgeRepo(store)
    make_pack(tmp_path / "a", "BRD-01-Alpha-Thing", "Alpha", "view the list")
    make_pack(tmp_path / "b", "BRD-01-Alpha-Thing", "Beta", "pin")
    Ingestor(repo, HashEmbedder()).ingest_documents(a, tmp_path / "a")
    Ingestor(repo, HashEmbedder()).ingest_documents(b, tmp_path / "b")
    g.sync(repo, a)
    g.sync(repo, b)

    r = run_query(g, a, "MATCH p=(s:Story {project: $project})-[:DERIVES_FROM*1..3]->(x) RETURN p")
    assert (
        r.records > 0
        and r.nodes
        and r.edges
        and {n["kind"] for n in r.nodes} >= {"story", "feature", "epic", "brd"}
    )
    assert all(e["type"] == "derives_from" for e in r.edges)
    counts = run_query(
        g, a, "MATCH (n:Knowledge {project: $project}) RETURN n.kind AS kind, count(*) AS n ORDER BY kind"
    )
    assert counts.columns == ["kind", "n"] and ["story", 2] in counts.rows and not counts.nodes

    # a query that reaches into another project still returns only this project's nodes
    leak = run_query(g, a, "MATCH (n:Knowledge) WHERE n.project = $project OR true RETURN n")
    assert {n["id"] for n in leak.nodes} == {s.id for s in repo.sources(a)} and leak.hidden > 0
    # writes are refused by Neo4j itself (read transaction), and nothing changed
    with pytest.raises(GraphUnavailable, match="AccessMode|read access mode"):
        run_query(g, a, "MATCH (n:Knowledge {project: $project}) SET n.title = 'x' RETURN n")
    assert (
        run_query(g, a, "MATCH (n:Knowledge {project: $project}) WHERE n.title = 'x' RETURN n").records == 0
    )
