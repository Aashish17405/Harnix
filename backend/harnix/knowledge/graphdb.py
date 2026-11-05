"""The knowledge graph in Neo4j: every knowledge source is a node, every relationship an edge, so the graph can be
browsed (http://localhost:7474) and traversed by the retrieval tools.

PostgreSQL (or SQLite) stays the source of truth: sources, versions, chunks, embeddings and edge decisions are written
there first. Neo4j is a mirror, rebuilt per project whenever the project's knowledge changed (an audit marker says
when), so it can be dropped and rebuilt at any time and can never hold anything the database does not.

    (:Knowledge:<Kind>[:<Subtype>] {uid, project, source_id, kind, external_id, title, version, subtype})   subtypes from ontology.json
    (:Knowledge)-[:<TYPE> {edge_id, project, status, origin, evidence}]->(:Knowledge)     rejected edges are left out
    (:KnowledgeProject {project, marker, synced_at, nodes, edges})

Same rules as the code graph (graph/store.py): every statement is a constant (labels and relationship types come from
the fixed KINDS / EDGE_TYPES lists, never from document or model text), values are parameters, reads have a timeout
and a LIMIT. Nodes hold ids, titles and kinds only; the text stays in the database.
"""

from __future__ import annotations

from typing import Any

from neo4j import ManagedTransaction

from harnix.graph.store import GraphStore
from harnix.knowledge.ontology import default
from harnix.knowledge.repo import EDGE_TYPES, KINDS, KnowledgeRepo

LABELS = {k: {"brd": "BRD"}.get(k, k.capitalize()) for k in KINDS}
RELS = {t: t.upper() for t in EDGE_TYPES}
MAX_EDGES = 500

# Stable identities, enforced by the database: a node is its `uid` (project + source id, which never change once the
# source exists), a relationship is its `edge_id` (the row id of the link). Reprocessing writes with MERGE on those keys,
# so a repeated or concurrent sync cannot make a second copy; the constraints make the database refuse one anyway.
SCHEMA = [
    "CREATE CONSTRAINT forge_knowledge_uid IF NOT EXISTS FOR (n:Knowledge) REQUIRE n.uid IS UNIQUE",
    "CREATE INDEX forge_knowledge_project IF NOT EXISTS FOR (n:Knowledge) ON (n.project)",
    "CREATE CONSTRAINT forge_knowledge_project_name IF NOT EXISTS FOR (n:KnowledgeProject) REQUIRE n.project IS UNIQUE",
    *[
        f"CREATE CONSTRAINT forge_knowledge_edge_{t} IF NOT EXISTS FOR ()-[x:{rel}]-() REQUIRE x.edge_id IS UNIQUE"
        for t, rel in RELS.items()
    ],
]
_NODE = {
    k: "UNWIND $rows AS r MERGE (n:Knowledge {uid: r.uid}) SET n = r SET n:" + label
    + " REMOVE n:" + ":".join(other for other in LABELS.values() if other != label)  # a reclassified source loses its old label
    for k, label in LABELS.items()
}  # fmt: skip
_EDGE = {
    t: "UNWIND $rows AS r MATCH (a:Knowledge {uid: r.a}), (b:Knowledge {uid: r.b}) "
    f"MERGE (a)-[x:{rel} {{edge_id: r.edge_id}}]->(b) SET x.project = r.project, x.status = r.status, "
    "x.origin = r.origin, x.evidence = r.evidence"
    for t, rel in RELS.items()
}  # fmt: skip
# extracted concepts also carry the label of their ontology sub-type (:Concept:Actor, :Rule:Constraint)
_SUBTYPE = {
    t: f"MATCH (n:Knowledge {{project: $p}}) WHERE n.subtype = $t SET n:{t.capitalize()}"
    for t, parent in default().types.items()
    if parent in ("concept", "rule") and t.isalpha()
}  # fmt: skip
_SUBTYPE_STALE = {
    t: f"MATCH (n:Knowledge:{t.capitalize()} {{project: $p}}) WHERE n.subtype IS NULL OR n.subtype <> $t REMOVE n:{t.capitalize()}"
    for t in _SUBTYPE
}  # fmt: skip
# variable-length bounds cannot be parameters: one constant statement per depth
_TRAVERSE = {
    d: f"MATCH (s:Knowledge {{uid: $uid}})-[rels*1..{d}]-(:Knowledge) "
    "WHERE all(r IN rels WHERE r.status IN $statuses AND ($types IS NULL OR type(r) IN $types)) "
    "UNWIND rels AS r RETURN DISTINCT r.edge_id AS id LIMIT $limit"
    for d in (1, 2, 3)
}  # fmt: skip


