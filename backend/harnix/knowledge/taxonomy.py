"""Tag taxonomies: a document whose headings are categories and whose bullets or table rows are tags (a "meta tag" list).

`is_taxonomy` decides from the document's own declaration (front matter `taxonomy: true`) or its name; `parse` reads the
hierarchy. Ingestion turns each entry into a `tag` concept under its category and links every item whose text uses a leaf
tag's name (string match, so the link is verified like a glossary mention).
"""

from __future__ import annotations

import re
from typing import Any

from harnix.knowledge.ontology import norm

MIN_TAGS = 3  # fewer leaves than this is not a taxonomy, whatever its name says
_NAMED = re.compile(r"(?i)\b(?:meta[\s_-]*tags?|tag[\s_-]*(?:taxonomy|hierarchy|list|catalogu?e)|taxonomy)\b")
_HEAD = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")
_BULLET = re.compile(r"^(\s*)(?:[-*+]|\d+[.)])\s+(.*\S)\s*$")
_ROW = re.compile(r"^\s*\|.*\|\s*$")
_SEPARATOR = re.compile(r"^[\s|:\-]+$")
_NUMBERING = re.compile(r"^(?:\d+(?:\.\d+)*[.)]?|[A-Z][.)])\s+")
_CHEAP = re.compile(r"(?i)^(?:category|tag|name|description|definition|meaning)s?$")


def is_taxonomy(front: dict[str, Any], stem: str, title: str) -> bool:
    flag = front.get("taxonomy")
    return flag not in (None, False, "false", "False") or bool(_NAMED.search(f"{stem} {title}"))


def clean(s: str) -> str:
    return _NUMBERING.sub("", re.sub(r"[*_`]+", "", s)).strip(" \t:-–—")


def _split(raw: str) -> tuple[str, str]:
    """`Tag name - what it means` / `Tag name: what it means` -> (name, meaning)."""
    text = re.sub(r"[*_`]+", "", raw).strip()
    m = re.match(r"^(.{2,80}?)\s+[-–—]\s+(.+)$|^(.{2,80}?):\s+(.+)$", text)
    if m:
        return (m.group(1) or m.group(3)).strip(), (m.group(2) or m.group(4)).strip()
    return text, ""


def parse(body: str, title: str = "") -> list[dict[str, Any]]:
    """Every tag of the document: {"path": [parents...], "name", "description", "quote", "leaf"}. Categories (headings,
    parents of a nested bullet, the first column of a Category | Tag table) are entries too, with leaf False."""
    entries: dict[tuple[str, ...], dict[str, Any]] = {}
    heads: list[tuple[int, str]] = []
    bullets: list[tuple[int, str]] = []
    header: list[str] | None = None  # the header row of the table being read
    last_category = ""
    skip_title = norm(title)

    def add(path: list[str], name: str, desc: str, quote: str) -> None:
        key = tuple(norm(x) for x in [*path, name])
        if name and norm(name) and key not in entries:
            entries[key] = {
                "path": list(path),
                "name": name,
                "description": desc,
                "quote": quote[:300],
                "leaf": True,
            }
            if len(key) > 1 and key[:-1] in entries:
                entries[key[:-1]]["leaf"] = False

    for line in body.splitlines():
        if not _ROW.match(line):
            header = None
        if m := _HEAD.match(line):
            level, name = len(m.group(1)), clean(m.group(2))
            if not name or (level == 1 and norm(name) == skip_title):
                continue
            heads = [x for x in heads if x[0] < level] + [(level, name)]
            bullets = []
            add([n for _, n in heads[:-1]], name, "", line.strip())
        elif m := _BULLET.match(line):
            indent = len(m.group(1))
            while bullets and bullets[-1][0] >= indent:
                bullets.pop()
            name, desc = _split(m.group(2))
            if name:
                add([n for _, n in heads] + [n for _, n in bullets], name, desc, line.strip())
                bullets.append((indent, name))
        elif _ROW.match(line) and not _SEPARATOR.match(line):
            cells = [clean(c) for c in line.strip().strip("|").split("|")]
            if header is None:
                header = [c.lower() for c in cells]
                if all(_CHEAP.match(c) or not c for c in cells):
                    continue  # the header row
                header = []  # no header: the first column is the tag
            base = [n for _, n in heads]
            if len(header) >= 2 and "categor" in header[0] and "tag" in header[1]:
                last_category = cells[0] or last_category
                if last_category:
                    add(base, last_category, "", line.strip())
                add([*base, last_category] if last_category else base, cells[1], cells[2] if len(cells) > 2 else "", line.strip())  # fmt: skip
            elif cells and cells[0]:
                add(base, cells[0], cells[1] if len(cells) > 1 else "", line.strip())
    out = list(entries.values())
    return out if sum(1 for e in out if e["leaf"]) >= MIN_TAGS else []
