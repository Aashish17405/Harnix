"""Graph retrieval tools for MCP: fixed, parameterised Cypher over the project's knowledge graph in Neo4j.

`knowledge_query` lets an agent write any read-only Cypher. These tools are the questions an agent asks again and again,
already written and safe to call without knowing the schema: what a change would touch, how two items connect, where a
concept is used, what is flagged as a gap or conflict, which concepts of a kind exist.

The query text is fixed here. What the caller supplies (an id, a name, a depth, a list of types) is validated and passed as a
Cypher parameter, never pasted into the text (a depth is an integer inside fixed bounds; relationship types come from
constants). Every query goes through query.run_query: a read transaction with a time limit, `$project` on every node, at most
500 records. Without Neo4j these tools are not offered at all (tools.defs).
"""

from __future__ import annotations

from typing import Any

from harnix.knowledge.graphdb import RELS
from harnix.knowledge.query import QueryRefused, run_query
from harnix.knowledge.service import KnowledgeService

STRUCTURE = "|".join(
    RELS[t] for t in ("derives_from", "depends_on", "satisfies", "implements", "tests", "refines")
)
USAGE = ["MENTIONS", "TAGGED", "USES", "PERFORMED_BY", "CONSTRAINS", "PART_OF", "TRIGGERS"]
GAP_TYPES = ("flags_gap", "contradicts", "clarifies")
CONCEPT_TYPES = ("actor", "field", "event", "system", "domain", "tag", "concept", "rule", "constraint")
_PROJECT = {
    "type": "string",
    "description": "Knowledge project; optional when the server is scoped to a single one",
}
_REF = "an id such as US-003, BRD-03/US-003, FEAT-002 or a source_id"
_VERIFIED = {"type": "boolean", "description": "Only links a document declared or a person confirmed (default: proposed ones too, each marked)"}  # fmt: skip

DEFS: list[dict[str, Any]] = [
    {
        "name": "knowledge_impact",
        "description": "What changes if this item changes (direction downstream: the stories, code and tests that derive from, depend on, satisfy, implement, test or refine it, transitively), or what it rests on (upstream). Each row: id, kind, title, hops, and the kind of link by which it is reached. Use before changing a requirement, a rule or a shared story.",
        "inputSchema": {"type": "object", "properties": {
            "ref": {"type": "string", "description": _REF},
            "direction": {"type": "string", "enum": ["downstream", "upstream"], "description": "downstream (default): what is affected; upstream: what it comes from or depends on"},
            "depth": {"type": "integer", "minimum": 1, "maximum": 4},
            "verified_only": _VERIFIED, "project": _PROJECT}, "required": ["ref"]},
    },
    {
        "name": "knowledge_path",
        "description": "The shortest chain of links between two items, either direction (for example why a rule matters to a story): the items along the way and the links between them with their status. Empty when nothing connects them within max_hops.",
        "inputSchema": {"type": "object", "properties": {
            "from": {"type": "string", "description": _REF}, "to": {"type": "string", "description": _REF},
            "max_hops": {"type": "integer", "minimum": 1, "maximum": 6},
            "verified_only": _VERIFIED, "project": _PROJECT}, "required": ["from", "to"]},
    },
    {
        "name": "knowledge_uses",
        "description": "Where a concept, actor, data field, system, tag, rule or constraint is used: finds it by id or by part of its name and lists the documents and concepts linked to it (mentions, tagged, uses, performed by, constrains, part of), each with the link's status.",
        "inputSchema": {"type": "object", "properties": {
            "name": {"type": "string", "description": "An id (concept:favorite account) or part of a name (favorite, CIF, Root VA)"},
            "verified_only": _VERIFIED, "project": _PROJECT}, "required": ["name"]},
    },
    {
        "name": "knowledge_gaps",
        "description": "Open questions and conflicts in the knowledge: which items flag a gap in another, contradict it, or (with types) clarify it, with the quote that says so. Most of these are proposals for a person to confirm; each row says which. Optionally only those touching one item.",
        "inputSchema": {"type": "object", "properties": {
            "ref": {"type": "string", "description": "Only links touching this item: " + _REF},
            "types": {"type": "array", "items": {"type": "string", "enum": list(GAP_TYPES)}, "description": "Default: flags_gap and contradicts"},
            "verified_only": _VERIFIED, "project": _PROJECT}},
    },
    {
        "name": "knowledge_concepts",
        "description": "List the concepts the documents define, by type: actors (roles), data fields, events, systems, domain terms, tags, rules and constraints. Optionally those whose name contains some text, or only those a given section or document mentions (the fields of one BRD section). Use knowledge_get on a concept id for its full definition and evidence.",
        "inputSchema": {"type": "object", "properties": {
            "type": {"type": "string", "enum": list(CONCEPT_TYPES)},
            "contains": {"type": "string", "description": "Part of the name"},
            "mentioned_by": {"type": "string", "description": "A section or document: " + _REF},
            "limit": {"type": "integer", "minimum": 1, "maximum": 200}, "project": _PROJECT}},
    },
]  # fmt: skip
NAMES = {t["name"] for t in DEFS}


