"""Intake of BRDs and stories (pasted or uploaded), the graph view, retrieval evaluation and task evaluation."""

from __future__ import annotations

import base64
import io
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from harnix.api.app import create_app
from harnix.config import Settings
from harnix.knowledge.embeddings import HashEmbedder
from harnix.knowledge.ingest import Ingestor
from harnix.knowledge.repo import KnowledgeRepo
from harnix.knowledge.sections import docx_to_markdown
from harnix.knowledge.service import KnowledgeService
from harnix.persistence.store import Store

from .test_knowledge import P, client, kset


@pytest.fixture
def repo(
    store: Store,
) -> KnowledgeRepo:  # shadows the repository-path fixture of conftest, as test_knowledge does
    return KnowledgeRepo(store)


BRD_TEXT = """# Payments BRD

Intro text about the payments module, long enough to be a real paragraph of a business document.

**BF-PAY-01: Card payment**

Customers can pay by card. Meets the requirements of PSF C.1.2.1. and C.1.2.2.

**BF-PAY-02: Refunds**

A refund returns money to the original card within five days. Only the owner may request it.

## 5.2 Detailed Requirements

### 5.2.1 Refund fields (BF-PAY-02)

| Field | Type |
| Amount | Numeric |
| Reason | Text, mandatory when the refund is partial |
"""

STORY = (
    "# As a customer I want a refund\n\nAs a customer I want a refund to return to my original card within five days.\n\n"
    "## Acceptance criteria\n- Refund goes to the original card\n- Only the owner can ask\n"
)


def make_docx(paragraphs: list[tuple[str, str]], table: list[list[str]] | None = None) -> bytes:
    ns = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
    body = "".join(
        f'<w:p><w:pPr><w:pStyle w:val="{st}"/></w:pPr><w:r><w:t>{tx}</w:t></w:r></w:p>'
        for st, tx in paragraphs
    )
    if table:
        rows = [
            "<w:tr>" + "".join(f"<w:tc><w:p><w:r><w:t>{c}</w:t></w:r></w:p></w:tc>" for c in row) + "</w:tr>"
            for row in table
        ]
        body += "<w:tbl>" + "".join(rows) + "</w:tbl>"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(
            "word/document.xml", f'<?xml version="1.0"?><w:document {ns}><w:body>{body}</w:body></w:document>'
        )
    return buf.getvalue()


def test_a_brd_is_split_into_requirement_sections_linked_to_it(repo: KnowledgeRepo):
    ing = Ingestor(repo, HashEmbedder())
    res = ing.ingest_text(P, kind="brd", filename="payments.md", text=BRD_TEXT)
    assert res.created == 4 and res.failed == []  # the BRD, two sections, one data-field section
    assert set(res.ingested) == {
        "brd:payments",
        "requirement:BF-PAY-01",
        "requirement:BF-PAY-02",
        "requirement:BF-PAY-02:5-2-1-refund-fields",
    }
    svc = KnowledgeService(repo, HashEmbedder(), [P])
    pairs = {(e["from"], e["type"], e["to"]) for e in svc.relations(P, "BF-PAY-02", depth=1)["edges"]}
    assert ("BF-PAY-02", "derives_from", "payments") in pairs
    assert ("BF-PAY-02:5-2-1-refund-fields", "refines", "BF-PAY-02") in pairs
    meta = svc.get(P, "BF-PAY-01")["metadata"]
    assert meta["references"] == ["C.1.2.1", "C.1.2.2"] and meta["section_of"] == "payments"
    again = ing.ingest_text(P, kind="brd", filename="payments.md", text=BRD_TEXT)
    assert again.created == 0 and again.updated == 0 and again.unchanged == 4  # idempotent


def test_word_documents_are_read_with_headings_and_tables(repo: KnowledgeRepo):
    data = make_docx(
        [("Heading1", "Refund policy"), ("Normal", "Refunds go back to the card."), ("Heading2", "BF-DOC-01: Partial refunds"),
         ("Normal", "A partial refund needs a reason given by the customer.")],
        [["Field", "Rule"], ["Amount", "Numeric"]],
    )  # fmt: skip
    md = docx_to_markdown(io.BytesIO(data))
    assert "# Refund policy" in md and "## BF-DOC-01: Partial refunds" in md and "| Amount | Numeric |" in md
    res = Ingestor(repo, HashEmbedder()).ingest_text(P, kind="brd", filename="refunds.docx", data=data)
    assert "requirement:BF-DOC-01" in res.ingested
    bad = Ingestor(repo, HashEmbedder()).ingest_text(P, kind="brd", filename="x.docx", data=b"not a zip")
    assert bad.failed and "Not a readable .docx" in bad.failed[0] and bad.created == 0


