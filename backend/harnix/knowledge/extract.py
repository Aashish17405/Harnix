"""The content graph: concepts, actors, fields and rules that the documents themselves state, with the relationships
between them.

A model reads one source at a time and proposes entities and relations, each with an EXACT QUOTE from the text. Nothing is
kept whose quote cannot be found in that text (whitespace, case and Markdown marks aside): a model cannot add a fact the
document does not contain, and every node and edge points at the sentence it came from.

Trust levels, as everywhere in the knowledge base:
* "source mentions concept" is verified when the concept's name appears verbatim in the source (a string match, not a judgement);
* every relationship BETWEEN concepts is only proposed (origin `extracted`) until a person confirms it.
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

from harnix.knowledge.ingest import Ingestor, IngestResult, merge_evidence
from harnix.knowledge.ontology import Ontology, OntologyViolation, default, for_project, norm
from harnix.knowledge.repo import KnowledgeRepo
from harnix.models.base import Message, ModelProvider, TextBlock
from harnix.persistence.models import KnowledgeSource

log = logging.getLogger(__name__)
ENTITY_TYPES = ("concept", "actor", "rule", "constraint", "field", "event", "system", "domain")
RELATION_TYPES = {
    "constrains",
    "part_of",
    "performed_by",
    "triggers",
    "uses",
    "depends_on",
    "relates_to",
    "requires",
}
RELATION_ALIAS = {"requires": "depends_on"}
RULE_TYPES = ("rule", "constraint")
MAX_ENTITIES, MAX_RELATIONS, MAX_EVIDENCE = 25, 40, 20
MAX_KNOWN = 200  # names of the project vocabulary shown to the model
SOURCE_KINDS = ("story", "brd", "epic", "feature", "requirement", "design", "doc")

SYSTEM = (
    "You extract a knowledge graph from ONE business document (a BRD section, epic, feature or user story). "
    "Return ONLY a JSON object, no prose. Be conservative: include only what the text states, never what you assume."
)
PROMPT = """Extract from the document below.

entities: the domain things it talks about. type is one of:
- concept (a domain term or object, e.g. Root VA, Statement), actor (a user role or party), rule (a business rule), constraint (a limit or condition with a number, length, period or format), field (a data field), event (something that happens), system (an external system or service), domain (domain knowledge: an industry or regulatory term, standard, product or business notion that needs domain expertise to understand, e.g. KYC, IBAN, settlement cut-off; use concept for the product's own objects. A requirement, limit, value or behaviour that this programme's BRD specifies is project context, never domain).
relations: how two of YOUR entities relate. type is one of:
- constrains (a rule or constraint -> what it limits), part_of (concept -> larger concept), performed_by (concept/event/rule -> actor), triggers (concept -> event or concept), uses (concept -> concept), depends_on, relates_to (any two).
Use the entity type that fits: a relation whose types do not fit the list above is discarded.

Every entity and every relation MUST have a "quote": the exact words copied from the document that support it (a phrase or sentence, no paraphrase, no ellipsis).
Entity names are short noun phrases (2-4 words, sentence case, singular). Do not invent entities that have no quote.

JSON shape:
{"entities":[{"name":"Favorite account","type":"concept","description":"one short sentence","quote":"exact words"}],
 "relations":[{"from":"entity name","type":"constrains","to":"entity name","quote":"exact words"}]}

