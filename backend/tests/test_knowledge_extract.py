"""The content graph: concepts, actors, fields and rules extracted from inside the documents, every one with a quote that is
checked against the text. The model is a scripted stand-in (a fixed answer per document): these tests prove the checking,
merging and storage, not that a real model extracts well (see docs/knowledge.md for the measured run)."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from harnix.api.app import create_app
from harnix.config import Settings
from harnix.knowledge.embeddings import HashEmbedder
from harnix.knowledge.extract import Extraction, key_of, normalize, parse_json, quote_found, validate
from harnix.knowledge.extract import run as extract_run
from harnix.knowledge.ingest import Ingestor
from harnix.knowledge.repo import KnowledgeRepo
from harnix.models.scripted import ScriptedProvider
from harnix.persistence.store import Store

from .test_knowledge import kset
from .test_knowledge_intake import make_pack

P = "va"
STORY = (
    "## US-003: Mark an account as favorite\n\nAs a Corporate User, I want to mark and unmark virtual accounts as favorites, "
    "so that my preferred accounts appear first.\n\n- Dependencies: Maximum 10 favorite accounts per user.\n"
)


@pytest.fixture
def repo(store: Store) -> KnowledgeRepo:
    return KnowledgeRepo(store)


def good() -> dict[str, Any]:
    return {
        "entities": [
            {
                "name": "Corporate User",
                "type": "actor",
                "description": "A bank customer's staff member",
                "quote": "As a Corporate User",
            },
            {
                "name": "Favorite account",
                "type": "concept",
                "description": "An account the user pinned",
                "quote": "mark and unmark virtual accounts as favorites",
            },
            {
                "name": "Favorite limit",
                "type": "constraint",
                "description": "At most ten",
                "quote": "Maximum 10 favorite accounts per user",
            },
            {
                "name": "Invented thing",
                "type": "concept",
                "description": "x",
                "quote": "this sentence is not in the document at all",
            },
            {"name": "Wrong type", "type": "banana", "description": "x", "quote": "As a Corporate User"},
        ],
        "relations": [
            {
                "from": "Favorite limit",
                "type": "constrains",
                "to": "Favorite account",
                "quote": "Maximum 10 favorite accounts per user",
            },
            {
                "from": "Corporate User",
                "type": "performed_by",
                "to": "Nothing",
                "quote": "As a Corporate User",
            },
            {
                "from": "Favorite limit",
                "type": "constrains",
                "to": "Corporate User",
                "quote": "made up words nobody wrote down",
            },
        ],
    }


# ---- the checking ---------------------------------------------------------------------------------------------------


def test_quotes_are_compared_ignoring_case_markdown_and_spacing():
    text = normalize("As a **Corporate   User**, I want to\nmark `favorites`.")
    assert quote_found(text, "as a corporate user, i want to mark favorites")
    assert not quote_found(text, "I want to delete favorites")
    assert not quote_found(text, "mark")  # too short to prove anything
    assert key_of("Favorite accounts") == key_of("favorite account") == "favorite account"
    assert parse_json('noise {"entities": []} trailing') == {"entities": []}
    with pytest.raises(ValueError):
        parse_json("no json here")


def test_only_facts_with_a_real_quote_and_known_names_survive():
    ex = validate(good(), STORY)
    assert [e["name"] for e in ex.entities] == ["Corporate User", "Favorite account", "Favorite limit"]
    assert [(r["from"], r["type"], r["to"]) for r in ex.relations] == [
        ("favorite limit", "constrains", "favorite account")
    ]
    assert ex.rejected == {
        "entity": 2,
        "relation": 2,
    }  # the invented quote, the unknown type, the unknown name, the made-up quote
    assert validate({"entities": "oops", "relations": [1, 2]}, STORY).entities == []


# ---- storing and merging --------------------------------------------------------------------------------------------


def responder(by_doc: dict[str, dict[str, Any]]):
    def step(messages) -> dict[str, Any]:  # noqa: ANN001
        text = messages[0].content[0].text
        for ext, answer in by_doc.items():
            if f"Document ({ext})" in text:
                return {"text": "Here you go: " + json.dumps(answer)}
        return {"text": json.dumps({"entities": [], "relations": []})}

    return step


def factory(by_doc):
    return lambda: ScriptedProvider([responder(by_doc)])


def load_stories(repo: KnowledgeRepo) -> Ingestor:
    ing = Ingestor(repo, HashEmbedder(), "test")
    ing.ingest_text(P, kind="story", filename="user_story.md", scope="BRD-03", text="# Stories\n\n" + STORY)
    other = "## US-004: Nickname\n\nAs a Corporate User, I want to name my accounts, so that I recognise them. The nickname is at most 30 characters.\n"
    ing.ingest_text(P, kind="story", filename="user_story.md", scope="BRD-03", text="# Stories\n\n" + other)
    return ing


def test_extraction_stores_concepts_rules_edges_and_merges_evidence(repo: KnowledgeRepo):
    ing = load_stories(repo)
    second = {
        "entities": [
            {
                "name": "Corporate user",
                "type": "actor",
                "description": "dup",
                "quote": "As a Corporate User, I want to name my accounts",
            },
            {
                "name": "Nickname length",
                "type": "constraint",
                "description": "30",
                "quote": "The nickname is at most 30 characters",
            },
        ],
        "relations": [],
    }
    rep = extract_run(
        repo, ing, factory({"BRD-03/US-003": good(), "BRD-03/US-004": second}), P, ("story",), workers=2
    )
    assert (
        rep.sources == 2 and rep.failed == [] and rep.rejected_entities == 2 and rep.rejected_relations == 2
    )
    kinds = {s.external_id: s.kind for s in repo.sources(P) if s.kind in ("concept", "rule")}
    assert kinds == {"concept:corporate user": "concept", "concept:favorite account": "concept", "rule:favorite limit": "rule", "rule:nickname length": "rule"}  # fmt: skip
    assert not any("invented" in k for k in kinds)  # the made-up fact never became a node
    actor = repo.find_source(P, "concept", "concept:corporate user")
    meta = repo.version(actor.id).meta  # type: ignore[union-attr]
    assert meta["concept_type"] == "actor" and {e["source"] for e in meta["evidence"]} == {
        "BRD-03/US-003",
        "BRD-03/US-004",
    }
    assert all(c.embedding for c in repo.chunks(actor.id))  # concepts are searchable like everything else
    edges = {
        (repo.get_source(e.src_id).external_id, e.type, repo.get_source(e.dst_id).external_id): e
        for e in repo.edges(P)
    }  # type: ignore[union-attr]
    mention = edges[("BRD-03/US-003", "mentions", "concept:corporate user")]
    assert (
        mention.status == "verified" and mention.origin == "source" and "Corporate User" in mention.evidence
    )  # the name is in the text
    rel = edges[("rule:favorite limit", "constrains", "concept:favorite account")]
    assert (
        rel.status == "proposed" and rel.origin == "extracted" and "BRD-03/US-003" in rel.evidence
    )  # a claim: a person decides
    again = extract_run(repo, ing, factory({"BRD-03/US-003": good()}), P, ("story",))
    assert (
        again.sources == 0 and again.skipped_unchanged == 2
    )  # nothing changed, nothing asked of the model again


def test_a_failing_model_call_is_reported_and_does_not_stop_the_rest(repo: KnowledgeRepo):
    ing = load_stories(repo)

    def broken() -> ScriptedProvider:
        return ScriptedProvider([{"error": "model down"}])

    rep = extract_run(repo, ing, broken, P, ("story",), workers=1)
    assert rep.sources == 2 and len(rep.failed) == 2 and rep.entities == 0
    ok = extract_run(
        repo, ing, factory({"BRD-03/US-003": good()}), P, ("story",)
    )  # failed sources are tried again
    assert ok.sources == 2 and ok.entities == 3


def test_extraction_changes_are_picked_up_when_a_document_changes(repo: KnowledgeRepo):
    ing = load_stories(repo)
    extract_run(repo, ing, factory({"BRD-03/US-003": good()}), P, ("story",))
    ing.ingest_text(
        P,
        kind="story",
        filename="user_story.md",
        scope="BRD-03",
        text="# Stories\n\n" + STORY.replace("10", "12"),
    )
    rep = extract_run(repo, ing, factory({}), P, ("story",))
    assert rep.sources == 1 and rep.skipped_unchanged == 1  # only the edited story is read again


# ---- through the API ------------------------------------------------------------------------------------------------


def app_client(settings: Settings, by_doc: dict[str, dict[str, Any]]) -> TestClient:
    s = kset(settings)
    return TestClient(create_app(s, provider_factory=factory(by_doc), create_schema=True))


def test_api_extract_job_and_the_two_graph_views(settings: Settings, tmp_path: Path):
    make_pack(tmp_path, "BRD-01-Alpha-Thing", "Alpha", "view the list")
    answer = {
        "entities": [
            {
                "name": "Corporate User",
                "type": "actor",
                "description": "d",
                "quote": "As a Corporate User, I want to view the list",
            },
            {
                "name": "Entitlement",
                "type": "rule",
                "description": "d",
                "quote": "GIVEN no entitlement WHEN they try THEN access is denied",
            },
        ],
        "relations": [
            {
                "from": "Entitlement",
                "type": "constrains",
                "to": "Corporate User",
                "quote": "GIVEN no entitlement WHEN they try THEN access is denied",
            }
        ],
    }
    with app_client(settings, {"BRD-01/US-001": answer}) as c:
        p = tmp_path
        c.app.state.forge.settings.knowledge_roots = str(p)  # type: ignore[attr-defined]
        r = c.post("/api/knowledge/ingest", json={"project": P, "source": "documents", "path": str(p)})
        assert r.status_code == 200
        job = c.post("/api/knowledge/extract", json={"project": P, "kinds": ["story"]}).json()
        for _ in range(100):
            job = c.get(f"/api/knowledge/extract/{job['id']}").json()
            if job["status"] != "running":
                break
            time.sleep(0.1)
        assert (
            job["status"] == "done" and job["report"]["entities"] == 2 and job["report"]["relations"] == 1
        ), job
        docs = c.get("/api/knowledge/graph", params={"project": P}).json()
        concepts = c.get("/api/knowledge/graph", params={"project": P, "view": "concepts"}).json()
        assert not {n["kind"] for n in docs["nodes"]} & {"concept", "rule"}
        assert {n["subtype"] for n in concepts["nodes"]} == {"actor", "rule"} and {
            n["kind"] for n in concepts["nodes"]
        } == {"concept", "rule"}
        assert [(e["type"], e["status"], e["origin"]) for e in concepts["edges"]] == [
            ("constrains", "proposed", "extracted")
        ]
        assert all(e["type"] != "mentions" for e in concepts["edges"])  # documents are the other view
        node = next(n for n in concepts["nodes"] if n["subtype"] == "actor")
        detail = c.get("/api/knowledge/source", params={"project": P, "ref": node["id"]}).json()
        assert detail["metadata"]["evidence"][0]["source"] == "BRD-01/US-001"
        assert c.post("/api/knowledge/extract", json={"project": P, "kinds": ["nope"]}).status_code == 422
        assert c.get("/api/knowledge/extract/missing").status_code == 404


def test_the_context_bundle_reaches_concepts_through_relations_not_the_first_search(repo: KnowledgeRepo):
    from harnix.knowledge.context import build_bundle
    from harnix.knowledge.service import KnowledgeService

    ing = load_stories(repo)
    extract_run(repo, ing, factory({"BRD-03/US-003": good()}), P, ("story",))
    svc = KnowledgeService(repo, HashEmbedder(), [P])
    seeds = svc.search(P, "favorite account limit", kinds=["story"])["hits"]
    assert seeds and all(h["kind"] == "story" for h in seeds)
    b = build_bundle(svc, P, "Implement marking favorite accounts with the limit", [])
    ids = {i["external_id"] for v in b.content["slots"].values() for i in v}
    assert (
        "BRD-03/US-003" in ids and "rule:favorite limit" in ids
    )  # the rule came along with the story that states it
    assert isinstance(Extraction().rejected, dict)
