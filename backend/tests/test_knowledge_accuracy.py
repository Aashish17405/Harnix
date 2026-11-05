"""Accuracy, context management and traceability of the knowledge base: contextual chunk embeddings, the target given
whole and other long sources cut to their most relevant sections, summaries never ranked above what they summarise,
the relevance floor, acceptance criteria read from the exact version, invented citations, criterion -> test pointers."""

from __future__ import annotations

from pathlib import Path

from harnix.knowledge import tools
from harnix.knowledge.context import _children_first, build_bundle
from harnix.knowledge.embeddings import HashEmbedder, doc_model
from harnix.knowledge.evaluation import cited_ids, criteria_of, find_tests, match_tests
from harnix.knowledge.ingest import Ingestor, chunk_text, embed_text
from harnix.knowledge.repo import KnowledgeRepo
from harnix.knowledge.service import KnowledgeService
from harnix.persistence.store import Store

from .test_knowledge_intake import make_pack

P = "acc"
LONG_STORY = (
    "# Stories\n\n## US-007: Export the statement\n\n- Epic: E-001 | Feature: FEAT-001\n\n"
    "As a Corporate User, I want to export statements, so that I can file them.\n\n"
    + "\n\n".join(f"Background paragraph {i}: " + "context words " * 30 for i in range(8))
    + "\n\n**Acceptance Criteria**\n\n1. GIVEN a statement WHEN exported THEN a PDF is produced\n2. GIVEN no entitlement WHEN exporting THEN access is denied\n\n"
    "**Additional Scenarios**\n\n1. GIVEN the service is down WHEN exporting THEN an error is shown\n"
)


def packs(store: Store, tmp_path: Path) -> KnowledgeRepo:
    repo = KnowledgeRepo(store)
    make_pack(tmp_path, "BRD-01-Alpha-Thing", "Alpha", "view the list")
    make_pack(tmp_path, "BRD-02-Beta-Thing", "Beta", "pin favourites")
    Ingestor(repo, HashEmbedder()).ingest_documents(P, tmp_path)
    return repo


def test_chunks_carry_their_document_and_heading_only_fragments_are_merged():
    text = (
        "# BRD\n\n## 2. Executive Summary\n\n### 2.1 Objective\n\n"
        + "The objective is to show accounts. " * 10
    )
    chunks = chunk_text(text)
    assert (
        all(len(t) >= 100 for _, t in chunks[:-1]) and "2. Executive Summary" in chunks[0][1]
    )  # no lone heading

    class Src:  # noqa: D106
        kind, external_id, title = "brd", "BRD-03", "Account details"

    e = embed_text(Src(), "2.1 Objective", "The objective is to show accounts.")
    assert e.startswith("[BRD BRD-03] Account details") and "Section: 2.1 Objective" in e


def test_vectors_record_their_format_and_a_rebuild_moves_old_ones(store: Store, tmp_path: Path):
    repo = packs(store, tmp_path)
    emb = HashEmbedder()
    assert {c.embedding_model for c, _ in repo.project_chunks(P)} == {doc_model(emb)}
    with repo.sessions.begin() as s:  # pretend an older version embedded them another way
        from sqlalchemy import update

        from harnix.persistence.models import KnowledgeChunk

        s.execute(update(KnowledgeChunk).where(KnowledgeChunk.project == P).values(embedding_model=emb.name))
    svc = KnowledgeService(repo, emb, [P])
    assert (
        "keyword results only" in svc.search(P, "pin favourites")["note"]
    )  # old-format vectors are not compared
    out = Ingestor(repo, emb).rebuild(P)
    assert out["partial"] == 0 and {c.embedding_model for c, _ in repo.project_chunks(P)} == {doc_model(emb)}
    assert svc.search(P, "pin favourites")["note"] == ""


def test_the_target_is_given_whole_and_other_long_sources_only_their_relevant_sections(
    store: Store, tmp_path: Path
):
    repo = packs(store, tmp_path)
    ing = Ingestor(repo, HashEmbedder())
    ing.ingest_text(P, kind="story", filename="user_story.md", scope="BRD-01", text=LONG_STORY)
    ing.ingest_text(P, kind="brd", filename="big.md", external_id="BIG-1", text="# Big\n\n" + "\n\n".join(f"## Section {i}\n\n" + f"topic {i} " * 120 for i in range(12)))  # fmt: skip
    svc = KnowledgeService(repo, HashEmbedder(), [P])
    b = build_bundle(svc, P, "Implement US-007 of BRD-01 and check section 3 of BIG-1", [])
    items = {i["external_id"]: i for v in b.content["slots"].values() for i in v}
    story = items["BRD-01/US-007"]
    assert (
        story["target"] and story["excerpt_of"] == "whole source" and "access is denied" in story["content"]
    )
    big = items.get("BIG-1")
    if big is not None:  # a long non-target: some sections, each recorded with its chunk id
        assert not big["target"] and big["sections"] and "sections, the most relevant" in big["excerpt_of"]
        assert all(s["chunk_id"] for s in big["sections"])
    assert "[TARGET" in __import__("forge.knowledge.context", fromlist=["render"]).render(b.content)