{known}Document ({ext}):
<<<
{text}
>>>"""


def normalize(s: str) -> str:
    """Lower case, no Markdown marks or quotes, single spaces: how quotes and texts are compared."""
    s = (
        re.sub(r"[#>|]", " ", re.sub(r"[*_`]", "", s.lower()))
        .replace("’", "'")
        .replace("“", '"')
        .replace("”", '"')
    )
    return re.sub(r"\s+", " ", s).strip(" \t.,;:\"'")


def quote_found(text_norm: str, quote: str) -> bool:
    q = normalize(quote)
    return len(q) >= 8 and q in text_norm


def key_of(name: str) -> str:
    """The identity of an entity across documents: normalized, a "(VA)" style aside and a simple plural removed.
    Synonyms are applied on top of this (Ontology.canonical)."""
    return norm(normalize(name))


def parse_json(text: str) -> dict[str, Any]:
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("no JSON object in the answer")
    data = json.loads(text[start : end + 1])
    if not isinstance(data, dict):
        raise ValueError("the answer is not an object")
    return data


@dataclass
class Extraction:
    entities: list[dict[str, Any]] = field(default_factory=list)  # validated
    relations: list[dict[str, Any]] = field(default_factory=list)  # validated, names are keys of entities
    rejected: dict[str, int] = field(default_factory=lambda: {"entity": 0, "relation": 0})


def about_project(text: str, quote: str) -> bool:
    """Is the quoted line one that talks about this programme's BRD? (domain notes keep their general part and their
    BRD commentary apart; the commentary is project context.)"""
    q = normalize(quote)
    # ponytail: a keyword cue on the quoted line; a heading-aware split of the document is the upgrade if it misfires
    return any(re.search(r"\bBRD\b", line) for line in text.split("\n") if q in normalize(line))


def validate(raw: dict[str, Any], text: str, onto: Ontology | None = None) -> Extraction:
    """Keep only entities and relations whose quote is in the text, whose names resolve and whose relation the ontology
    allows between the two entity types. Names go through the ontology's synonyms. Counts what was dropped."""
    onto = onto or default()
    out = Extraction()
    types: dict[str, str] = {}
    tnorm = normalize(text)
    seen: set[str] = set()
    for e in (raw.get("entities") or [])[: MAX_ENTITIES * 2]:
        if not isinstance(e, dict):
            out.rejected["entity"] += 1
            continue
        name, etype, quote = (
            str(e.get("name", "")).strip(),
            str(e.get("type", "")).strip().lower(),
            str(e.get("quote", "")),
        )
        k = onto.canonical(key_of(name))
        if not (2 <= len(name) <= 80) or etype not in ENTITY_TYPES or not k or not quote_found(tnorm, quote):
            out.rejected["entity"] += 1
            continue
        if etype == "domain" and about_project(text, quote):
            etype = "concept"  # said inside a passage about this programme's BRD: project context, not domain knowledge
        if k in seen or len(out.entities) >= MAX_ENTITIES:
            continue
        seen.add(k)
        types[k] = etype
        out.entities.append({"name": name, "key": k, "type": etype, "description": str(e.get("description", ""))[:300], "quote": quote.strip()[:400]})  # fmt: skip
    for r in (raw.get("relations") or [])[: MAX_RELATIONS * 2]:
        if not isinstance(r, dict):
            out.rejected["relation"] += 1
            continue
        rtype = RELATION_ALIAS.get(str(r.get("type", "")).lower(), str(r.get("type", "")).lower())
        a, b = onto.canonical(key_of(str(r.get("from", "")))), onto.canonical(key_of(str(r.get("to", ""))))
        quote = str(r.get("quote", ""))
        if (
            rtype not in RELATION_TYPES
            or a not in seen
            or b not in seen
            or a == b
            or not onto.allowed(rtype, types[a], types[b])
            or not quote_found(tnorm, quote)
        ):
            out.rejected["relation"] += 1
            continue
        if len(out.relations) < MAX_RELATIONS and not any(
            x["from"] == a and x["to"] == b and x["type"] == rtype for x in out.relations
        ):
            out.relations.append({"from": a, "to": b, "type": rtype, "quote": quote.strip()[:400]})
    return out


def extract_one(provider: ModelProvider, ext: str, text: str, timeout: float = 120.0, onto: Ontology | None = None, known: list[str] | None = None) -> Extraction:  # fmt: skip
    vocab = ""
    if known:  # one vocabulary per project: reuse a known name when the document means the same thing
        vocab = (
            "Names already used in this project (reuse one EXACTLY when the document means the same thing; "
            "otherwise make a new one):\n" + "; ".join(known[:MAX_KNOWN]) + "\n\n"
        )
    prompt = PROMPT.replace("{known}", vocab).replace("{ext}", ext).replace("{text}", text[:12000])
    resp = provider.complete(
        system=SYSTEM, messages=[Message("user", [TextBlock(prompt)])],
        tools=[], max_tokens=4000, timeout=timeout, thinking="low",
    )  # fmt: skip
    return validate(parse_json(resp.text), text, onto)


def _concept_text(name: str, etype: str, description: str, evidence: list[dict[str, str]]) -> str:
    lines = [f"# {name}", f"Type: {etype}", description or "", "", "Stated in:"]
    lines += [f"- {e['source']}: “{e['quote']}”" for e in evidence]
    return "\n".join(lines).strip() + "\n"


@dataclass
class Report:
    sources: int = 0
    skipped_unchanged: int = 0
    failed: list[str] = field(default_factory=list)
    entities: int = 0
    relations: int = 0
    mentions: int = 0
    rejected_entities: int = 0
    rejected_relations: int = 0
    removed: int = 0  # extracted concepts/rules deleted first (reset)
    restored: int = 0  # decisions a person made on their links, put back after a reset

    def as_dict(self) -> dict[str, Any]:
        return {**self.__dict__}


