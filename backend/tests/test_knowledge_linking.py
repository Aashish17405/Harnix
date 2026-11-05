"""The coverage audit, taxonomy documents and links between documents that the text implies.

The model is a scripted stand-in (a fixed answer per document): these tests prove the checking, storage and refusal of
proposals, not that a real model finds good links (see docs/knowledge.md for what was and was not measured)."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from harnix.api.app import create_app
from harnix.config import Settings
from harnix.knowledge import linking
from harnix.knowledge.coverage import audit, audit_chunks, audit_inputs, audit_orphans
from harnix.knowledge.embeddings import HashEmbedder
from harnix.knowledge.ingest import Ingestor
from harnix.knowledge.repo import KnowledgeRepo
from harnix.knowledge.taxonomy import is_taxonomy, parse
from harnix.models.scripted import ScriptedProvider
from harnix.persistence.store import Store

from .test_knowledge import kset
from .test_knowledge_intake import make_pack

P = "lk"


@pytest.fixture
def repo(store: Store) -> KnowledgeRepo:
    return KnowledgeRepo(store)


def ing(repo: KnowledgeRepo) -> Ingestor:
    return Ingestor(repo, HashEmbedder(), "test")


def edges(repo: KnowledgeRepo, type_: str) -> list[tuple[str, str, str, str]]:
    name = {s.id: s.external_id for s in repo.sources(P, limit=1000)}
    return sorted((name[e.src_id], e.type, name[e.dst_id], e.status) for e in repo.edges(P, limit=1000) if e.type == type_)  # fmt: skip


# ---- coverage ------------------------------------------------------------------------------------------------------

LONG = " ".join(
    f"Sentence {i} explains how settlement number {i} is reconciled against the ledger." for i in range(60)
)


def test_coverage_finds_what_never_reached_the_knowledge_base(repo: KnowledgeRepo, tmp_path: Path):
    (tmp_path / "brd.md").write_text(
        "# BRD\n\nThe module lists accounts.\n\n| Field | Rule |\n|---|---|\n| Name | Required |\n\n" + LONG
    )
    (tmp_path / "notes.json").write_text(
        json.dumps({"owner": "Operations", "tags": ["alpha", "beta"], "nested": {"limit": 10}})
    )
    res = ing(repo).ingest_documents(P, tmp_path)
    assert res.failed == []
    inputs = audit_inputs(repo, P, tmp_path)
    assert inputs["score"] == 1.0 and inputs["missing_total"] == 0, inputs["missing"]
    assert audit_chunks(repo, P)["uncovered"] == 0  # a paragraph cut across overlapping chunks is still whole

    # files added after the ingest are not in the knowledge base: each of their datapoints is reported
    (tmp_path / "late.md").write_text("# Late\n\nA sentence nobody ingested about wire cut-off times.\n")
    (tmp_path / "late.json").write_text(json.dumps({"approved": "yes", "reviewer": "Priya Raman"}))
    again = audit_inputs(repo, P, tmp_path)
    assert again["missing_total"] >= 3 and again["score"] < 1.0
    lost = {(m["file"], m["text"]) for m in again["missing"]}
    assert ("late.md", "A sentence nobody ingested about wire cut-off times.") in lost
    assert ("late.json", "Priya Raman") in lost
    # "yes" is short and appears elsewhere ("Required"... no); it must be found next to its key, so it is missing too
    assert ("late.json", "yes") in lost


def test_a_short_value_needs_its_key_and_an_unreadable_file_is_a_finding(repo: KnowledgeRepo, tmp_path: Path):
    ing(repo).ingest_text(
        P,
        kind="doc",
        filename="a.md",
        text="# A\n\nThe answer is yes for the pilot, and the owner is Priya Raman.\n",
    )
    (tmp_path / "b.json").write_text(json.dumps({"approved": "yes"}))
    (tmp_path / "broken.json").write_text("{not json")
    r = audit_inputs(repo, P, tmp_path)
    assert [m["text"] for m in r["missing"]] == ["yes"]  # the word is stored, but not as `approved: yes`
    assert "error" in r["files"]["broken.json"] and r["files"]["b.json"]["datapoints"] == 1


def test_orphans_and_the_report_shape(repo: KnowledgeRepo, tmp_path: Path):
    make_pack(tmp_path, "BRD-01-Alpha-Thing", "Alpha", "view the list")
    ing(repo).ingest_documents(P, tmp_path)
    ing(repo).ingest_text(
        P,
        kind="story",
        filename="user_story.md",
        text="# S\n\n## US-900: Alone\n\nAs a user I want something nobody links to.\n",
    )
    o = audit_orphans(repo, P)
    assert "US-900" in o["examples"] and o["by_kind"]["story"] >= 1
    rep = audit(repo, P, tmp_path)
    assert set(rep) == {"project", "chunks", "orphans", "inputs"} and audit(repo, P).get("inputs") is None


# ---- taxonomy ------------------------------------------------------------------------------------------------------

TAGS = """# Meta tags

