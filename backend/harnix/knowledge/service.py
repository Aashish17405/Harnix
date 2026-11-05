"""Retrieval over the knowledge base: hybrid search (vector + keyword, fused), exact lookup, relationship traversal.

Storage-agnostic on purpose: callers (the MCP server, the context builder, the API) use these methods and the tool
handlers in tools.py, never the tables. `projects` is the access list of whoever is asking (None = every project).
"""

from __future__ import annotations

import math
import re
from collections import defaultdict
from typing import TYPE_CHECKING, Any, Protocol

from harnix.graph.store import GraphUnavailable
from harnix.knowledge.embeddings import Embedder, cosine, doc_model
from harnix.knowledge.repo import KnowledgeRepo
from harnix.persistence.models import KnowledgeChunk, KnowledgeEdge, KnowledgeSource

if TYPE_CHECKING:
    from harnix.knowledge.graphdb import KnowledgeGraph

_TOK = re.compile(r"[A-Za-z0-9_]+")
STOP = {"the", "a", "an", "and", "or", "of", "to", "in", "is", "for", "on", "with", "as", "be", "it", "that", "this", "by", "are"}  # fmt: skip
RRF_K = 60
# (keyword, vector) weight in the rank fusion. Measured with OpenAI embeddings on 20 questions over two small corpora:
# equal weights ranked worse than vectors alone on one of them, counting keywords half gave the best MRR on both.
DEFAULT_WEIGHTS = (0.5, 1.0)
MAX_NODES = 60


class AccessDenied(Exception):
    pass


class NotFoundError(Exception):
    pass


class VectorIndex(Protocol):
    def search(self, project: str, qvec: list[float], model: str, kinds: list[str] | None, limit: int) -> list[tuple[str, float]]: ...  # fmt: skip


class PythonIndex:
    """Exact cosine in Python over the embeddings stored in the chunk rows: fine for a POC-sized corpus."""

    def __init__(self, repo: KnowledgeRepo):
        self.repo = repo

    def search(self, project, qvec, model, kinds, limit):  # noqa: ANN001
        scored = [
            (c.id, cosine(qvec, c.embedding))
            for c, _ in self.repo.project_chunks(project, kinds)
            if c.embedding and c.embedding_model == model and len(c.embedding) == len(qvec)
        ]
        return sorted(scored, key=lambda x: -x[1])[:limit]


_CAMEL = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|[0-9]+")


def tokens(text: str) -> list[str]:
    """Lower-case words; identifiers are also split (shipping_cents, orderTotal) so prose finds code."""
    out: list[str] = []
    for w in _TOK.findall(text):
        parts = (
            [p.lower() for p in _CAMEL.findall(w.replace("_", " "))] if ("_" in w or not w.islower()) else []
        )
        out += [w.lower(), *(p for p in parts if p != w.lower())]
    return [t for t in out if t not in STOP and len(t) > 1]


def bm25(
    query: str, docs: list[tuple[str, str]], k1: float = 1.5, b: float = 0.75
) -> list[tuple[str, float]]:
    q = set(tokens(query))
    toks = {i: tokens(t) for i, t in docs}
    n = len(docs) or 1
    avg = sum(len(t) for t in toks.values()) / n or 1.0
    df = {w: sum(1 for t in toks.values() if w in t) for w in q}
    out = []
    for i, t in toks.items():
        score = 0.0
        for w in q:
            tf = t.count(w)
            if tf:
                idf = math.log(1 + (n - df[w] + 0.5) / (df[w] + 0.5))
                score += idf * tf * (k1 + 1) / (tf + k1 * (1 - b + b * len(t) / avg))
        if score > 0:
            out.append((i, score))
    return sorted(out, key=lambda x: -x[1])


def make_service(
    settings, repo: KnowledgeRepo, embedder: Embedder, projects: list[str] | None
) -> KnowledgeService:
    """The service with the vector index the operator chose (python by default, pgvector on PostgreSQL)."""
    index: VectorIndex | None = None
    if settings.knowledge_vector == "pgvector":
        from harnix.knowledge.pgvector import PgVectorIndex

        index = PgVectorIndex(repo)
    from harnix.knowledge.graphdb import for_settings

    return KnowledgeService(
        repo,
        embedder,
        projects,
        index,
        (settings.knowledge_keyword_weight, 1.0),
        graph=for_settings(settings),
        min_relevance=settings.min_relevance,
    )


