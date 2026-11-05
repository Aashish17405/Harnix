"""Quality / review reports and dependency graphs.

* `dag_edges` reads the Mermaid edges of a dependency-graph document (`US-001 --> US-002`, `-.->` dotted = provisional,
  `==>` thick): the left item has to exist before the right one, so ingestion stores `US-002 depends_on US-001` (verified:
  the document draws it).
* `parse_report` reads a gate or review report: its date, a one-line summary, the items it names and the items on lines that
  report a finding (WARN, BLOCK, gap, failed, not ready). Ingestion proposes `flags_gap` from the report to each flagged item
  (a proposal: a line that says "failed" and names two items does not say which one failed).
"""

from __future__ import annotations

import re
from typing import Any

from harnix.knowledge.sections import ID

_MERMAID = re.compile(rf"({ID})(?:\[[^\]\n]*\])?\s*(-\.->|-->|==>)\s*(?:\|[^|\n]*\|\s*)?({ID})")
_NAMED = re.compile(r"(?i)\b(?:gate|review)[\s_-]*report\b")
_FINDING = re.compile(r"\b(WARN(?:ING)?|BLOCK(?:ED)?|gap|GAP|Gap|Failed|failed|Not ready)\b")
_NONE = re.compile(r"(?i)\b(?:0|no|zero|without)\s+(?:failed|failures?|gaps?|warnings?|blockers?)\b")
_ID_IN_TEXT = re.compile(rf"(?<![\w/.-])({ID})(?![\w-])")
_DATE = (
    re.compile(r"\*\*Review date:\*\*\s*(\d{4}-\d{2}-\d{2})"),
    re.compile(r"Run date:\s*(\d{4}-\d{2}-\d{2})"),
    re.compile(r"(?<!\d)(\d{4})-?(\d{2})-?(\d{2})(?!\d)"),
)


def dag_edges(text: str) -> list[tuple[str, str, bool]]:
    """(before, after, firm) for every Mermaid edge: `after` cannot start until `before` is done. Dotted arrows are not firm."""
    return [(m.group(1), m.group(3), m.group(2) != "-.->") for m in _MERMAID.finditer(text)]


def is_report(stem: str, front: dict[str, Any]) -> bool:
    return str(front.get("kind", "")).lower() == "report" or bool(_NAMED.search(stem.replace("_", "-")))


def parse_report(text: str, name: str = "") -> dict[str, Any]:
    """{"kind", "date", "summary", "mentioned": [ids], "flagged": [{"id", "quote"}]} of a report's text."""
    lines = text.lstrip("﻿").splitlines()
    date = ""
    for i, pat in enumerate(_DATE):
        m = pat.search(text) or (pat.search(name) if i == 2 else None)
        if m:
            date = m.group(1) if i < 2 else f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
            break
    summary = ""
    if m := re.search(r"\*\*Overall result:\*\*\s*(.+)", text):
        summary = m.group(1).strip()
    else:  # the first sentence under "Executive summary"; nothing when the report has none (not its own title)
        start = next((i for i, ln in enumerate(lines) if re.match(r"(?i)^#+\s*executive summary", ln)), None)
        if start is not None:
            summary = next((ln.strip() for ln in lines[start + 1 :] if ln.strip() and not ln.lstrip().startswith(("|", "#"))), "")  # fmt: skip
    mentioned: dict[str, None] = {}
    flagged: dict[str, str] = {}
    for ln in lines:
        ids = [m.group(1) for m in _ID_IN_TEXT.finditer(ln)]
        for i in ids:
            mentioned.setdefault(i)
        if ids and _FINDING.search(ln) and not _NONE.search(ln):
            for i in ids:
                flagged.setdefault(i, ln.strip()[:300])
    kind = "quality_gate" if re.search(r"(?i)gate", name) else "review"
    return {"kind": kind, "date": date, "summary": summary[:300], "mentioned": list(mentioned), "flagged": [{"id": i, "quote": q} for i, q in flagged.items()]}  # fmt: skip