def test_a_story_gets_proposed_links_never_verified_ones(repo: KnowledgeRepo):
    ing = Ingestor(repo, HashEmbedder())
    ing.ingest_text(P, kind="brd", filename="payments.md", text=BRD_TEXT)
    res = ing.ingest_text(P, kind="story", text=STORY)
    assert res.suggested >= 1 and res.ingested[0].startswith("story:US-")
    story = repo.find_source(P, "story", res.ingested[0].split(":", 1)[1])
    assert story is not None
    edges = [e for e in repo.edges(P) if e.src_id == story.id]
    assert edges and all(
        e.status == "proposed" and e.origin == "agent" and e.type == "derives_from" for e in edges
    )
    declared = ing.ingest_text(P, kind="story", external_id="US-9", text="As a user I want something unrelated about invoices.", links={"derives_from": ["BF-PAY-01"]})  # fmt: skip
    nine = repo.find_source(P, "story", "US-9")
    assert nine is not None and declared.suggested == 0
    assert any(
        e.status == "verified" and e.type == "derives_from" for e in repo.edges(P) if e.src_id == nine.id
    )


def test_api_documents_projects_graph_and_eval(settings: Settings):
    with client(settings) as c:
        r = c.post(
            "/api/knowledge/documents",
            json={"project": "pay", "kind": "brd", "filename": "payments.md", "text": BRD_TEXT},
        )
        assert (
            r.status_code == 200
            and r.json()["created"] == 4
            and "requirement:BF-PAY-01" in r.json()["ingested"]
        )
        docx = base64.b64encode(make_docx([("Heading2", "BF-D-01: Disputes"), ("Normal", "A dispute freezes the refund until a clerk decides, within three days.")])).decode()  # fmt: skip
        post = lambda **kw: c.post("/api/knowledge/documents", json={"project": "pay", "kind": "brd", **kw})  # noqa: E731
        assert post(filename="d.docx", data_base64=docx).status_code == 200
        assert post(filename="d.docx", data_base64="@@@").status_code == 422
        assert post().status_code == 422
        assert post(filename="x.docx", data_base64=base64.b64encode(b"junk").decode()).status_code == 422
        pj = c.get("/api/knowledge/projects").json()["projects"]
        assert pj[0]["project"] == "pay" and pj[0]["by_kind"]["requirement"] >= 3
        g = c.get("/api/knowledge/graph", params={"project": "pay"}).json()
        assert {n["external_id"] for n in g["nodes"]} >= {"payments", "BF-PAY-01", "BF-PAY-02"} and g["edges"]
        assert all(set(e) >= {"from", "to", "type", "status", "stale", "in_bundle"} for e in g["edges"])
        b = c.post(
            "/api/knowledge/bundles",
            json={"project": "pay", "instructions": "Implement refunds to the original card"},
        ).json()
        ctx = c.get(
            "/api/knowledge/graph", params={"project": "pay", "bundle": b["bundle_id"], "only_bundle": True}
        ).json()
        assert (
            ctx["nodes"]
            and all(n["in_bundle"] for n in ctx["nodes"])
            and len(ctx["nodes"]) <= len(g["nodes"])
        )
        assert c.get("/api/knowledge/graph", params={"project": "pay", "bundle": "nope"}).status_code == 404
        cases = [
            {"query": "refund returns to the card", "expect": ["BF-PAY-02"]},
            {"query": "paying with a card", "expect": ["BF-PAY-01"]},
        ]
        ev = c.post("/api/knowledge/eval", json={"project": "pay", "cases": cases}).json()
        assert set(ev["modes"]) == {"hybrid", "vector", "keyword"} and ev["modes"]["hybrid"]["hit@3"] == 1.0
        assert ev["keyword_weight"] == 0.5  # the server default (FORGE_KNOWLEDGE_KEYWORD_WEIGHT)
        vec_only = c.post(
            "/api/knowledge/eval", json={"project": "pay", "cases": cases, "keyword_weight": 0}
        ).json()
        assert vec_only["keyword_weight"] == 0
        assert (
            vec_only["modes"]["hybrid"]["mrr"] == vec_only["modes"]["vector"]["mrr"]
        )  # weight 0 = vectors alone
        bad = c.post("/api/knowledge/eval", json={"project": "pay", "cases": cases, "keyword_weight": -1})
        assert bad.status_code == 422
        assert (
            c.post("/api/knowledge/eval", json={"project": "pay", "cases": [{"query": 1}]}).status_code == 422
        )


