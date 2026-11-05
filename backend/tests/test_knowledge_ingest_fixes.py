"""Ingestion fixes: a metadata-only change makes a version, an unknown kind goes to review, big documents are not cut."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from harnix.api.app import create_app
from harnix.config import Settings
from harnix.knowledge import ingest as ingest_mod
from harnix.knowledge.embeddings import HashEmbedder
from harnix.knowledge.ingest import CHUNK_CHARS, Ingestor, chunk_text
from harnix.knowledge.repo import KnowledgeRepo
from harnix.persistence.store import Store

from .test_knowledge import kset

RUNBOOK = "---\nid: X-1\nkind: runbook\n---\n# Runbook\n\nSteps to restart.\n"
BODY = "# Story\n\nAs a user I want to pin an account so that I find it fast. " * 3


def test_front_matter_only_change_makes_a_version_and_keeps_chunks_and_embeddings(store: Store):
    repo = KnowledgeRepo(store)
    ing = Ingestor(repo, HashEmbedder())
    ing.ingest_text("p", kind="brd", filename="BRD.md", text="# BRD\n\nThe product.\n", external_id="BRD-1")
    first = f"---\nid: US-1\nkind: story\n---\n{BODY}"
    ing.ingest_text("p", kind="story", filename="s.md", text=first, external_id="US-1")
    story = repo.resolve("p", "US-1")
    before = repo.chunks(story.id)  # type: ignore[union-attr]
    assert before and all(c.embedding for c in before) and repo.version(story.id).version == 1  # type: ignore[union-attr]
    again = ing.ingest_text("p", kind="story", filename="s.md", text=first, external_id="US-1")
    assert again.unchanged == 1 and repo.version(story.id).version == 1  # type: ignore[union-attr]  # nothing changed: nothing written
    # the same text, now declaring a link in its front matter
    second = f"---\nid: US-1\nkind: story\nderives_from: [BRD-1]\n---\n{BODY}"
    res = ing.ingest_text("p", kind="story", filename="s.md", text=second, external_id="US-1")
    assert res.updated == 1 and res.edges_created == 1
    story = repo.resolve("p", "US-1")
    assert repo.version(story.id).version == 2  # type: ignore[union-attr]
    after = repo.chunks(story.id)  # type: ignore[union-attr]
    assert [c.id for c in after] == [c.id for c in before]  # same chunks: not re-cut, not re-embedded
    assert all(c.embedding for c in after) and {c.version_id for c in after} == {repo.version(story.id).id}  # type: ignore[union-attr]
    assert [(e.type, e.status) for e in repo.edges("p")] == [("derives_from", "verified")]


def test_unknown_kind_is_kept_for_review_not_failed(store: Store, tmp_path: Path):
    repo = KnowledgeRepo(store)
    ing = Ingestor(repo, HashEmbedder())
    (tmp_path / "x.md").write_text(RUNBOOK)
    res = ing.ingest_documents("p", tmp_path)
    assert not res.failed and len(res.review) == 1 and "runbook" in res.review[0]
    src = repo.resolve("p", "X-1")
    assert src is not None and src.kind == "doc"
    assert [s.external_id for s, _ in repo.needs_review("p")] == ["X-1"]
    assert repo.chunks(src.id)  # searchable meanwhile


def test_review_api_lists_and_resolves(settings: Settings, store: Store, tmp_path: Path):
    with TestClient(create_app(kset(settings), create_schema=True)) as c:
        repo = KnowledgeRepo(c.app.state.forge.store)  # type: ignore[attr-defined]
        ing = Ingestor(repo, HashEmbedder())
        (tmp_path / "x.md").write_text(RUNBOOK)
        ing.ingest_documents("p", tmp_path)
        items = c.get("/api/knowledge/review", params={"project": "p"}).json()["items"]
        assert [i["external_id"] for i in items] == ["X-1"] and items[0]["requested_kind"] == "runbook"
        assert (
            c.get("/api/knowledge/pipeline", params={"project": "p"}).json()["ingest"]["review"][0][
                "external_id"
            ]
            == "X-1"
        )
        sid = items[0]["source_id"]
        assert c.post(f"/api/knowledge/review/{sid}", json={"kind": "bogus"}).status_code == 422
        ok = c.post(f"/api/knowledge/review/{sid}", json={"kind": "design"})
        assert ok.status_code == 200 and ok.json()["kind"] == "design"
        assert c.get("/api/knowledge/review", params={"project": "p"}).json()["items"] == []
        assert (
            c.post(f"/api/knowledge/review/{sid}", json={"kind": "doc"}).status_code == 404
        )  # decided already


def test_a_document_over_the_old_limits_is_indexed_whole_and_a_cut_is_reported(store: Store, monkeypatch):
    repo = KnowledgeRepo(store)
    ing = Ingestor(repo, HashEmbedder())
    text = "# Big\n\n" + "\n\n".join(
        f"## Part {i}\n\n" + f"Fact number {i} about refunds. " * 30 for i in range(300)
    )
    assert len(text.encode()) > 400_000 // 2 and len(chunk_text(text)) > 200  # past the old 200-chunk cut
    res = ing.ingest_text("p", kind="doc", filename="big.md", text=text, external_id="big")
    assert not res.skipped and not res.failed
    assert len(repo.chunks(repo.resolve("p", "big").id)) == len(chunk_text(text))  # type: ignore[union-attr]
    monkeypatch.setattr(ingest_mod, "MAX_CHUNKS", 50)
    res = ing.ingest_text("p", kind="doc", filename="big2.md", text=text + "x", external_id="big2")
    assert len(repo.chunks(repo.resolve("p", "big2").id)) == 50  # type: ignore[union-attr]
    assert any("only the first 50" in s for s in res.skipped)  # reported, never silent


def test_size_cut_chunks_of_a_section_overlap_but_headings_and_tables_do_not():
    prose = " ".join(f"Sentence {i} says one thing about refunds and cards." for i in range(60))
    pieces = [t for _, t in chunk_text(f"# Notes\n\n{prose}\n")]
    assert len(pieces) > 2 and all(len(p) <= CHUNK_CHARS for p in pieces)
    for a, b in zip(pieces, pieces[1:], strict=False):
        assert a.rstrip().split(". ")[-1][-40:] in b  # the end of one chunk opens the next
    two = [t for _, t in chunk_text("# A\n\n" + prose + "\n\n# B\n\n" + prose + "\n")]
    assert not any(p.startswith("Sentence") and "# B" in p for p in two)  # a new heading starts clean