class KnowledgeService:
    def __init__(self, repo: KnowledgeRepo, embedder: Embedder, projects: list[str] | None = None, index: VectorIndex | None = None, weights: tuple[float, float] = DEFAULT_WEIGHTS, graph: KnowledgeGraph | None = None, min_relevance: float = 0.0):  # fmt: skip
        self.repo, self.embedder, self.projects, self.weights = repo, embedder, projects, weights
        self.min_relevance = min_relevance  # see Settings.knowledge_min_relevance; 0 = no floor
        self.graph = graph  # Neo4j mirror for traversal; None = traverse the edges table
        self._qcache: dict[str, list[float]] = {}
        self.index = index or PythonIndex(repo)

    def project(self, requested: str | None) -> str:
        if requested is None:
            if self.projects is not None and len(self.projects) == 1:
                return self.projects[0]
            raise ValueError("'project' is required")
        if self.projects is not None and requested not in self.projects:
            raise AccessDenied(f"No access to project '{requested}'")
        return requested

    def embed_query(self, query: str) -> list[float]:
        """One embedding call per distinct query for the life of the service (a bundle asks several times)."""
        if query not in self._qcache:
            if len(self._qcache) > 256:
                self._qcache.clear()
            self._qcache[query] = self.embedder.embed([query])[0]
        return self._qcache[query]

    def similarity(self, project: str | None, query: str, source_ids: list[str]) -> dict[str, float]:
        """Best cosine between the query and any chunk of each source (sources without embeddings are left out)."""
        proj = self.project(project)
        qv = self.embed_query(query)
        out: dict[str, float] = {}
        for c in self.repo.chunks_of(proj, source_ids):
            if c.embedding and c.embedding_model == doc_model(self.embedder) and len(c.embedding) == len(qv):
                out[c.source_id] = max(out.get(c.source_id, -1.0), cosine(qv, c.embedding))
        return out

    def chunk_scores(
        self, project: str | None, query: str, source_id: str
    ) -> list[tuple[KnowledgeChunk, float]]:
        """Every chunk of one source with its cosine similarity to the query, in document order (0.0 when the chunk
        has no current vector)."""
        proj = self.project(project)
        qv = self.embed_query(query)
        model = doc_model(self.embedder)
        out = []
        for c in sorted(self.repo.chunks_of(proj, [source_id]), key=lambda c: c.ordinal):
            ok = c.embedding and c.embedding_model == model and len(c.embedding) == len(qv)
            out.append((c, cosine(qv, c.embedding) if ok else 0.0))  # type: ignore[arg-type]
        return out

    # ---- search ---------------------------------------------------------------------------------------
    def search(
        self, project: str | None, query: str, kinds: list[str] | None = None, top_k: int = 8,
        mode: str = "hybrid", weights: tuple[float, float] | None = None, explain: bool = False,
    ) -> dict[str, Any]:  # fmt: skip
        """`mode` (hybrid | vector | keyword) and `weights` (keyword, vector) exist for evaluation; the MCP tool and
        the API always use the default. `explain` adds the three rankings the result was fused from, chunk by chunk
        (only the context builder asks: it is what the dashboard's drill-down shows)."""
        proj = self.project(project)
        pool = self.repo.project_chunks(proj, kinds)
        by_id: dict[str, tuple[KnowledgeChunk, KnowledgeSource]] = {c.id: (c, s) for c, s in pool}
        lexical = bm25(query, [(c.id, f"{s.external_id} {s.title} {c.heading} {c.text}") for c, s in pool])[
            :50
        ]
        vector: list[tuple[str, float]] = []
        qv: list[float] = []
        note = ""
        try:
            qv = self.embed_query(query)
            vector = [x for x in self.index.search(proj, qv, doc_model(self.embedder), kinds, 50) if x[1] > 0]
            if not vector and pool:
                note = f"no stored vectors from {self.embedder.name}: keyword results only"
        except Exception as e:  # noqa: BLE001 - search degrades to keywords instead of failing
            note = f"vector search unavailable ({type(e).__name__}): keyword results only"
        fused: dict[str, float] = defaultdict(float)
        how: dict[str, set[str]] = defaultdict(set)
        for (name, ranked), w in zip(
            (("keyword", lexical), ("vector", vector)), weights or self.weights, strict=True
        ):
            if (name == "keyword" and mode == "vector") or (name == "vector" and mode == "keyword"):
                continue
            for rank, (cid, _) in enumerate(ranked):
                fused[cid] += w / (RRF_K + rank + 1)
                how[cid].add(name)
        best: dict[str, str] = {}
        for cid in sorted(fused, key=lambda x: -fused[x]):
            best.setdefault(by_id[cid][1].id, cid)
        hits = []
        ranked_chunks: dict[str, list[str]] = defaultdict(list)
        for cid in sorted(fused, key=lambda x: -fused[x]):
            if len(ranked_chunks[by_id[cid][1].id]) < 2:
                ranked_chunks[by_id[cid][1].id].append(cid)
        for cid in list(best.values())[: max(1, min(top_k, 25))]:
            c, s = by_id[cid]
            ver = self.repo.version(s.id)
            hits.append({
                "source_id": s.id, "kind": s.kind, "external_id": s.external_id, "title": s.title,
                "version": s.current_version, "content_hash": (ver.content_hash if ver else "")[:16],
                "uri": s.uri, "origin": s.origin, "score": round(fused[cid], 5), "matched_by": sorted(how[cid]),
                "heading": c.heading, "snippet": c.text[:600],
                "chunk_ids": list(ranked_chunks[s.id]),  # the pieces that matched (the dashboard marks exactly these)
                "matched_text": [
                    (by_id[x][0].heading + "\n" if by_id[x][0].heading else "") + by_id[x][0].text[:1000]
                    for x in sorted(ranked_chunks[s.id], key=lambda x: by_id[x][0].ordinal)
                ],
            })  # fmt: skip
        out: dict[str, Any] = {
            "project": proj,
            "query": query,
            "hits": hits,
            "note": note,
            "embedding_model": self.embedder.name,
        }
        if explain:
            kw_rank = {cid: i + 1 for i, (cid, _) in enumerate(lexical)}
            vec_rank = {cid: i + 1 for i, (cid, _) in enumerate(vector)}

            def row(cid: str, **extra: Any) -> dict[str, Any]:
                c, s = by_id[cid]
                return {"chunk_id": cid, "source_id": s.id, "external_id": s.external_id, "kind": s.kind, "heading": c.heading, "ordinal": c.ordinal, "snippet": c.text[:160], **extra}  # fmt: skip

            w = weights or self.weights
            out["explain"] = {
                "mode": mode, "chunks_searched": len(pool), "rrf_k": RRF_K, "weights": {"keyword": w[0], "vector": w[1]},
                "keyword": [row(cid, rank=i + 1, score=round(sc, 3)) for i, (cid, sc) in enumerate(lexical[:20])],
                "vector": [row(cid, rank=i + 1, score=round(sc, 4)) for i, (cid, sc) in enumerate(vector[:20])],
                "fused": [
                    row(cid, rrf=round(fused[cid], 5), keyword_rank=kw_rank.get(cid), vector_rank=vec_rank.get(cid))
                    for cid in sorted(fused, key=lambda x: -fused[x])[:20]
                ],
                "embedding": {"model": self.embedder.name, "dims": len(qv), "head": [round(x, 4) for x in qv[:8]], "nonzero": sum(1 for x in qv if x), "top": [[i, round(qv[i], 4)] for i in sorted(range(len(qv)), key=lambda j: -abs(qv[j]))[:8] if qv[i]]},
            }  # fmt: skip
        return out

    # ---- exact lookup ----------------------------------------------------------------------------------
    def lookup(self, proj: str, ref: str) -> KnowledgeSource:
        """A source by id or external id; a short id (`US-003`) also matches one backlog-qualified id
        (`BRD-03/US-003`) when exactly one exists. Ambiguous or unknown: NotFoundError naming the candidates."""
        src = self.repo.resolve(proj, ref)
        if src is not None:
            return src
        cands = [x for x in self.repo.sources(proj, limit=100_000) if x.external_id.endswith("/" + ref)]
        if len(cands) == 1:
            return cands[0]
        if cands:
            names = ", ".join(sorted(c.external_id for c in cands)[:10])
            raise NotFoundError(f"'{ref}' is ambiguous in project '{proj}': use one of {names}")
        raise NotFoundError(f"No source '{ref}' in project '{proj}'")

    def get(
        self,
        project: str | None,
        ref: str,
        version: int | None = None,
        max_chars: int = 6000,
        offset: int = 0,
    ) -> dict[str, Any]:
        proj = self.project(project)
        src = self.lookup(proj, ref)
        ver = self.repo.version(src.id, version)
        if ver is None:
            raise NotFoundError(f"Source '{ref}' has no version {version}")
        return {
            "project": proj, "source_id": src.id, "kind": src.kind, "external_id": src.external_id, "title": src.title,
            "uri": src.uri, "origin": src.origin, "version": ver.version, "current_version": src.current_version,
            "content_hash": ver.content_hash[:16], "ingestion_state": src.ingestion_state, "metadata": ver.meta or {},
            "content": ver.content[offset : offset + max_chars], "offset": offset, "length": len(ver.content),
            "truncated": len(ver.content) > offset + max_chars,
            "next_offset": offset + max_chars if len(ver.content) > offset + max_chars else None,
        }  # fmt: skip

    def list_sources(self, project: str | None, kind: str | None = None, limit: int = 100) -> dict[str, Any]:
        proj = self.project(project)
        return {"project": proj, "sources": [
            {"source_id": s.id, "kind": s.kind, "external_id": s.external_id, "title": s.title, "version": s.current_version, "state": s.ingestion_state, "origin": s.origin}  # fmt: skip
            for s in self.repo.sources(proj, kind, limit)
        ]}  # fmt: skip

    # ---- relationships ---------------------------------------------------------------------------------
    def relations(
        self, project: str | None, ref: str, depth: int = 1, types: list[str] | None = None,
        statuses: tuple[str, ...] = ("verified", "proposed"),
    ) -> dict[str, Any]:  # fmt: skip
        proj = self.project(project)
        start = self.lookup(proj, ref)
        depth = max(1, min(depth, 3))
        seen = {start.id}
        frontier = [start.id]
        edges: dict[str, KnowledgeEdge] = {}
        via = "database"
        if self.graph is not None:
            try:
                self.graph.ensure_synced(self.repo, proj)
                found = self.repo.edges_by_ids(self.graph.traverse(proj, start.id, depth, types, statuses))
                for e in found:
                    if e.project == proj and e.status in statuses and len(seen) < MAX_NODES:
                        edges[e.id] = e
                        seen.update((e.src_id, e.dst_id))
                via, frontier, depth = "neo4j", [], 0  # done: skip the table walk below
            except GraphUnavailable as ex:
                via = f"database (Neo4j unavailable: {ex})"
        for _ in range(depth):
            nxt: list[str] = []
            for e in self.repo.edges_of(frontier, statuses):
                if e.project != proj or (types and e.type not in types) or e.id in edges:
                    continue
                edges[e.id] = e
                for sid in (e.src_id, e.dst_id):
                    if sid not in seen and len(seen) < MAX_NODES:
                        seen.add(sid)
                        nxt.append(sid)
            frontier = nxt
            if not frontier:
                break
        nodes = {sid: self.repo.get_source(sid) for sid in seen}

        def label(sid: str) -> str:
            n = nodes.get(sid)
            return n.external_id if n else sid

        return {
            "project": proj,
            "root": {
                "source_id": start.id,
                "external_id": start.external_id,
                "kind": start.kind,
                "version": start.current_version,
            },  # fmt: skip
            "nodes": [
                {
                    "source_id": s.id,
                    "kind": s.kind,
                    "external_id": s.external_id,
                    "title": s.title,
                    "version": s.current_version,
                }
                for s in nodes.values()
                if s
            ],  # fmt: skip
            "edges": [
                {
                    "edge_id": e.id,
                    "from": label(e.src_id),
                    "to": label(e.dst_id),
                    "type": e.type,
                    "status": e.status,
                    "authoritative": e.status == "verified",
                    "origin": e.origin,
                    "evidence": e.evidence,
                    "stale": self._stale(e, nodes),
                }  # fmt: skip
                for e in edges.values()
            ],
            "truncated": len(seen) >= MAX_NODES,
            "via": via,
        }

    @staticmethod
    def _stale(e: KnowledgeEdge, nodes: dict[str, Any]) -> bool:
        """True when either end changed since the edge was recorded: the relationship needs another look."""
        s, d = nodes.get(e.src_id), nodes.get(e.dst_id)
        if any(x and x.kind in ("concept", "rule") for x in (s, d)):
            return False  # evidence merging bumps their version without changing what they mean
        return bool((s and s.current_version != e.src_version) or (d and d.current_version != e.dst_version))