def _ref_match(var: str, param: str) -> str:
    """The node is the one named by the parameter: its external id, its source id, or a namespaced id ending in it."""
    return f"({var}.external_id = ${param} OR {var}.source_id = ${param} OR {var}.external_id ENDS WITH ('/' + ${param}))"


def _text(args: dict[str, Any], key: str, required: bool = True) -> str | None:
    v = args.get(key)
    if v is None and not required:
        return None
    if not isinstance(v, str) or not v.strip():
        raise ValueError(f"'{key}' is required")
    return v.strip()[:200]


def _int(args: dict[str, Any], key: str, default: int, low: int, high: int) -> int:
    try:
        return max(low, min(high, int(args.get(key) or default)))
    except (TypeError, ValueError):
        raise ValueError(f"'{key}' must be a number") from None


def _statuses(args: dict[str, Any]) -> list[str]:
    return ["verified"] if args.get("verified_only") else ["verified", "proposed"]


def _run(service: KnowledgeService, p: str | None, cypher: str, params: dict[str, Any]) -> dict[str, Any]:
    if service.graph is None:
        raise ValueError("the graph database (Neo4j) is not enabled on this server")
    proj = service.project(p)
    service.graph.ensure_synced(service.repo, proj)
    try:
        res = run_query(service.graph, proj, cypher, params)
    except QueryRefused as e:
        raise ValueError(str(e)) from None
    return {
        "project": proj,
        "columns": res.columns,
        "rows": res.rows,
        "records": res.records,
        "truncated": res.truncated,
    }


def impact(service: KnowledgeService, p: str | None, args: dict[str, Any]) -> dict[str, Any]:
    ref, depth = _text(args, "ref"), _int(args, "depth", 2, 1, 4)
    direction = args.get("direction") or "downstream"
    if direction not in ("downstream", "upstream"):
        raise ValueError("'direction' must be downstream or upstream")
    node = "(x:Knowledge {project: $project})-[rels:%s*1..%d]->(t:Knowledge {project: $project})"
    if direction == "upstream":
        node = "(t:Knowledge {project: $project})-[rels:%s*1..%d]->(x:Knowledge {project: $project})"
    cypher = (
        f"MATCH p = {node % (STRUCTURE, depth)} WHERE {_ref_match('t', 'ref')} AND x <> t AND all(r IN rels WHERE r.status IN $statuses) "
        "RETURN x.external_id AS id, x.kind AS kind, x.title AS title, min(length(p)) AS hops, collect(DISTINCT type(last(rels))) AS via "
        "ORDER BY hops, id LIMIT 200"
    )
    return _run(service, p, cypher, {"ref": ref, "statuses": _statuses(args)})


