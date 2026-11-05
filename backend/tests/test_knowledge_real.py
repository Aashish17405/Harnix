"""The knowledge pipeline on the real documents (`testing_docs/`: a BRD, a domain note, a JSON backlog of epics and features,
a JSON file of user stories). These are the project's only test data files; they are read, never written (a guard below
checks their bytes before and after every test). What they cannot exercise (a Jira export, a code repository, a design
document, a dependency graph, a report, a CSV or XLSX rendering) is tested with inline text in the other test files."""

from __future__ import annotations

import hashlib
import shutil
import uuid
from pathlib import Path

import pytest

from harnix.config import Settings
from harnix.graph.store import GraphStore
from harnix.knowledge import graph_tools, markdown_export, tools
from harnix.knowledge.context import build_bundle
from harnix.knowledge.coverage import audit
from harnix.knowledge.embeddings import HashEmbedder
from harnix.knowledge.graphdb import KnowledgeGraph
from harnix.knowledge.ingest import Ingestor
from harnix.knowledge.ontology import default
from harnix.knowledge.repo import KnowledgeRepo
from harnix.knowledge.service import KnowledgeService
from harnix.persistence.store import Store

from .test_knowledge import DOCS, P


def fingerprint() -> dict[str, str]:
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(DOCS.iterdir()) if p.is_file()}


@pytest.fixture(autouse=True)
def real_documents_stay_untouched():
    before = fingerprint()
    assert set(before) == {"brd.md", "domain_knowledge.md", "epic features.json", "user_story.json"}
    yield
    assert fingerprint() == before


@pytest.fixture
def repo(store: Store) -> KnowledgeRepo:
    return KnowledgeRepo(store)


@pytest.fixture
def loaded(repo: KnowledgeRepo):
    res = Ingestor(repo, HashEmbedder(), "test").ingest_documents(P, DOCS)
    return repo, res


def kinds(repo: KnowledgeRepo) -> dict[str, int]:
    out: dict[str, int] = {}
    for s in repo.sources(P, limit=10_000):
        out[s.kind] = out.get(s.kind, 0) + 1
    return out


def test_the_real_documents_are_taken_in_whole_and_reprocessing_changes_nothing(loaded, tmp_path: Path):
    repo, res = loaded
    assert res.failed == [] and res.skipped == [] and res.unresolved == [] and res.findings == []
    k = kinds(repo)
    assert (k["brd"], k["epic"], k["feature"], k["story"], k["rule"], k["doc"]) == (1, 4, 11, 48, 29, 3)
    assert k["requirement"] >= 48  # the BRD's sections and the features' in-scope items
    again = Ingestor(repo, HashEmbedder(), "test").ingest_documents(P, DOCS)
    assert again.created == 0 and again.updated == 0 and again.edges_created == 0 and again.findings == []
    assert audit(repo, P, DOCS)["inputs"]["score"] == 1.0  # every line and value of the four files is stored


def test_every_stored_line_is_in_a_chunk_and_every_link_is_one_the_ontology_allows(loaded):
    repo, _ = loaded
    a = audit(repo, P)
    assert a["chunks"]["uncovered"] == 0 and a["orphans"]["by_kind"] == {
        "rule": 3
    }  # BND-003..005 name no one
    onto = default()
    by_id = {s.id: s for s in repo.sources(P, limit=10_000)}
    bad = [e for e in repo.edges(P, limit=100_000) if not onto.allowed(e.type, repo.node_type(by_id[e.src_id]), repo.node_type(by_id[e.dst_id]))]  # fmt: skip
    assert bad == []


def test_exact_lookup_and_the_declared_links_of_a_story(loaded):
    repo, _ = loaded
    svc = KnowledgeService(repo, HashEmbedder(), [P])
    assert svc.get(P, "US-003")["title"] == "US-003: Select specific entities to scope the summary"
    pairs = {(e["from"], e["type"], e["to"]): e for e in svc.relations(P, "US-003", 1, None, ("verified", "proposed"))["edges"]}  # fmt: skip
    assert ("US-003", "derives_from", "FEAT-001") in pairs and ("US-003", "depends_on", "US-002") in pairs
    assert all(e["authoritative"] for e in pairs.values())  # declared by the file itself
    trace = tools.call(svc, "knowledge_trace", {"ref": "US-003"})
    chain = [c["external_id"] for c in trace["chain_to_brd"]]
    assert chain and chain[-1] == "brd"  # the chain goes up to the BRD, through the section the story cites


