"""Renderings of a backlog: the same stories as a CSV or XLSX export or as `.jsonl` units.

A rendering adds nothing the backlog JSON does not say, except columns only a tracker knows (status, sprint, story points,
labels, issue type). So ingestion:
* reads the rendering (stdlib only: `csv`, `zipfile`, `xml.etree`);
* attaches those tracker columns to the item of the row (`extras`), never to a parent: a story row's issue type is the
  story's, not its feature's;
* checks every other cell against the text already stored for the project and lists the cells that are not there
  (`compare`): a rendering that disagrees with the backlog is a finding.
"""

from __future__ import annotations

import csv
import json
import re
import zipfile
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

from harnix.knowledge.sections import plain as norm

SUFFIXES = (".csv", ".xlsx", ".jsonl")
MAX_UNCOMPRESSED = 50_000_000  # an xlsx is a zip: refuse one that expands to more than this
MAX_ROWS = 20_000
ID_COLUMNS = (
    "story id",
    "feature id",
    "epic id",
    "id",
    "key",
)  # in this order: the row's own item is the narrowest
EXTRA_COLUMNS = {
    "labels": "labels", "jira labels": "labels", "issue type": "issue_type", "status": "status", "sprint": "sprint",
    "story points": "story_points",
}  # fmt: skip
EXTRA_KEYS = {"labels": "labels", "jira_labels": "labels", "issue_type": "issue_type", "jira_issue_type": "issue_type", "status": "status", "backlog_status": "status", "sprint": "sprint", "story_points": "story_points"}  # fmt: skip
_QUOTES = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-", " ": " "})


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _column(ref: str) -> int:
    m = re.match(r"[A-Za-z]+", ref)
    n = 0
    for ch in m.group(0).upper() if m else "A":
        n = n * 26 + ord(ch) - 64
    return n - 1


def read_csv(path: Path) -> list[list[str]]:
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    return [r for _, r in zip(range(MAX_ROWS), csv.reader(text.splitlines(keepends=True)), strict=False)]


def read_xlsx(path: Path) -> dict[str, list[list[str]]]:
    """{sheet name: rows}. Cells are placed by their reference (C5), so an omitted empty cell does not shift the row."""
    with zipfile.ZipFile(path) as z:
        if sum(i.file_size for i in z.infolist()) > MAX_UNCOMPRESSED:
            raise ValueError("the workbook expands to more than 50 MB")
        shared: list[str] = []
        if "xl/sharedStrings.xml" in z.namelist():
            for si in ElementTree.fromstring(z.read("xl/sharedStrings.xml")).iter():
                if _local(si.tag) == "si":
                    shared.append("".join(t.text or "" for t in si.iter() if _local(t.tag) == "t"))
        rels = {
            r.get("Id", ""): r.get("Target", "")
            for r in ElementTree.fromstring(z.read("xl/_rels/workbook.xml.rels")).iter()
            if _local(r.tag) == "Relationship"
        }
        out: dict[str, list[list[str]]] = {}
        for sheet in ElementTree.fromstring(z.read("xl/workbook.xml")).iter():
            if _local(sheet.tag) != "sheet":
                continue
            rid = next((v for k, v in sheet.attrib.items() if _local(k) == "id"), "")
            target = rels.get(rid, "").lstrip("/").removeprefix("xl/")
            if not target or f"xl/{target}" not in z.namelist():
                continue
            rows: list[list[str]] = []
            for row in ElementTree.fromstring(z.read(f"xl/{target}")).iter():
                if _local(row.tag) != "row":
                    continue
                cells: dict[int, str] = {}
                for n, c in enumerate(x for x in row if _local(x.tag) == "c"):
                    col = _column(c.get("r", "")) if c.get("r") else n
                    kind = c.get("t")
                    v = next((x.text or "" for x in c if _local(x.tag) == "v"), "")
                    if kind == "s" and v.isdigit() and int(v) < len(shared):
                        v = shared[int(v)]
                    elif kind == "inlineStr":
                        v = "".join(t.text or "" for t in c.iter() if _local(t.tag) == "t")
                    cells[col] = v
                rows.append([cells.get(i, "") for i in range(max(cells) + 1)] if cells else [])
                if len(rows) >= MAX_ROWS:
                    break
            out[str(sheet.get("name", "sheet"))] = rows
        return out


def read_units(path: Path) -> list[dict[str, Any]]:
    out = []
    for line in path.read_text(encoding="utf-8-sig", errors="replace").splitlines()[:MAX_ROWS]:
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if isinstance(rec, dict):
            out.append(rec)
    return out


def read(path: Path) -> list[dict[str, Any]]:
    """Every record of a rendering as {"id", "cells": {header: text}, "sheet"}; `id` is the row's own item (None when it has none)."""
    suffix = path.suffix.lower()
    if suffix == ".jsonl":
        recs = read_units(path)
        return [{"id": str(r.get("story_id") or r.get("id") or "") or None, "sheet": "", "cells": {k: _text(v) for k, v in r.items() if _text(v)}} for r in recs]  # fmt: skip
    sheets = {"": read_csv(path)} if suffix == ".csv" else read_xlsx(path)
    out: list[dict[str, Any]] = []
    for sheet, rows in sheets.items():
        rows = [r for r in rows if any(c.strip() for c in r)]
        if not rows:
            continue
        header = [" ".join(h.split("\n")[0].lower().split()) for h in rows[0]]
        id_cols = [header.index(h) for h in ID_COLUMNS if h in header]
        for r in rows[1:]:
            cells = {
                header[i]: r[i].strip() for i in range(min(len(header), len(r))) if header[i] and r[i].strip()
            }
            rid = next((r[i].strip() for i in id_cols if i < len(r) and r[i].strip()), None)
            out.append({"id": rid, "sheet": sheet, "cells": cells})
    return out


def _text(v: Any) -> str:
    if isinstance(v, (list, tuple)):
        return "; ".join(_text(x) for x in v)
    return "" if v in (None, "", {}) else str(v)


def extras(records: list[dict[str, Any]]) -> dict[str, dict[str, str]]:
    """{item id: {labels, issue_type, status, sprint, story_points}} from the tracker columns (or keys) of the records."""
    out: dict[str, dict[str, str]] = {}
    for r in records:
        if not r["id"]:
            continue
        for key, val in r["cells"].items():
            name = EXTRA_COLUMNS.get(key) or EXTRA_KEYS.get(key)
            if name and val:
                out.setdefault(r["id"], {})[name] = val
    return out


def _known(cell: str, corpus: str) -> bool:
    text = cell.translate(_QUOTES)
    parts = [p for p in re.split(r"\n+| \| |(?=\bScenario:)", text) if norm(p)]
    parts = [re.sub(r"(\.\.\.|…)$", "", p).strip() if len(norm(p)) >= 11 else p for p in parts]
    return all(norm(p) in corpus or re.fullmatch(r"[\d.,\s|]*", p) for p in parts)


def compare(records: list[dict[str, Any]], corpus: str) -> dict[str, Any]:
    """How the rendering's cells stand against the stored text (`corpus` = every stored text, normalised by coverage.norm)."""
    cells = matched = 0
    differences: list[str] = []
    for n, r in enumerate(records, 2):
        for header, value in r["cells"].items():
            if header in EXTRA_COLUMNS or header in EXTRA_KEYS or header in ID_COLUMNS or header.endswith("count") or header == "attribute":  # fmt: skip
                continue
            cells += 1
            if _known(value, corpus):
                matched += 1
            else:
                differences.append(f"row {n} '{header}': {value[:120]}")
    return {"rows": len(records), "cells": cells, "matched": matched, "differences": differences}