def persist(
    ing: Ingestor, project: str, src: KnowledgeSource, text: str, ex: Extraction, rep: Report
) -> None:
    """Store the validated entities as concept/rule sources (evidence merged across documents) and their edges."""
    repo = ing.repo
    tnorm = normalize(text)
    made: dict[str, KnowledgeSource] = {}
    for e in ex.entities:
        kind = "rule" if e["type"] in RULE_TYPES else "concept"
        ext = f"{kind}:{e['key']}"
        prev = repo.find_source(project, kind, ext)
        old = repo.version(prev.id) if prev else None
        meta = dict((old.meta if old else None) or {})
        evidence = merge_evidence(
            meta.get("evidence", []), src.external_id, e["quote"], MAX_EVIDENCE
        )  # this document's quote is replaced in place
        ctype = (
            "domain" if meta.get("concept_type") == "domain" else e["type"]
        )  # a glossary term stays domain knowledge
        meta.update({"concept_type": ctype, "name": e["name"], "evidence": evidence, "extracted": True})
        desc = meta.get("description") or e["description"]
        meta["description"] = desc
        ing.add(project, kind, ext, e["name"], _concept_text(e["name"], ctype, desc, evidence), origin="extracted", meta=meta, res=None, defer=True)  # fmt: skip
        rep.entities += 1
    ing.flush_embeddings(IngestResult())
    for e in ex.entities:
        kind = "rule" if e["type"] in RULE_TYPES else "concept"
        c = repo.find_source(project, kind, f"{kind}:{e['key']}")
        if c is None:
            continue
        made[e["key"]] = c
        verbatim = key_of(e["name"]) in re.sub(r"[^a-z0-9 /&-]", "", tnorm) or normalize(e["name"]) in tnorm
        _, created = repo.upsert_edge(project, src, c, "mentions", "verified" if verbatim else "proposed", "source" if verbatim else "extracted", e["quote"][:300])  # fmt: skip
        rep.mentions += int(created)
    for r in ex.relations:
        a, b = made.get(r["from"]), made.get(r["to"])
        if a and b:
            try:
                _, created = repo.upsert_edge(project, a, b, r["type"], "proposed", "extracted", f"{src.external_id}: “{r['quote'][:260]}”")  # fmt: skip
            except OntologyViolation:  # the stored node has another type than this document gave it
                rep.rejected_relations += 1
                continue
            rep.relations += int(created)


def _decisions(repo: KnowledgeRepo, project: str) -> list[dict[str, str]]:
    """What a person decided about links that touch extracted concepts/rules (a rejection, a promotion): a reset deletes
    those concepts and their links and reads the documents again, and must not forget the decisions. Mentions the model's
    reading re-creates by a string match (origin `source`, verified) are not decisions."""
    by_id = {s.id: s for s in repo.sources(project, limit=100_000)}
    extracted = {i for i, s in by_id.items() if s.kind in ("concept", "rule") and s.origin == "extracted"}
    out = []
    for e in repo.edges(project, limit=1_000_000):
        if e.status not in ("verified", "rejected") or not (e.src_id in extracted or e.dst_id in extracted):
            continue
        if e.status == "verified" and e.origin == "source":
            continue
        a, b = by_id.get(e.src_id), by_id.get(e.dst_id)
        if a and b:
            out.append({"src_kind": a.kind, "src": a.external_id, "dst_kind": b.kind, "dst": b.external_id, "type": e.type, "status": e.status, "origin": e.origin, "evidence": e.evidence, "by": e.decided_by or "reset"})  # fmt: skip
    return out


def _restore(repo: KnowledgeRepo, project: str, decisions: list[dict[str, str]]) -> int:
    """Put the decisions back on the links of the re-extracted concepts. A rejected link stays rejected even if the model
    does not propose it again (so it is not proposed anew later)."""
    n = 0
    for d in decisions:
        a, b = (
            repo.find_source(project, d["src_kind"], d["src"]),
            repo.find_source(project, d["dst_kind"], d["dst"]),
        )
        if a is None or b is None:
            continue  # the document no longer says it: nothing to attach the decision to
        try:
            e, _ = repo.upsert_edge(project, a, b, d["type"], "proposed", d["origin"], d["evidence"])
        except OntologyViolation:
            continue
        n += int(repo.decide_edge(e.id, d["status"], d["by"]))
    return n


