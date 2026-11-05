"""Data-field tables of a requirements document: `| # | Field Name | Data Type | Display/Input | Mandatory | Processing ... |`.

Each row is a fact a coding agent needs exactly (a field, its type, whether it is mandatory, how it is handled), so ingestion
keeps every row as a `field` concept of its section, with the other columns as attributes. The section mentions the field
(verified: its own table row). Rows that only name a group ("Account List") label the rows after them.
"""

from __future__ import annotations

import re
from typing import Any

_ROW = re.compile(r"^\s*\|.*\|\s*$")
_SEPARATOR = re.compile(r"^[\s|:\-]+$")
_FIELD_HEADER = re.compile(r"(?i)^(?:data\s+)?(?:field|attribute|element)(?:\s+name)?$|^field\s*/\s*\w+$")
_NUMBERING = re.compile(r"(?i)^#+$|^(?:s\.?\s*)?no\.?$|^sr\.?\s*no\.?$")
_MARKS = re.compile(r"\*+|`|\\(?=[#*_\-.|\[\]()])")
MAX_FIELDS = 200  # per section


def cells(line: str) -> list[str]:
    inner = line.strip()[1:-1]
    return [" ".join(_MARKS.sub("", c).split()) for c in re.split(r"(?<!\\)\|", inner)]


def field_rows(text: str) -> list[dict[str, Any]]:
    """Every row of every field table in the text: {"name", "group", "attributes": {header: value}, "quote"}."""
    out: list[dict[str, Any]] = []
    header: list[str] | None = None
    column = -1
    group = ""
    for line in text.splitlines():
        if not _ROW.match(line):
            header, column, group = None, -1, ""
            continue
        if _SEPARATOR.match(line):
            continue
        row = cells(line)
        if header is None:
            header = row
            column = next((i for i, h in enumerate(row) if _FIELD_HEADER.match(h.strip())), -1)
            continue
        if column < 0 or column >= len(row) or not row[column]:
            continue
        others = [c for i, c in enumerate(row) if i != column and c and not re.fullmatch(r"\d+", c)]
        if not others and "**" in line:  # a bold name alone in its row: a group label
            group = row[column]
            continue
        attrs = {
            header[i]: row[i]
            for i in range(min(len(header), len(row)))
            if i != column and header[i] and row[i] and not _NUMBERING.match(header[i].strip())
        }
        out.append(
            {
                "name": row[column][:120],
                "group": group,
                "attributes": attrs,
                "quote": " ".join(line.split())[:400],
            }
        )
        if len(out) >= MAX_FIELDS:
            break
    return out
