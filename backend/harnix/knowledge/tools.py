"""The agent-facing retrieval interface: read-only tools only, nothing that writes or runs. The MCP server offers the ones
that can work on this server (`defs`): the graph tools only when Neo4j is on, the project list only when the server is not
scoped to one project. The context builder calls the same handlers, so there is one implementation behind MCP."""

from __future__ import annotations

import json
from typing import Any

from harnix.knowledge import graph_tools
from harnix.knowledge.repo import EDGE_TYPES, KINDS
from harnix.knowledge.service import AccessDenied, KnowledgeService, NotFoundError

_PROJECT = {
    "type": "string",
    "description": "Knowledge project; optional when the server is scoped to a single one",
}
TOOL_DEFS: list[dict[str, Any]] = [
    {
        "name": "knowledge_search",
        "description": "Hybrid (semantic + keyword) search over the project's knowledge: BRDs, requirements, stories, tickets, design, code and tests. Returns ranked sources with id, kind, version and a snippet.",
        "inputSchema": {"type": "object", "properties": {
            "query": {"type": "string", "description": "What to look for, in natural language"},
            "kinds": {"type": "array", "items": {"type": "string", "enum": list(KINDS)}},
            "top_k": {"type": "integer", "minimum": 1, "maximum": 25},
            "project": _PROJECT}, "required": ["query"]},
    },
    {
        "name": "knowledge_get",
        "description": "Exact lookup of one source by its source_id or external id (REQ-1, PROJ-12, a file path): content, version, metadata. Long sources come in pieces: when `truncated` is true, call again with `offset` = `next_offset`.",
        "inputSchema": {"type": "object", "properties": {
            "ref": {"type": "string"}, "version": {"type": "integer", "minimum": 1},
            "offset": {"type": "integer", "minimum": 0}, "max_chars": {"type": "integer", "minimum": 200, "maximum": 40000},
            "project": _PROJECT}, "required": ["ref"]},
    },
    {
        "name": "knowledge_relations",
        "description": "Traverse relationships from a source (requirement -> story -> ticket -> design -> code -> tests). Each edge says whether it is verified (authoritative) or only proposed, and whether either end changed since.",
        "inputSchema": {"type": "object", "properties": {
            "ref": {"type": "string"}, "depth": {"type": "integer", "minimum": 1, "maximum": 3},
            "types": {"type": "array", "items": {"type": "string", "enum": list(EDGE_TYPES)}},
            "verified_only": {"type": "boolean"}, "project": _PROJECT}, "required": ["ref"]},
    },
]  # fmt: skip
TOOL_DEFS += [
    {
        "name": "knowledge_context",
        "description": "START HERE for a coding task. Builds the curated, cited context for a task description: the story/requirement it is about (given whole, marked TARGET, with its acceptance criteria), its feature/epic/BRD, the rules and concepts it mentions, related code and tests, and whether the knowledge is sufficient (if not: what is missing). Returns `text` (ready to read, every item with id and version to cite), `bundle_id` (reproducible: same inputs, same id) and the items. Name ids in the task (e.g. 'Implement US-003 of BRD-03') for an exact match.",
        "inputSchema": {"type": "object", "properties": {
            "task": {"type": "string", "description": "What should be built, in plain language; ids like US-003 or BRD-02/US-001 are looked up exactly"},
            "criteria": {"type": "array", "items": {"type": "string"}, "description": "Optional acceptance criteria to match as well"},
            "project": _PROJECT}, "required": ["task"]},
    },
    {
        "name": "knowledge_trace",
        "description": "Traceability for one item (story, requirement, feature, BRD, file): what it derives from up to the BRD, what derives from it, the code that implements it, the tests that test it, the rules and concepts it mentions, and what it depends on. Each link says verified (declared by a document or confirmed by a person) or proposed (suggested, not confirmed).",
        "inputSchema": {"type": "object", "properties": {"ref": {"type": "string", "description": "An id such as BRD-03/US-003, US-003, FEAT-002, a file path, or a source_id"}, "project": _PROJECT}, "required": ["ref"]},
    },
    {
        "name": "knowledge_criteria",
        "description": "The acceptance criteria of a story, requirement or ticket, read from an exact version (default: current), plus those it inherits from the story it implements. Use it to check that work meets what was asked.",
        "inputSchema": {"type": "object", "properties": {"ref": {"type": "string"}, "version": {"type": "integer", "minimum": 1}, "project": _PROJECT}, "required": ["ref"]},
    },
    {
        "name": "knowledge_list",
        "description": "List the sources of the project (id, kind, title, version), optionally of one kind: brd, requirement, epic, feature, story, ticket, design, code, test, doc, concept, rule.",
        "inputSchema": {"type": "object", "properties": {"kind": {"type": "string", "enum": list(KINDS)}, "limit": {"type": "integer", "minimum": 1, "maximum": 500}, "project": _PROJECT}},
    },
    {
        "name": "knowledge_projects",
        "description": "The knowledge projects this server may answer for (its scope), with how many sources of each kind they hold.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "knowledge_schema",
        "description": "The graph schema (node labels, relationship types, properties, what they mean) and the ontology (which link joins which types), for writing knowledge_query Cypher.",
        "inputSchema": {"type": "object", "properties": {"project": _PROJECT}},
    },
    {
        "name": "knowledge_query",
        "description": "Run a READ-ONLY Cypher query on the project's knowledge graph (Neo4j). Every node must be restricted with $project (always set to this project), e.g. MATCH (s:Story {project: $project})-[:DERIVES_FROM*1..3]->(x) RETURN s, x LIMIT 25. Writes, LOAD CSV, procedures and admin commands are refused; at most 500 records. Prefer knowledge_impact, knowledge_path, knowledge_uses, knowledge_gaps and knowledge_concepts for the common questions; write your own Cypher only for anything else, after knowledge_schema.",
        "inputSchema": {"type": "object", "properties": {"cypher": {"type": "string"}, "project": _PROJECT}, "required": ["cypher"]},
    },
]  # fmt: skip
TOOL_DEFS += graph_tools.DEFS
NAMES = {t["name"] for t in TOOL_DEFS}
GRAPH_TOOLS = {"knowledge_schema", "knowledge_query"} | graph_tools.NAMES  # need the graph database


