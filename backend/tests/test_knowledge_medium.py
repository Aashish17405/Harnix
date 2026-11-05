"""Dependency graphs and reports, renderings (CSV / XLSX / JSONL), data-field tables, findings in the documents, and the
review of proposed links surviving re-runs. No model is involved except the scripted stand-in of the extraction tests."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

import pytest
from fastapi.testclient import TestClient

from harnix.api.app import create_app
from harnix.config import Settings
from harnix.knowledge import findings as findings_mod
from harnix.knowledge import linking, renderings
from harnix.knowledge.capture import decide
from harnix.knowledge.coverage import audit_inputs
from harnix.knowledge.embeddings import HashEmbedder
from harnix.knowledge.extract import run as extract_run
from harnix.knowledge.fields import field_rows
from harnix.knowledge.ingest import Ingestor
from harnix.knowledge.repo import KnowledgeRepo
from harnix.knowledge.reports import dag_edges, parse_report
from harnix.persistence.store import Store

from .test_knowledge import kset
from .test_knowledge_extract import P as EXTRACT_P
from .test_knowledge_extract import factory, good, load_stories
from .test_knowledge_linking import answers

P = "md"


@pytest.fixture
def repo(store: Store) -> KnowledgeRepo:
    return KnowledgeRepo(store)


def ing(repo: KnowledgeRepo) -> Ingestor:
    return Ingestor(repo, HashEmbedder(), "test")


def edges(repo: KnowledgeRepo, type_: str, project: str = P) -> list[tuple[str, str, str, str]]:
    name = {s.id: s.external_id for s in repo.sources(project, limit=1000)}
    return sorted((name[e.src_id], e.type, name[e.dst_id], e.status) for e in repo.edges(project, limit=1000) if e.type == type_)  # fmt: skip


STORIES = "# Stories\n\n" + "".join(
    f"## US-00{i}: Story {i}\n\nAs a user I want thing {i}, so that I can work.\n\n" for i in (1, 2, 3)
)


# ---- dependency graphs ----------------------------------------------------------------------------------------------


def test_a_mermaid_graph_becomes_depends_on_with_firm_and_provisional_edges(repo: KnowledgeRepo):
    i = ing(repo)
    i.ingest_text(P, kind="story", filename="user_story.md", text=STORIES)
    dag = 'graph TD\n  US-001["US-001 Story 1"] --> US-002["US-002 Story 2"]\n  US-001 -.-> US-003\n  US-002 ==> US-099\n'
    i.ingest_text(P, kind="doc", filename="x-dependency-dag.md", text="```mermaid\n" + dag + "```\n")
    assert dag_edges(dag) == [
        ("US-001", "US-002", True),
        ("US-001", "US-003", False),
        ("US-002", "US-099", True),
    ]
    assert edges(repo, "depends_on") == [
        ("US-002", "depends_on", "US-001", "verified"),
        ("US-003", "depends_on", "US-001", "verified"),
    ]  # the right item depends on the left one; US-099 does not exist and is ignored
    ev = {
        repo.get_source(e.src_id).external_id: e.evidence
        for e in repo.edges(P, limit=100)
        if e.type == "depends_on"
    }  # type: ignore[union-attr]
    assert "firm" in ev["US-002"] and "provisional" in ev["US-003"] and "-.->" in ev["US-003"]
    before = len(repo.edges(P, limit=1000))
    i.ingest_text(P, kind="doc", filename="x-dependency-dag.md", text="```mermaid\n" + dag + "```\n")
    assert len(repo.edges(P, limit=1000)) == before  # idempotent


# ---- reports --------------------------------------------------------------------------------------------------------

REPORT = (
    "# Gate report\n\n**Review date:** 2026-10-12\n\n**Overall result:** Not ready for sign-off\n\n"
    "| Story | Result | Note |\n|---|---|---|\n| US-001 | PASS | fine |\n| US-002 | WARN | acceptance criteria missing |\n\n"
    "US-003 and US-001: 0 failed checks.\nUS-003 failed the entitlement gap check.\n"
)


def test_a_report_flags_the_items_on_its_finding_lines_as_proposals(repo: KnowledgeRepo):
    got = parse_report(REPORT, "gate-report-20261012")
    assert (
        got["date"] == "2026-10-12"
        and got["summary"] == "Not ready for sign-off"
        and got["kind"] == "quality_gate"
    )
    assert got["mentioned"] == ["US-001", "US-002", "US-003"]
    assert [f["id"] for f in got["flagged"]] == [
        "US-002",
        "US-003",
    ]  # "0 failed" flags nothing, a PASS row flags nothing
    plain = parse_report("# User Story Review Report\n\nUS-001 is fine.\n", "review-report-2026-10-05")
    assert plain["summary"] == "" and plain["date"] == "2026-10-05"  # no summary is not the title

    i = ing(repo)
    i.ingest_text(P, kind="story", filename="user_story.md", text=STORIES)
    i.ingest_text(P, kind="doc", filename="gate-report-20261012.md", text=REPORT)
    assert edges(repo, "flags_gap") == [
        ("gate-report-20261012", "flags_gap", "US-002", "proposed"),
        ("gate-report-20261012", "flags_gap", "US-003", "proposed"),
    ]
    src = repo.find_source(P, "doc", "gate-report-20261012")
    assert repo.version(src.id).meta["report"]["summary"] == "Not ready for sign-off"  # type: ignore[union-attr]


# ---- data-field tables ----------------------------------------------------------------------------------------------

BRD = (
    "# BRD\n\n## 5.2.1 Accounts List Information (BF-AS-VA-02)\n\n"
    "| \\#\\# | Field Name | Data Type | Mandatory (M/C/O) | Handling |\n| :---- | :---- | :---- | :---- | :---- |\n"
    "|  | **Account List** |  |  |  |\n"
    "| 1 | Account Number | Numeric | M | Display the number |\n| 2 | Account Name | Text | M | Display the name |\n"
)


def test_field_tables_keep_every_row_as_a_field_with_its_attributes(repo: KnowledgeRepo):
    rows = field_rows(BRD)
    assert [(r["name"], r["group"]) for r in rows] == [
        ("Account Number", "Account List"),
        ("Account Name", "Account List"),
    ]
    assert rows[0]["attributes"] == {
        "Data Type": "Numeric",
        "Mandatory (M/C/O)": "M",
        "Handling": "Display the number",
    }  # no "##"
    assert field_rows("| Item | Value |\n|---|---|\n| a | b |\n") == []  # not a field table

    i = ing(repo)
    i.ingest_text(P, kind="brd", filename="brd.md", text=BRD)
    fields = [s for s in repo.sources(P, kind="concept", limit=100) if s.origin == "fields"]
    assert sorted(repo.version(s.id).meta["name"] for s in fields) == ["Account Name", "Account Number"]  # type: ignore[union-attr]
    assert all(repo.node_type(s) == "field" for s in fields)
    text = repo.version(fields[0].id).content  # type: ignore[union-attr]
    assert "Mandatory (M/C/O): M" in text and "Group: Account List" in text and "Section: 5.2.1" in text
    mentions = [e for e in repo.edges(P, limit=100) if e.type == "mentions" and e.status == "verified"]
    assert len(mentions) == 2
    count = len(repo.edges(P, limit=1000))
    again = i.ingest_text(P, kind="brd", filename="brd.md", text=BRD)
    assert again.created == 0 and again.updated == 0 and len(repo.edges(P, limit=1000)) == count  # idempotent


# ---- renderings -----------------------------------------------------------------------------------------------------

BACKLOG = {
    "user_stories": [
        {
            "story_id": "US-001",
            "epic_id": "E-001",
            "feature_id": "FEAT-001",
            "title": "Single summary",
            "description": "As a user, I want a summary of accounts.",
            "acceptance_criteria": ["GIVEN a user WHEN opened THEN totals show"],
        },  # fmt: skip
        {
            "story_id": "US-002",
            "epic_id": "E-001",
            "feature_id": "FEAT-001",
            "title": "Second story",
            "description": "As a user, I want a second thing.",
        },  # fmt: skip
    ]
}
CSV = 'Story ID,Summary,Status,Sprint,Story Points,Labels,Priority\nUS-001,US-001: Single summary,Done,Sprint 3,5,"a, b",Critical priority nobody wrote\nUS-404,Ghost,To Do,,,,\n'  # fmt: skip


def make_xlsx(path: Path, rows: list[list[str | None]]) -> None:
    """A minimal workbook: shared strings, one sheet; a None cell is left out of its row (as spreadsheet tools do)."""
    shared: list[str] = []
    xml_rows = []
    for r, row in enumerate(rows, 1):
        cells = []
        for c, v in enumerate(row):
            if v is None:
                continue
            if v not in shared:
                shared.append(v)
            cells.append(f'<c r="{chr(65 + c)}{r}" t="s"><v>{shared.index(v)}</v></c>')
        xml_rows.append(f'<row r="{r}">{"".join(cells)}</row>')
    ns = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
    with zipfile.ZipFile(path, "w") as z:
        z.writestr(
            "xl/sharedStrings.xml",
            f"<sst {ns}>" + "".join(f"<si><t>{escape(s)}</t></si>" for s in shared) + "</sst>",
        )
        z.writestr("xl/workbook.xml", f'<workbook {ns} xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Backlog" sheetId="1" r:id="rId1"/></sheets></workbook>')  # fmt: skip
        z.writestr("xl/_rels/workbook.xml.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>')  # fmt: skip
        z.writestr(
            "xl/worksheets/sheet1.xml",
            f"<worksheet {ns}><sheetData>" + "".join(xml_rows) + "</sheetData></worksheet>",
        )


def pack(tmp_path: Path) -> Path:
    d = tmp_path / "BRD-01-Alpha"
    d.mkdir()
    (d / "user_story.json").write_text(json.dumps(BACKLOG))
    (d / "export.csv").write_text(CSV)
    (d / "units.jsonl").write_text(json.dumps({"story_id": "US-002", "status": "In Progress"}) + "\n")
    make_xlsx(
        d / "sheet.xlsx",
        [["Story ID", "Summary", "Sprint", "Story Points"], ["US-002", "US-002: Second story", None, "3"]],
    )
    (d / "broken.xlsx").write_bytes(b"this is not a zip file")
    return tmp_path


def test_renderings_add_tracker_fields_to_their_items_and_report_cells_the_backlog_lacks(
    repo: KnowledgeRepo, tmp_path: Path
):
    root = pack(tmp_path)
    res = ing(repo).ingest_documents(P, root)
    s1, s2 = repo.find_source(P, "story", "BRD-01/US-001"), repo.find_source(P, "story", "BRD-01/US-002")
    v1, v2 = repo.version(s1.id), repo.version(s2.id)  # type: ignore[union-attr]
    assert "**Tracking**: Status: Done | Sprint: Sprint 3 | Story points: 5 | Labels: a, b" in v1.content  # type: ignore[union-attr]
    assert v1.meta["tracking"]["story_points"] == "5"  # type: ignore[union-attr]
    assert v2.meta["tracking"] == {"status": "In Progress", "story_points": "3"}  # type: ignore[union-attr]  # the xlsx row omitted its Sprint cell: Story Points stayed in its own column
    note = repo.find_source(P, "doc", "BRD-01/export-rendering")
    meta = repo.version(note.id).meta  # type: ignore[union-attr]
    assert meta["rendering"] is True and meta["differences"] == 2  # the Priority cell and "Ghost"
    kinds = {f["kind"] for f in res.findings}
    assert {"rendering_differs", "failed"} <= kinds
    assert any(
        "broken.xlsx" in f["message"] for f in res.findings if f["kind"] == "failed"
    )  # a corrupt export is a finding
    assert not any(e.type == "depends_on" for e in repo.edges(P, limit=1000))  # tracker rows make no links

    again = ing(repo).ingest_documents(P, root)
    assert again.created == 0 and again.updated == 0  # idempotent, extras included

    r = audit_inputs(repo, P, root)
    lost = {(m["file"].split("/")[-1], m["text"]) for m in r["missing"]}
    assert ("export.csv", "Critical priority nobody wrote") in lost and ("export.csv", "Done") not in lost
    assert ("sheet.xlsx", "3") not in lost  # found next to its key (story points)


def test_the_readers_place_cells_by_reference_and_refuse_a_bomb(tmp_path: Path):
    make_xlsx(tmp_path / "w.xlsx", [["A", "B", "C"], ["1", None, "3"]])
    assert renderings.read_xlsx(tmp_path / "w.xlsx") == {"Backlog": [["A", "B", "C"], ["1", "", "3"]]}
    (tmp_path / "x.csv").write_text("\n".join(["a,b", '"line one', 'line two",2', ""]))
    assert renderings.read_csv(tmp_path / "x.csv")[1] == ["line one\nline two", "2"]
    with zipfile.ZipFile(tmp_path / "bomb.xlsx", "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("xl/sharedStrings.xml", "a" * 60_000_000)
    with pytest.raises(ValueError):
        renderings.read_xlsx(tmp_path / "bomb.xlsx")


# ---- findings -------------------------------------------------------------------------------------------------------

TEXT = "This paragraph is long enough to be compared with another one. " * 5


def test_problems_in_the_documents_are_reported_and_kept_per_folder(repo: KnowledgeRepo, tmp_path: Path):
    (tmp_path / "a.md").write_text(TEXT)
    (tmp_path / "b.md").write_text(TEXT)
    (tmp_path / "stories.md").write_text(
        "# S\n\n## US-001: First\n\nAs a user I want this.\n\n## US-001: Other\n\nAs a user I want something else entirely.\n"
    )
    (tmp_path / "c.md").write_text(
        "---\nkind: design\nimplements: REQ-404\n---\nA design for something that was never written down.\n"
    )
    res = ing(repo).ingest_documents(P, tmp_path)
    by = {}
    for f in res.findings:
        by.setdefault(f["kind"], []).append(f)
    assert set(by) >= {"duplicate_content", "duplicate_id", "unresolved_reference"}
    assert (
        "US-001" in by["duplicate_id"][0]["message"]
        and "replaces the first" in by["duplicate_id"][0]["message"]
    )
    assert (
        "a" in by["duplicate_content"][0]["message"] and "REQ-404" in by["unresolved_reference"][0]["message"]
    )
    got = findings_mod.latest(repo, P)
    assert got["count"] == len(res.findings) and got["by_kind"]["duplicate_id"] == 1
    ing(repo).ingest_documents(P, tmp_path)
    assert (
        findings_mod.latest(repo, P)["count"] == got["count"]
    )  # the newest ingestion of the folder, not a running total


def test_api_findings(settings: Settings, tmp_path: Path):
    (tmp_path / "a.md").write_text(TEXT)
    (tmp_path / "b.md").write_text(TEXT)
    s = kset(settings)
    s.knowledge_roots = str(tmp_path)
    with TestClient(create_app(s, provider_factory=answers(), create_schema=True)) as c:
        assert (
            c.post(
                "/api/knowledge/ingest", json={"project": P, "source": "documents", "path": str(tmp_path)}
            ).status_code
            == 200
        )
        r = c.get("/api/knowledge/findings", params={"project": P}).json()
        assert r["by_kind"] == {"duplicate_content": 1} and r["findings"][0]["root"] == tmp_path.name
        assert c.get("/api/knowledge/findings").status_code == 422


# ---- the review of proposed links survives re-runs --------------------------------------------------------------------


def test_a_rejected_proposal_is_not_proposed_again_when_the_documents_are_read_again(repo: KnowledgeRepo):
    i = ing(repo)
    i.ingest_text(P, kind="story", filename="user_story.md", text=STORIES)
    i.ingest_text(
        P,
        kind="doc",
        filename="domain.md",
        text="Closed virtual accounts must stay visible for a year. The BRD is silent about archived ones. "
        * 2,
    )
    link = {
        "type": "flags_gap",
        "to": "US-001",
        "quote": "The BRD is silent about archived ones",
        "confidence": 0.9,
    }
    linking.run(repo, answers(domain=[link]), P)
    (edge,) = [e for e in repo.edges(P, limit=100) if e.type == "flags_gap"]
    assert edge.status == "proposed"
    assert decide(repo, edge.id, False, "reviewer", None) == "rejected"
    # the document changes and is read again, the model proposes the same link again: still rejected, nothing new
    i.ingest_text(
        P,
        kind="doc",
        filename="domain.md",
        text="Closed virtual accounts must stay visible for a year. The BRD is silent about archived ones. Also, more words here. "
        * 2,
    )
    rep = linking.run(repo, answers(domain=[link]), P)
    assert rep.sources >= 1 and rep.proposed == 0 and rep.already_linked == 1
    assert [(e.type, e.status) for e in repo.edges(P, limit=100) if e.type == "flags_gap"] == [
        ("flags_gap", "rejected")
    ]


def test_a_reset_of_the_extraction_keeps_what_a_person_decided(repo: KnowledgeRepo):
    ing_ = load_stories(repo)
    by_doc = {"BRD-03/US-003": good()}
    extract_run(repo, ing_, factory(by_doc), EXTRACT_P, ("story",))
    proposed = [
        e for e in repo.edges(EXTRACT_P, limit=100) if e.status == "proposed" and e.type == "constrains"
    ]
    assert len(proposed) == 1  # "Favorite limit constrains Favorite account"
    assert decide(repo, proposed[0].id, False, "reviewer", None) == "rejected"
    rep = extract_run(repo, ing_, factory(by_doc), EXTRACT_P, ("story",), reset=True)
    assert rep.removed > 0 and rep.restored == 1
    after = [
        (repo.get_source(e.src_id).external_id, e.type, e.status)
        for e in repo.edges(EXTRACT_P, limit=100)
        if e.type == "constrains"
    ]  # type: ignore[union-attr]
    assert after == [("rule:favorite limit", "constrains", "rejected")]  # not proposed anew
    again = extract_run(repo, ing_, factory(by_doc), EXTRACT_P, ("story",), reset=True)
    assert again.restored == 1  # and the decision survives a second reset