def test_projection_is_deterministic_in_range_and_keeps_alike_things_together():
    from harnix.knowledge.projection import pca2

    def vec(axis: int, jitter: float) -> list[float]:
        v = [0.0] * 16
        v[axis] = 1.0
        v[(axis + 5) % 16] = jitter
        return v

    a = [vec(0, 0.02 * i) for i in range(8)]
    b = [vec(8, 0.02 * i) for i in range(8)]
    pts = pca2(a + b)
    assert pts == pca2(a + b) and all(0 <= x <= 1 and 0 <= y <= 1 for x, y in pts)
    dist = lambda p, q: ((p[0] - q[0]) ** 2 + (p[1] - q[1]) ** 2) ** 0.5  # noqa: E731
    within = max(dist(pts[i], pts[j]) for i in range(8) for j in range(8))
    between = min(dist(pts[i], pts[8 + j]) for i in range(8) for j in range(8))
    assert within < between  # the two groups are separated on the map
    assert pca2([]) == [] and pca2([[1.0, 2.0]]) == [(0.5, 0.5)]


def test_api_embedding_map_and_neighbours(settings: Settings):
    with client(settings) as c:
        c.post(
            "/api/knowledge/documents",
            json={"project": "pay", "kind": "brd", "filename": "payments.md", "text": BRD_TEXT},
        )
        c.post("/api/knowledge/documents", json={"project": "pay", "kind": "story", "text": STORY})
        m = c.get("/api/knowledge/embeddings", params={"project": "pay"}).json()
        assert (
            m["model"] == "mock-hash-256|ctx1"
            and m["dimensions"] == 256
            and m["embedded"] == m["chunks"] == len(m["points"]) > 4
        )
        assert all(0 <= p["x"] <= 1 and 0 <= p["y"] <= 1 and p["text"] for p in m["points"])
        assert {p["kind"] for p in m["points"]} >= {"brd", "requirement", "story"}
        refund = next(p for p in m["points"] if p["external_id"] == "BF-PAY-02")
        nb = c.get(
            f"/api/knowledge/chunks/{refund['id']}/neighbors", params={"project": "pay", "k": 4}
        ).json()
        sims = [n["similarity"] for n in nb["neighbors"]]
        assert (
            sims == sorted(sims, reverse=True)
            and len(sims) == 4
            and refund["id"] not in {n["id"] for n in nb["neighbors"]}
        )
        assert (
            nb["chunk"]["dimensions"] == 256
            and len(nb["chunk"]["vector_head"]) == 12
            and "refund" in nb["chunk"]["text"].lower()
        )
        assert any(n["external_id"].startswith(("US-", "BF-PAY", "payments")) for n in nb["neighbors"])
        hits = c.get("/api/knowledge/search", params={"project": "pay", "q": "refund original card"}).json()[
            "hits"
        ]
        ids = {p["id"] for p in m["points"]}
        assert hits and all(
            h["chunk_ids"] and set(h["chunk_ids"]) <= ids for h in hits
        )  # the exact matching chunks, drawable on the map
        assert c.get("/api/knowledge/chunks/nope/neighbors", params={"project": "pay"}).status_code == 404
        empty = c.get("/api/knowledge/embeddings", params={"project": "nothing-here"}).json()
        assert empty["points"] == [] and empty["model"] is None


# ---- backlog packs: BRD.md + epic.md + user_story.md per folder, ids local to each folder ---------------------------

PACK_BRD = "# BUSINESS REQUIREMENTS DOCUMENT\n\n## {name}\n\n**Platform:** Bank\n\n## 5. Business Requirements\n\n**E-001 - Something:** Do the thing for users.\n\n- **FEAT-001 Do it**\n\n| Field | Type |\n|---|---|\n| A | Text |\n"  # fmt: skip
PACK_EPIC = "# {name} - Epic\n\n## Epics & Features\n\n### E-001: Do the thing\n\nDescription of the epic.\n- **FEAT-001 Do it** (2 stories)\n- **FEAT-002 Do it again** (1 stories)\n\n## Split Justification\n\ntext\n"  # fmt: skip
PACK_STORY = """# {name} - User Stories

- Source BRD: x

## US-001: First story for {name}

- Epic: E-001 | Feature: FEAT-001 | Traces: ITEM-001
- Priority: Highest | Channel: Web, Mobile

As a Corporate User, I want to {verb}, so that I can work.

**Acceptance Criteria**

1. GIVEN a user WHEN they {verb} THEN it works
2. GIVEN no entitlement WHEN they try THEN access is denied

## US-002: Second story for {name}

- Epic: E-001 | Feature: FEAT-002 | Traces: ITEM-002
- Priority: High | Channel: Web

As a Corporate User, I want to export, so that I can report.

**Acceptance Criteria**

1. GIVEN data WHEN export THEN a file is produced

## Assumptions Register

- an assumption that must not become part of the last story
"""