def test_search_finds_the_right_things_for_questions_about_the_documents(loaded):
    repo, _ = loaded
    svc = KnowledgeService(repo, HashEmbedder(), [P])

    def top(q: str, n: int = 4) -> list[str]:
        return [h["external_id"] for h in svc.search(P, q, None, n)["hits"]]

    assert top("Root VA", 1) == ["concept:root va"]  # a glossary term of the domain note
    assert "US-009" in top("closed accounts how long listed")
    only = svc.search(P, "closed accounts how long listed", ["story"], 5)["hits"]
    assert only and {h["kind"] for h in only} == {"story"}


def test_the_context_for_a_story_is_sufficient_cited_and_reproducible(loaded):
    repo, _ = loaded
    svc = KnowledgeService(repo, HashEmbedder(), [P])
    one = build_bundle(svc, P, "Implement US-003", [])
    two = build_bundle(svc, P, "Implement US-003", [])
    assert one.status == "sufficient" and one.id == two.id  # same inputs, same id
    assert (
        one.content["slots"]["requirements"][0]["external_id"] == "US-003"
    )  # the target comes first, given whole


def test_the_bundle_records_every_layer_down_to_the_chunk_and_the_link(loaded):
    repo, _ = loaded
    svc = KnowledgeService(repo, HashEmbedder(), [P])
    b = build_bundle(svc, P, "Implement US-039: mark and unmark favorite virtual accounts", [])
    pipe = b.content["pipeline"]
    assert pipe["prompt"]["named"][0]["id"] == "US-039" and pipe["prompt"]["embedding"]["dims"] == 256
    assert 0 < pipe["prompt"]["embedding"]["nonzero"] <= 256 and len(pipe["prompt"]["embedding"]["top"]) <= 8
    search = pipe["search"]  # layer 2: the three rankings the search was fused from, chunk by chunk
    assert (
        search["chunks_searched"] > 100
        and search["rrf_k"] == 60
        and set(search["weights"]) == {"keyword", "vector"}
    )
    assert [r["rank"] for r in search["keyword"]] == list(range(1, len(search["keyword"]) + 1))
    scores = [r["rrf"] for r in search["fused"]]
    assert scores == sorted(scores, reverse=True) and all(
        r["keyword_rank"] or r["vector_rank"] for r in search["fused"]
    )
    assert all(
        r["chunk_id"] and r["external_id"] and r["snippet"] and r["ordinal"] is not None
        for r in search["fused"]
    )
    cand = {
        c["external_id"]: c for c in pipe["candidates"] if c["decision"] == "chosen"
    }  # layer 3: rerank per candidate
    target = cand["US-039"]
    assert (
        target["chunks"]
        and target["chunks"][0]["similarity"] >= target["chunks"][-1]["similarity"]
        and target["chunks"][0]["text"]
    )
    assert target["bonus"]["named_id"] > 0 and target["source_id"] and target["title"].startswith("US-039")
    seeds = {
        e["seed"] for e in pipe["expansion"]
    }  # layer 4: every traversal, its nodes with their distance, its links
    assert "US-039" in seeds and seeds <= set(pipe["seeds"])
    deep = next(e for e in pipe["expansion"] if e["seed"] == "US-039" and e["depth"] == 2)
    assert (
        deep["via"] == "database"
        and deep["nodes"]
        and deep["edges"]
        and {n["hop"] for n in deep["nodes"]} >= {0, 1}
    )
    assert all(e["from"] and e["to"] and e["status"] in ("verified", "proposed") for e in deep["edges"])
    assert any(c["pulled_by"] == "US-039" and c["external_id"] != "US-039" for c in pipe["candidates"])
    # the explanation is not part of what the agent is told or of the bundle's identity, and search through MCP stays plain
    assert "explain" not in tools.call(svc, "knowledge_search", {"query": "favorite"})
    again = build_bundle(svc, P, "Implement US-039: mark and unmark favorite virtual accounts", [])
    assert again.id == b.id


def test_a_changed_story_becomes_a_new_version_and_nothing_else_does(repo: KnowledgeRepo, tmp_path: Path):
    docs = tmp_path / "docs"
    shutil.copytree(DOCS, docs)
    ing = Ingestor(repo, HashEmbedder(), "test")
    ing.ingest_documents(P, docs)
    story = docs / "user_story.json"
    story.write_text(story.read_text(encoding="utf-8").replace("Select specific entities to scope the summary", "Choose entities to narrow the summary", 1), encoding="utf-8")  # fmt: skip
    res = ing.ingest_documents(P, docs)
    assert res.updated >= 1 and res.created == 0
    src = repo.find_source(P, "story", "US-003")
    assert src is not None and src.current_version == 2
    assert (
        "Select specific entities" in repo.version(src.id, 1).content
        and "Choose entities" in repo.version(src.id, 2).content
    )  # type: ignore[union-attr]
    other = repo.find_source(P, "story", "US-004")
    assert other is not None and other.current_version == 1  # an untouched story keeps its version


