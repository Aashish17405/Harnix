"""Links between documents that the text implies but does not name by id.

Ids written in a text are linked by ingestion (resolve_cited). This step reads each document with a model and an inventory
of the project's other items, and proposes the links the sentences imply: a story that cannot work without another story
(`depends_on`), two requirements that cannot both hold (`contradicts`), domain text that explains a requirement
(`clarifies`) or names an open question about it (`flags_gap`), a story that relies on a system (`uses`).

Nothing is trusted from the model, as in extract.py:
* the relationship must be in CATALOG and allowed between those two kinds, the target must be in the inventory that was
  shown, and the ontology must allow it (repo.upsert_edge checks again);
* the evidence must be an exact quote from the document (whitespace, case and Markdown marks aside);
* the confidence must be at least MIN_CONFIDENCE;
* a pair that a person rejected, or that a document already links (verified), is left alone: a rejection stands, and a
  declared or cited link is not second-guessed.
Every link is `proposed` (origin `extracted`) until a person promotes it; its evidence names the document, the quote and
the confidence.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

from harnix.knowledge.extract import normalize, parse_json, quote_found
from harnix.knowledge.ontology import OntologyViolation
from harnix.knowledge.repo import KnowledgeRepo
from harnix.models.base import Message, ModelProvider, TextBlock
from harnix.persistence.models import KnowledgeSource

MIN_CONFIDENCE = 0.7
MAX_INVENTORY = 120  # other items shown to the model with one document
MAX_LINKS = 20  # per document
CATALOG_VERSION = (
    1  # part of every cached answer's hash: change the catalog, bump it, documents are read again
)
ITEMS = ("brd", "requirement", "epic", "feature", "story")
CATALOG: dict[str, dict[str, Any]] = {
    "depends_on": {
        "from": ("story", "feature", "requirement", "epic"),
        "to": ("story", "feature", "requirement", "epic"),
        "about": "the source cannot be delivered or used without the target, although the text does not name the target's id",
    },
    "contradicts": {
        "from": (*ITEMS, "rule", "doc"),
        "to": (*ITEMS, "rule", "doc"),
        "about": "the two state requirements that cannot both hold as written",
    },
    "clarifies": {
        "from": ("doc", "design"),
        "to": (*ITEMS, "rule"),
        "about": "the source explains the meaning or intent of the target without changing it",
    },
    "flags_gap": {
        "from": ("doc", "rule", "requirement", "story"),
        "to": (*ITEMS, "rule"),
        "about": "the source names an unresolved decision, ambiguity or inconsistency that affects the target",
    },
    "uses": {
        "from": ("story", "feature", "requirement", "epic", "rule"),
        "to": ("system",),
        "about": "the source relies on, calls, receives data from or is constrained by this external system",
    },
}
SOURCE_KINDS = tuple(sorted({k for c in CATALOG.values() for k in c["from"] if k != "system"}))
SYSTEM = (
    "You find relationships between project documents. Return ONLY a JSON object, no prose. Be conservative: propose a "
    "link only when the document's own words imply it, never because the two items merely share a topic."
)
PROMPT = """Document {ext} (kind: {kind}):
<<<
{text}
>>>

Relationship types (the document is always the SOURCE):
{catalog}

Other items you may link TO (id | kind | title). Use an id from this list EXACTLY; link to nothing else:
{inventory}

Return {{"links":[{{"type":"depends_on","to":"<id from the list>","quote":"exact words copied from the document","confidence":0.0-1.0}}]}}
Every link MUST have a quote copied word for word from the document that supports it (no paraphrase, no ellipsis), and a
confidence between 0 and 1. If nothing is implied, return {{"links":[]}}."""


@dataclass
class Report:
    sources: int = 0
    skipped_unchanged: int = 0
    failed: list[str] = field(default_factory=list)
    proposed: int = 0
    already_linked: int = 0
    rejected: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {**self.__dict__}


def item_kind(repo: KnowledgeRepo, s: KnowledgeSource) -> str:
    """The catalog's name for a source: its kind, or `system` for a concept the documents call a system."""
    if s.kind == "concept":
        return "system" if repo.node_type(s) == "system" else "concept"
    return s.kind