def run(
    repo: KnowledgeRepo, ing: Ingestor, provider_factory: Callable[[], ModelProvider], project: str, kinds: tuple[str, ...] = ("story", "brd"),
    limit: int = 200, workers: int = 4, progress: Callable[[int, int], None] | None = None, reset: bool = False,
    on_event: Callable[[str, str, dict[str, Any]], None] | None = None,
) -> Report:  # fmt: skip
    """Extract from every current source of the given kinds that has not been extracted at its current content yet.
    The model calls run in parallel; results are stored one at a time. `reset` first removes every extracted concept
    and rule of the project (with their links) and reads every document again, e.g. after the ontology changed."""
    rep = Report()
    decisions = _decisions(repo, project) if reset else []
    if reset:
        rep.removed = repo.delete_sources(project, ("concept", "rule"), origin="extracted")
    onto = for_project(repo, project)
    todo: list[tuple[KnowledgeSource, str, str]] = []
    done = {
        a.object_id: (a.detail or {}).get("hash")
        for a in reversed(repo.audit_log(project, limit=20000))
        if a.action == "extract.done"
    }
    if reset:
        done = {}
    for s in repo.sources(project, limit=5000):
        if s.kind not in kinds:
            continue
        v = repo.version(s.id)
        if v is None:
            continue
        if done.get(s.id) == v.content_hash:
            rep.skipped_unchanged += 1
            continue
        todo.append((s, v.content, v.content_hash))
    todo = todo[:limit]

    def call(
        item: tuple[KnowledgeSource, str, str],
    ) -> tuple[KnowledgeSource, str, str, Extraction | Exception]:
        s, text, h = item
        try:
            return (
                s,
                text,
                h,
                extract_one(provider_factory(), s.external_id, text, onto=onto, known=vocabulary()),
            )
        except Exception as e:  # noqa: BLE001 - one source failing must not stop the rest
            return s, text, h, e

    def vocabulary() -> list[str]:
        """Glossary terms, then the names extracted so far: what later documents should reuse."""
        names = {norm(t): t for t in onto.terms}
        for c in repo.sources(project, limit=5000):
            if c.kind in ("concept", "rule"):
                names.setdefault(norm(c.title), c.title)
        return list(names.values())

    # BRDs, epics and features first, stored before the stories are read: they define the terms stories reuse
    started = time.monotonic()

    def say(stage: str, message: str, **data: Any) -> None:
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
            on_event(stage, message, data)

    first = {"brd", "requirement", "epic", "feature"}
    phases = [[t for t in todo if t[0].kind in first], [t for t in todo if t[0].kind not in first]]
    if rep.removed:
        say("extract", f"Removed {rep.removed} previously extracted concepts and rules (reset)")
    say("extract", f"{len(todo)} documents to read ({rep.skipped_unchanged} unchanged since the last extraction); BRDs, epics and features first so stories reuse their names", total=len(todo), done=0)  # fmt: skip
    n = 0
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        for phase in phases:
            for s, text, h, res in pool.map(call, phase):
                n += 1
                rep.sources += 1
                if isinstance(res, Exception):
                    rep.failed.append(f"{s.external_id}: {type(res).__name__}: {str(res)[:120]}")
                    say(
                        "extract",
                        f"{s.external_id}: model call failed ({type(res).__name__})",
                        done=n,
                        total=len(todo),
                    )
                else:
                    say("extract", f"{s.external_id}: {len(res.entities)} concepts/rules and {len(res.relations)} relationships kept, {res.rejected['entity'] + res.rejected['relation']} dropped (quote not found or not allowed)", done=n, total=len(todo), kept=len(res.entities) + len(res.relations), dropped=res.rejected["entity"] + res.rejected["relation"], source=s.external_id, concepts=[{k: x[k] for k in ("name", "type", "quote")} for x in res.entities][:40], relations=[{k: x[k] for k in ("from", "type", "to", "quote")} for x in res.relations][:40])  # fmt: skip
                    persist(ing, project, s, text, res, rep)
                    rep.rejected_entities += res.rejected["entity"]
                    rep.rejected_relations += res.rejected["relation"]
                    repo.audit(project, ing.actor, "extract.done", "source", s.id, {"hash": h, "entities": len(res.entities), "relations": len(res.relations)})  # fmt: skip
                if progress:
                    progress(n, len(todo))
    rep.restored = _restore(repo, project, decisions)
    repo.audit(project, ing.actor, "extract.run", "project", project, rep.as_dict())
    return rep
