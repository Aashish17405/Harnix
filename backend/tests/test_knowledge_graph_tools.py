"""The graph retrieval tools of the knowledge MCP (fixed Cypher with parameters) and which tools a server offers.

The Neo4j tests run against the real database (skipped when it is not reachable) under a random project name and delete what
they created."""

from __future__ import annotations

import uuid

import pytest

from harnix.config import Settings
from harnix.graph.store import GraphStore
from harnix.knowledge import graph_tools, tools
from harnix.knowledge.embeddings import HashEmbedder
from harnix.knowledge.graphdb import KnowledgeGraph
from harnix.knowledge.ingest import Ingestor
from harnix.knowledge.repo import KnowledgeRepo
from harnix.knowledge.service import KnowledgeService
from harnix.persistence.store import Store

from .test_knowledge_medium import BRD, STORIES

OLD = {"knowledge_context", "knowledge_search", "knowledge_get", "knowledge_relations", "knowledge_trace", "knowledge_criteria", "knowledge_list", "knowledge_projects", "knowledge_schema", "knowledge_query"}  # fmt: skip
NEW = {"knowledge_impact", "knowledge_path", "knowledge_uses", "knowledge_gaps", "knowledge_concepts"}


@pytest.fixture
def repo(store: Store) -> KnowledgeRepo:
    return KnowledgeRepo(store)


# ---- which tools a server offers (no Neo4j needed) ------------------------------------------------------------------


def test_only_tools_that_can_work_on_this_server_are_offered(repo: KnowledgeRepo):
    assert tools.NAMES == OLD | NEW
    no_graph_scoped = KnowledgeService(repo, HashEmbedder(), ["p"])
    assert tools.names(no_graph_scoped) == OLD - {"knowledge_schema", "knowledge_query", "knowledge_projects"}
    assert tools.names(KnowledgeService(repo, HashEmbedder(), None)) == OLD - {
        "knowledge_schema",
        "knowledge_query",
    }  # unscoped: the project list is useful
    assert "knowledge_projects" in tools.names(
        KnowledgeService(repo, HashEmbedder(), ["a", "b"])
    )  # there is something to choose
    assert tools.GRAPH_TOOLS == {"knowledge_schema", "knowledge_query"} | NEW
    for t in tools.TOOL_DEFS:  # every tool says what it is for and takes an object
        assert t["description"].strip() and t["inputSchema"]["type"] == "object", t["name"]
        assert t["name"].startswith("knowledge_")


def test_a_graph_tool_without_a_graph_database_says_so_and_bad_arguments_are_refused_first(
    repo: KnowledgeRepo,
):
    svc = KnowledgeService(repo, HashEmbedder(), ["p"])
    ok, text = tools.call_text(svc, "knowledge_impact", {"ref": "US-001"})
    assert not ok and "(Neo4j) is not enabled" in text
    for name, args, why in [
        ("knowledge_impact", {}, "'ref' is required"),
        ("knowledge_impact", {"ref": "US-001", "direction": "sideways"}, "'direction'"),
        ("knowledge_impact", {"ref": "US-001", "depth": "deep"}, "'depth' must be a number"),
        ("knowledge_path", {"from": "US-001"}, "'to' is required"),
        ("knowledge_uses", {"name": " "}, "'name' is required"),
        ("knowledge_gaps", {"types": ["flags_gap", "drop database"]}, "'types' must be a list of"),
        ("knowledge_concepts", {"type": "banana"}, "'type' must be one of"),
    ]:
        ok, text = tools.call_text(svc, name, args)
        assert not ok and why in text, (name, text)


def test_the_cypher_is_fixed_text_and_the_caller_only_supplies_parameters():
    import inspect

    src = inspect.getsource(graph_tools)
    assert (
        "params" in src
        and "LOAD CSV" not in src
        and "MERGE" not in src
        and "DELETE" not in src
        and "CREATE" not in src
    )
    from harnix.knowledge.query import check_cypher

    assert graph_tools.STRUCTURE.split("|") == [
        "DERIVES_FROM",
        "DEPENDS_ON",
        "SATISFIES",
        "IMPLEMENTS",
        "TESTS",
        "REFINES",
    ]
    for depth in (1, 4):  # bounds are integers inside fixed limits
        check_cypher(f"MATCH p = (x:Knowledge {{project: $project}})-[rels:{graph_tools.STRUCTURE}*1..{depth}]->(t:Knowledge {{project: $project}}) RETURN x")  # fmt: skip


