"""The Markdown copy of the graph, concepts shared across backlogs, and live progress with an estimate."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from harnix.api.app import create_app
from harnix.config import Settings
from harnix.knowledge import linking, markdown_export
from harnix.knowledge.embeddings import HashEmbedder
from harnix.knowledge.ingest import Ingestor, merge_evidence
from harnix.knowledge.repo import KnowledgeRepo
from harnix.persistence.store import Store

from .test_knowledge import kset
from .test_knowledge_intake import make_pack
from .test_knowledge_linking import answers
from .test_knowledge_linking import corpus as linking_corpus

P = "lp"


@pytest.fixture
def repo(store: Store) -> KnowledgeRepo:
    return KnowledgeRepo(store)


def ing(repo: KnowledgeRepo) -> Ingestor:
    return Ingestor(repo, HashEmbedder(), "test")


# ---- the Markdown copy ----------------------------------------------------------------------------------------------


def test_the_markdown_copy_is_deterministic_linked_and_only_rewrites_what_changed(
    repo: KnowledgeRepo, tmp_path: Path
):
    src = tmp_path / "in"
    make_pack(src, "BRD-01-Alpha-Thing", "Alpha", "view the list")
    ing(repo).ingest_documents(P, src)
    out = tmp_path / "out"
    first = markdown_export.export(repo, P, out)
    n = len(repo.sources(P, limit=1000))
    assert first["files"] == n and first["written"] == n and first["unchanged"] == 0
    story = (out / "story" / "BRD-01__US-001.md").read_text(encoding="utf-8")
    assert story.startswith('---\nid: "BRD-01/US-001"\nkind: "story"\n') and "version: 1" in story
    assert (
        'derives_from:\n    - id: "BRD-01/E-001"\n      status: verified\n    - id: "BRD-01/FEAT-001"\n      status: verified'
        in story
    )  # links in the front matter, sorted
    assert "- derives_from [[BRD-01/FEAT-001]] (verified)" in story  # and as wikilinks
    feature = (out / "feature" / "BRD-01__FEAT-001.md").read_text(encoding="utf-8")
    assert "## Linked from" in feature and "[[BRD-01/US-001]] derives_from this (verified)" in feature
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert (
        manifest["files"]["story/BRD-01/US-001"] == "story/BRD-01__US-001.md" and len(manifest["files"]) == n
    )

    snapshot = {p.relative_to(out).as_posix(): p.read_bytes() for p in out.rglob("*") if p.is_file()}
    again = markdown_export.export(repo, P, out)
    assert again["written"] == 0 and again["unchanged"] == n and again["removed"] == 0
    assert {
        p.relative_to(out).as_posix(): p.read_bytes() for p in out.rglob("*") if p.is_file()
    } == snapshot  # byte for byte

    ing(repo).ingest_text(P, kind="story", filename="user_story.md", scope="BRD-01", text="# S\n\n## US-002: Changed\n\nAs a user I want something else.\n")  # fmt: skip
    changed = markdown_export.export(repo, P, out)
    assert changed["written"] >= 1 and changed["written"] < n  # a small diff, not a rewrite


def test_removed_items_lose_their_files_and_foreign_folders_are_refused(repo: KnowledgeRepo, tmp_path: Path):
    i = ing(repo)
    i.ingest_text(
        P, kind="story", filename="user_story.md", text="# S\n\n## US-001: A\n\nAs a user I want a.\n"
    )
    i.add(P, "concept", "concept:thing", "Thing", "# Thing\nType: concept\n", origin="extracted", meta={"concept_type": "concept", "name": "Thing"})  # fmt: skip
    out = tmp_path / "out"
    markdown_export.export(repo, P, out)
    assert (out / "concept" / "concept_thing.md").exists()
    repo.delete_sources(P, ("concept",), origin="extracted")
    res = markdown_export.export(repo, P, out)
    assert res["removed"] == 1 and not (out / "concept" / "concept_thing.md").exists()
    (out / "notes.txt").write_text("mine")  # a file the export never wrote is never touched
    markdown_export.export(repo, P, out)
    assert (out / "notes.txt").read_text() == "mine"
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    (foreign / "keep.txt").write_text("x")
    with pytest.raises(ValueError, match="no manifest"):
        markdown_export.export(repo, P, foreign)
    assert (foreign / "keep.txt").read_text() == "x"


def test_hostile_and_colliding_ids_stay_inside_the_folder_and_apart(repo: KnowledgeRepo, tmp_path: Path):
    i = ing(repo)
    for ext in ("../../evil", "a/b", "a__b", "A__B", "x:y|z"):
        i.add(P, "doc", ext, f"Doc {ext}", f"# {ext}\n\nSome text for {ext}.\n")
    out = tmp_path / "deep" / "out"
    res = markdown_export.export(repo, P, out)
    files = sorted(p.relative_to(out).as_posix() for p in out.rglob("*.md"))
    assert (
        res["files"] == 5 and len(set(f.lower() for f in files)) == 5
    )  # no two ids share a file, even by case
    assert all(p.resolve().is_relative_to(out.resolve()) for p in out.rglob("*"))
    assert not (tmp_path / "evil.md").exists() and not (tmp_path / "deep" / "evil.md").exists()
    assert 'title: "Doc ../../evil"' in (out / "doc" / "evil.md").read_text(
        encoding="utf-8"
    )  # the title keeps its text


def test_api_and_roots(settings: Settings, tmp_path: Path):
    src = tmp_path / "in"
    make_pack(src, "BRD-01-Alpha-Thing", "Alpha", "view the list")
    s = kset(settings)
    s.knowledge_roots = str(tmp_path)
    with TestClient(create_app(s, provider_factory=answers(), create_schema=True)) as c:
        assert (
            c.post(
                "/api/knowledge/ingest", json={"project": P, "source": "documents", "path": str(src)}
            ).status_code
            == 200
        )
        r = c.post("/api/knowledge/export", json={"project": P, "path": str(tmp_path / "md")})
        assert r.status_code == 200 and r.json()["written"] == r.json()["files"] > 0, r.text
        assert (tmp_path / "md" / "manifest.json").exists()
        assert (
            c.post(
                "/api/knowledge/export", json={"project": P, "path": str(tmp_path.parent / "elsewhere")}
            ).status_code
            == 422
        )
        assert c.post("/api/knowledge/export", json={"project": P}).status_code == 422


# ---- concepts shared across backlogs --------------------------------------------------------------------------------


def backlog(root: Path, folder: str, caps: str, story: str, term_file: str, definition: str) -> None:
    d = root / folder
    d.mkdir(parents=True)
    (d / "user_story.json").write_text(
        json.dumps(
            {
                "user_stories": [
                    {
                        "story_id": "US-001",
                        "epic_id": "E-001",
                        "feature_id": "FEAT-001",
                        "title": story,
                        "description": f"As a Corporate Maker, I want to {story.lower()}, so that I can work.",
                    }
                ],  # fmt: skip
                "role_capabilities": [{"role": "Corporate Maker", "capabilities": caps}],
            }
        )
    )
    (d / term_file).write_text(
        f"# Notes\n\n## Glossary\n\n| Term | Definition |\n|---|---|\n| Root VA | {definition} |\n"
    )


def test_a_role_and_a_term_in_two_backlogs_are_one_node_with_evidence_from_both(
    repo: KnowledgeRepo, tmp_path: Path
):
    backlog(
        tmp_path,
        "BRD-01-Alpha",
        "creates payment requests",
        "View the list",
        "glossary-a.md",
        "The top virtual account of a hierarchy",
    )
    backlog(
        tmp_path,
        "BRD-02-Beta",
        "prepares transfers for approval",
        "Pin favourites",
        "glossary-b.md",
        "The root of the virtual account tree",
    )
    first = ing(repo).ingest_documents(P, tmp_path)
    assert first.failed == []
    makers = [s for s in repo.sources(P, kind="concept", limit=100) if "maker" in s.external_id]
    roots = [s for s in repo.sources(P, kind="concept", limit=100) if "root" in s.external_id]
    assert len(makers) == 1 and len(roots) == 1  # shared, not one per backlog
    maker, root = makers[0], roots[0]
    meta = repo.version(maker.id).meta  # type: ignore[union-attr]
    assert [e["source"] for e in meta["evidence"]] == [
        "BRD-01-Alpha/user_story.json",
        "BRD-02-Beta/user_story.json",
    ]
    assert (
        meta["description"] == "creates payment requests" and repo.node_type(maker) == "actor"
    )  # the first definition stays
    assert len({e["source"] for e in repo.version(root.id).meta["evidence"]}) == 2  # type: ignore[union-attr]
    name = {s.id: s.external_id for s in repo.sources(P, limit=1000)}
    mentioned_by = sorted(
        name[e.src_id] for e in repo.edges(P, limit=1000) if e.dst_id == maker.id and e.type == "mentions"
    )
    assert mentioned_by == ["BRD-01/US-001", "BRD-02/US-001"]  # each backlog's story points at the one role
    version = repo.version(maker.id).version  # type: ignore[union-attr]  # 2: made by the first backlog, extended by the second
    for _ in range(2):  # reprocessing: nothing is created, and no concept gets a new version
        again = ing(repo).ingest_documents(P, tmp_path)
        assert again.created == 0 and again.updated == 0, again.as_dict()
    assert repo.version(maker.id).version == version == 2  # type: ignore[union-attr]


def test_a_document_that_says_it_again_keeps_its_place_in_the_evidence():
    ev = [{"source": "a", "quote": "1"}, {"source": "b", "quote": "2"}, {"source": "c", "quote": "3"}]
    assert merge_evidence(ev, "a", "changed") == [{"source": "a", "quote": "changed"}, {"source": "b", "quote": "2"}, {"source": "c", "quote": "3"}]  # fmt: skip
    assert [e["source"] for e in merge_evidence(ev, "d", "4")] == ["a", "b", "c", "d"]
    assert [e["source"] for e in merge_evidence(ev, "d", "4", keep=2)] == ["c", "d"]


# ---- live progress --------------------------------------------------------------------------------------------------


def test_progress_events_carry_time_spent_and_an_estimate(repo: KnowledgeRepo):
    linking_corpus(repo)
    events: list[dict[str, Any]] = []
    linking.run(repo, answers(), "lk", on_event=lambda stage, msg, d: events.append(d))
    steps = [d for d in events if d.get("done")]
    assert steps and all("elapsed_s" in d and "eta_s" in d for d in steps)
    assert steps[-1]["done"] == steps[-1]["total"] and steps[-1]["eta_s"] == 0