def _tokens(s: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]{4,}", s.lower()))


def inventory(src: KnowledgeSource, text: str, pool: list[tuple[KnowledgeSource, str, set[str]]]) -> list[tuple[KnowledgeSource, str]]:  # fmt: skip
    """The items worth showing with this document: those whose title and opening share the most words with it (a
    project of hundreds of items must not cost a prompt of hundreds of lines). Systems come first: there are few."""
    mine = _tokens(text)
    ranked = sorted(
        ((k != "system", -len(mine & toks), s.external_id, s, k) for s, k, toks in pool if s.id != src.id),
        key=lambda x: x[:3],
    )
    return [(s, k) for *_, s, k in ranked[:MAX_INVENTORY]]


def _catalog_text(kind: str) -> str:
    return "\n".join(
        f"- {name}: {c['about']} (from: {', '.join(c['from'])}; to: {', '.join(c['to'])})"
        for name, c in CATALOG.items()
        if kind in c["from"]
    )


def validate(raw: dict[str, Any], kind: str, text: str, items: dict[str, tuple[KnowledgeSource, str]]) -> tuple[list[dict[str, Any]], dict[str, int]]:  # fmt: skip
    """The links of a model answer that pass every check, and why the others did not."""
    out: list[dict[str, Any]] = []
    why: dict[str, int] = {}

    def no(reason: str) -> None:
        why[reason] = why.get(reason, 0) + 1

    tnorm = normalize(text)
    for r in (raw.get("links") or [])[: MAX_LINKS * 2]:
        if not isinstance(r, dict):
            no("malformed")
            continue
        rtype, to, quote = (
            str(r.get("type", "")).lower(),
            str(r.get("to", "")).strip(),
            str(r.get("quote", "")),
        )
        try:
            conf = float(r.get("confidence"))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            no("no confidence")
            continue
        spec = CATALOG.get(rtype)
        if spec is None or kind not in spec["from"]:
            no("type not allowed for this document")
        elif to not in items:
            no("target not in the inventory")
        elif items[to][1] not in spec["to"]:
            no("target kind not allowed")
        elif not quote_found(tnorm, quote):
            no("quote not in the document")
        elif not conf >= MIN_CONFIDENCE:
            no("low confidence")
        elif not any(x["type"] == rtype and x["to"] == to for x in out) and len(out) < MAX_LINKS:
            out.append({"type": rtype, "to": to, "quote": quote.strip()[:400], "confidence": min(conf, 1.0)})
    return out, why


def link_one(provider: ModelProvider, src: KnowledgeSource, kind: str, text: str, shown: list[tuple[KnowledgeSource, str]], timeout: float = 120.0) -> tuple[list[dict[str, Any]], dict[str, int]]:  # fmt: skip
    inv = "\n".join(f"{s.external_id} | {k} | {s.title[:120]}" for s, k in shown) or "(none)"
    prompt = PROMPT.format(
        ext=src.external_id, kind=kind, text=text[:12000], catalog=_catalog_text(kind), inventory=inv
    )
    resp = provider.complete(
        system=SYSTEM, messages=[Message("user", [TextBlock(prompt)])], tools=[], max_tokens=3000, timeout=timeout, thinking="low",
    )  # fmt: skip
    return validate(parse_json(resp.text), kind, text, {s.external_id: (s, k) for s, k in shown})


