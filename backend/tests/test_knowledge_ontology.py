"""The ontology: allowed links between types (is-a aware), synonyms (file + BRD glossaries), enforced on every write."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from harnix.api.app import create_app
from harnix.config import Settings
from harnix.knowledge.embeddings import HashEmbedder
from harnix.knowledge.extract import key_of, validate
from harnix.knowledge.extract import run as extract_run
from harnix.knowledge.ingest import Ingestor
from harnix.knowledge.ontology import OntologyViolation, default, for_project, glossary_synonyms
from harnix.knowledge.repo import KnowledgeRepo
from harnix.persistence.store import Store

from .test_knowledge import kset
from .test_knowledge_extract import factory

GLOSSARY = """# BRD

## 8. Glossary

| Term | Definition |
|---|---|
| Virtual Account (VA) | Virtual sub-account linked to a physical account |
| CBS | Core Banking System |
| MTD | Month-to-date |
| CIF | Customer identification file/identifier for a corporate customer |
"""


def test_types_inherit_and_links_are_checked_both_ends():
    o = default()
    assert o.ancestors("actor") == ["actor", "concept"] and o.ancestors("story") == ["story", "document"]
    assert o.allowed("derives_from", "story", "feature") and o.allowed(
        "mentions", "story", "actor"
    )  # actor is a concept
    assert o.allowed("constrains", "constraint", "field")  # constraint is a rule, field a concept
    assert not o.allowed("derives_from", "brd", "story")  # wrong way round
    assert not o.allowed("tests", "code", "story") and not o.allowed("implements", "code", "brd")
    assert not o.allowed("performed_by", "event", "system")  # performed_by needs an actor
    assert not o.allowed("invented", "story", "story")
    with pytest.raises(OntologyViolation, match="derives_from"):
        o.check("derives_from", "brd", "story")


def test_glossaries_give_synonyms_and_names_resolve_through_them():
    syn = glossary_synonyms([GLOSSARY])
    assert (
        syn["va"] == "virtual account"
        and syn["cbs"] == "core banking system"
        and syn["mtd"] == "month-to-date"
    )
    assert "cif" not in syn  # a long definition is a description, not a synonym
    o = default().with_synonyms(syn)
    assert o.canonical(key_of("VA")) == o.canonical(key_of("Virtual Accounts")) == o.canonical(key_of("Virtual Account (VA)")) == "virtual account"  # fmt: skip


def test_validation_merges_aliases_and_drops_links_the_ontology_forbids():
    text = "The VA is shown. Each virtual account has a balance. The Corporate User opens the list. CBS sends the balance."
    o = default().with_synonyms(glossary_synonyms([GLOSSARY]))
    raw = {
        "entities": [
            {"name": "VA", "type": "concept", "quote": "The VA is shown"},
            {"name": "Virtual account", "type": "concept", "quote": "Each virtual account has a balance"},
            {"name": "Corporate User", "type": "actor", "quote": "The Corporate User opens the list"},
            {"name": "CBS", "type": "system", "quote": "CBS sends the balance"},
        ],
        "relations": [
            {
                "from": "Virtual account",
                "type": "performed_by",
                "to": "Corporate User",
                "quote": "The Corporate User opens the list",
            },  # fmt: skip
            {
                "from": "Corporate User",
                "type": "performed_by",
                "to": "CBS",
                "quote": "CBS sends the balance",
            },  # CBS is no actor
            {
                "from": "VA",
                "type": "constrains",
                "to": "Corporate User",
                "quote": "The VA is shown",
            },  # a concept is no rule
        ],
    }
    ex = validate(raw, text, o)
    assert [e["key"] for e in ex.entities] == [
        "virtual account",
        "corporate user",
        "core banking system",
    ]  # VA = virtual account
    assert [(r["from"], r["type"], r["to"]) for r in ex.relations] == [
        ("virtual account", "performed_by", "corporate user")
    ]
    assert ex.rejected["relation"] == 2


def test_every_write_is_checked_and_documents_report_what_they_declared_wrongly(store: Store, tmp_path: Path):
    repo = KnowledgeRepo(store)
    ing = Ingestor(repo, HashEmbedder())
    ing.ingest_text("p", kind="brd", filename="BRD.md", text="# BRD\n\nThe product.\n", external_id="BRD-1")
    ing.ingest_text(
        "p", kind="story", filename="s.md", text="# US-1: Story\n\nAs a user.\n", external_id="US-1"
    )
    brd, story = repo.resolve("p", "BRD-1"), repo.resolve("p", "US-1")
    with pytest.raises(OntologyViolation):
        repo.upsert_edge("p", brd, story, "derives_from", "verified", "source")  # type: ignore[arg-type]
    assert repo.upsert_edge("p", story, brd, "derives_from", "verified", "source")[1]  # type: ignore[arg-type]
    # a document declaring a link the ontology forbids: not stored, reported with the reason
    (tmp_path / "BRD.md").write_text(
        "---\nid: BRD-2\nkind: brd\nderives_from: [US-1]\n---\n# BRD 2\n\ntext\n"
    )
    res = ing.ingest_documents("p", tmp_path)
    assert any("not allowed by the ontology" in u for u in res.unresolved), res.unresolved
    assert not [e for e in repo.edges("p") if repo.get_source(e.src_id).external_id == "BRD-2"]  # type: ignore[union-attr]


def test_reset_rebuilds_extracted_concepts_under_the_ontology(store: Store):
    repo = KnowledgeRepo(store)
    ing = Ingestor(repo, HashEmbedder())
    ing.ingest_text("p", kind="brd", filename="BRD.md", text=GLOSSARY, external_id="BRD-03")
    story = "## US-1: Favourites\n\nAs a Corporate User, I want to pin a VA. Each virtual account can be pinned.\n"
    ing.ingest_text("p", kind="story", filename="user_story.md", scope="BRD-03", text="# S\n\n" + story)
    answer = {"entities": [
        {"name": "VA", "type": "concept", "quote": "I want to pin a VA"},
        {"name": "Virtual accounts", "type": "concept", "quote": "Each virtual account can be pinned"},
    ], "relations": []}  # fmt: skip
    rep = extract_run(repo, ing, factory({"BRD-03/US-1": answer}), "p", ("story",))
    concepts = [s.external_id for s in repo.sources("p") if s.kind == "concept"]
    assert (
        concepts == ["concept:virtual account"] and rep.entities == 1
    )  # the alias did not become a second node
    again = extract_run(repo, ing, factory({"BRD-03/US-1": answer}), "p", ("story",), reset=True)
    assert (
        again.removed == 1 and again.sources == 1 and again.skipped_unchanged == 0
    )  # read again despite no change
    assert for_project(repo, "p").canonical("cbs") == "core banking system"


def test_api_shows_the_ontology_and_the_links_that_break_it(settings: Settings, store: Store):
    s = kset(settings)
    with TestClient(create_app(s, create_schema=True)) as c:
        repo = KnowledgeRepo(c.app.state.forge.store)  # type: ignore[attr-defined]
        ing = Ingestor(repo, HashEmbedder())
        ing.ingest_text("p", kind="brd", filename="BRD.md", text=GLOSSARY, external_id="BRD-1")
        ing.ingest_text(
            "p", kind="story", filename="s.md", text="# US-1: Story\n\nAs a user.\n", external_id="US-1"
        )
        brd, story = repo.resolve("p", "BRD-1"), repo.resolve("p", "US-1")
        repo.upsert_edge("p", story, brd, "derives_from", "verified", "source")  # type: ignore[arg-type]
        # an old link from before the ontology, written straight to the table
        from harnix.persistence.models import KnowledgeEdge

        with repo.sessions.begin() as sess:
            sess.add(KnowledgeEdge(id="old1", project="p", src_id=brd.id, dst_id=story.id, type="tests", status="proposed", origin="agent", evidence="", src_version=1, dst_version=1))  # type: ignore[union-attr]  # fmt: skip
        out = c.get("/api/knowledge/ontology", params={"project": "p"}).json()
        assert out["types"]["actor"] == "concept" and out["synonyms"]["va"] == "virtual account"
        assert out["links_checked"] == 2 and out["violations_total"] == 1
        assert out["violations"][0]["edge_id"] == "old1" and out["violations"][0]["from_type"] == "brd"


def test_api_lists_the_links_refused_at_intake(settings: Settings, store: Store, tmp_path: Path):
    s = kset(settings)
    with TestClient(create_app(s, create_schema=True)) as c:
        repo = KnowledgeRepo(c.app.state.forge.store)  # type: ignore[attr-defined]
        ing = Ingestor(repo, HashEmbedder())
        ing.ingest_text(
            "p", kind="story", filename="s.md", text="# US-1: Story\n\nAs a user.\n", external_id="US-1"
        )
        (tmp_path / "BRD.md").write_text(
            "---\nid: BRD-2\nkind: brd\nderives_from: [US-1]\n---\n# BRD 2\n\ntext\n"
        )
        ing.ingest_documents("p", tmp_path)
        out = c.get("/api/knowledge/ontology", params={"project": "p"}).json()
        assert out["refused_total"] == 1
        r = out["refused"][0]
        assert (r["from"], r["type"], r["to"], r["from_type"], r["to_type"]) == ("BRD-2", "derives_from", "US-1", "brd", "story")  # fmt: skip
        assert "derives_from" in r["rule"] and r["at"].endswith("Z")
        assert (
            c.get("/api/knowledge/pipeline", params={"project": "p"}).json()["ontology"]["refused_total"] == 1
        )


def test_domain_is_its_own_concept_subtype_and_extraction_keeps_it():
    o = default()
    assert o.ancestors("domain") == ["domain", "concept"]
    assert o.allowed("mentions", "story", "domain") and o.allowed("relates_to", "domain", "actor")
    assert not o.allowed("performed_by", "domain", "system")  # still needs an actor
    text = "Settlement follows the KYC check before the cut-off."
    ex = validate(
        {"entities": [{"name": "KYC", "type": "domain", "quote": "the KYC check"}], "relations": []}, text
    )
    assert [(e["key"], e["type"]) for e in ex.entities] == [("kyc", "domain")] and ex.rejected["entity"] == 0


DOMAIN_NOTE = """Virtual accounts in banking

