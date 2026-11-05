"""The task context bundle: the evidence an agent gets for one task, assembled from the knowledge base through the
same tool handlers the MCP server exposes.

Versioned and reproducible: `bundle_id` is a hash of the project, the query, the exact source versions (with content
hashes) and the relationships used, so the same inputs give the same id and a later `drift()` shows what changed since.
It says what is missing or contradictory instead of papering over it.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from harnix.knowledge import tools
from harnix.knowledge.repo import KINDS
from harnix.knowledge.service import KnowledgeService

REQ_KINDS = ("requirement", "story", "ticket", "brd", "epic", "feature")
# ids written in a task: US-003, FEAT-001, BRD-02, BF-AS-VA-02, PROJ-12, or namespaced BRD-02/US-003
ID_RE = re.compile(r"\b(?:[A-Z][A-Z0-9]*-\d+/)?[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)*-\d+\b")
MAX_ID_HITS = 6
# traversal: the backlog structure two hops out (story -> feature -> epic -> BRD), but what a document SAYS (concepts,
# rules) only one hop: going through a shared concept would pull in every other backlog that mentions it
STRUCTURE = ("derives_from", "implements", "satisfies", "tests", "designs", "depends_on", "refines", "relates_to", "contradicts", "part_of")  # fmt: skip
CONTENT = ("mentions",)
SEEDS = 3  # requirement-class items the graph is expanded from
ID_BONUS, SEARCH_BONUS, SEED_BONUS = 1.0, 0.05, 0.12  # added to the cosine similarity when reranking
FLOOR = 0.85  # graph-only items below this share of the best similarity are dropped (unless linked to the best seed)
SLOT_CAP = {"requirements": 8, "design": 3, "code": 4, "tests": 3, "constraints": 5}
KIND_CAP = {"rule": 4, "concept": 2}
ID_STEP = 0.05  # named items keep the order the task names them in (qualifying backlogs last)
# the items work is planned and tested at (story, requirement, ticket) before their summaries (feature, epic, BRD)
KIND_PRIOR = {"story": 0.04, "requirement": 0.04, "ticket": 0.04}
MAX_ITEMS = 14
PER_KIND = 4
EXCERPT = {"code": 1200, "test": 1200}
DEFAULT_EXCERPT = 1800
WEAK_MARGIN = 0.07  # above the relevance floor by less than this: sufficient, with a "weak match" note
TARGET_CHARS = 9000  # what the task is about is given whole (up to this), acceptance criteria included
LEAF_KINDS = ("story", "requirement", "ticket")


@dataclass
class Bundle:
    id: str
    project: str
    status: str  # sufficient | insufficient
    content: dict[str, Any]


def _slot(kind: str) -> str:
    return (
        "requirements"
        if kind in REQ_KINDS
        else {"design": "design", "code": "code", "test": "tests"}.get(kind, "constraints")
    )


def build_bundle(service: KnowledgeService, project: str, instructions: str, criteria: list[str] | None = None, *, max_items: int = MAX_ITEMS) -> Bundle:  # fmt: skip
    query = (instructions + "\n" + "\n".join(criteria or [])).strip()
    trace: list[dict[str, Any]] = []
    # what the pipeline view shows: how long each stage took and what happened to every candidate
    timings: dict[str, float] = {}
    clock = [time.perf_counter()]

    def lap(stage: str) -> None:
        now = time.perf_counter()
        timings[stage] = round((now - clock[0]) * 1000, 1)
        clock[0] = now

    dropped: dict[str, dict[str, Any]] = {}  # source id -> the candidate and why it was left out

    def run(name: str, **args: Any) -> dict[str, Any]:
        out = tools.call(service, name, {"project": project, **args})
        trace.append({"tool": name, "args": {k: v for k, v in args.items() if k != "project"}, "results": len(out.get("hits") or out.get("nodes") or [1])})  # fmt: skip
        return out

    ranked: dict[str, float] = {}  # source external id -> order key (lower = earlier)
    meta: dict[str, dict[str, Any]] = {}

    twins: set[str] = set()
    named_ext: set[str] = set()

    def consider(hit: dict[str, Any], order: float, via: str) -> None:
        key = hit["source_id"]
        ext = hit["external_id"]
        if via != "id" and "/" in ext and ext.split("/", 1)[1] in twins and ext not in named_ext:
            # the task said which backlog's US-003; another backlog's US-003 is not it, however it was found
            dropped.setdefault(key, {**hit, "found_by": [via], "reason": "same id in another backlog than the task names"})  # fmt: skip
            return
        if key not in meta:
            meta[key] = {**hit, "found_by": [via]}
            ranked[key] = order
        else:
            ranked[key] = min(ranked[key], order)
            if via not in meta[key]["found_by"]:
                meta[key]["found_by"].append(via)

    # 1. exact id lookup: what the task names explicitly comes first
    named = _named_sources(service, project, query)
    for i, sid in enumerate(named):
        consider(run("knowledge_get", ref=sid), -100 + i, "id")
        meta[sid]["id_rank"] = i
    named_ext.update(meta[s]["external_id"] for s in named)
    twins.update(_other_namespace_twins(named_ext))

    main_kinds = [k for k in KINDS if k not in ("concept", "rule")]
    s1 = service.search(project, query, main_kinds, 10, explain=True)  # with the rankings it was fused from
    trace.append({"tool": "knowledge_search", "args": {"query": query, "top_k": 10, "kinds": main_kinds}, "results": len(s1["hits"])})  # fmt: skip
    for i, h in enumerate(s1["hits"]):
        consider(h, i, "search")
    lap("id_lookup_and_search")
    # 2. rerank what was found by its similarity to the task (stored chunk embeddings), ids named in the task first
    sims = service.similarity(project, query, list(meta))

    def score(key: str) -> float:
        fb = meta[key]["found_by"]
        named_bonus = ID_BONUS - ID_STEP * meta[key].get("id_rank", 0) if "id" in fb else 0.0
        return sims.get(key, 0.0) + named_bonus + (SEARCH_BONUS if "search" in fb else 0.0) + reach.get(key, 0.0) + KIND_PRIOR.get(meta[key]["kind"], 0.0)  # fmt: skip

    reach: dict[str, float] = {}  # bonus for being linked to the best seed
    # 3. graph traversal from the strongest requirement-class seeds only (Neo4j when enabled): their backlog structure
    # two hops out, and the concepts/rules they mention one hop out
    seeds = sorted((k for k in meta if meta[k]["kind"] in REQ_KINDS), key=lambda k: -score(k))[:SEEDS]
    from_seed: dict[str, int] = {}
    expansion: list[dict[str, Any]] = []  # what each traversal pulled, for the drill-down
    for si, key in enumerate(seeds):
        for types, depth in ((list(STRUCTURE), 2), (list(CONTENT), 1)):
            rel = run("knowledge_relations", ref=key, depth=depth, types=types)
            expansion.append(_expansion(meta[key]["external_id"], types, depth, rel))
            via = "graph:neo4j" if rel.get("via") == "neo4j" else "graph"
            for n in rel["nodes"]:
                consider({**n, "score": 0.0, "snippet": ""}, 100 + si, via)
                from_seed[n["source_id"]] = min(from_seed.get(n["source_id"], si), si)
    sims.update(service.similarity(project, query, [k for k in meta if k not in sims]))
    lap("graph_traversal")
    for key, si in from_seed.items():
        # the best match's own feature, epic, BRD and rules: context for exactly that item. Not its sibling stories:
        # they are alternatives to it, and a bonus would rank them above the best match itself
        if si == 0 and key != seeds[0] and meta[key]["kind"] not in LEAF_KINDS:
            reach[key] = SEED_BONUS
    # 4. filter: an item only the graph found, not linked to the best seed and much less similar than the best, is noise
    best = max((sims.get(k, 0.0) for k in meta), default=0.0)
    for key in [k for k in meta if meta[k]["found_by"] == [meta[k]["found_by"][0]] and meta[k]["found_by"][0].startswith("graph")]:  # fmt: skip
        content = (
            meta[key]["kind"] in ("concept", "rule") and key in from_seed
        )  # what a seed says: its evidence
        if not content and from_seed.get(key) != 0 and sims.get(key, 0.0) < FLOOR * best:
            reason = f"only the graph found it, not linked to the best match, similarity {sims.get(key, 0.0):.3f} < {FLOOR:.0%} of the best ({best:.3f})"  # fmt: skip
            dropped[key] = {**meta.pop(key), "reason": reason}
            ranked.pop(key, None)
    for key in meta:
        ranked[key] = -score(key)
    _children_first(service, ranked, meta)

    def present(kinds: tuple[str, ...]) -> bool:
        return any(m["kind"] in kinds for m in meta.values())

    # targeted retrieval for what is still missing
    notes: list[str] = []
    for slot_kinds, label in (
        (REQ_KINDS, "a requirement, story or ticket"),
        (("design", "code"), "a design or code source"),
    ):
        if not present(slot_kinds):
            extra = run("knowledge_search", query=query, kinds=list(slot_kinds), top_k=5)
            for i, h in enumerate(extra["hits"]):
                consider(h, 200 + i, "search")
            notes.append(f"targeted search for {label}: {len(extra['hits'])} found")

    # choose a bounded, balanced set: best first, within per-kind and per-slot budgets so rules keep their room
    chosen: list[str] = []
    per: dict[str, int] = {}
    per_slot: dict[str, int] = {}
    over_budget: dict[str, str] = {}
    for key in sorted(ranked, key=lambda k: ranked[k]):
        k = meta[key]["kind"]
        slot = _slot(k)
        if len(chosen) < max_items and per.get(k, 0) < KIND_CAP.get(k, PER_KIND) and per_slot.get(slot, 0) < SLOT_CAP[slot]:  # fmt: skip
            chosen.append(key)
            per[k] = per.get(k, 0) + 1
            per_slot[slot] = per_slot.get(slot, 0) + 1
        elif len(chosen) >= max_items:
            over_budget[key] = f"bundle full ({max_items} items)"
        elif per.get(k, 0) >= KIND_CAP.get(k, PER_KIND):
            over_budget[key] = f"already {KIND_CAP.get(k, PER_KIND)} items of kind {k}"
        else:
            over_budget[key] = f"{slot} already has {SLOT_CAP[slot]} items"
    lap("rerank_filter_budget")

    # the target: what the task names (a story, requirement or ticket), else the best-ranked one; it is given whole
    named_leaves = [k for k in chosen if "id" in meta[k]["found_by"] and meta[k]["kind"] in LEAF_KINDS]
    first_leaf = next((k for k in chosen if meta[k]["kind"] in LEAF_KINDS), None)
    targets = set(named_leaves or ([first_leaf] if first_leaf else []))
    items: list[dict[str, Any]] = []
    for key in chosen:
        full = run("knowledge_get", ref=key, max_chars=TARGET_CHARS * 4)
        budget = TARGET_CHARS if key in targets else EXCERPT.get(full["kind"], DEFAULT_EXCERPT)
        shown, how, sections = _excerpt(service, project, query, full, budget)
        items.append({
            "source_id": full["source_id"], "kind": full["kind"], "external_id": full["external_id"], "title": full["title"],
            "version": full["version"], "content_hash": full["content_hash"], "uri": full["uri"], "origin": full["origin"],
            "ingestion_state": full["ingestion_state"], "slot": _slot(full["kind"]),
            "content": shown, "content_truncated": how != "whole source", "excerpt_of": how, "sections": sections,
            "target": key in targets,
            "open_questions": list((full["metadata"] or {}).get("open_questions") or []),
            "matched": meta[key].get("score", 0.0) > 0 or "id" in meta[key]["found_by"],
            "found_by": meta[key]["found_by"],
        })  # fmt: skip

    ids = {i["external_id"] for i in items}
    edges: dict[tuple[str, str, str], dict[str, Any]] = {}
    for it in items:
        rel = run("knowledge_relations", ref=it["source_id"], depth=1)
        for e in rel["edges"]:
            if e["from"] in ids and e["to"] in ids:
                edges[(e["from"], e["to"], e["type"])] = {
                    k: e[k] for k in ("from", "to", "type", "status", "evidence", "stale")
                }
    edge_list = [edges[k] for k in sorted(edges)]

    questions: list[str] = []
    for it in items:
        questions += [f"{it['external_id']}: {q}" for q in it["open_questions"]]
        if it["ingestion_state"] != "ok":
            notes.append(f"{it['external_id']} is only partly indexed ({it['ingestion_state']})")
    for e in edge_list:
        if e["type"] == "contradicts":
            questions.append(f"{e['from']} contradicts {e['to']} ({e['status']}): which one applies?")
        if e["stale"]:
            notes.append(
                f"relationship {e['from']} -{e['type']}-> {e['to']} predates a newer version of one end"
            )
        if e["status"] == "proposed":
            notes.append(f"relationship {e['from']} -{e['type']}-> {e['to']} is only proposed (not verified)")

    missing = []
    if not any(i["slot"] == "requirements" for i in items):
        missing.append("no requirement, story or ticket matches this task")
    # relevance: a task that names no id must be close to something, else the "context" is just the nearest noise
    best_rel = max((sims.get(k, 0.0) for k in chosen if meta[k]["kind"] in REQ_KINDS), default=0.0)
    floor = service.min_relevance
    if floor and not named and best_rel < floor:
        missing.append(f"nothing in the knowledge base is close to this request (best match {best_rel:.2f}, needs {floor:.2f}): name the story or ticket, or add the documents that describe it")  # fmt: skip
    elif floor and not named and best_rel < floor + WEAK_MARGIN:
        notes.insert(0, f"weak match: the closest requirement is only {best_rel:.2f} similar to the task; check that the context is about the right thing")  # fmt: skip
    if not any(i["slot"] in ("design", "code") for i in items):
        # a new project has neither: that is a fact for the agent, not a reason to stop
        notes.append("no design or code source matches this task: build from the requirements")
    contradiction = any(e["type"] == "contradicts" for e in edge_list)
    status = "insufficient" if (missing or contradiction) else "sufficient"

    fingerprint = {
        "project": project, "query": query,
        "items": sorted((i["source_id"], i["version"], i["content_hash"]) for i in items),
        "edges": [(e["from"], e["to"], e["type"], e["status"]) for e in edge_list],
    }  # fmt: skip
    bundle_id = hashlib.sha256(json.dumps(fingerprint, sort_keys=True).encode()).hexdigest()[:32]
    slots: dict[str, list[dict[str, Any]]] = {
        "requirements": [],
        "design": [],
        "code": [],
        "tests": [],
        "constraints": [],
    }
    for it in items:
        slots[it["slot"]].append(it)

    chunk_budget = [40]  # candidates whose best chunks are listed (each is one query on stored vectors)

    def best_chunks(key: str) -> list[dict[str, Any]]:
        if chunk_budget[0] <= 0:
            return []
        chunk_budget[0] -= 1
        try:
            scored = service.chunk_scores(project, query, key)
        except Exception:  # noqa: BLE001 - the drill-down must never break a bundle
            return []
        top = sorted(scored, key=lambda cs: -cs[1])[:4]
        return [{"chunk_id": c.id, "ordinal": c.ordinal, "heading": c.heading, "similarity": round(sc, 4), "text": c.text[:220]} for c, sc in top]  # fmt: skip

    def candidate(key: str, m: dict[str, Any], decision: str, reason: str | None) -> dict[str, Any]:
        fb = m.get("found_by") or []
        pulled = from_seed.get(key)
        return {
            "chunks": best_chunks(key),
            "pulled_by": meta[seeds[pulled]]["external_id"] if pulled is not None and pulled < len(seeds) and seeds[pulled] in meta else None,  # fmt: skip
            "title": m.get("title", ""), "source_id": key,
            "external_id": m["external_id"], "kind": m["kind"], "found_by": fb, "decision": decision, "reason": reason,
            "similarity": round(sims.get(key, 0.0), 4),
            "bonus": {
                "named_id": round(ID_BONUS - ID_STEP * m.get("id_rank", 0), 3) if "id" in fb else 0.0,
                "search": SEARCH_BONUS if "search" in fb else 0.0, "best_seed_link": reach.get(key, 0.0),
                "kind": KIND_PRIOR.get(m["kind"], 0.0),
            },
            "score": round(-ranked[key], 4) if key in ranked and ranked[key] <= 0 else None,  # targeted extras are not reranked
            "seed": key in seeds,
        }  # fmt: skip

    candidates = [candidate(k, meta[k], "chosen", None) for k in chosen]
    candidates += [candidate(k, meta[k], "over_budget", r) for k, r in over_budget.items()]
    candidates += [candidate(k, m, "dropped", m["reason"]) for k, m in dropped.items()]
    lap("assemble")
    content = {
        "bundle_id": bundle_id, "project": project, "query": query, "status": status, "embedding_model": s1.get("embedding_model"),
        "sufficiency": {"status": status, "missing": missing, "contradictions": contradiction, "notes": notes[:20]},
        "slots": slots, "relationships": edge_list, "unresolved_questions": questions[:20], "retrieval_trace": trace,
        # for the pipeline view (not part of the bundle id): every candidate with its score and fate, stage timings
        "pipeline": {
            "named_ids": sorted(named_ext), "seeds": [meta[k]["external_id"] for k in seeds if k in meta],
            "relevance": {"best": round(best_rel, 4), "floor": floor},
            "candidates": candidates, "timings_ms": timings,
            "weights": {"named_id": ID_BONUS, "search": SEARCH_BONUS, "best_seed_link": SEED_BONUS, "kind": KIND_PRIOR, "floor": FLOOR},
            "prompt": {"text": query[:3000], "named": [{"id": meta[s]["external_id"], "source_id": s} for s in named if s in meta], "embedding": (s1.get("explain") or {}).get("embedding")},  # fmt: skip
            "search": s1.get("explain"), "expansion": expansion,
            "budgets": {"max_items": max_items, "per_slot": SLOT_CAP, "per_kind": KIND_CAP, "default_per_kind": PER_KIND},
        },
    }  # fmt: skip
    return Bundle(bundle_id, project, status, content)


def _expansion(seed: str, types: Sequence[str], depth: int, rel: dict[str, Any]) -> dict[str, Any]:
    """One traversal as the drill-down shows it: from which seed, along which link types and how deep, by what route
    (Neo4j or the tables), every node it reached with its distance from the seed, and every link walked with its
    status and evidence."""
    root = (rel.get("root") or {}).get("external_id")
    near: dict[str, int] = {root: 0} if root else {}
    edges = rel.get("edges") or []
    for _ in range(depth + 1):  # breadth-first over the returned links, either direction
        for e in edges:
            for a, b in ((e["from"], e["to"]), (e["to"], e["from"])):
                if a in near and b not in near:
                    near[b] = near[a] + 1
    return {
        "seed": seed,
        "link_types": types,
        "depth": depth,
        "via": rel.get("via"),
        "truncated": rel.get("truncated", False),
        "nodes": [
            {
                "external_id": n["external_id"],
                "kind": n["kind"],
                "title": n["title"][:140],
                "hop": near.get(n["external_id"]),
            }
            for n in (rel.get("nodes") or [])[:80]
        ],  # fmt: skip
        "edges": [
            {
                "from": e["from"],
                "to": e["to"],
                "type": e["type"],
                "status": e["status"],
                "origin": e.get("origin", ""),
                "evidence": (e.get("evidence") or "")[:160],
                "stale": e.get("stale", False),
            }
            for e in edges[:120]
        ],  # fmt: skip
    }


def _children_first(
    service: KnowledgeService, ranked: dict[str, float], meta: dict[str, dict[str, Any]]
) -> None:
    """A summary never ranks above the more specific item it summarises when both are candidates: a feature (or epic,
    BRD, BRD section) is placed right after its best-ranked child (a story deriving from it). Short summaries often
    resemble a question more than the full story does, but the story is what the work is about."""
    keys = [k for k in ranked if meta[k]["kind"] in REQ_KINDS]
    kids: dict[str, list[str]] = {}
    for e in service.repo.edges_of(keys, ("verified", "proposed")):
        if e.type in ("derives_from", "refines", "part_of") and e.src_id in ranked and e.dst_id in ranked:
            kids.setdefault(e.dst_id, []).append(e.src_id)
    for _ in range(4):  # a chain story -> feature -> epic -> BRD settles in a few passes
        moved = False
        for parent, children in kids.items():
            best = min(ranked[c] for c in children)
            if ranked[parent] < best:
                ranked[parent] = best + 1e-6
                moved = True
        if not moved:
            break


def _excerpt(service: KnowledgeService, project: str, query: str, full: dict[str, Any], budget: int) -> tuple[str, str, list[dict[str, Any]]]:  # fmt: skip
    """What of one source goes into the context: all of it when it fits the budget; otherwise its sections most similar
    to the task (by their stored embeddings), shown in document order. Returns (text, how it was cut, the sections
    given), so the bundle records exactly which parts of which version the agent saw."""
    content = full["content"]
    if len(content) <= budget:
        return content, "whole source", []
    try:
        scored = service.chunk_scores(project, query, full["source_id"])
    except Exception:  # noqa: BLE001 - no vectors (or no embedder): fall back to the start of the source
        scored = []
    if not scored or all(s == 0.0 for _, s in scored):
        return content[:budget], "start of the source", []
    picked: list[tuple[Any, float]] = []
    used = 0
    for c, sim in sorted(scored, key=lambda x: -x[1]):
        piece = len(c.text) + len(c.heading) + 8
        if used + piece > budget and picked:
            continue
        picked.append((c, sim))
        used += piece
        if used >= budget:
            break
    picked.sort(key=lambda x: x[0].ordinal)
    text = "\n[...]\n".join((f"## {c.heading}\n" if c.heading and not c.text.lstrip().startswith("#") else "") + c.text for c, _ in picked)  # fmt: skip
    sections = [{"chunk_id": c.id, "ordinal": c.ordinal, "heading": c.heading, "similarity": round(sim, 3)} for c, sim in picked]  # fmt: skip
    return (
        text[: budget + 400],
        f"{len(picked)} of {len(scored)} sections, the most relevant to the task",
        sections,
    )


def _named_sources(service: KnowledgeService, project: str, query: str) -> list[str]:
    """Source ids for the ids the task names. A bare id that exists in several backlogs (US-003 in BRD-02 and BRD-03)
    is narrowed by a namespace named in the same task; otherwise every match is kept (at most MAX_ID_HITS)."""
    named = list(dict.fromkeys(ID_RE.findall(query)))
    if not named:
        return []
    sources = service.repo.sources(project, limit=100_000)
    by_ext: dict[str, list[str]] = {}
    for s in sources:
        by_ext.setdefault(s.external_id, []).append(s.id)
        if "/" in s.external_id:
            by_ext.setdefault(s.external_id.split("/", 1)[1], []).append(s.id)
    spaces = {n for n in named if "/" not in n}
    ext = {s.id: s.external_id for s in sources}
    out: list[str] = []
    qualifiers: set[str] = set()
    for n in named:
        ids = by_ext.get(n, [])
        if "/" not in n and len(ids) > 1:
            narrowed = [i for i in ids if ext[i].split("/", 1)[0] in spaces]
            if narrowed:
                qualifiers |= {
                    ext[i].split("/", 1)[0] for i in narrowed
                }  # "of BRD-03" only says which US-003
            ids = narrowed or ids
        out += [i for i in ids if i not in out]
    out = out[:MAX_ID_HITS]
    # the item a task names is its target; a backlog named only to qualify it comes after (still context)
    return sorted(out, key=lambda i: ext[i] in qualifiers)


def _other_namespace_twins(named_ext: set[str]) -> set[str]:
    """Bare ids the task named inside a backlog: the same id in OTHER backlogs is a different item ("US-003")."""
    return {e.split("/", 1)[1] for e in named_ext if "/" in e}


def drift(service: KnowledgeService, content: dict[str, Any]) -> list[dict[str, Any]]:
    """Which sources of a stored bundle have a newer version (or vanished) since it was built."""
    out = []
    for items in content["slots"].values():
        for it in items:
            try:
                cur = service.get(content["project"], it["source_id"], max_chars=1)
            except Exception:  # noqa: BLE001
                out.append(
                    {
                        "external_id": it["external_id"],
                        "bundle_version": it["version"],
                        "current_version": None,
                    }
                )
                continue
            if cur["current_version"] != it["version"] or (
                cur["version"] == it["version"] and cur["content_hash"] != it["content_hash"]
            ):
                out.append({"external_id": it["external_id"], "bundle_version": it["version"], "current_version": cur["current_version"]})  # fmt: skip
    return out


def render(content: dict[str, Any], max_chars: int = 24000) -> str:
    """Markdown for the first message. The caller fences it as untrusted (documents can contain anything)."""
    suff = content["sufficiency"]
    lines = [f"Context bundle {content['bundle_id']} (project {content['project']}, status {content['status']}). "
             "Every item names its source and version; cite them (e.g. REQ-1 v2) in your plan and summary."]  # fmt: skip
    if suff["missing"]:
        lines.append("MISSING: " + "; ".join(suff["missing"]))
    if content["unresolved_questions"]:
        lines.append(
            "UNRESOLVED QUESTIONS (do not guess answers):\n"
            + "\n".join(f"- {q}" for q in content["unresolved_questions"])
        )
    titles = {"requirements": "Requirements, stories and tickets", "design": "Design", "code": "Code", "tests": "Tests", "constraints": "Other knowledge and constraints"}  # fmt: skip
    for slot, title in titles.items():
        for n, it in enumerate(content["slots"].get(slot, [])):
            if n == 0:
                lines.append(f"## {title}")
            flag = "" if it["origin"] == "ingest" else f" [origin: {it['origin']}]"
            nl = chr(10)
            cut = f"{nl}[excerpt: {it.get('excerpt_of', 'part of the source')}; knowledge_get {it['external_id']} returns the rest (pass offset for long sources)]"
            more = cut if it["content_truncated"] else ""
            aim = " [TARGET: what this task is about]" if it.get("target") else ""
            head = f"### {it['external_id']} v{it['version']} ({it['kind']}){flag}{aim}: {it['title']}"
            lines.append(
                f"{head}{nl}source_id={it['source_id']} hash={it['content_hash']}{nl}{it['content'].strip()}{more}"
            )
    if content["relationships"]:
        lines.append("## Relationships\n" + "\n".join(f"- {e['from']} -{e['type']}-> {e['to']} [{e['status']}{', stale' if e['stale'] else ''}]" for e in content["relationships"]))  # fmt: skip
    text = "\n\n".join(lines)
    return text if len(text) <= max_chars else text[:max_chars] + "\n[bundle truncated]"
