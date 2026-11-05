"""Database access for the knowledge base (the only place that writes its tables). Reuses the Store's engine,
sessions and redactor; every text that is persisted passes through the redactor."""

from __future__ import annotations

import functools
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError

from harnix.knowledge.ontology import default, node_type
from harnix.persistence.models import (
    ContextBundle,
    KnowledgeAudit,
    KnowledgeChunk,
    KnowledgeEdge,
    KnowledgeSource,
    KnowledgeVersion,
    utcnow,
)
from harnix.persistence.store import Store, new_id

KINDS = (
    "brd",
    "requirement",
    "epic",
    "feature",
    "story",
    "ticket",
    "design",
    "code",
    "test",
    "doc",
    "concept",
    "rule",
)
EDGE_TYPES = (
    "implements", "derives_from", "satisfies", "tests", "designs", "depends_on", "refines", "relates_to", "contradicts",
    "mentions", "constrains", "part_of", "performed_by", "triggers", "uses", "clarifies", "flags_gap", "tagged",
)  # fmt: skip


def _once_more(fn):  # noqa: ANN001, ANN202
    """Two runs reprocessing the same documents can both find no row and both insert. The unique key lets one win; the other
    starts again, finds the winner's row and updates it instead, so reprocessing never fails on or duplicates a row."""

    @functools.wraps(fn)
    def run(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        try:
            return fn(*args, **kwargs)
        except IntegrityError:
            return fn(*args, **kwargs)

    return run


class KnowledgeRepo:
    def __init__(self, store: Store):
        self.store = store
        self.sessions = store.sessions
        self.redactor = store.redactor

    # ---- sources and versions ---------------------------------------------------------------------
    def find_source(self, project: str, kind: str, external_id: str) -> KnowledgeSource | None:
        with self.sessions() as s:
            return s.scalars(
                select(KnowledgeSource).where(
                    KnowledgeSource.project == project, KnowledgeSource.kind == kind, KnowledgeSource.external_id == external_id
                )
            ).first()  # fmt: skip

    def resolve(self, project: str, ref: str, kind: str | None = None) -> KnowledgeSource | None:
        """A source by its id or by its external id (REQ-1, a file path); optionally limited to a kind."""
        with self.sessions() as s:
            src = s.get(KnowledgeSource, ref)
            if src is not None and src.project == project:
                return src
            q = select(KnowledgeSource).where(
                KnowledgeSource.project == project, KnowledgeSource.external_id == ref
            )
            if kind:
                q = q.where(KnowledgeSource.kind == kind)
            return s.scalars(q.order_by(KnowledgeSource.kind)).first()

    def projects(self) -> list[dict[str, Any]]:
        """Every project with source counts per kind (the dashboard's project picker)."""
        with self.sessions() as s:
            rows = s.execute(
                select(KnowledgeSource.project, KnowledgeSource.kind).order_by(KnowledgeSource.project)
            ).all()
        out: dict[str, dict[str, int]] = {}
        for project, kind in rows:
            out.setdefault(project, {})[kind] = out.get(project, {}).get(kind, 0) + 1
        return [{"project": p, "sources": sum(c.values()), "by_kind": c} for p, c in out.items()]

    def get_source(self, source_id: str) -> KnowledgeSource | None:
        with self.sessions() as s:
            return s.get(KnowledgeSource, source_id)

    def sources(self, project: str, kind: str | None = None, limit: int = 500) -> list[KnowledgeSource]:
        with self.sessions() as s:
            q = select(KnowledgeSource).where(KnowledgeSource.project == project)
            if kind:
                q = q.where(KnowledgeSource.kind == kind)
            return list(s.scalars(q.order_by(KnowledgeSource.kind, KnowledgeSource.external_id).limit(limit)))

    def version(self, source_id: str, version: int | None = None) -> KnowledgeVersion | None:
        with self.sessions() as s:
            if version is None:
                src = s.get(KnowledgeSource, source_id)
                if src is None:
                    return None
                version = src.current_version
            return s.scalars(
                select(KnowledgeVersion).where(KnowledgeVersion.source_id == source_id, KnowledgeVersion.version == version)
            ).first()  # fmt: skip

    @_once_more
    def upsert_version(
        self, project: str, kind: str, external_id: str, title: str, uri: str | None, origin: str, content: str,
        content_hash: str, meta: dict[str, Any],
    ) -> tuple[KnowledgeSource, KnowledgeVersion | None, bool]:  # fmt: skip
        """Make `content` the current version. Returns (source, new_version, content_changed). A changed text OR changed
        metadata (front matter, declared links) makes a new version, since versions are immutable and bundles cite them;
        with the same text its chunks and embeddings are kept (content_changed False). new_version is None when nothing
        changed (idempotent re-ingest)."""
        meta = self.redactor.redact_obj(meta)  # compared as it is stored
        content_changed = True
        with self.sessions.begin() as s:
            src = s.scalars(
                select(KnowledgeSource).where(
                    KnowledgeSource.project == project, KnowledgeSource.kind == kind, KnowledgeSource.external_id == external_id
                )
            ).first()  # fmt: skip
            if src is None:
                src = KnowledgeSource(
                    id=new_id(), project=project, kind=kind, external_id=external_id, title=title[:300], uri=uri,
                    origin=origin, current_version=0, ingestion_state="ok",
                )  # fmt: skip
                s.add(src)
                s.flush()
            else:
                cur = s.scalars(
                    select(KnowledgeVersion).where(KnowledgeVersion.source_id == src.id, KnowledgeVersion.version == src.current_version)
                ).first()  # fmt: skip
                if cur is not None and cur.content_hash == content_hash:
                    if cur.meta == meta:
                        return src, None, False
                    content_changed = False
            ver = KnowledgeVersion(
                id=new_id(), source_id=src.id, version=src.current_version + 1, content_hash=content_hash,
                content=self.redactor.redact(content), meta=meta,
            )  # fmt: skip
            src.current_version = ver.version
            src.title, src.uri, src.origin, src.updated_at = title[:300], uri, origin, utcnow()
            s.add(ver)
            if content_changed:
                s.execute(
                    delete(KnowledgeChunk).where(KnowledgeChunk.source_id == src.id)
                )  # chunks follow the current version
            else:  # same text: the chunks (and their embeddings) are still right, they now belong to the new version
                s.execute(
                    update(KnowledgeChunk).where(KnowledgeChunk.source_id == src.id).values(version_id=ver.id)
                )
            s.flush()
            s.expunge_all()
            return src, ver, content_changed

    # ---- review: sources whose kind was not recognised -------------------------------------------------
    def needs_review(self, project: str) -> list[tuple[KnowledgeSource, KnowledgeVersion]]:
        """Sources stored as `doc` because their declared kind is unknown, waiting for a person (see review_kind)."""
        out = []
        for s in self.sources(project, kind="doc", limit=100_000):
            v = self.version(s.id)
            if v is not None and (v.meta or {}).get("needs_review"):
                out.append((s, v))
        return out

    def resolve_review(self, source_id: str, kind: str, actor: str) -> KnowledgeSource:
        """A person's decision on a needs-review source: keep it as `doc` or give it a real kind. Writes a new version with
        the flag cleared (same text, so chunks and embeddings stay). Links made under the old kind are not re-checked here:
        the ontology check on the pipeline shows any that no longer fit."""
        src = self.get_source(source_id)
        ver = self.version(source_id) if src else None
        if src is None or ver is None or not (ver.meta or {}).get("needs_review"):
            raise KeyError("not waiting for review")
        if kind != src.kind and self.find_source(src.project, kind, src.external_id) is not None:
            raise ValueError(f"a {kind} with the id {src.external_id} already exists")
        with self.sessions.begin() as s:
            row = s.get(KnowledgeSource, source_id)
            assert row is not None
            row.kind = kind
        meta = {k: v for k, v in (ver.meta or {}).items() if k not in ("needs_review", "requested_kind")}
        out, _, _ = self.upsert_version(src.project, kind, src.external_id, src.title, src.uri, src.origin, ver.content, ver.content_hash, meta)  # fmt: skip
        self.audit(src.project, actor, "source.reviewed", "source", source_id, {"requested_kind": (ver.meta or {}).get("requested_kind"), "kind": kind})  # fmt: skip
        return out

    def set_state(self, source_id: str, state: str, error: str | None = None) -> None:
        with self.sessions.begin() as s:
            src = s.get(KnowledgeSource, source_id)
            if src is not None:
                src.ingestion_state, src.error = (
                    state,
                    (self.redactor.redact(error)[:2000] if error else None),
                )

    # ---- chunks ------------------------------------------------------------------------------------
    def add_chunks(
        self, src: KnowledgeSource, version_id: str, chunks: list[tuple[str, str]]
    ) -> list[KnowledgeChunk]:
        rows = [
            KnowledgeChunk(
                id=new_id(),
                source_id=src.id,
                version_id=version_id,
                project=src.project,
                ordinal=i,
                heading=h[:300],
                text=self.redactor.redact(t),
            )  # fmt: skip
            for i, (h, t) in enumerate(chunks)
        ]
        with self.sessions.begin() as s:
            s.add_all(rows)
        return rows

    def replace_chunks(self, src: KnowledgeSource, version_id: str, chunks: list[tuple[str, str]]) -> list[KnowledgeChunk]:  # fmt: skip
        with self.sessions.begin() as s:
            s.execute(delete(KnowledgeChunk).where(KnowledgeChunk.source_id == src.id))
        return self.add_chunks(src, version_id, chunks)

    def chunks(self, source_id: str) -> list[KnowledgeChunk]:
        with self.sessions() as s:
            return list(
                s.scalars(
                    select(KnowledgeChunk)
                    .where(KnowledgeChunk.source_id == source_id)
                    .order_by(KnowledgeChunk.ordinal)
                )
            )

    def chunks_source(self, chunk_id: str) -> str:
        with self.sessions() as s:
            c = s.get(KnowledgeChunk, chunk_id)
            return c.source_id if c else ""

    def project_chunks(
        self, project: str, kinds: list[str] | None = None
    ) -> list[tuple[KnowledgeChunk, KnowledgeSource]]:
        with self.sessions() as s:
            q = select(KnowledgeChunk, KnowledgeSource).join(KnowledgeSource, KnowledgeSource.id == KnowledgeChunk.source_id).where(KnowledgeChunk.project == project)  # fmt: skip
            if kinds:
                q = q.where(KnowledgeSource.kind.in_(kinds))
            return [(c, src) for c, src in s.execute(q).all()]

    def chunks_of(self, project: str, source_ids: list[str]) -> list[KnowledgeChunk]:
        if not source_ids:
            return []
        with self.sessions() as s:
            q = select(KnowledgeChunk).where(
                KnowledgeChunk.project == project, KnowledgeChunk.source_id.in_(source_ids)
            )
            return list(s.scalars(q))

    def set_embeddings(self, vectors: dict[str, list[float]], model: str) -> None:
        with self.sessions.begin() as s:
            for cid, v in vectors.items():
                c = s.get(KnowledgeChunk, cid)
                if c is not None:
                    c.embedding, c.embedding_model = v, model

    # ---- edges -------------------------------------------------------------------------------------
    @_once_more
    def upsert_edge(
        self, project: str, src: KnowledgeSource, dst: KnowledgeSource, type_: str, status: str, origin: str,
        evidence: str = "", task_id: str | None = None,
    ) -> tuple[KnowledgeEdge, bool]:  # fmt: skip
        """(edge, created). An existing edge keeps its status (a verified edge is never downgraded by a re-ingest or
        a new proposal) but follows the versions it was last seen against. Raises OntologyViolation for a link the
        ontology does not allow between these two types (whoever proposes it)."""
        default().check(type_, self.node_type(src), self.node_type(dst))
        with self.sessions.begin() as s:
            e = s.scalars(
                select(KnowledgeEdge).where(
                    KnowledgeEdge.project == project, KnowledgeEdge.src_id == src.id, KnowledgeEdge.dst_id == dst.id, KnowledgeEdge.type == type_
                )
            ).first()  # fmt: skip
            if e is not None:
                if e.status == "proposed" and status == "verified" and origin == "source":
                    e.status, e.origin = "verified", "source"  # a document now declares it
                if origin == "source" and e.src_version != src.current_version:
                    # the declaring document was edited and still declares it: its author has seen the other end
                    e.src_version, e.dst_version = src.current_version, dst.current_version
                s.flush()
                s.expunge(e)
                return e, False
            e = KnowledgeEdge(
                id=new_id(), project=project, src_id=src.id, dst_id=dst.id, type=type_, status=status, origin=origin,
                evidence=self.redactor.redact(evidence)[:1000], src_version=src.current_version,
                dst_version=dst.current_version, task_id=task_id,
            )  # fmt: skip
            s.add(e)
            s.flush()
            s.expunge(e)
            return e, True

    def table_counts(self, project: str) -> dict[str, Any]:
        """Rows this project has in each knowledge table (what ingestion writes to PostgreSQL / SQLite)."""
        with self.sessions() as s:

            def n(q: Any) -> int:
                return int(s.scalar(q) or 0)

            src_ids = select(KnowledgeSource.id).where(KnowledgeSource.project == project)
            edges = dict(s.execute(select(KnowledgeEdge.status, func.count()).where(KnowledgeEdge.project == project).group_by(KnowledgeEdge.status)).all())  # fmt: skip
            return {
                "knowledge_sources": n(select(func.count()).select_from(KnowledgeSource).where(KnowledgeSource.project == project)),
                "knowledge_versions": n(select(func.count()).select_from(KnowledgeVersion).where(KnowledgeVersion.source_id.in_(src_ids))),
                "knowledge_chunks": n(select(func.count()).select_from(KnowledgeChunk).where(KnowledgeChunk.project == project)),
                "chunks_with_embedding": n(select(func.count()).select_from(KnowledgeChunk).where(KnowledgeChunk.project == project, KnowledgeChunk.embedding.is_not(None))),
                "knowledge_edges": sum(int(v) for v in edges.values()),
                "edges_by_status": {str(k): int(v) for k, v in edges.items()},
                "knowledge_audit": n(select(func.count()).select_from(KnowledgeAudit).where(KnowledgeAudit.project == project)),
            }  # fmt: skip

    def node_type(self, src: KnowledgeSource) -> str:
        meta = None
        if src.kind in ("concept", "rule"):
            v = self.version(src.id)
            meta = v.meta if v else None
        return node_type(src.kind, meta)

    def delete_sources(self, project: str, kinds: tuple[str, ...], origin: str | None = None) -> int:
        """Remove sources of these kinds (and this origin) with their versions, chunks and links (used to rebuild
        extracted concepts)."""
        with self.sessions.begin() as s:
            q = select(KnowledgeSource.id).where(
                KnowledgeSource.project == project, KnowledgeSource.kind.in_(kinds)
            )
            if origin is not None:
                q = q.where(KnowledgeSource.origin == origin)
            ids = list(s.scalars(q))
            if not ids:
                return 0
            s.execute(
                delete(KnowledgeEdge).where(KnowledgeEdge.src_id.in_(ids) | KnowledgeEdge.dst_id.in_(ids))
            )
            s.execute(delete(KnowledgeChunk).where(KnowledgeChunk.source_id.in_(ids)))
            s.execute(delete(KnowledgeVersion).where(KnowledgeVersion.source_id.in_(ids)))
            s.execute(delete(KnowledgeSource).where(KnowledgeSource.id.in_(ids)))
        return len(ids)

    def edges_of(self, source_ids: list[str], statuses: tuple[str, ...]) -> list[KnowledgeEdge]:
        if not source_ids:
            return []
        with self.sessions() as s:
            q = select(KnowledgeEdge).where(
                (KnowledgeEdge.src_id.in_(source_ids) | KnowledgeEdge.dst_id.in_(source_ids)) & KnowledgeEdge.status.in_(statuses)
            )  # fmt: skip
            return list(s.scalars(q.order_by(KnowledgeEdge.type, KnowledgeEdge.id)))

    def edges(self, project: str, status: str | None = None, limit: int = 500) -> list[KnowledgeEdge]:
        with self.sessions() as s:
            q = select(KnowledgeEdge).where(KnowledgeEdge.project == project)
            if status:
                q = q.where(KnowledgeEdge.status == status)
            return list(s.scalars(q.order_by(KnowledgeEdge.created_at, KnowledgeEdge.id).limit(limit)))

    def edges_by_ids(self, edge_ids: list[str]) -> list[KnowledgeEdge]:
        if not edge_ids:
            return []
        with self.sessions() as s:
            q = select(KnowledgeEdge).where(KnowledgeEdge.id.in_(edge_ids))
            return list(s.scalars(q.order_by(KnowledgeEdge.type, KnowledgeEdge.id)))

    def change_marker(self, project: str) -> str:
        """Changes to sources or edges since a point in time, as a string: the Neo4j mirror rebuilds when it differs.
        Retrieval and review records (bundles, evaluations) do not change the graph and are ignored."""
        with self.sessions() as s:
            n, last = s.execute(
                select(func.count(), func.max(KnowledgeAudit.created_at)).where(
                    KnowledgeAudit.project == project,
                    KnowledgeAudit.action.not_in(("bundle.built", "evaluation.reviewed", "retrieval.evaluated", "graph.queried")),
                )
            ).one()  # fmt: skip
        return f"{n}:{last.isoformat() if last else '-'}"

    def get_edge(self, edge_id: str) -> KnowledgeEdge | None:
        with self.sessions() as s:
            return s.get(KnowledgeEdge, edge_id)

    def decide_edge(self, edge_id: str, status: str, actor: str) -> bool:
        """proposed -> verified | rejected, once. False if it was not proposed any more."""
        with self.sessions.begin() as s:
            e = s.get(KnowledgeEdge, edge_id)
            if e is None or e.status != "proposed":
                return False
            e.status, e.decided_by, e.decided_at = status, actor[:80], utcnow()
            return True

    # ---- audit and bundles -------------------------------------------------------------------------
    def audit(self, project: str, actor: str, action: str, object_type: str, object_id: str, detail: dict[str, Any] | None = None) -> None:  # fmt: skip
        with self.sessions.begin() as s:
            s.add(KnowledgeAudit(id=new_id(), project=project, actor=actor[:80], action=action, object_type=object_type, object_id=object_id[:64], detail=self.redactor.redact_obj(detail or {})))  # fmt: skip

    def audit_log(self, project: str, limit: int = 100) -> list[KnowledgeAudit]:
        with self.sessions() as s:
            return list(s.scalars(select(KnowledgeAudit).where(KnowledgeAudit.project == project).order_by(KnowledgeAudit.created_at.desc(), KnowledgeAudit.id).limit(limit)))  # fmt: skip

    def save_bundle(
        self, bundle_id: str, project: str, task_id: str | None, status: str, content: dict[str, Any]
    ) -> bool:
        """Stores the bundle once (same inputs, same id). True if it was new."""
        with self.sessions.begin() as s:
            if s.get(ContextBundle, bundle_id) is not None:
                return False
            s.add(ContextBundle(id=bundle_id, project=project, task_id=task_id, status=status, content=self.redactor.redact_obj(content)))  # fmt: skip
            return True

    def get_bundle(self, bundle_id: str) -> ContextBundle | None:
        with self.sessions() as s:
            return s.get(ContextBundle, bundle_id)

    def bundles(self, project: str, limit: int = 50) -> list[tuple[ContextBundle, list[str], int]]:
        """Stored context bundles, newest first, each with the tasks that were given it and how many previews built it."""
        with self.sessions() as s:
            rows = list(s.scalars(select(ContextBundle).where(ContextBundle.project == project).order_by(ContextBundle.created_at.desc()).limit(limit)))  # fmt: skip
            built = list(s.scalars(select(KnowledgeAudit).where(KnowledgeAudit.project == project, KnowledgeAudit.action == "bundle.built")))  # fmt: skip
        tasks: dict[str, list[str]] = {}
        previews: dict[str, int] = {}
        for a in built:
            if a.actor.startswith("task:"):
                tasks.setdefault(a.object_id, []).append(a.actor[5:])
            else:
                previews[a.object_id] = previews.get(a.object_id, 0) + 1
        return [(r, sorted(set(tasks.get(r.id, []))), previews.get(r.id, 0)) for r in rows]

    def bundle_for_task(self, task_id: str) -> ContextBundle | None:
        """The bundle a task was given. A bundle's id is a hash of its content, so two requests with the same inputs share
        one stored row (whoever stored it first owns `task_id`): the link comes from the audit record of the task."""
        with self.sessions() as s:
            built = s.scalars(
                select(KnowledgeAudit)
                .where(KnowledgeAudit.actor == f"task:{task_id}", KnowledgeAudit.action == "bundle.built")
                .order_by(KnowledgeAudit.created_at.desc())
            ).first()
            if built is not None and (row := s.get(ContextBundle, built.object_id)) is not None:
                return row
            return s.scalars(select(ContextBundle).where(ContextBundle.task_id == task_id).order_by(ContextBundle.created_at.desc())).first()  # fmt: skip