def make_pack(root: Path, folder: str, name: str, verb: str) -> None:
    d = root / folder
    d.mkdir(parents=True)
    (d / "BRD.md").write_text(PACK_BRD.format(name=name))
    (d / "epic.md").write_text(PACK_EPIC.format(name=name))
    (d / "user_story.md").write_text(PACK_STORY.format(name=name, verb=verb))


def test_backlog_packs_are_split_namespaced_and_linked(repo: KnowledgeRepo, tmp_path: Path):
    make_pack(tmp_path, "BRD-01-Alpha-Thing", "Alpha", "view the list")
    make_pack(tmp_path, "BRD-02-Beta-Thing", "Beta", "pin favourites")
    res = Ingestor(repo, HashEmbedder()).ingest_documents(P, tmp_path)
    assert res.failed == [] and res.unresolved == []
    by_kind: dict[str, list[str]] = {}
    for s in repo.sources(P):
        by_kind.setdefault(s.kind, []).append(s.external_id)
    assert by_kind["brd"] == ["BRD-01", "BRD-02"]  # one BRD per pack, named by the folder
    assert by_kind["epic"] == ["BRD-01/E-001", "BRD-02/E-001"]
    assert by_kind["feature"] == ["BRD-01/FEAT-001", "BRD-01/FEAT-002", "BRD-02/FEAT-001", "BRD-02/FEAT-002"]
    assert by_kind["story"] == [
        "BRD-01/US-001",
        "BRD-01/US-002",
        "BRD-02/US-001",
        "BRD-02/US-002",
    ]  # same local ids, no clash
    assert "requirement" not in by_kind  # backlog ids (E-, FEAT-, US-) in a BRD are not requirement ids
    svc = KnowledgeService(repo, HashEmbedder(), [P])
    pairs = {(e["from"], e["to"]) for e in svc.relations(P, "BRD-02/US-002", depth=3)["edges"]}
    assert {
        ("BRD-02/US-002", "BRD-02/FEAT-002"),
        ("BRD-02/FEAT-002", "BRD-02/E-001"),
        ("BRD-02/E-001", "BRD-02"),
    } <= pairs
    assert not any("BRD-01" in a or "BRD-01" in b for a, b in pairs)  # never crosses into the other pack
    story = svc.get(P, "BRD-02/US-001")
    assert (
        story["metadata"]["feature"] == "FEAT-001"
        and story["metadata"]["traces"] == ["ITEM-001"]
        and story["metadata"]["priority"] == "Highest"
    )
    assert (
        "Assumptions Register" not in svc.get(P, "BRD-02/US-002")["content"]
    )  # the story ends where the next heading starts
    assert svc.get(P, "BRD-02")["title"].startswith(
        "Business Requirements Document: Beta"
    )  # not the same title five times
    from harnix.knowledge.evaluation import criteria_of

    assert criteria_of(story["content"]) == ["GIVEN a user WHEN they pin favourites THEN it works", "GIVEN no entitlement WHEN they try THEN access is denied"]  # fmt: skip
    again = Ingestor(repo, HashEmbedder()).ingest_documents(P, tmp_path)
    assert (
        again.created == 0 and again.updated == 0 and again.unchanged == 16
    )  # idempotent: 2 packs x (BRD, epic, 2 features, 2 stories, notes of the epic file and of the story file)


def test_pasted_backlog_files_take_a_namespace(repo: KnowledgeRepo):
    ing = Ingestor(repo, HashEmbedder())
    r = ing.ingest_text(
        P,
        kind="story",
        filename="user_story.md",
        text=PACK_STORY.format(name="Gamma", verb="see totals"),
        scope="BRD-09",
    )
    assert r.ingested == [
        "story:BRD-09/US-001",
        "story:BRD-09/US-002",
        "doc:BRD-09/user_story-notes",
    ]  # the file title and assumptions register are kept as a note
    single = ing.ingest_text(
        P,
        kind="story",
        text="# As a user I want one thing\n\nAs a user I want one thing done so that it is done.\n",
    )
    assert single.ingested[0].startswith("story:US-")  # a single story keeps working as before