# ---- against the real Neo4j -----------------------------------------------------------------------------------------


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


def world(repo: KnowledgeRepo, g: KnowledgeGraph, projects: list[str]) -> tuple[KnowledgeService, str]:
    p = "gt" + uuid.uuid4().hex[:8]
    projects.append(p)
    i = Ingestor(repo, HashEmbedder(), "test")
    i.ingest_text(p, kind="story", filename="user_story.md", text=STORIES + "## US-004: Story 4\n\nAs a user I want thing 4, so that I can work.\n\n## US-005: Story 5\n\nAs a user I want thing 5.\n")  # fmt: skip
    dag = "graph TD\n  US-001 --> US-002\n  US-001 --> US-003\n  US-002 --> US-004\n"
    i.ingest_text(p, kind="doc", filename="x-dependency-dag.md", text="```mermaid\n" + dag + "```\n")
    i.ingest_text(p, kind="brd", filename="brd.md", text=BRD)
    by = {s.external_id: s for s in repo.sources(p, limit=1000)}
    repo.upsert_edge(
        p, by["US-005"], by["US-001"], "depends_on", "proposed", "extracted", "a guess"
    )  # a proposal
    repo.upsert_edge(
        p, by["US-003"], by["US-002"], "contradicts", "proposed", "extracted", "inferred: they disagree"
    )
    repo.upsert_edge(
        p, by["x-dependency-dag"], by["US-005"], "flags_gap", "proposed", "source", "flagged: open question"
    )
    i.add(p, "concept", "concept:corporate banking system", "Corporate Banking System", "# Corporate Banking System\nType: system\n", origin="extracted", meta={"concept_type": "system", "name": "Corporate Banking System"})  # fmt: skip
    system = repo.find_source(p, "concept", "concept:corporate banking system")
    repo.upsert_edge(
        p, by["US-002"], system, "mentions", "verified", "source", "relies on the Corporate Banking System"
    )  # type: ignore[arg-type]
    return KnowledgeService(repo, HashEmbedder(), [p], graph=g), p


def rows(service: KnowledgeService, name: str, args: dict) -> list[dict]:
    out = tools.call(service, name, args)
    return [dict(zip(out["columns"], r, strict=True)) for r in out["rows"]]


def test_impact_follows_dependencies_downstream_and_upstream_within_the_depth(repo: KnowledgeRepo, kgraph):
    g, projects = kgraph
    svc, _ = world(repo, g, projects)
    one = rows(svc, "knowledge_impact", {"ref": "US-001", "depth": 1, "verified_only": True})
    assert {r["id"]: r["hops"] for r in one} == {"US-002": 1, "US-003": 1}
    two = rows(svc, "knowledge_impact", {"ref": "US-001", "depth": 2, "verified_only": True})
    assert {r["id"]: r["hops"] for r in two} == {"US-002": 1, "US-003": 1, "US-004": 2} and two[0]["via"] == [
        "DEPENDS_ON"
    ]
    with_proposed = {r["id"] for r in rows(svc, "knowledge_impact", {"ref": "US-001", "depth": 1})}
    assert with_proposed == {"US-002", "US-003", "US-005"}  # the proposal counts unless verified_only
    up = rows(
        svc, "knowledge_impact", {"ref": "US-004", "direction": "upstream", "depth": 3, "verified_only": True}
    )
    assert {r["id"] for r in up} == {"US-002", "US-001"}
    assert rows(svc, "knowledge_impact", {"ref": "US-404"}) == []