def defs(service: KnowledgeService) -> list[dict[str, Any]]:
    """The tools worth offering this server: no graph tool without a graph database, and no project list when the server
    can only ever answer for one project (it is the scope, there is nothing to choose)."""
    scoped = service.projects is not None and len(service.projects) == 1
    return [t for t in TOOL_DEFS if not (t["name"] in GRAPH_TOOLS and service.graph is None) and not (t["name"] == "knowledge_projects" and scoped)]  # fmt: skip


def names(service: KnowledgeService) -> set[str]:
    return {t["name"] for t in defs(service)}


def call(service: KnowledgeService, name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Run a tool. Returns the JSON-able answer; raises ValueError/AccessDenied/NotFoundError for the caller to report."""
    if name not in NAMES:
        raise ValueError(f"Unknown tool '{name}'")
    if not isinstance(args, dict):
        raise ValueError("arguments must be an object")
    p = args.get("project")
    if name in _EXTRA:
        return _EXTRA[name](service, p, args)
    if name == "knowledge_search":
        q = args.get("query")
        if not isinstance(q, str) or not q.strip():
            raise ValueError("'query' is required")
        kinds = args.get("kinds")
        if kinds is not None and (not isinstance(kinds, list) or any(k not in KINDS for k in kinds)):
            raise ValueError(f"'kinds' must be a list of: {', '.join(KINDS)}")
        return service.search(p, q, kinds, int(args.get("top_k") or 8))
    ref = args.get("ref")
    if not isinstance(ref, str) or not ref.strip():
        raise ValueError("'ref' is required")
    if name == "knowledge_get":
        try:
            offset = max(0, int(args.get("offset") or 0))
            size = min(40000, max(200, int(args.get("max_chars") or 6000)))
        except (TypeError, ValueError):
            raise ValueError("'offset' and 'max_chars' must be numbers") from None
        return service.get(p, ref, args.get("version"), max_chars=size, offset=offset)
    types = args.get("types")
    if types is not None and (not isinstance(types, list) or any(t not in EDGE_TYPES for t in types)):
        raise ValueError(f"'types' must be a list of: {', '.join(EDGE_TYPES)}")
    st = ("verified",) if args.get("verified_only") else ("verified", "proposed")
    return service.relations(p, ref, int(args.get("depth") or 1), types, st)


# ---- the tools for other harnesses ------------------------------------------------------------------------------------


def _context(service: KnowledgeService, p: str | None, args: dict[str, Any]) -> dict[str, Any]:
    from harnix.knowledge.context import build_bundle, render

    task = args.get("task")
    if not isinstance(task, str) or not task.strip():
        raise ValueError("'task' is required")
    crit = args.get("criteria") or []
    if not isinstance(crit, list) or not all(isinstance(c, str) for c in crit):
        raise ValueError("'criteria' must be a list of strings")
    proj = service.project(p)
    b = build_bundle(service, proj, task.strip()[:4000], [c[:500] for c in crit[:20]])
    service.repo.save_bundle(
        b.id, proj, None, b.status, b.content
    )  # kept: listed under Context graphs, reproducible
    service.repo.audit(proj, "mcp", "bundle.built", "bundle", b.id, {"status": b.status, "via": "mcp"})
    items = [{k: i.get(k) for k in ("external_id", "kind", "title", "version", "target", "found_by", "excerpt_of")} for v in b.content["slots"].values() for i in v]  # fmt: skip
    return {
        "project": proj, "bundle_id": b.id, "status": b.status,
        "missing": b.content["sufficiency"]["missing"], "notes": b.content["sufficiency"]["notes"][:10],
        "questions": b.content["unresolved_questions"], "items": items, "text": render(b.content),
    }  # fmt: skip


def _trace(service: KnowledgeService, p: str | None, args: dict[str, Any]) -> dict[str, Any]:
    ref = args.get("ref")
    if not isinstance(ref, str) or not ref.strip():
        raise ValueError("'ref' is required")
    proj = service.project(p)
    src = service.lookup(proj, ref.strip())
    out: dict[str, Any] = {"item": {"external_id": src.external_id, "kind": src.kind, "title": src.title, "version": src.current_version},  # fmt: skip
                           "derives_from": [], "derived_by": [], "implemented_by": [], "tested_by": [], "mentions": [], "depends_on": [], "other": []}  # fmt: skip
    rel = service.relations(proj, src.id, 1)
    kinds = {n["external_id"]: n["kind"] for n in rel["nodes"]}
    for e in rel["edges"]:
        mine_from = e["from"] == src.external_id
        other = e["to"] if mine_from else e["from"]
        row = {"external_id": other, "kind": kinds.get(other), "type": e["type"], "status": e["status"]}
        if e["type"] in ("derives_from", "refines", "part_of"):
            out["derives_from" if mine_from else "derived_by"].append(row)
        elif e["type"] == "implements" and not mine_from:
            out["implemented_by"].append(row)
        elif e["type"] == "tests" and not mine_from:
            out["tested_by"].append(row)
        elif e["type"] == "mentions" and mine_from:
            out["mentions"].append(row)
        elif e["type"] == "depends_on" and mine_from:
            out["depends_on"].append(row)
        else:
            out["other"].append(row)
    # the chain up to the BRD (story -> feature -> epic -> BRD)
    chain, cur = [], src
    for _ in range(4):
        up = [e for e in service.relations(proj, cur.id, 1, ["derives_from", "refines"])["edges"] if e["from"] == cur.external_id]  # fmt: skip
        if not up:
            break
        nxt = service.repo.resolve(proj, up[0]["to"])
        if nxt is None or nxt.id == cur.id:
            break
        chain.append(
            {"external_id": nxt.external_id, "kind": nxt.kind, "title": nxt.title, "status": up[0]["status"]}
        )
        cur = nxt
    out["chain_to_brd"] = chain
    return out


def _criteria(service: KnowledgeService, p: str | None, args: dict[str, Any]) -> dict[str, Any]:
    from harnix.knowledge.evaluation import criteria_of

    ref = args.get("ref")
    if not isinstance(ref, str) or not ref.strip():
        raise ValueError("'ref' is required")
    got = service.get(p, ref.strip(), args.get("version"), max_chars=400_000)
    out = [
        {"id": f"{got['external_id']}#{n}", "text": t} for n, t in enumerate(criteria_of(got["content"]), 1)
    ]
    inherited = []
    for e in service.relations(got["project"], got["source_id"], 1, ["implements", "derives_from"])["edges"]:
        if e["from"] == got["external_id"]:
            parent = service.repo.resolve(got["project"], e["to"])
            if parent is not None and parent.kind in ("story", "requirement", "ticket"):
                v = service.repo.version(parent.id)
                inherited += [{"id": f"{parent.external_id}#{n}", "text": t, "from": parent.external_id} for n, t in enumerate(criteria_of(v.content if v else ""), 1)]  # fmt: skip
    return {"project": got["project"], "external_id": got["external_id"], "version": got["version"], "content_hash": got["content_hash"], "criteria": out, "inherited": inherited}  # fmt: skip


def _list(service: KnowledgeService, p: str | None, args: dict[str, Any]) -> dict[str, Any]:
    kind = args.get("kind")
    if kind is not None and kind not in KINDS:
        raise ValueError(f"'kind' must be one of: {', '.join(KINDS)}")
    return service.list_sources(p, kind, max(1, min(500, int(args.get("limit") or 100))))


def _projects(service: KnowledgeService, p: str | None, args: dict[str, Any]) -> dict[str, Any]:
    rows = service.repo.projects()
    return {"projects": [r for r in rows if service.projects is None or r["project"] in service.projects]}


def _schema(service: KnowledgeService, p: str | None, args: dict[str, Any]) -> dict[str, Any]:
    from harnix.knowledge.ontology import for_project
    from harnix.knowledge.query import schema_text

    proj = service.project(p)
    o = for_project(service.repo, proj)
    return {"project": proj, "graph": schema_text(), "ontology": {"types": o.types, "relations": o.relations, "synonyms": o.synonyms}}  # fmt: skip


def _query(service: KnowledgeService, p: str | None, args: dict[str, Any]) -> dict[str, Any]:
    from harnix.knowledge.query import QueryRefused, run_query

    cypher = args.get("cypher")
    if not isinstance(cypher, str) or not cypher.strip():
        raise ValueError("'cypher' is required")
    if service.graph is None:
        raise ValueError("the graph database (Neo4j) is not enabled on this server")
    proj = service.project(p)
    service.graph.ensure_synced(service.repo, proj)
    try:
        res = run_query(service.graph, proj, cypher)
    except QueryRefused as e:
        raise ValueError(str(e)) from None
    out = res.as_dict()
    out.pop("nodes", None)  # rows already carry them; keep the answer compact for a model
    out.pop("edges", None)
    return out


_EXTRA = {
    "knowledge_context": _context, "knowledge_trace": _trace, "knowledge_criteria": _criteria, "knowledge_list": _list,
    "knowledge_projects": _projects, "knowledge_schema": _schema, "knowledge_query": _query, **graph_tools.HANDLERS,
}  # fmt: skip


def call_text(
    service: KnowledgeService, name: str, args: dict[str, Any], limit: int = 120_000
) -> tuple[bool, str]:
    """(ok, text) for the MCP server: errors become an isError result, never a crash."""
    try:
        text = json.dumps(call(service, name, args), ensure_ascii=False, default=str)
        return True, text[:limit]
    except (ValueError, NotFoundError, AccessDenied) as e:
        return False, f"{type(e).__name__}: {e}"
    except Exception as e:  # noqa: BLE001 - e.g. the graph database is unreachable: an error result, not a crash
        return False, f"{type(e).__name__}: {str(e)[:300]}"