def path(service: KnowledgeService, p: str | None, args: dict[str, Any]) -> dict[str, Any]:
    a, b, hops = _text(args, "from"), _text(args, "to"), _int(args, "max_hops", 4, 1, 6)
    cypher = (
        f"MATCH (a:Knowledge {{project: $project}}), (b:Knowledge {{project: $project}}) WHERE {_ref_match('a', 'a_ref')} AND {_ref_match('b', 'b_ref')} "
        "WITH a, b LIMIT 1 "
        f"MATCH p = shortestPath((a)-[rels*1..{hops}]-(b)) WHERE all(r IN rels WHERE r.status IN $statuses) AND all(n IN nodes(p) WHERE n.project = $project) "
        "RETURN [n IN nodes(p) | n.external_id] AS items, [r IN relationships(p) | type(r) + ' (' + r.status + ')'] AS links, length(p) AS hops"
    )
    return _run(service, p, cypher, {"a_ref": a, "b_ref": b, "statuses": _statuses(args)})


def uses(service: KnowledgeService, p: str | None, args: dict[str, Any]) -> dict[str, Any]:
    q = _text(args, "name")
    cypher = (
        "MATCH (c:Knowledge {project: $project}) WHERE c.kind IN ['concept', 'rule'] "
        "AND (c.external_id = $q OR toLower(c.title) CONTAINS toLower($q) OR toLower(c.external_id) CONTAINS toLower($q)) "
        "WITH c ORDER BY size(c.title) LIMIT 5 "
        "OPTIONAL MATCH (d:Knowledge {project: $project})-[r]-(c) WHERE type(r) IN $usage AND r.status IN $statuses "
        "RETURN c.external_id AS concept, c.subtype AS type, c.title AS title, "
        "[x IN collect(DISTINCT {id: d.external_id, kind: d.kind, link: type(r), status: r.status}) WHERE x.id IS NOT NULL][..60] AS linked"
    )
    return _run(service, p, cypher, {"q": q, "usage": USAGE, "statuses": _statuses(args)})


def gaps(service: KnowledgeService, p: str | None, args: dict[str, Any]) -> dict[str, Any]:
    types = args.get("types") or ["flags_gap", "contradicts"]
    if not isinstance(types, list) or not types or any(t not in GAP_TYPES for t in types):
        raise ValueError(f"'types' must be a list of: {', '.join(GAP_TYPES)}")
    cypher = (
        "MATCH (a:Knowledge {project: $project})-[r]->(b:Knowledge {project: $project}) WHERE type(r) IN $types AND r.status IN $statuses "
        f"AND ($ref IS NULL OR {_ref_match('a', 'ref')} OR {_ref_match('b', 'ref')}) "
        "RETURN type(r) AS link, r.status AS status, a.external_id AS source, a.title AS source_title, b.external_id AS target, "
        "b.title AS target_title, r.evidence AS evidence ORDER BY link, status, source LIMIT 100"
    )
    return _run(service, p, cypher, {"ref": _text(args, "ref", required=False), "types": [RELS[t] for t in types], "statuses": _statuses(args)})  # fmt: skip


def concepts(service: KnowledgeService, p: str | None, args: dict[str, Any]) -> dict[str, Any]:
    kind = args.get("type")
    if kind is not None and kind not in CONCEPT_TYPES:
        raise ValueError(f"'type' must be one of: {', '.join(CONCEPT_TYPES)}")
    cypher = (
        "MATCH (c:Knowledge {project: $project}) WHERE c.kind IN ['concept', 'rule'] AND ($type IS NULL OR c.subtype = $type) "
        "AND ($q IS NULL OR toLower(c.title) CONTAINS toLower($q)) "
        "AND ($section IS NULL OR EXISTS { MATCH (s:Knowledge {project: $project})-[:MENTIONS]->(c) WHERE "
        + _ref_match("s", "section")
        + " }) RETURN c.external_id AS id, c.subtype AS type, c.title AS title ORDER BY title LIMIT $limit"
    )
    params = {"type": kind, "q": _text(args, "contains", required=False), "section": _text(args, "mentioned_by", required=False), "limit": _int(args, "limit", 50, 1, 200)}  # fmt: skip
    return _run(service, p, cypher, params)


HANDLERS = {"knowledge_impact": impact, "knowledge_path": path, "knowledge_uses": uses, "knowledge_gaps": gaps, "knowledge_concepts": concepts}  # fmt: skip