def test_a_summary_is_never_ranked_above_the_item_it_summarises(store: Store, tmp_path: Path):
    repo = packs(store, tmp_path)
    svc = KnowledgeService(repo, HashEmbedder(), [P])
    story, feat, epic = (repo.resolve(P, x) for x in ("BRD-01/US-001", "BRD-01/FEAT-001", "BRD-01/E-001"))
    meta = {s.id: {"kind": s.kind} for s in (story, feat, epic)}  # type: ignore[union-attr]
    ranked = {feat.id: -0.9, epic.id: -0.8, story.id: -0.5}  # type: ignore[union-attr]  # summaries scored higher
    _children_first(svc, ranked, meta)
    assert sorted(ranked, key=lambda k: ranked[k]) == [story.id, feat.id, epic.id]  # type: ignore[union-attr]
    # and siblings of the best match get no "linked to the best match" bonus (they are alternatives, not context)
    b = build_bundle(svc, P, "Implement US-001 of BRD-02", [])
    stories = [c for c in b.content["pipeline"]["candidates"] if c["kind"] == "story"]
    assert stories and all(c["bonus"]["best_seed_link"] == 0 for c in stories)


def test_a_request_unlike_anything_in_the_knowledge_is_insufficient(store: Store, tmp_path: Path):
    repo = packs(store, tmp_path)
    picky = KnowledgeService(repo, HashEmbedder(), [P], min_relevance=0.99)
    b = build_bundle(picky, P, "Bake a chocolate cake", [])
    assert b.status == "insufficient" and "nothing in the knowledge base is close" in b.content["sufficiency"]["missing"][0]  # fmt: skip
    assert b.content["pipeline"]["relevance"]["floor"] == 0.99
    assert (
        build_bundle(picky, P, "Implement US-001 of BRD-02", []).status == "sufficient"
    )  # a named id is relevant
    assert build_bundle(KnowledgeService(repo, HashEmbedder(), [P]), P, "Bake a chocolate cake", []).status == "sufficient"  # no floor  # fmt: skip


def test_acceptance_criteria_stop_at_the_next_section():
    assert criteria_of(LONG_STORY) == ["GIVEN a statement WHEN exported THEN a PDF is produced", "GIVEN no entitlement WHEN exporting THEN access is denied"]  # fmt: skip


def test_invented_citations_are_told_apart_from_real_ones(store: Store, tmp_path: Path):
    repo = packs(store, tmp_path)
    out = cited_ids(
        repo,
        P,
        "Done: BRD-02/US-001 and US-002 implemented, see US-099; encoding UTF-8, SHA-256.",
        {"BRD-02/US-001"},
    )
    assert (
        out["given"] == ["BRD-02/US-001"] and out["known"] == ["US-002"] and out["unknown"] == ["US-099"]
    )  # UTF-8 is no id


def test_each_criterion_points_at_the_test_most_like_it():
    src = 'def test_rejects_an_eleventh_favorite():\n    """no more than 10"""\n    assert add(10) is None\n\n\ndef test_pdf_export():\n    assert export() == "pdf"\n'  # fmt: skip
    tests = find_tests("tests/test_fav.py", src)
    assert [t["name"] for t in tests] == ["test_rejects_an_eleventh_favorite", "test_pdf_export"]
    crit = [{"text": "pdf export"}]
    match_tests(HashEmbedder(), crit, tests)
    assert crit[0]["test"]["file"] == "tests/test_fav.py" and "similarity" in crit[0]["test"]


def test_knowledge_get_reads_long_sources_in_pieces(store: Store, tmp_path: Path):
    repo = packs(store, tmp_path)
    Ingestor(repo, HashEmbedder()).ingest_text(
        P, kind="story", filename="user_story.md", scope="BRD-01", text=LONG_STORY
    )
    svc = KnowledgeService(repo, HashEmbedder(), [P])
    first = tools.call(svc, "knowledge_get", {"project": P, "ref": "BRD-01/US-007", "max_chars": 500})
    assert first["truncated"] and first["next_offset"] == 500 and len(first["content"]) == 500
    rest = tools.call(
        svc, "knowledge_get", {"project": P, "ref": "BRD-01/US-007", "offset": 500, "max_chars": 40000}
    )
    assert rest["next_offset"] is None and first["content"] + rest["content"] == repo.version(repo.resolve(P, "BRD-01/US-007").id).content  # type: ignore[union-attr]  # fmt: skip