## Notifications

- Alert preferences - how a user chooses alerts
- Delivery channel

## Accounts

| Category | Tag | Meaning |
|---|---|---|
| Lifecycle | Account closure | closing an account |
| Lifecycle | Dormant account | no activity for a year |
"""


def test_a_taxonomy_document_becomes_tags_under_categories_and_items_are_tagged(repo: KnowledgeRepo):
    i = ing(repo)
    i.ingest_text(P, kind="doc", filename="meta-tags.md", text=TAGS)
    story = "# S\n\n## US-001: Preferences\n\nAs a user I set Alert preferences for each account and handle Account closure.\n"
    i.ingest_text(P, kind="story", filename="user_story.md", text=story)
    tags = {s.title: s for s in repo.sources(P, kind="concept", limit=100) if s.origin == "taxonomy"}
    assert set(tags) == {"Notifications", "Alert preferences", "Delivery channel", "Accounts", "Lifecycle", "Account closure", "Dormant account"}  # fmt: skip
    assert all(repo.node_type(s) == "tag" for s in tags.values())
    part = {(a, b) for a, _, b, st in edges(repo, "part_of") if st == "verified"}
    assert ("concept:tag/notification/alert preference", "concept:tag/notification") in part
    assert ("concept:tag/account/lifecycle/account closure", "concept:tag/account/lifecycle") in part
    tagged = edges(repo, "tagged")
    assert tagged == [
        ("US-001", "tagged", "concept:tag/account/lifecycle/account closure", "verified"),
        ("US-001", "tagged", "concept:tag/notification/alert preference", "verified"),
    ]  # a verbatim name, a leaf tag, and not the taxonomy's own document or a tag the story never names
    before = len(repo.edges(P, limit=1000))
    i.ingest_text(P, kind="doc", filename="meta-tags.md", text=TAGS)  # idempotent
    assert len(repo.edges(P, limit=1000)) == before


def test_only_a_taxonomy_is_read_as_one():
    assert is_taxonomy({}, "meta-tags", "Anything") and is_taxonomy({"taxonomy": True}, "x", "y") and not is_taxonomy({}, "notes", "Release notes")  # fmt: skip
    assert (
        parse("# Release notes\n\n- fixed a\n- fixed b\n") == []
    )  # fewer than three tags is a list, not a taxonomy
    got = parse(TAGS, "Meta tags")
    assert [e["name"] for e in got if e["leaf"]] == ["Alert preferences", "Delivery channel", "Account closure", "Dormant account"]  # fmt: skip
    assert (
        next(e for e in got if e["name"] == "Alert preferences")["description"] == "how a user chooses alerts"
    )


# ---- links the text implies ----------------------------------------------------------------------------------------

US1 = "## US-001: Single entity summary\n\nAs a Corporate User, I want a summary of the virtual accounts of my entity, so that I can monitor collections.\n"
US2 = (
    "## US-002: All entities\n\nAs a Corporate User, I want the consolidated view across entities. It cannot be used until the single "
    "entity summary exists. It relies on the Corporate Banking System for entitlements.\n"
)
DOMAIN = (
    "Virtual accounts must be reported per parent account. The BRD leaves open whether closed accounts stay visible in the list. "
    "Nothing else is said about it here.\n"
)


def answers(**by_doc: list[dict[str, Any]]):
    def step(messages) -> dict[str, Any]:  # noqa: ANN001
        text = messages[0].content[0].text
        for ext, links in by_doc.items():
            if text.startswith(f"Document {ext} "):
                return {"text": json.dumps({"links": links})}
        return {"text": json.dumps({"links": []})}

    return lambda: ScriptedProvider([step])


def corpus(repo: KnowledgeRepo) -> None:
    i = ing(repo)
    i.ingest_text(P, kind="story", filename="user_story.md", text="# Stories\n\n" + US1 + "\n" + US2)
    i.ingest_text(P, kind="doc", filename="domain.md", text=DOMAIN)
    i.add(P, "concept", "concept:corporate banking system", "Corporate Banking System", "# Corporate Banking System\nType: system\n", origin="extracted", meta={"concept_type": "system", "name": "Corporate Banking System"})  # fmt: skip


def test_implied_links_are_proposed_only_when_every_check_passes(repo: KnowledgeRepo):
    corpus(repo)
    ids = {s.external_id for s in repo.sources(P, limit=100)}
    assert {"US-001", "US-002", "domain", "concept:corporate banking system"} <= ids
    rep = linking.run(
        repo,
        answers(
            **{
                "US-002": [
                    {
                        "type": "depends_on",
                        "to": "US-001",
                        "quote": "It cannot be used until the single entity summary exists",
                        "confidence": 0.9,
                    },
                    {
                        "type": "uses",
                        "to": "concept:corporate banking system",
                        "quote": "It relies on the Corporate Banking System for entitlements",
                        "confidence": 0.8,
                    },
                    {
                        "type": "depends_on",
                        "to": "US-001",
                        "quote": "this sentence is not in the document",
                        "confidence": 0.95,
                    },
                    {
                        "type": "depends_on",
                        "to": "US-099",
                        "quote": "It cannot be used until the single entity summary exists",
                        "confidence": 0.95,
                    },
                    {
                        "type": "flags_gap",
                        "to": "US-001",
                        "quote": "It cannot be used until the single entity summary exists",
                        "confidence": 0.4,
                    },
                    {
                        "type": "clarifies",
                        "to": "US-001",
                        "quote": "It cannot be used until the single entity summary exists",
                        "confidence": 0.9,
                    },
                    {
                        "type": "uses",
                        "to": "US-001",
                        "quote": "It cannot be used until the single entity summary exists",
                        "confidence": 0.9,
                    },
                ],
                "domain": [
                    {
                        "type": "clarifies",
                        "to": "US-002",
                        "quote": "Virtual accounts must be reported per parent account",
                        "confidence": 0.85,
                    },
                    {
                        "type": "flags_gap",
                        "to": "US-002",
                        "quote": "The BRD leaves open whether closed accounts stay visible",
                        "confidence": 0.9,
                    },
                ],
            }
        ),
        P,
    )
    assert rep.failed == [] and rep.proposed == 4, rep
    assert rep.rejected == {
        "quote not in the document": 1,
        "target not in the inventory": 1,
        "low confidence": 1,
        "type not allowed for this document": 1,  # a story does not clarify
        "target kind not allowed": 1,  # a story uses systems, not stories
    }
    assert edges(repo, "depends_on") == [("US-002", "depends_on", "US-001", "proposed")]
    assert edges(repo, "uses") == [("US-002", "uses", "concept:corporate banking system", "proposed")]
    assert edges(repo, "clarifies") == [("domain", "clarifies", "US-002", "proposed")]
    assert edges(repo, "flags_gap") == [("domain", "flags_gap", "US-002", "proposed")]
    e = next(x for x in repo.edges(P, limit=100) if x.type == "depends_on")
    assert (
        e.origin == "extracted" and "inferred from US-002" in e.evidence and "confidence 0.90" in e.evidence
    )

    again = linking.run(
        repo,
        answers(**{"US-002": [{"type": "depends_on", "to": "US-001", "quote": "x", "confidence": 1}]}),
        P,
    )
    assert (
        again.sources == 0 and again.skipped_unchanged >= 2 and again.proposed == 0
    )  # nothing changed: nothing is asked


def test_a_pair_that_is_already_linked_is_left_alone_even_when_rejected(repo: KnowledgeRepo):
    corpus(repo)
    by = {s.external_id: s for s in repo.sources(P, limit=100)}
    repo.upsert_edge(P, by["domain"], by["US-002"], "relates_to", "rejected", "human")  # a person said no
    rep = linking.run(
        repo,
        answers(
            domain=[
                {
                    "type": "clarifies",
                    "to": "US-002",
                    "quote": "Virtual accounts must be reported per parent account",
                    "confidence": 0.9,
                }
            ]
        ),
        P,
    )
    assert rep.proposed == 0 and rep.already_linked == 1 and edges(repo, "clarifies") == []


def test_a_failing_model_call_is_reported_and_the_rest_continue(repo: KnowledgeRepo):
    corpus(repo)

    def factory() -> ScriptedProvider:
        def step(messages) -> dict[str, Any]:  # noqa: ANN001
            text = messages[0].content[0].text
            if text.startswith("Document US-002 "):
                raise RuntimeError("model unavailable")
            return {"text": json.dumps({"links": [{"type": "flags_gap", "to": "US-002", "quote": "The BRD leaves open whether closed accounts stay visible", "confidence": 0.9}]})}  # fmt: skip

        return ScriptedProvider([step])

    rep = linking.run(repo, factory, P)
    assert len(rep.failed) == 1 and "US-002" in rep.failed[0]
    assert rep.proposed == 1 and edges(repo, "flags_gap") == [("domain", "flags_gap", "US-002", "proposed")]
    # the failed document is read again next time, the others are not
    assert linking.run(repo, answers(), P).sources == 1


def test_the_inventory_prefers_related_items_and_always_shows_systems(repo: KnowledgeRepo):
    corpus(repo)
    src = repo.resolve(P, "US-002")
    assert src is not None
    pool = [(s, linking.item_kind(repo, s), linking._tokens(f"{s.title} {(repo.version(s.id).content if repo.version(s.id) else '')}")) for s in repo.sources(P, limit=100)]  # fmt: skip
    shown = linking.inventory(src, "the single entity summary", pool)
    assert [k for _, k in shown][0] == "system" and "US-002" not in [s.external_id for s, _ in shown]


# ---- through the API -----------------------------------------------------------------------------------------------


def test_api_coverage_and_link_job(settings: Settings, tmp_path: Path):
    make_pack(tmp_path, "BRD-01-Alpha-Thing", "Alpha", "view the list")
    s = kset(settings)
    s.knowledge_roots = str(tmp_path)
    with TestClient(create_app(s, provider_factory=answers(), create_schema=True)) as c:
        assert (
            c.post(
                "/api/knowledge/ingest", json={"project": P, "source": "documents", "path": str(tmp_path)}
            ).status_code
            == 200
        )
        r = c.get("/api/knowledge/coverage", params={"project": P, "path": str(tmp_path)})
        assert (
            r.status_code == 200
            and r.json()["inputs"]["score"] == 1.0
            and r.json()["chunks"]["uncovered"] == 0
        ), r.text
        assert "inputs" not in c.get("/api/knowledge/coverage", params={"project": P}).json()
        assert (
            c.get("/api/knowledge/coverage", params={"project": P, "path": str(tmp_path.parent)}).status_code
            == 422
        )  # outside the roots
        job = c.post("/api/knowledge/link", json={"project": P}).json()
        for _ in range(100):
            job = c.get(f"/api/knowledge/link/{job['id']}").json()
            if job["status"] != "running":
                break
            time.sleep(0.1)
        assert job["status"] == "done" and job["report"]["sources"] > 0 and job["report"]["proposed"] == 0, (
            job
        )
        stages = {e["stage"] for e in job["events"]}
        assert (
            "infer" in stages and "link" not in stages
        )  # its own stage (ingestion's "link" is another card)
        pipe = c.get("/api/knowledge/pipeline", params={"project": P}).json()
        assert (
            pipe["linking"]["last"]["detail"]["sources"] == job["report"]["sources"]
            and pipe["linking"]["by_status"] == {}
        )
        assert c.post("/api/knowledge/link", json={"project": P, "limit": 0}).status_code == 422
        assert c.get("/api/knowledge/link/missing").status_code == 404
