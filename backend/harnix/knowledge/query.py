"""Queries over the knowledge graph in Neo4j, typed as Cypher or asked in plain language, the way a graph database
browser runs them, but behind the harness's rules.

The query text is untrusted (a person typed it, or a model wrote it from a question), so:
* it runs in a READ transaction: Neo4j itself refuses any write, whatever the text says;
* file access and admin features are refused before it runs (LOAD CSV, procedure calls other than a few read-only
  schema ones, USE <database>, periodic commits);
* the server-side transaction timeout and a cap on records bound what one query can cost;
* results are scoped: only nodes of the asked project (and links between them) come back, whatever the query matched,
  so one project cannot read another's graph (or the code graph) through a hand-written query;
* `$project` is always passed as a parameter, so a query never needs the project name pasted into its text.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any

from neo4j import ManagedTransaction
from neo4j.graph import Node, Path, Relationship

from harnix.knowledge.graphdb import LABELS, RELS, KnowledgeGraph
from harnix.knowledge.ontology import default
from harnix.models.base import Message, ModelProvider, TextBlock

MAX_RECORDS = 500
MAX_QUERY_CHARS = 4000
_ALLOWED_CALLS = {"db.labels", "db.relationshiptypes", "db.propertykeys", "db.schema.visualization"}
_CALL = re.compile(r"\bCALL\s+([A-Za-z_][\w.]*)\s*\(", re.I)
_REFUSED = [
    (re.compile(r"\bLOAD\s+CSV\b", re.I), "LOAD CSV reads files: not allowed here"),
    (re.compile(r"\bUSING\s+PERIODIC\s+COMMIT\b", re.I), "periodic commits write: not allowed here"),
    (re.compile(r"^\s*USE\s+\w+", re.I | re.M), "switching databases (USE) is not allowed here"),
    (
        re.compile(
            r"\b(?:CREATE|DROP|ALTER|GRANT|DENY|REVOKE|START|STOP)\s+(?:DATABASE|USER|ROLE|INDEX|CONSTRAINT|ALIAS)\b",
            re.I,
        ),
        "administration commands are not allowed here",
    ),  # fmt: skip
]


class QueryRefused(ValueError):
    pass


def check_cypher(cypher: str) -> None:
    """Refuse what a read-only, project-scoped browser must not run (writes are also refused by the read transaction)."""
    text = cypher.strip()
    if not text:
        raise QueryRefused("Write a query")
    if len(text) > MAX_QUERY_CHARS:
        raise QueryRefused(f"The query is longer than {MAX_QUERY_CHARS} characters")
    no_strings = re.sub(
        r"'(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\"", "''", text
    )  # keywords inside quotes do not count
    for rx, why in _REFUSED:
        if rx.search(no_strings):
            raise QueryRefused(why)
    calls = _CALL.findall(no_strings)
    for name in calls:
        if name.lower() not in _ALLOWED_CALLS:
            raise QueryRefused(f"calling procedure '{name}' is not allowed here (allowed: db.labels(), db.relationshipTypes(), db.propertyKeys(), db.schema.visualization())")  # fmt: skip
    # every query is about one project: without $project, counts and lists would cover every project's graph
    if "$project" not in no_strings and not calls:
        raise QueryRefused(
            "Restrict the query to this project with $project, e.g. MATCH (n:Knowledge {project: $project}) ..."
        )


@dataclass
class QueryResult:
    columns: list[str] = field(default_factory=list)
    rows: list[list[Any]] = field(default_factory=list)  # JSON-safe values, nodes and links shown by id
    nodes: list[dict[str, Any]] = field(default_factory=list)  # in the dashboard's graph shape
    edges: list[dict[str, Any]] = field(default_factory=list)
    records: int = 0
    truncated: bool = False
    hidden: int = 0  # nodes the query matched outside this project (not returned)
    ms: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {**self.__dict__}


def _node(n: Node) -> dict[str, Any]:
    p = dict(n)
    return {
        "id": p.get("source_id") or n.element_id, "kind": p.get("kind") or next(iter(n.labels), "node"),
        "external_id": p.get("external_id") or p.get("project") or n.element_id, "title": p.get("title") or "",
        "version": p.get("version") or 0, "origin": "ingest", "state": "ok", "in_bundle": False, "subtype": p.get("subtype"),
    }  # fmt: skip


def _value(v: Any, keep: dict[str, Any], rels: dict[str, Any], scope: tuple[str, list[int]]) -> Any:
    """A JSON-safe cell; nodes and links found inside are collected (only this project's)."""
    project, hidden = scope
    if isinstance(v, Node):
        if dict(v).get("project") != project:
            hidden[0] += 1
            return "(outside this project)"
        if "Knowledge" in v.labels:
            keep[v.element_id] = v
        p = dict(v)
        return {"labels": sorted(v.labels), **{k: p[k] for k in ("external_id", "title", "kind", "subtype", "version", "project", "nodes", "edges") if k in p}}  # fmt: skip
    if isinstance(v, Relationship):
        rels[v.element_id] = v
        return {"type": v.type, **{k: v[k] for k in ("status", "origin") if k in v}}
    if isinstance(v, Path):
        for n in v.nodes:
            _value(n, keep, rels, scope)
        for r in v.relationships:
            rels[r.element_id] = r
        return f"path of {len(v.relationships)} links"
    if isinstance(v, list):
        return [_value(x, keep, rels, scope) for x in v[:100]]
    if isinstance(v, dict):
        return {str(k): _value(x, keep, rels, scope) for k, x in list(v.items())[:100]}
    if v is None or isinstance(v, str | int | float | bool):
        return v
    return str(v)


def run_query(
    graph: KnowledgeGraph, project: str, cypher: str, params: dict[str, Any] | None = None
) -> QueryResult:
    check_cypher(cypher)
    started = time.perf_counter()
    out = QueryResult()
    keep: dict[str, Node] = {}
    rels: dict[str, Relationship] = {}
    hidden = [0]

    def run(tx: ManagedTransaction) -> None:
        res = tx.run(cypher, {**(params or {}), "project": project})  # type: ignore[arg-type]
        out.columns = list(res.keys())
        for rec in res:
            if out.records >= MAX_RECORDS:
                out.truncated = True
                break
            out.records += 1
            out.rows.append([_value(rec[c], keep, rels, (project, hidden)) for c in out.columns])
        res.consume()

    graph.store._read(run)  # noqa: SLF001 - read transaction with the server-side timeout: writes are refused by Neo4j
    ids = {eid: (n.get("source_id") or eid) for eid, n in keep.items()}
    out.nodes = [_node(n) for n in keep.values()]
    for r in rels.values():
        s, t = r.start_node, r.end_node
        if s is None or t is None or s.element_id not in ids or t.element_id not in ids:
            continue  # a link to something outside the project (or to a node the query did not return)
        out.edges.append({
            "id": r.get("edge_id") or r.element_id, "from": ids[s.element_id], "to": ids[t.element_id], "type": r.type.lower(),
            "status": r.get("status") or "verified", "origin": r.get("origin") or "", "evidence": r.get("evidence") or "",
            "stale": False, "in_bundle": False,
        })  # fmt: skip
    out.hidden = hidden[0]
    out.ms = round((time.perf_counter() - started) * 1000, 1)
    return out


# ---- plain language -> Cypher ----------------------------------------------------------------------------------------


def schema_text() -> str:
    o = default()
    subtypes = [t.capitalize() for t, parent in o.types.items() if parent in ("concept", "rule")]
    return (
        "Nodes: (:Knowledge) with one kind label: " + ", ".join(f":{v}" for v in LABELS.values())
        + ". Extracted concepts may also carry " + ", ".join(f":{s}" for s in subtypes) + ".\n"
        "Node properties: project, source_id, kind, external_id (e.g. 'BRD-03/US-003', 'E-001', 'V-1', 'concept:favorite account'), title, version, subtype.\n"
        "Relationships (directed): " + ", ".join(f":{r}" for r in RELS.values()) + ". Properties: status ('verified' or 'proposed'), origin, evidence.\n"
        "Meaning: (story)-[:DERIVES_FROM]->(feature)-[:DERIVES_FROM]->(epic)-[:DERIVES_FROM]->(brd); (document)-[:MENTIONS]->(concept or rule); "
        "(rule)-[:CONSTRAINS]->(concept); (story)-[:DEPENDS_ON]->(story or rule); (code)-[:IMPLEMENTS]->(story); (test)-[:TESTS]->(story or code)."
    )  # fmt: skip


SYSTEM = (
    "You translate a question about a project's knowledge graph into ONE read-only Cypher query for Neo4j. "
    "Return ONLY a JSON object, no prose."
)
PROMPT = """{schema}

Rules:
- Read only: MATCH / OPTIONAL MATCH / WHERE / WITH / RETURN / ORDER BY / LIMIT / UNWIND / aggregation. Never CREATE, MERGE, SET, DELETE, REMOVE, LOAD CSV or CALL a procedure.
- Always restrict every node to the project with the parameter: {{project: $project}} or WHERE n.project = $project.
- When the answer is about things and how they connect, RETURN the nodes and relationships (paths are fine) so they can be drawn as a graph; for counts or lists, return named columns.
- Add LIMIT 100 unless the question asks for a number.
- Match ids with external_id, names with toLower(n.title) CONTAINS toLower('...').

JSON shape: {{"cypher": "...", "explanation": "one sentence on what the query returns"}}

Question: {question}"""


def nl_to_cypher(provider: ModelProvider, question: str, timeout: float = 60.0) -> dict[str, str]:
    """A model writes the query; it is untrusted like any query: checked here and run read-only and scoped."""
    prompt = PROMPT.replace("{schema}", schema_text()).replace("{question}", question.strip()[:1000]).replace("{{", "{").replace("}}", "}")  # fmt: skip
    resp = provider.complete(system=SYSTEM, messages=[Message("user", [TextBlock(prompt)])], tools=[], max_tokens=1200, timeout=timeout, thinking="low")  # fmt: skip
    text = resp.text or ""
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise QueryRefused("The model did not return a query")
    data = json.loads(text[start : end + 1])
    cypher = str(data.get("cypher") or "").strip()
    check_cypher(cypher)
    return {"cypher": cypher, "explanation": str(data.get("explanation") or "")[:400]}