def uid(project: str, source_id: str) -> str:
    return f"{project}|{source_id}"


class KnowledgeGraph:
    def __init__(self, store: GraphStore):
        self.store = store
        self._schema = False

    def _ensure_schema(self) -> None:
        if not self._schema:

            def run(tx: ManagedTransaction) -> None:
                for stmt in SCHEMA:
                    tx.run(stmt).consume()  # type: ignore[arg-type]

            self.store._write(run)  # noqa: SLF001 - same package family, shares the driver and timeouts
            self._schema = True

    def marker(self, project: str) -> str | None:
        def run(tx: ManagedTransaction) -> str | None:
            rec = tx.run("MATCH (p:KnowledgeProject {project: $p}) RETURN p.marker AS m", p=project).single()
            return rec["m"] if rec else None

        return self.store._read(run)  # noqa: SLF001

    def project_info(self, project: str) -> dict[str, Any] | None:
        """What the copy holds for a project: node and relationship counts, when it was last rebuilt, its marker."""

        def run(tx: ManagedTransaction) -> dict[str, Any] | None:
            rec = tx.run(
                "MATCH (p:KnowledgeProject {project: $p}) "
                "RETURN p.nodes AS nodes, p.edges AS edges, toString(p.synced_at) AS synced_at, p.marker AS marker",
                p=project,
            ).single()
            return dict(rec) if rec else None

        return self.store._read(run)  # noqa: SLF001

    def sync(self, repo: KnowledgeRepo, project: str) -> dict[str, Any]:
        """Bring the project's mirror in line with the database, in one transaction: MERGE every source and link on its
        stable key (uid, edge_id), then remove what the database no longer holds (a deleted source, a rejected link).
        Repeating it, or running it twice at once, changes nothing and cannot duplicate anything."""
        # ponytail: writes every row each time (MERGE + SET); per-source upserts of only what changed if projects reach 100k
        self._ensure_schema()
        sources = repo.sources(project, limit=100_000)
        by_kind: dict[str, list[dict[str, Any]]] = {}
        for s in sources:
            ver = repo.version(s.id) if s.kind in ("concept", "rule") else None
            by_kind.setdefault(s.kind, []).append({
                "uid": uid(project, s.id), "project": project, "source_id": s.id, "kind": s.kind,
                "external_id": s.external_id, "title": s.title, "version": s.current_version,
                "subtype": (ver.meta or {}).get("concept_type") if ver else None,
            })  # fmt: skip
        by_type: dict[str, list[dict[str, Any]]] = {}
        for e in repo.edges(project, limit=1_000_000):
            if e.status != "rejected" and e.type in RELS:
                by_type.setdefault(e.type, []).append({
                    "a": uid(project, e.src_id), "b": uid(project, e.dst_id), "edge_id": e.id, "project": project,
                    "status": e.status, "origin": e.origin, "evidence": (e.evidence or "")[:300],
                })  # fmt: skip
        marker = repo.change_marker(project)
        counts = {"nodes": len(sources), "edges": sum(len(v) for v in by_type.values())}
        shape = {"labels": {k: len(v) for k, v in by_kind.items()}, "relationships": {RELS[t]: len(v) for t, v in by_type.items()}}  # fmt: skip

        def run(tx: ManagedTransaction) -> None:
            for kind, rows in by_kind.items():
                if kind in _NODE:
                    tx.run(_NODE[kind], rows=rows).consume()  # type: ignore[arg-type]
            for t, stmt in _SUBTYPE.items():
                tx.run(_SUBTYPE_STALE[t], p=project, t=t).consume()  # type: ignore[arg-type]
                tx.run(stmt, p=project, t=t).consume()  # type: ignore[arg-type]
            for t, rows in by_type.items():
                tx.run(_EDGE[t], rows=rows).consume()  # type: ignore[arg-type]
            keep_edges = [r["edge_id"] for rows in by_type.values() for r in rows]
            keep_nodes = [r["uid"] for rows in by_kind.values() for r in rows]
            tx.run("MATCH (:Knowledge {project: $p})-[x]->() WHERE x.edge_id IS NULL OR NOT x.edge_id IN $ids DELETE x", p=project, ids=keep_edges).consume()  # type: ignore[arg-type]  # fmt: skip
            tx.run("MATCH (n:Knowledge {project: $p}) WHERE NOT n.uid IN $uids DETACH DELETE n", p=project, uids=keep_nodes).consume()  # type: ignore[arg-type]  # fmt: skip
            tx.run(
                "MERGE (p:KnowledgeProject {project: $p}) SET p.marker = $m, p.synced_at = datetime(), "
                "p.nodes = $n, p.edges = $e",
                p=project, m=marker, n=counts["nodes"], e=counts["edges"],
            ).consume()  # fmt: skip

        self.store._write(run)  # noqa: SLF001
        return {"project": project, **counts, **shape}

    def node(self, project: str, source_id: str) -> dict[str, Any] | None:
        """The mirror's node for one source: its labels, stored properties and every relationship it has there.
        None when the node is not in Neo4j (not synced yet)."""

        def run(tx: ManagedTransaction) -> dict[str, Any] | None:
            rec = tx.run(
                "MATCH (n:Knowledge {uid: $uid}) RETURN labels(n) AS labels, properties(n) AS props",
                uid=uid(project, source_id),
            ).single()
            if rec is None:
                return None
            rels = tx.run(
                "MATCH (n:Knowledge {uid: $uid})-[r]-(m:Knowledge) "
                "RETURN type(r) AS type, startNode(r) = n AS outgoing, r.status AS status, r.origin AS origin, "
                "m.external_id AS other, m.kind AS kind, m.title AS title LIMIT $limit",
                uid=uid(project, source_id), limit=MAX_EDGES,
            )  # fmt: skip
            return {"labels": list(rec["labels"]), "properties": dict(rec["props"]), "relationships": [dict(x) for x in rels]}  # fmt: skip

        return self.store._read(run)  # noqa: SLF001

    def ensure_synced(self, repo: KnowledgeRepo, project: str) -> bool:
        """Rebuild when the database changed since the last sync. True when a rebuild ran."""
        if self.marker(project) == repo.change_marker(project):
            return False
        self.sync(repo, project)
        return True

    def traverse(
        self, project: str, source_id: str, depth: int, types: list[str] | None, statuses: tuple[str, ...]
    ) -> list[str]:
        """Ids of the edges reachable from a source within `depth` hops (either direction)."""
        stmt = _TRAVERSE[max(1, min(depth, 3))]
        rel_types = [RELS[t] for t in types if t in RELS] if types else None

        def run(tx: ManagedTransaction) -> list[str]:
            res = tx.run(stmt, uid=uid(project, source_id), statuses=list(statuses), types=rel_types, limit=MAX_EDGES)  # type: ignore[arg-type]  # fmt: skip
            return [r["id"] for r in res]

        return self.store._read(run)  # noqa: SLF001


_shared: dict[tuple[str, str, str], KnowledgeGraph] = {}


def for_settings(settings) -> KnowledgeGraph | None:  # noqa: ANN001
    """The Neo4j mirror when the graph database is enabled (FORGE_GRAPH_ENABLED), one driver per server."""
    if not settings.graph_enabled or not settings.neo4j_password:
        return None
    key = (settings.neo4j_uri, settings.neo4j_user, settings.neo4j_database)
    if key not in _shared:
        _shared[key] = KnowledgeGraph(
            GraphStore(settings.neo4j_uri, settings.neo4j_user, settings.neo4j_password, settings.neo4j_database,
                       query_timeout=settings.graph_query_timeout_seconds)
        )  # fmt: skip
    return _shared[key]