def test_the_real_virtual_account_packs_if_present(repo: KnowledgeRepo):
    root = (
        Path(__file__).resolve().parents[2]
        / "virtual_account_brd_epics_user_stories"
        / "virtual_account_brd_pack"
    )
    if not root.is_dir():
        pytest.skip("the virtual account packs are not in this checkout")
    res = Ingestor(repo, HashEmbedder()).ingest_documents(P, root)
    kinds = {k: sum(1 for s in repo.sources(P) if s.kind == k) for k in ("brd", "epic", "feature", "story")}
    assert (
        kinds == {"brd": 5, "epic": 6, "feature": 15, "story": 24}
        and res.failed == []
        and res.unresolved == []
    )


JSON_EPICS = {
    "document_info": {"product": "VA", "source_brd": "brd.md"},
    "epics": [{"id": "E-001", "name": "Inquiry", "description": "See accounts", "brd_traceability": "BF-AS-VA-01, BF-AS-VA-02",
               "features": [{"id": "FEAT-001", "name": "Summary", "description": "Totals", "brd_reference": "BF-AS-VA-01 (Summary)",
                             "in_scope_items": [{"id": "ITEM-001", "text": "Single CIF view"}]}]}],
    "validation_rules": [{"rule_id": "V-1", "rule_name": "Entitlement scoping", "description": "Only entitled accounts"}],
    "boundary_items": [{"id": "BND-001", "text": "Fees are out of scope", "kind": "other-module"}],
    "role_capabilities": [{"role": "Corporate Maker", "capabilities": "Inquires accounts within entitlements"}],
    "reporting_requirements": [{"report_type": "Account List", "formats": "PDF, XLSX", "features": "FEAT-001 Summary"}],
}  # fmt: skip
JSON_STORIES = {
    "user_stories": [
        {"story_id": "US-001", "epic_id": "E-001", "feature_id": "FEAT-001", "title": "Single CIF summary", "description": "As a Corporate Maker, I want a summary.", "traces": ["ITEM-001"],
         "acceptance_criteria": ["GIVEN one CIF WHEN opened THEN totals show", "GIVEN no entitlement WHEN opened THEN denied"],
         "business_validation_rule_ids": ["V-1"], "brd_reference": "BF-AS-VA-01 (Summary)", "depends_on": []},
        {"story_id": "US-002", "epic_id": "E-001", "feature_id": "FEAT-001", "title": "Multi CIF", "description": "As a user, I want several CIFs.",
         "acceptance_criteria": ["GIVEN two CIFs WHEN opened THEN both show"], "depends_on": [{"story_id": "US-001", "reason": "extends the summary"}]},
    ],
    "assumptions_register": [{"number": 1, "story_id": "US-001", "assumption_text": "Entity means the corporate CIF"}],
}  # fmt: skip
BRD_MD = "# BRD\n\n## 5.1 Business Functions\n\n### BF-AS-VA-01: Summary\n\nTotals per CIF.\n\n### BF-AS-VA-02: List\n\nThe account list.\n"