def test_path_between_two_items_and_none_beyond_the_hop_limit(repo: KnowledgeRepo, kgraph):
    g, projects = kgraph
    svc, _ = world(repo, g, projects)
    (found,) = rows(svc, "knowledge_path", {"from": "US-004", "to": "US-003", "verified_only": True})
    # the dependency chain is three links, but the graph document that cites both stories is two: the shortest wins
    assert found["items"] == ["US-004", "x-dependency-dag", "US-003"] and found["hops"] == 2
    assert all(link.endswith("(verified)") for link in found["links"])
    assert (
        rows(svc, "knowledge_path", {"from": "US-004", "to": "US-003", "max_hops": 1, "verified_only": True})
        == []
    )
    assert rows(svc, "knowledge_path", {"from": "US-004", "to": "nothing-like-this"}) == []


def test_uses_gaps_and_concepts(repo: KnowledgeRepo, kgraph):
    g, projects = kgraph
    svc, proj = world(repo, g, projects)
    (used,) = rows(svc, "knowledge_uses", {"name": "banking system"})
    assert used["concept"] == "concept:corporate banking system" and used["type"] == "system"
    assert used["linked"] == [{"id": "US-002", "kind": "story", "link": "MENTIONS", "status": "verified"}]
    gaps = rows(svc, "knowledge_gaps", {})
    assert {(r["link"], r["source"], r["target"], r["status"]) for r in gaps} == {
        ("CONTRADICTS", "US-003", "US-002", "proposed"),
        ("FLAGS_GAP", "x-dependency-dag", "US-005", "proposed"),
    }
    assert [r["target"] for r in rows(svc, "knowledge_gaps", {"ref": "US-005"})] == ["US-005"]
    assert rows(svc, "knowledge_gaps", {"types": ["clarifies"]}) == []
    assert rows(svc, "knowledge_gaps", {"verified_only": True}) == []  # all of these are proposals
    fields = rows(svc, "knowledge_concepts", {"type": "field"})
    assert sorted(r["title"].split(" [")[0] for r in fields) == ["Account Name", "Account Number"]
    section = repo.find_source(proj, "concept", fields[0]["id"])
    sec_id = repo.version(section.id).meta["section"]  # type: ignore[union-attr]
    assert len(rows(svc, "knowledge_concepts", {"mentioned_by": sec_id})) == 2
    assert [r["id"] for r in rows(svc, "knowledge_concepts", {"type": "system", "contains": "bank"})] == ["concept:corporate banking system"]  # fmt: skip
    assert rows(svc, "knowledge_concepts", {"type": "actor"}) == []


def test_a_hostile_id_is_a_parameter_not_query_text_and_projects_stay_apart(repo: KnowledgeRepo, kgraph):
    g, projects = kgraph
    svc, _ = world(repo, g, projects)
    _, other_project = world(repo, g, projects)
    evil = "US-001' OR true //"
    assert rows(svc, "knowledge_impact", {"ref": evil}) == []
    assert rows(svc, "knowledge_uses", {"name": "') MATCH (n) DETACH DELETE n //"}) == []
    assert rows(svc, "knowledge_gaps", {"ref": "' OR 1=1 //"}) == []
    # both projects have a US-001: an answer holds only its own project's items, each once
    mine = rows(svc, "knowledge_impact", {"ref": "US-001", "verified_only": True})
    assert sorted(r["id"] for r in mine) == ["US-002", "US-003", "US-004"]
    theirs = rows(KnowledgeService(repo, HashEmbedder(), [other_project], graph=g), "knowledge_impact", {"ref": "US-001", "verified_only": True})  # fmt: skip
    assert sorted(r["id"] for r in theirs) == ["US-002", "US-003", "US-004"]
    assert (
        g.store._read(
            lambda tx: tx.run(
                "MATCH (n:Knowledge) WHERE n.project IN $ps RETURN count(n) AS c", ps=projects
            ).single()["c"]
        )
        > 10
    )  # the attempts deleted nothing  # noqa: SLF001


def test_a_server_with_a_graph_offers_the_graph_tools(repo: KnowledgeRepo, kgraph):
    g, projects = kgraph
    svc, _ = world(repo, g, projects)
    assert tools.names(svc) == tools.NAMES - {"knowledge_projects"}  # scoped to one project
    assert tools.names(KnowledgeService(repo, HashEmbedder(), None, graph=g)) == tools.NAMES
