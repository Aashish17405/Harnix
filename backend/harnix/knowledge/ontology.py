"""The ontology (ontology.json): node types with an is-a hierarchy, which link types may join which types, synonyms.

* Every link written to the knowledge base is checked here (repo.upsert_edge raises OntologyViolation), whoever writes
  it: a document's declared links, intake suggestions, agent proposals, extraction.
* Extraction names go through `canonical()`, so an alias and its term become one node. Besides the synonyms in the
  file, each project's BRD glossaries are read: "Virtual Account (VA)" makes "va" an alias of "virtual account", and an
  acronym defined in a few words ("CBS | Core Banking System") an alias of its expansion.

It is deliberately small: types and relations only (no OWL reasoning), and synonyms are exact aliases, not similarity.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

FILE = Path(__file__).with_name("ontology.json")
DOCUMENT_KINDS_PARENT = "document"
_ROW = re.compile(r"^\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|", re.M)
_ACRONYM = re.compile(r"^(.+?)\s*\(([A-Z][A-Za-z0-9&/-]{1,9})\)$")


class OntologyViolation(ValueError):
    pass


def norm(name: str) -> str:
    """Lower case, letters/digits/spaces, a simple plural removed: the identity of a name (as extraction keys)."""
    n = re.sub(r"\([^)]*\)", " ", name.lower())
    n = re.sub(r"[^a-z0-9 /&-]", "", re.sub(r"[*_`]", "", n))
    words = n.split()
    if words and len(words[-1]) > 3 and words[-1].endswith("s") and not words[-1].endswith("ss"):
        words[-1] = words[-1][:-1]
    return " ".join(words)[:90]


@dataclass
class Ontology:
    types: dict[str, str | None]
    relations: dict[str, dict[str, Any]]
    synonyms: dict[str, str] = field(default_factory=dict)  # alias key -> canonical key
    terms: list[str] = field(default_factory=list)  # the project's glossary terms, as written ("Root VA")

    def ancestors(self, t: str) -> list[str]:
        out, seen = [], set()
        while t and t not in seen:
            seen.add(t)
            out.append(t)
            t = self.types.get(t) or ""
        return out

    def allowed(self, rel: str, src_type: str, dst_type: str) -> bool:
        spec = self.relations.get(rel)
        if spec is None:
            return False
        return bool(set(self.ancestors(src_type)) & set(spec["from"])) and bool(set(self.ancestors(dst_type)) & set(spec["to"]))  # fmt: skip

    def check(self, rel: str, src_type: str, dst_type: str) -> None:
        if not self.allowed(rel, src_type, dst_type):
            spec = self.relations.get(rel)
            want = f"{'|'.join(spec['from'])} -> {'|'.join(spec['to'])}" if spec else "an unknown relation"
            raise OntologyViolation(
                f"'{src_type} {rel} {dst_type}' is not allowed by the ontology ({rel}: {want})"
            )

    def canonical(self, key: str) -> str:
        return self.synonyms.get(key, key)

    def with_synonyms(self, more: dict[str, str]) -> Ontology:
        merged = {**more, **self.synonyms}  # the file wins over glossaries
        return Ontology(
            self.types, self.relations, {a: merged.get(c, c) for a, c in merged.items() if a != c}, self.terms
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "types": self.types,
            "relations": self.relations,
            "synonyms": self.synonyms,
            "terms": self.terms,
        }


@lru_cache(maxsize=1)
def default() -> Ontology:
    data = json.loads(FILE.read_text(encoding="utf-8"))
    syn = {norm(a): norm(c) for c, aliases in data.get("synonyms", {}).items() for a in aliases}
    return Ontology(data["types"], data["relations"], syn)


_PARA = re.compile(r"^([^.|#*\d-][^.\n]{1,79}?)\.\s+(\S.{15,})$")  # "Term (T). A definition sentence ..."
_SECTION_END = re.compile(
    r"^(#|Part\s+[IVX\d]+\b|\d+\.\s)"
)  # the next heading ends a glossary written as paragraphs


def _paragraph_rows(text: str) -> list[tuple[str, str]]:
    """Glossary entries written as one paragraph each ("virtual account (VA). A unique logical identifier ..."), read
    from the line after the "glossary" heading up to the next heading. Tables are handled by the caller."""
    i = text.lower().find("glossary")
    if i < 0:
        return []
    rows: list[tuple[str, str]] = []
    for line in text[text.find("\n", i) + 1 :].split("\n"):
        line = line.strip()
        if not line:
            continue
        if _SECTION_END.match(line):
            break
        if m := _PARA.match(line):
            rows.append((m.group(1).strip(), line))
    return rows


def _glossary_rows(texts: list[str]) -> list[tuple[str, str]]:
    rows = []
    for text in texts:
        i = text.lower().find("glossary")
        if i < 0:
            continue
        for term, definition in _ROW.findall(text[i : i + 6000]):
            term, definition = term.strip(), definition.strip()
            if term.lower() not in ("term", "---") and not set(term) <= set("-: "):
                rows.append((term, definition))
        rows += _paragraph_rows(text)
    return rows


def domain_terms(text: str) -> list[tuple[str, str, str]]:
    """(name, description, quote) of every glossary entry of a document, one per name: "Root VA / Parent VA / Child VA"
    gives three, "virtual account (VA)" is named "virtual account". The quote is the glossary line itself."""
    out: dict[str, tuple[str, str, str]] = {}
    for term, line in _paragraph_rows(text) + [(t, f"{t}. {d}") for t, d in _table_rows(text)]:
        desc = line[len(term) :].lstrip(". ").strip()
        for part in re.split(r"\s+/\s+", term):
            m = _ACRONYM.match(part.strip())
            name = (m.group(1) if m else part).strip()
            if len(name) >= 2:
                out.setdefault(norm(name), (name, desc[:300], line[:400]))
    return list(out.values())


def _table_rows(text: str) -> list[tuple[str, str]]:
    i = text.lower().find("glossary")
    if i < 0:
        return []
    return [(t.strip(), d.strip()) for t, d in _ROW.findall(text[i : i + 6000]) if t.strip().lower() not in ("term", "---") and not set(t.strip()) <= set("-: ")]  # fmt: skip


def glossary_terms(texts: list[str]) -> list[str]:
    """The terms the glossaries define, as written ("Root VA", "Virtual Account"), each once."""
    out: dict[str, str] = {}
    for term, _ in _glossary_rows(texts):
        m = _ACRONYM.match(term)
        name = m.group(1) if m else term
        out.setdefault(norm(name), name)
    return list(out.values())


def glossary_synonyms(texts: list[str]) -> dict[str, str]:
    """Aliases from Markdown glossary tables (| Term | Definition |) in BRDs."""
    out: dict[str, str] = {}
    for text in [t for t in texts if t]:
        i = text.lower().find("glossary")
        if i < 0:
            continue
        for term, definition in [*_table_rows(text), *_paragraph_rows(text)]:
            m = _ACRONYM.match(term)
            if m:  # "Virtual Account (VA)"
                out[norm(m.group(2))] = norm(m.group(1))
            elif (
                term.isupper() and len(term) <= 8 and 1 <= len(definition.split()) <= 5
            ):  # "CBS | Core Banking System"
                out[norm(term)] = norm(definition)
    return {a: c for a, c in out.items() if a and c and a != c}


def node_type(kind: str, meta: dict[str, Any] | None) -> str:
    """The ontology type of a stored source: its kind, or for extracted concepts/rules their concept_type."""
    if kind in ("concept", "rule"):
        return str((meta or {}).get("concept_type") or kind)
    return kind


def for_project(repo: Any, project: str) -> Ontology:
    """The default ontology plus the synonyms the project's BRD glossaries define."""
    texts = []
    for kind in ("brd", "doc"):  # a domain note carries a glossary too
        for s in repo.sources(project, kind=kind, limit=1000):
            v = repo.version(s.id)
            if v is not None:
                texts.append(v.content)
    o = default().with_synonyms(glossary_synonyms(texts))
    o.terms = glossary_terms(texts)
    return o