def test_json_backlogs_are_read_and_linked_like_markdown_ones(repo: KnowledgeRepo, tmp_path: Path):
    import json as _json

    from harnix.knowledge.evaluation import criteria_of

    (tmp_path / "brd.md").write_text(BRD_MD)
    (tmp_path / "epic features.json").write_text(_json.dumps(JSON_EPICS))
    (tmp_path / "user_story.json").write_text(_json.dumps(JSON_STORIES))
    (tmp_path / "settings.json").write_text('{"theme": "dark"}')
    res = Ingestor(repo, HashEmbedder()).ingest_documents(P, tmp_path)
    assert res.failed == [] and res.unresolved == [], (res.failed, res.unresolved)
    settings = repo.resolve(P, "settings.json")  # other JSON is taken in as a readable document
    assert settings is not None and settings.kind == "doc"
    assert "theme: dark" in repo.version(settings.id).content  # type: ignore[union-attr]
    kinds = {i.split(":")[0] for i in res.ingested}
    assert {"brd", "requirement", "epic", "feature", "story", "rule", "doc"} <= kinds
    edges = {
        (repo.get_source(e.src_id).external_id, e.type, repo.get_source(e.dst_id).external_id)
        for e in repo.edges(P)
    }  # type: ignore[union-attr]
    assert {("US-001", "derives_from", "FEAT-001"), ("FEAT-001", "derives_from", "E-001"), ("E-001", "derives_from", "brd"),
            ("US-001", "derives_from", "BF-AS-VA-01"), ("US-001", "depends_on", "V-1"), ("US-002", "depends_on", "US-001")} <= edges  # fmt: skip
    us1 = repo.version(repo.resolve(P, "US-001").id).content  # type: ignore[union-attr]
    assert len(criteria_of(us1)) == 2  # the task evaluation finds the acceptance criteria
    assert "extends the summary" in repo.version(repo.resolve(P, "US-002").id).content  # type: ignore[union-attr]
    # nothing a story hangs on is left in a notes file: scope items, assumptions, reports, boundaries and roles
    assert {("US-001", "satisfies", "ITEM-001"), ("ITEM-001", "derives_from", "FEAT-001"),
            ("US-001", "mentions", "concept:corporate maker")} <= edges  # fmt: skip
    assert "Entity means the corporate CIF" in us1 and "ITEM-001" in us1
    feat = repo.version(repo.resolve(P, "FEAT-001").id).content  # type: ignore[union-attr]
    assert "ITEM-001: Single CIF view" in feat and "Account List (PDF, XLSX)" in feat
    bnd = repo.resolve(P, "BND-001")
    assert bnd is not None and bnd.kind == "rule" and repo.node_type(bnd) == "constraint"
    assert "Fees are out of scope" in repo.version(bnd.id).content  # type: ignore[union-attr]
    maker = repo.resolve(P, "concept:corporate maker")
    assert maker is not None and repo.node_type(maker) == "actor"
    assert "Fees are out of scope" not in repo.version(repo.resolve(P, "epic-features-notes").id).content  # type: ignore[union-attr]
    # re-extracting concepts never removes the rules a backlog file declares
    assert (
        repo.delete_sources(P, ("concept", "rule"), origin="extracted") == 0
        and repo.resolve(P, "V-1") is not None
    )


def test_api_accepts_a_quoted_folder_path(settings: Settings, tmp_path: Path):
    s = kset(settings, knowledge_roots="")
    (tmp_path / "BRD.md").write_text(BRD_MD)
    with TestClient(create_app(s, create_schema=True)) as c:
        r = c.post(
            "/api/knowledge/ingest", json={"project": P, "source": "documents", "path": f'"{tmp_path}"'}
        )
        assert r.status_code == 200 and r.json()["created"] >= 1, r.text


def test_background_ingest_reports_every_step_live(settings: Settings, tmp_path: Path):
    import json as _json
    import time as _time

    (tmp_path / "brd.md").write_text(BRD_MD)
    (tmp_path / "user_story.json").write_text(_json.dumps(JSON_STORIES))
    with TestClient(create_app(kset(settings, knowledge_roots=""), create_schema=True)) as c:
        job = c.post("/api/knowledge/ingest", json={"project": P, "source": "documents", "path": str(tmp_path), "background": True}).json()  # fmt: skip
        assert job["status"] == "running" and job["kind"] == "ingest"
        for _ in range(100):
            job = c.get(f"/api/knowledge/jobs/{job['id']}").json()
            if job["status"] != "running":
                break
            _time.sleep(0.1)
        assert job["status"] == "done", job
        stages = [e["stage"] for e in job["events"]]
        assert stages[:2] == ["postgres", "scan"] and stages[-1] == "done"  # rows before, then the files
        assert {"read", "embed", "link", "ontology", "neo4j"} <= set(stages)  # every step said what it did
        reads = [e["message"] for e in job["events"] if e["stage"] == "read"]
        assert any("user_story.json: 2 story" in m for m in reads) and job["report"]["created"] >= 4
        assert c.get("/api/knowledge/jobs", params={"project": P}).json()["jobs"][0]["id"] == job["id"]
        # what was captured, per step, for the Ingestion page
        ev = {st: [e["data"] for e in job["events"] if e["stage"] == st] for st in set(stages)}
        captured = [x for d in ev["read"] for x in d.get("entities", [])]
        assert {"story"} <= {x["kind"] for x in captured} and all(x["status"] == "created" for x in captured)
        assert all({"external_id", "version", "chunks", "sections", "declares"} <= set(x) for x in captured)
        sample = next(d for d in ev["embed"] if "sample" in d)
        assert sample["dims"] > 0 and len(sample["sample"]["vector_head"]) == 8
        assert sample["sample"]["embedded_text"].startswith(
            "["
        )  # the source's kind and id travel with each chunk
        links = next(d for d in ev["link"])["links"]
        assert links and all(x["status"] == "verified" for x in links)
        before, after = ev["postgres"]
        assert (
            before["rows"]["knowledge_sources"] == 0
            and after["delta"]["knowledge_sources"] == job["report"]["created"]
        )
        assert after["delta"]["knowledge_chunks"] == after["rows"]["chunks_with_embedding"] > 0
        assert after["delta"]["knowledge_edges"] == len(links) + job["report"]["suggested"]
        assert "entities" not in job["report"] and "links" not in job["report"]  # kept out of the audit log