def test_the_tables_of_the_brd_become_fields_with_their_columns(loaded):
    repo, _ = loaded
    fields = [s for s in repo.sources(P, kind="concept", limit=10_000) if s.origin == "fields"]
    assert len(fields) == 63  # three data-field tables
    number = next(
        s
        for s in fields
        if repo.version(s.id).meta["name"] == "Virtual Account Number"
        and "02" in repo.version(s.id).meta["section"]
    )  # type: ignore[union-attr]
    meta = repo.version(number.id).meta  # type: ignore[union-attr]
    assert (
        meta["attributes"]["Data Type"] == "Alphanumeric"
        and meta["group"]
        and repo.node_type(number) == "field"
    )


def test_the_mcp_tools_answer_from_the_real_documents(loaded):
    repo, _ = loaded
    svc = KnowledgeService(repo, HashEmbedder(), [P])
    svc.projects = [P]
    assert tools.names(svc) == tools.NAMES - tools.GRAPH_TOOLS - {
        "knowledge_projects"
    }  # no Neo4j, one project
    listed = tools.call(svc, "knowledge_list", {"kind": "story", "limit": 100})
    assert len(listed["sources"]) == 48
    crit = tools.call(svc, "knowledge_criteria", {"ref": "US-003"})
    assert crit["criteria"] and all("GIVEN" in c["text"] for c in crit["criteria"])
    ok, text = tools.call_text(svc, "knowledge_get", {"ref": "US-404"})
    assert not ok and "US-404" in text  # an error result, not a crash


def test_the_markdown_copy_holds_every_source_and_a_second_export_writes_nothing(loaded, tmp_path: Path):
    repo, _ = loaded
    out = tmp_path / "md"
    first = markdown_export.export(repo, P, out)
    n = len(repo.sources(P, limit=10_000))
    assert first["files"] == n and first["written"] == n
    story = (out / "story" / "US-003.md").read_text(encoding="utf-8")
    assert "- derives_from [[FEAT-001]] (verified)" in story and "Select specific entities" in story
    assert markdown_export.export(repo, P, out)["written"] == 0


# ---- the graph tools on the real documents (real Neo4j; skipped when it is not reachable) ---------------------------


def test_the_graph_tools_on_the_real_documents(neo4j_settings: Settings, repo: KnowledgeRepo):
    s = neo4j_settings
    store = GraphStore(s.neo4j_uri, s.neo4j_user, s.neo4j_password or "", s.neo4j_database, query_timeout=15)
    project = "rd" + uuid.uuid4().hex[:8]
    try:
        Ingestor(repo, HashEmbedder(), "test").ingest_documents(project, DOCS)
        svc = KnowledgeService(repo, HashEmbedder(), [project], graph=KnowledgeGraph(store))

        def rows(name: str, args: dict) -> list[dict]:
            out = tools.call(svc, name, args)
            return [dict(zip(out["columns"], r, strict=True)) for r in out["rows"]]

        down = {
            r["id"]: r["hops"]
            for r in rows("knowledge_impact", {"ref": "US-001", "depth": 1, "verified_only": True})
        }
        assert down["US-002"] == 1 and all(h == 1 for h in down.values())
        assert {r["id"] for r in rows("knowledge_impact", {"ref": "US-003", "direction": "upstream", "depth": 1})} >= {"FEAT-001", "US-002"}  # fmt: skip
        assert rows("knowledge_gaps", {}) == []  # nothing in these files flags a gap or a contradiction
        assert len(rows("knowledge_concepts", {"type": "field", "limit": 200})) == 63
        assert [r["title"] for r in rows("knowledge_concepts", {"type": "domain", "contains": "root va"})][
            :1
        ] == ["Root VA"]
        (found,) = rows("knowledge_path", {"from": "US-003", "to": "E-001", "verified_only": True})
        assert found["items"][0] == "US-003" and found["items"][-1] == "E-001"
        assert graph_tools.NAMES <= tools.names(svc)
    finally:
        store._write(
            lambda tx: tx.run("MATCH (n:Knowledge {project: $p}) DETACH DELETE n", p=project).consume()
        )  # noqa: SLF001
        store._write(
            lambda tx: tx.run("MATCH (n:KnowledgeProject {project: $p}) DELETE n", p=project).consume()
        )  # noqa: SLF001
        store.close()