def run(
    repo: KnowledgeRepo, provider_factory: Callable[[], ModelProvider], project: str, actor: str = "link", kinds: tuple[str, ...] = SOURCE_KINDS,
    limit: int = 200, workers: int = 4, on_event: Callable[[str, str, dict[str, Any]], None] | None = None,
) -> Report:  # fmt: skip
    """Read every current source of these kinds that was not read at its current text and inventory, and store the links
    that pass validation as proposals. The model calls run in parallel; results are stored one at a time."""
    rep = Report()

    started = time.monotonic()

    def say(msg: str, **data: Any) -> None:
        if on_event is not None:
            if data.get("done") and data.get(
                "total"
            ):  # live progress: time spent and an estimate of the time left
                spent = time.monotonic() - started
                data = {
                    **data,
                    "elapsed_s": round(spent),
                    "eta_s": round(spent / data["done"] * max(0, data["total"] - data["done"])),
                }
            on_event("infer", msg, data)

    sources = repo.sources(project, limit=100_000)
    targets = {t for c in CATALOG.values() for t in c["to"]}
    pool = [(s, k, _tokens(f"{s.title} {v.content[:600]}")) for s in sources if (k := item_kind(repo, s)) in targets and (v := repo.version(s.id)) is not None]  # fmt: skip
    done = {a.object_id: (a.detail or {}).get("hash") for a in reversed(repo.audit_log(project, limit=50000)) if a.action == "link.done"}  # fmt: skip
    blocked = {frozenset((e.src_id, e.dst_id)) for e in repo.edges(project, limit=1_000_000) if e.status in ("verified", "rejected")}  # fmt: skip
    todo: list[tuple[KnowledgeSource, str, str, str, list[tuple[KnowledgeSource, str]]]] = []
    for s in sources:
        v = repo.version(s.id)
        k = item_kind(repo, s)
        if v is None or k not in kinds or len(normalize(v.content)) < 60:
            continue
        shown = inventory(s, v.content, pool)
        h = hashlib.sha256(json.dumps([v.content_hash, CATALOG_VERSION, sorted(x.external_id for x, _ in shown)]).encode()).hexdigest()  # fmt: skip
        if done.get(s.id) == h:
            rep.skipped_unchanged += 1
        else:
            todo.append((s, k, v.content, h, shown))
    todo = todo[:limit]
    say(
        f"{len(todo)} documents to read ({rep.skipped_unchanged} unchanged since the last time)",
        total=len(todo),
        done=0,
    )

    def call(item: tuple[KnowledgeSource, str, str, str, list[tuple[KnowledgeSource, str]]]) -> Any:
        s, k, text, _, shown = item
        try:
            return link_one(provider_factory(), s, k, text, shown)
        except Exception as e:  # noqa: BLE001 - one document failing must not stop the rest
            return e

    with ThreadPoolExecutor(max_workers=max(1, workers)) as threads:
        for n, (item, res) in enumerate(zip(todo, threads.map(call, todo), strict=True), 1):
            s, k, _, h, shown = item
            rep.sources += 1
            if isinstance(res, Exception):
                rep.failed.append(f"{s.external_id}: {type(res).__name__}: {str(res)[:120]}")
                say(f"{s.external_id}: model call failed ({type(res).__name__})", done=n, total=len(todo))
                continue
            links, why = res
            by_id = {x.external_id: x for x, _ in shown}
            kept = 0
            for ln in links:
                dst = by_id[ln["to"]]
                if frozenset((s.id, dst.id)) in blocked:
                    rep.already_linked += 1
                    continue
                try:
                    _, created = repo.upsert_edge(project, s, dst, ln["type"], "proposed", "extracted", f"inferred from {s.external_id}: “{ln['quote'][:240]}” (confidence {ln['confidence']:.2f})")  # fmt: skip
                except OntologyViolation:
                    why["not allowed by the ontology"] = why.get("not allowed by the ontology", 0) + 1
                    continue
                kept += int(created)
                rep.already_linked += int(not created)
            rep.proposed += kept
            for reason, c in why.items():
                rep.rejected[reason] = rep.rejected.get(reason, 0) + c
            repo.audit(
                project,
                actor,
                "link.done",
                "source",
                s.id,
                {"hash": h, "proposed": kept, "rejected": sum(why.values())},
            )
            say(f"{s.external_id}: {kept} links proposed, {sum(why.values())} dropped", done=n, total=len(todo), source=s.external_id, links=[{"type": x["type"], "to": x["to"], "quote": x["quote"]} for x in links][:20])  # fmt: skip
    repo.audit(project, actor, "link.run", "project", project, rep.as_dict())
    return rep