def test_any_json_file_in_a_folder_is_taken_in_as_a_readable_document(repo: KnowledgeRepo, tmp_path: Path):
    import json as _json

    from harnix.knowledge.ingest import json_to_markdown

    api = {
        "info": {"title": "Payments API", "version": "2"},
        "paths": [
            {"id": "POST /refunds", "summary": "Refund a payment", "limits": {"max_cents": 50000}},
            {"id": "GET /refunds", "summary": "List refunds", "tags": ["read", "audit"]},
        ],
        "empty": {},
    }
    (tmp_path / "api").mkdir()
    (tmp_path / "api" / "payments.json").write_text(_json.dumps(api))
    (tmp_path / "list.json").write_text(_json.dumps([{"name": "alpha", "note": "first"}, "loose"]))
    (tmp_path / "broken.json").write_text("{not json")
    (tmp_path / "nothing.json").write_text("{}")
    res = Ingestor(repo, HashEmbedder()).ingest_documents(P, tmp_path)
    doc = repo.resolve(P, "api/payments.json")
    assert doc is not None and doc.kind == "doc" and doc.title == "Payments API"
    text = repo.version(doc.id).content  # type: ignore[union-attr]
    assert "## paths" in text and "### POST /refunds" in text and "limits.max_cents: 50000" in text
    assert "tags: read; audit" in text and "empty" not in text  # empty values are left out
    assert "### alpha" in repo.version(repo.resolve(P, "list.json").id).content  # type: ignore[union-attr]
    assert any("broken.json" in f for f in res.failed)  # reported, not guessed
    assert any("nothing.json" in s and "nothing to read" in s for s in res.skipped)
    assert len(repo.chunks(doc.id)) >= 1 and all(c.embedding for c in repo.chunks(doc.id))
    again = Ingestor(repo, HashEmbedder()).ingest_documents(P, tmp_path)
    assert again.created == 0 and again.unchanged == len(again.ingested)  # idempotent
    assert json_to_markdown("T", {"a": 1}).startswith("# T\n\n- a: 1")


def test_an_uploaded_json_file_is_read_like_one_from_a_folder(settings: Settings):
    with client(settings) as c:
        spec = '{"name": "Limits", "rules": [{"id": "R1", "text": "At most 10 favourites"}]}'
        r = c.post("/api/knowledge/documents", json={"project": P, "kind": "design", "filename": "limits.json", "text": spec})  # fmt: skip
        assert r.status_code == 200 and r.json()["created"] == 1, r.text
        got = c.get("/api/knowledge/source", params={"project": P, "ref": "limits.json"}).json()
        assert (
            got["kind"] == "design"
            and "### R1" in got["content"]
            and "At most 10 favourites" in got["content"]
        )
        bad = c.post("/api/knowledge/documents", json={"project": P, "kind": "doc", "filename": "x.json", "text": "{oops"})  # fmt: skip
        assert bad.status_code == 422