Part I - The product
A virtual account is only an identifier. Settlement happens in the physical account, which is a KYC-checked deposit.
10. Glossary of terms
Physical account. The real bank account in which the deposit is booked and settled.
virtual account (VA). A logical identifier associated with an underlying bank account.
Root VA / Parent VA. Levels of a virtual reporting hierarchy used for attribution.
Part II - Applying the Domain to the BRD
The BRD says a closed virtual account stays visible for 12 months.
"""


def test_a_domain_note_glossary_becomes_domain_concepts_and_part_ii_does_not(store: Store):
    repo = KnowledgeRepo(store)
    ing = Ingestor(repo, HashEmbedder())
    ing.ingest_text("p", kind="doc", filename="domain.md", text=DOMAIN_NOTE, external_id="domain")
    ing.ingest_text(
        "p", kind="doc", filename="domain.md", text=DOMAIN_NOTE, external_id="domain"
    )  # again: no copies
    nodes = {s.title: s for s in repo.sources("p") if s.kind == "concept"}
    assert set(nodes) == {"Physical account", "virtual account", "Root VA", "Parent VA"}
    assert all(repo.version(s.id).meta["concept_type"] == "domain" for s in nodes.values())  # type: ignore[union-attr]
    doc = repo.resolve("p", "domain")
    edges = {(e.src_id, e.dst_id, e.type, e.status) for e in repo.edges("p")}
    assert all((doc.id, s.id, "mentions", "verified") in edges for s in nodes.values())  # type: ignore[union-attr]
    assert for_project(repo, "p").canonical("va") == "virtual account"  # the acronym in the glossary entry


def test_domain_is_demoted_to_concept_inside_a_passage_about_the_brd():
    general = "Settlement follows the KYC check."
    project = "The BRD requires the KYC check for closed accounts."
    ask = {"entities": [{"name": "KYC", "type": "domain", "quote": "the KYC check"}], "relations": []}
    assert validate(ask, general).entities[0]["type"] == "domain"
    assert validate(ask, project).entities[0]["type"] == "concept"