def test_ids_named_in_a_text_become_typed_links_only_to_what_exists(repo: KnowledgeRepo):
    from harnix.knowledge.ingest import cited_ids

    got = {r: t for r, t, _ in cited_ids("Depends on US-1, US-2 and US-3.\nParent: E-9\nSee Detailed Requirements (BF-X-01)\nepic_id: E-4\n"
                                         "- Epic: E-5 | Feature: FEAT-5 | Traces: ITEM-5\nIt requires a login. Ask about REQ-7\n")}  # fmt: skip
    assert got == {"US-1": "depends_on", "US-2": "depends_on", "US-3": "depends_on", "E-9": "derives_from",
                   "BF-X-01": "relates_to", "E-4": "derives_from", "E-5": "derives_from", "FEAT-5": "derives_from",
                   "ITEM-5": "satisfies", "REQ-7": "relates_to"}  # fmt: skip  # "Requirements" is not "requires"; a phrase in an earlier sentence does not count
    ing = Ingestor(repo, HashEmbedder())
    ing.ingest_text(P, kind="brd", filename="payments.md", text=BRD_TEXT)
    text = "# Refund screen\n\nThis screen implements BF-PAY-02 and builds on BF-PAY-01. Encoding is UTF-8; REQ-404 is gone.\n"  # fmt: skip
    res = ing.ingest_text(P, kind="design", external_id="DES-1", text=text)
    des = repo.resolve(P, "DES-1")
    assert des is not None
    mine = {
        (repo.get_source(e.dst_id).external_id, e.type, e.status) for e in repo.edges(P) if e.src_id == des.id
    }  # type: ignore[union-attr]
    assert {("BF-PAY-02", "implements", "verified"), ("BF-PAY-01", "depends_on", "verified")} <= mine
    assert (
        not any(x[0] in ("UTF-8", "REQ-404") for x in mine) and res.unresolved == []
    )  # cited, not declared: no noise
    # the BRD names its own sections: they already derive from it, so no second link is added
    brd = repo.resolve(P, "payments")
    assert brd is not None and not [e for e in repo.edges(P) if e.src_id == brd.id and e.type == "relates_to"]


def test_long_tables_keep_their_header_and_chunks_know_their_section_path():
    from harnix.knowledge.ingest import CHUNK_CHARS, chunk_text

    rows = "\n".join(
        f"| F{i} | Text | Mandatory, at most 40 characters, shown on the account list |" for i in range(60)
    )
    doc = f"# **5\\. Business Requirements** {{#5}}\n\n# \n\n## 5.2.1 Accounts (BF-AS-VA-02)\n\n| Field | Rule |\n| :-- | :-- |\n{rows}\n"  # fmt: skip
    chunks = chunk_text(doc)
    tables = [t for _, t in chunks if "| F" in t]
    assert len(tables) > 2 and all("| Field | Rule |\n| :-- | :-- |\n| F" in t for t in tables)
    assert all(
        t.startswith("| Field | Rule |") for t in tables[1:]
    )  # every later piece repeats the header row
    assert (
        all(len(t) <= CHUNK_CHARS + 150 for t in tables)
        and sum(t.count("| F") - t.count("| Field") for t in tables) == 60
    )
    assert (
        chunks[-1][0] == "5. Business Requirements > 5.2.1 Accounts (BF-AS-VA-02)"
    )  # cleaned, empty `#` ignored
    prose = " ".join(f"Sentence {i} says one thing about refunds and cards." for i in range(120))
    pieces = [t for _, t in chunk_text(f"# Notes\n\n{prose}\n")]
    assert len(pieces) > 3 and all(p.endswith(".") and len(p) <= CHUNK_CHARS for p in pieces)


def test_identified_json_records_become_their_own_linked_sources(repo: KnowledgeRepo, tmp_path: Path):
    import json as _json

    data = {
        "owner": "payments team",
        "requirements": [
            {"id": "REQ-1", "title": "Refund within five days", "depends_on": ["REQ-2"]},
            {"id": "REQ-2", "title": "Card on file", "detail": {"max_cards": 3}},
        ],
        "notes": [{"name": "no ids here"}],
    }
    (tmp_path / "reqs.json").write_text(_json.dumps(data))
    res = Ingestor(repo, HashEmbedder()).ingest_documents(P, tmp_path)
    assert {"requirement:REQ-1", "requirement:REQ-2", "doc:reqs.json"} <= set(res.ingested)
    r2 = repo.resolve(P, "REQ-2")
    assert r2 is not None and r2.title == "REQ-2: Card on file"
    assert "detail.max_cards: 3" in repo.version(r2.id).content  # type: ignore[union-attr]
    rest = repo.version(repo.resolve(P, "reqs.json").id).content  # type: ignore[union-attr]
    assert "owner: payments team" in rest and "no ids here" in rest and "Refund within" not in rest
    edges = {
        (repo.get_source(e.src_id).external_id, e.type, repo.get_source(e.dst_id).external_id)
        for e in repo.edges(P)
    }  # type: ignore[union-attr]
    assert ("REQ-1", "depends_on", "REQ-2") in edges
