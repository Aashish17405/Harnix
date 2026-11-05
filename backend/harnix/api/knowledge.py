"""HTTP API of the knowledge base (docs/knowledge.md). Retrieval goes through the same tool handlers as the MCP server."""

from __future__ import annotations

import base64
import re
import sys
import urllib.parse
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Body, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from harnix.graph.store import GraphUnavailable
from harnix.harness.report import estimated_cost
from harnix.knowledge import findings as findings_mod
from harnix.knowledge import linking, markdown_export, tools
from harnix.knowledge.capture import decide, metrics
from harnix.knowledge.context import build_bundle, drift, render
from harnix.knowledge.coverage import audit as coverage_audit
from harnix.knowledge.embeddings import Embedder, cosine, doc_model, make_embedder
from harnix.knowledge.evaluate import evaluate, evaluate_bundles
from harnix.knowledge.evaluation import evaluate_task, record_review
from harnix.knowledge.extract import run as run_extraction
from harnix.knowledge.ingest import Ingestor, embed_text
from harnix.knowledge.ingest import configure as configure_ingest
from harnix.knowledge.kiro import kiro_files
from harnix.knowledge.mcp_server import handle as mcp_handle
from harnix.knowledge.ontology import for_project
from harnix.knowledge.projection import pca2
from harnix.knowledge.query import QueryRefused, nl_to_cypher, run_query
from harnix.knowledge.repo import KINDS, KnowledgeRepo
from harnix.knowledge.service import AccessDenied, KnowledgeService, NotFoundError, make_service
from harnix.persistence.models import KnowledgeSource
from harnix.persistence.store import NotFound
from harnix.policies.paths import PathViolation, authorize_repo

PROJECT = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


class IngestIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project: str = PROJECT
    source: Literal["documents", "repository", "jira_mock"]
    path: str = Field(min_length=1, max_length=1000, description="Directory (documents, repository) or JSON file (jira_mock) inside FORGE_KNOWLEDGE_ROOTS")  # fmt: skip
    background: bool = Field(False, description="Return a job at once and report every step (GET /knowledge/jobs/{id})")  # fmt: skip


class DocumentIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    background: bool = False  # return a job at once and report every step (GET /knowledge/jobs/{id})
    project: str = PROJECT
    kind: Literal["brd", "requirement", "epic", "feature", "story", "ticket", "design", "doc"]
    filename: str = Field("", max_length=200, description="Used for the id and the type (.md, .txt, .docx)")
    text: str | None = Field(None, max_length=2_000_000, description="The document text (Markdown or plain)")
    data_base64: str | None = Field(
        None, max_length=40_000_000, description="An uploaded file, base64 (.docx needs this)"
    )
    external_id: str | None = Field(None, max_length=100, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
    title: str | None = Field(None, max_length=300)
    scope: str | None = Field(
        None,
        max_length=40,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$",
        description="Namespace for the ids found inside the file (BRD-01 makes US-001 into BRD-01/US-001)",
    )
    links: dict[str, list[str]] = Field(
        default_factory=dict, description='Relationships to declare, e.g. {"derives_from": ["REQ-1"]}'
    )


class KindDecisionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["brd", "requirement", "epic", "feature", "story", "ticket", "design", "doc"]


class ReviewIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    criterion: str = Field(min_length=1, max_length=200)
    verdict: Literal["pass", "fail", "unsure"]
    note: str = Field("", max_length=1000)


class EvalIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project: str = PROJECT
    cases: list[dict[str, Any]] = Field(
        min_length=1, max_length=200, description='[{"query": "...", "expect": ["REQ-1"]}]'
    )
    bundles: bool = Field(False, description="Also measure the context bundles (slower: one bundle per case)")
    keyword_weight: float | None = Field(
        None, ge=0, le=10, description="Try this keyword weight (vector = 1.0) instead of the server's"
    )


class ExtractIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project: str = PROJECT
    kinds: list[Literal["story", "brd", "epic", "feature", "requirement", "design", "doc"]] = Field(default_factory=lambda: ["story", "brd", "doc"])  # fmt: skip
    limit: int = Field(200, ge=1, le=1000)
    reset: bool = (
        False  # remove extracted concepts/rules and read every document again (e.g. after an ontology change)
    )


class ExportIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project: str = PROJECT
    path: str = Field(min_length=1, max_length=1000, description="Folder to write the Markdown copy to (new or empty, or a previous export)")  # fmt: skip


class LinkIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project: str = PROJECT
    limit: int = Field(200, ge=1, le=1000)


class QueryIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project: str = PROJECT
    cypher: str | None = Field(
        None, max_length=4000, description="A read-only Cypher query; $project is always set"
    )
    question: str | None = Field(
        None, max_length=1000, description="Or a question in plain language: the model writes the Cypher"
    )
    run: bool = Field(True, description="False: only translate the question, do not run it")


class BundleIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project: str = PROJECT
    instructions: str = Field(min_length=1, max_length=20000)
    acceptance_criteria: list[str] = Field(default_factory=list, max_length=20)


def make_router(state: Callable[[Request], Any], principal: Callable[[Request], tuple[str, list[str] | None]], dependencies: list) -> APIRouter:  # fmt: skip
    r = APIRouter(prefix="/api/knowledge", dependencies=dependencies, tags=["knowledge"])
    embedders: dict[str, Embedder] = {}

    def parts(request: Request, project: str | None = None) -> tuple[KnowledgeRepo, KnowledgeService, str]:
        st = state(request)
        configure_ingest(st.settings)  # size limits (FORGE_KNOWLEDGE_MAX_DOC_BYTES, _MAX_CHUNKS)
        actor, projects = principal(request)
        if project is not None and projects is not None and project not in projects:
            raise HTTPException(403, f"No access to project '{project}'")
        key = st.settings.resolved_embedding_provider + st.settings.embedding_model
        if key not in embedders:
            try:
                embedders[key] = make_embedder(st.settings)
            except ValueError as e:
                raise HTTPException(503, str(e)) from None
        repo = KnowledgeRepo(st.store)
        return repo, make_service(st.settings, repo, embedders[key], projects), actor

    def review_items(repo: KnowledgeRepo, project: str, limit: int = 100) -> list[dict[str, Any]]:
        return [
            {"source_id": s.id, "external_id": s.external_id, "title": s.title, "uri": s.uri, "requested_kind": (v.meta or {}).get("requested_kind")}
            for s, v in repo.needs_review(project)[:limit]
        ]  # fmt: skip

    def check_links(repo: KnowledgeRepo, project: str, onto: Any, limit: int) -> dict[str, Any]:
        """Stored, non-rejected links the ontology does not allow (the first `limit` of them, and how many)."""
        bad: list[dict[str, Any]] = []
        total = checked = 0
        types: dict[str, str] = {}
        names: dict[str, str] = {}
        for e in repo.edges(project, limit=1_000_000):
            if e.status == "rejected":
                continue
            checked += 1
            for sid in (e.src_id, e.dst_id):
                if sid not in types:
                    s = repo.get_source(sid)
                    types[sid], names[sid] = (repo.node_type(s), s.external_id) if s else ("?", sid)
            if not onto.allowed(e.type, types[e.src_id], types[e.dst_id]):
                total += 1
                if len(bad) < limit:
                    bad.append({"edge_id": e.id, "from": names[e.src_id], "from_type": types[e.src_id], "type": e.type, "to": names[e.dst_id], "to_type": types[e.dst_id], "status": e.status})  # fmt: skip
        return {"links_checked": checked, "violations_total": total, "violations": bad}

    _REFUSED = re.compile(r"^(?P<src>.+?) -(?P<type>\w+)-> (?P<dst>.+?): '(?P<st>\w+) \w+ (?P<dt>\w+)' is not allowed by the ontology \((?P<rule>.*)\)$")  # fmt: skip

    def refused_links(audit: list[Any], limit: int) -> dict[str, Any]:
        """Links a document declared that the ontology refused at intake. They are never stored as edges, so the only
        record is the unresolved list in each ingest's audit entry (newest first); the newest report of a link wins."""
        seen: dict[tuple[str, str, str], dict[str, Any]] = {}
        for a in audit:
            if not a.action.startswith("ingest."):
                continue
            for line in (a.detail or {}).get("unresolved") or []:
                m = _REFUSED.match(str(line))
                if m:
                    seen.setdefault((m["src"], m["type"], m["dst"]), {
                        "from": m["src"], "type": m["type"], "to": m["dst"], "from_type": m["st"], "to_type": m["dt"],
                        "rule": m["rule"], "at": a.created_at.isoformat() + "Z", "action": a.action,
                    })  # fmt: skip
        rows = list(seen.values())
        return {"refused_total": len(rows), "refused": rows[:limit]}

    def mirror(svc: KnowledgeService, repo: KnowledgeRepo, project: str, emit: Callable[..., None] | None = None) -> dict[str, Any]:  # fmt: skip
        """Bring the Neo4j copy up to date after a change, so the graph can be browsed at once. Best effort: the
        database is the source of truth, and retrieval re-syncs before it traverses anyway."""
        say = emit or (lambda *a, **k: None)
        if svc.graph is None:
            say("neo4j", "Neo4j is off (FORGE_GRAPH_ENABLED): the graph stays in PostgreSQL only")
            return {"enabled": False}
        say("neo4j", "Rebuilding the Neo4j copy of the project's graph")
        try:
            out = svc.graph.sync(repo, project)
            say(
                "neo4j", f"Neo4j rebuilt: {out['nodes']} nodes, {out['edges']} relationships", **out
            )  # labels and types counted
            return {"enabled": True, **out}
        except GraphUnavailable as e:
            say("neo4j", f"Neo4j not updated: {e}", error=str(e))
            return {"enabled": True, "error": str(e)}

    def tables(repo: KnowledgeRepo, project: str, emit: Callable[..., None], before: dict[str, Any] | None = None) -> dict[str, Any]:  # fmt: skip
        """Report the project's rows per table; with `before`, also what this job added."""
        now = repo.table_counts(project)
        dialect = repo.store.engine.dialect.name
        if before is None:
            emit("postgres", f"Before: {now['knowledge_sources']} sources, {now['knowledge_chunks']} chunks, {now['knowledge_edges']} links in {dialect}", rows=now, dialect=dialect)  # fmt: skip
        else:
            delta = {k: now[k] - before.get(k, 0) for k, v in now.items() if isinstance(v, int)}
            emit("postgres", f"Written: +{delta['knowledge_sources']} sources, +{delta['knowledge_versions']} versions, +{delta['knowledge_chunks']} chunks, +{delta['knowledge_edges']} links", rows=now, delta=delta, dialect=dialect)  # fmt: skip
        return now

    jobs: dict[str, dict[str, Any]] = {}

    def start_job(kind: str, project: str, title: str, work: Callable[[Callable[..., None]], dict[str, Any]]) -> dict[str, Any]:  # fmt: skip
        """Run `work` in the background; every step it reports is kept on the job, which the dashboard polls."""
        import threading
        import time
        import uuid

        job: dict[str, Any] = {
            "id": uuid.uuid4().hex[:12], "kind": kind, "project": project, "title": title, "status": "running",
            "stage": "start", "started_at": time.time(), "finished_at": None, "done": 0, "total": 0,
            "events": [], "report": None, "error": None,
        }  # fmt: skip

        def emit(stage: str, message: str, **data: Any) -> None:
            job["stage"] = stage
            if "done" in data and "total" in data:
                job["done"], job["total"] = data["done"], data["total"]
            job["events"].append({"at": time.time(), "stage": stage, "message": message[:500], "data": {k: v for k, v in data.items() if k != "files"}})  # fmt: skip
            del job["events"][:-2000]  # keep the latest 2000 (one per file, plus a few per step)

        def run() -> None:
            try:
                job["report"] = work(emit)
                emit("done", "Finished")
                job["status"] = "done"
            except Exception as e:  # noqa: BLE001
                job["status"], job["error"] = "failed", f"{type(e).__name__}: {e}"[:300]
                emit("failed", job["error"])
            job["finished_at"] = time.time()

        for old in [k for k, j in jobs.items() if j["status"] != "running"][
            :-50
        ]:  # remember the last 50 finished
            jobs.pop(old, None)
        jobs[job["id"]] = job
        threading.Thread(target=run, daemon=True, name=f"{kind}-{job['id']}").start()
        return job

    @r.get("/jobs")
    def list_jobs(request: Request, project: str = Query(...)):
        """Background work for a project (indexing, uploads, extraction), newest first, with every step reported."""
        parts(request, project)
        mine = [j for j in jobs.values() if j["project"] == project]
        return {"jobs": sorted(mine, key=lambda j: -j["started_at"])[:10]}

    @r.get("/jobs/{job_id}")
    def get_job(job_id: str, request: Request):
        job = jobs.get(job_id)
        if job is None:
            raise HTTPException(404, "No such job")
        parts(request, job["project"])
        return job

    def run_tool(request: Request, name: str, args: dict[str, Any]) -> dict[str, Any]:
        _, svc, _ = parts(request, args.get("project"))
        try:
            return tools.call(svc, name, args)
        except NotFoundError as e:
            raise HTTPException(404, str(e)) from None
        except AccessDenied as e:
            raise HTTPException(403, str(e)) from None
        except ValueError as e:
            raise HTTPException(422, str(e)) from None

    @r.post("/ingest")
    def ingest(body: IngestIn, request: Request):
        st = state(request)
        repo, svc, actor = parts(request, body.project)
        body.path = body.path.strip().strip('"').strip("'").strip()  # Windows "Copy as path" adds quotes
        try:
            path = Path(body.path).expanduser().resolve()
            # The operator names this path (the agent has no ingest tool). No FORGE_KNOWLEDGE_ROOTS = any folder; set it to restrict.
            if st.settings.knowledge_roots.strip():
                if body.source != "jira_mock":
                    authorize_repo(body.path, st.settings.knowledge_root_paths)
                elif not any(path.is_relative_to(x) for x in st.settings.knowledge_root_paths):
                    raise PathViolation("The file is outside FORGE_KNOWLEDGE_ROOTS")
            elif not (path.is_file() if body.source == "jira_mock" else path.is_dir()):
                raise PathViolation(f"Not found or wrong type (needs a {'file' if body.source == 'jira_mock' else 'directory'}): {body.path}")  # fmt: skip
        except PathViolation as e:
            raise HTTPException(422, str(e)) from None

        def work(emit: Callable[..., None]) -> dict[str, Any]:
            before = tables(repo, body.project, emit)
            ing = Ingestor(repo, svc.embedder, actor, progress=lambda st_, msg, data: emit(st_, msg, **data))
            if body.source == "documents":
                res = ing.ingest_documents(body.project, path)
            elif body.source == "repository":
                res = ing.ingest_repository(body.project, path)
            else:
                res = ing.ingest_jira_mock(body.project, path)
            if body.source != "documents":  # these readers report no per-file steps: list what they captured
                emit(
                    "read",
                    f"{path.name}: {len(res.entities)} items",
                    file=path.name,
                    entities=res.entities[:200],
                )
                ing.report_links(res)
            tables(repo, body.project, emit, before)
            return {"project": body.project, "embedding_model": svc.embedder.name, **res.as_dict(), "neo4j": mirror(svc, repo, body.project, emit)}  # fmt: skip

        if body.background:
            return start_job("ingest", body.project, f"Index {path.name}", work)
        try:
            return work(lambda *a, **k: None)
        except (OSError, ValueError, KeyError) as e:
            raise HTTPException(422, f"Cannot ingest: {type(e).__name__}: {e}") from None

    @r.get("/sources")
    def sources(
        request: Request,
        project: str = Query(...),
        kind: str | None = None,
        limit: int = Query(200, ge=1, le=1000),
    ):
        return run_tool_list(request, project, kind, limit)

    def run_tool_list(request: Request, project: str, kind: str | None, limit: int):
        _, svc, _ = parts(request, project)
        return svc.list_sources(project, kind, limit)

    @r.get("/search")
    def search(request: Request, project: str = Query(...), q: str = Query(..., min_length=1), kinds: Annotated[list[str] | None, Query()] = None, top_k: int = Query(8, ge=1, le=25)):  # fmt: skip
        return run_tool(
            request, "knowledge_search", {"project": project, "query": q, "kinds": kinds, "top_k": top_k}
        )

    @r.get("/source")
    def source(
        request: Request,
        project: str = Query(...),
        ref: str = Query(...),
        version: int | None = Query(None, ge=1),
    ):
        return run_tool(request, "knowledge_get", {"project": project, "ref": ref, "version": version})

    @r.get("/source/captured")
    def source_captured(request: Request, project: str = Query(...), ref: str = Query(...)):
        """What ingesting one document produced: the text, each chunk with its place in the text and the exact text that
        was embedded, the vector's size and first numbers, every relationship of the source (including the concepts
        read out of it) and its node in Neo4j. Everything comes from what is stored, not from a re-run."""
        repo, svc, _ = parts(request, project)
        try:
            src = svc.lookup(project, ref)
        except NotFoundError as e:
            raise HTTPException(404, str(e)) from None
        ver = repo.version(src.id)
        if ver is None:
            raise HTTPException(404, "The source has no version")
        content, cursor, covered = ver.content, 0, [False] * len(ver.content)
        chunks: list[dict[str, Any]] = []
        for c in repo.chunks(src.id):
            head, tail = c.text[:60], c.text[-60:]
            a = content.find(head, cursor) if head else -1
            lines = c.text.split("\n")
            if a < 0 and len(lines) > 2 and lines[0].lstrip().startswith("|"):
                # a later piece of a long table repeats the table's header row: place it by its first own row
                a = content.find("\n".join(lines[2:])[:60], cursor)
            b = content.find(tail, max(a, 0)) + len(tail) if a >= 0 else -1
            start, end = (a, b) if a >= 0 and b > a else (None, None)
            if start is not None and end is not None:
                cursor = start + 1
                covered[start:end] = [True] * (end - start)
            vec = c.embedding or []
            chunks.append({
                "id": c.id, "ordinal": c.ordinal, "heading": c.heading, "text": c.text, "chars": len(c.text),
                "start": start, "end": end, "embedded_text": embed_text(src, c.heading, c.text),
                "embedded": bool(vec), "model": c.embedding_model, "dimensions": len(vec),
                "vector_head": [round(float(x), 4) for x in vec[:8]],
                "norm": round(sum(float(x) ** 2 for x in vec) ** 0.5, 4) if vec else None,
            })  # fmt: skip
        names: dict[str, KnowledgeSource] = {}
        links: list[dict[str, Any]] = []
        for e in repo.edges_of([src.id], ("verified", "proposed", "rejected")):
            outgoing = e.src_id == src.id
            oid = e.dst_id if outgoing else e.src_id
            if oid not in names and (o := repo.get_source(oid)) is not None:
                names[oid] = o
            o = names.get(oid)
            links.append({
                "id": e.id, "type": e.type, "direction": "out" if outgoing else "in", "status": e.status, "origin": e.origin,
                "evidence": (e.evidence or "")[:400],
                "other": {"external_id": o.external_id, "kind": o.kind, "title": o.title, "node_type": repo.node_type(o)} if o else None,
            })  # fmt: skip
        neo: dict[str, Any] = {"enabled": svc.graph is not None}
        if svc.graph is not None:
            try:
                svc.graph.ensure_synced(repo, project)
                neo["node"] = svc.graph.node(project, src.id)
            except GraphUnavailable as e:
                neo["error"] = str(e)
        return {
            "project": project,
            "source": {"id": src.id, "kind": src.kind, "external_id": src.external_id, "title": src.title, "uri": src.uri, "origin": src.origin, "version": src.current_version, "state": src.ingestion_state, "error": src.error, "node_type": repo.node_type(src), "content_hash": ver.content_hash[:16]},  # fmt: skip
            "metadata": ver.meta or {},
            "content": content[:300_000], "length": len(content), "truncated": len(content) > 300_000,
            "chunks": chunks, "covered_chars": sum(covered),
            "links": links, "neo4j": neo,
        }  # fmt: skip

    @r.get("/relations")
    def relations(request: Request, project: str = Query(...), ref: str = Query(...), depth: int = Query(1, ge=1, le=3), verified_only: bool = False):  # fmt: skip
        return run_tool(request, "knowledge_relations", {"project": project, "ref": ref, "depth": depth, "verified_only": verified_only})  # fmt: skip

    @r.post("/bundles")
    def preview_bundle(body: BundleIn, request: Request):
        repo, svc, actor = parts(request, body.project)
        b = build_bundle(svc, body.project, body.instructions, body.acceptance_criteria)
        repo.save_bundle(b.id, body.project, None, b.status, b.content)
        repo.audit(body.project, actor, "bundle.built", "bundle", b.id, {"status": b.status, "preview": True})
        return b.content

    @r.get("/bundles")
    def list_bundles(request: Request, project: str = Query(...), limit: int = Query(50, ge=1, le=500)):
        """Every context graph built for the project (task runs and previews), newest first: what was asked, what it
        holds per slot, and which tasks were given it. GET /bundles/{id} has the content."""
        repo, _, _ = parts(request, project)
        out = []
        for row, task_ids, previews in repo.bundles(project, limit):
            c = row.content or {}
            slots = {k: len(v) for k, v in (c.get("slots") or {}).items() if v}
            out.append({
                "bundle_id": row.id, "created_at": row.created_at.isoformat() + "Z", "status": row.status,
                "query": str(c.get("query") or "")[:300], "items": slots, "relationships": len(c.get("relationships") or []),
                "tasks": task_ids, "previews": previews, "has_pipeline": "pipeline" in c,
            })  # fmt: skip
        return {"project": project, "bundles": out}

    @r.get("/bundles/{bundle_id}")
    def get_bundle(bundle_id: str, request: Request, format: Literal["json", "text", "kiro"] = "json"):
        repo, _, _ = parts(request)
        row = repo.get_bundle(bundle_id)
        if row is None:
            raise HTTPException(404, "No such bundle")
        parts(request, row.project)  # access check on its project
        if format == "text":
            return {"text": render(row.content)}
        if format == "kiro":
            return {"files": kiro_files(row.content)}
        return row.content

    @r.get("/bundles/{bundle_id}/drift")
    def bundle_drift(bundle_id: str, request: Request):
        repo, svc, _ = parts(request)
        row = repo.get_bundle(bundle_id)
        if row is None:
            raise HTTPException(404, "No such bundle")
        parts(request, row.project)
        d = drift(svc, row.content)
        return {"bundle_id": bundle_id, "reproducible": not d, "changed_sources": d}

    @r.get("/edges")
    def edges(
        request: Request,
        project: str = Query(...),
        status: Literal["proposed", "verified", "rejected"] | None = None,
    ):
        repo, svc, _ = parts(request, project)
        name = lambda sid: s.external_id if (s := repo.get_source(sid)) else sid  # noqa: E731
        return {"project": project, "edges": [
            {"edge_id": e.id, "from": name(e.src_id), "to": name(e.dst_id), "type": e.type, "status": e.status, "origin": e.origin, "evidence": e.evidence, "task_id": e.task_id, "decided_by": e.decided_by}  # fmt: skip
            for e in repo.edges(project, status)
        ]}  # fmt: skip

    def _decide(request: Request, edge_id: str, approve: bool):
        repo, svc, actor = parts(request)
        _, projects = principal(request)
        try:
            status = decide(repo, edge_id, approve, actor, projects)
            edge = repo.get_edge(edge_id)
            return {
                "edge_id": edge_id,
                "status": status,
                "neo4j": mirror(svc, repo, edge.project) if edge else None,
            }
        except KeyError:
            raise HTTPException(404, "No such edge") from None
        except AccessDenied as e:
            raise HTTPException(403, str(e)) from None
        except ValueError as e:
            raise HTTPException(409, str(e)) from None

    @r.post("/edges/{edge_id}/promote")
    def promote(edge_id: str, request: Request):
        return _decide(request, edge_id, True)

    @r.post("/edges/{edge_id}/reject")
    def reject(edge_id: str, request: Request):
        return _decide(request, edge_id, False)

    @r.get("/ontology")
    def ontology(request: Request, project: str = Query(...), violations: int = Query(50, ge=0, le=1000)):
        """The ontology in force for this project (ontology.json plus the project's glossary synonyms) and the stored
        links it does not allow (from before it existed, or with a changed type)."""
        repo, _, _ = parts(request, project)
        onto = for_project(repo, project)
        return {"project": project, **onto.as_dict(), **check_links(repo, project, onto, violations), **refused_links(repo.audit_log(project, limit=100_000), 200)}  # fmt: skip

    @r.get("/review")
    def review_list(request: Request, project: str = Query(...)):
        """Documents whose declared kind is not known: kept as `doc` and waiting for a person to accept or reclassify."""
        repo, _, _ = parts(request, project)
        return {"project": project, "kinds": list(KINDS), "items": review_items(repo, project)}

    @r.post("/review/{source_id}")
    def review_decide(source_id: str, body: KindDecisionIn, request: Request):
        """A person's decision: keep the source as `doc`, or give it a real kind (a new version with the flag cleared)."""
        repo, _, actor = parts(request)
        src = repo.get_source(source_id)
        if src is None:
            raise HTTPException(404, "No such source")
        parts(request, src.project)  # project access
        try:
            out = repo.resolve_review(source_id, body.kind, actor)
        except KeyError:
            raise HTTPException(404, "This source is not waiting for review") from None
        except ValueError as e:
            raise HTTPException(409, str(e)) from None
        return {"source_id": out.id, "external_id": out.external_id, "kind": out.kind}

    @r.get("/pipeline")
    def pipeline(request: Request, project: str = Query(...)):
        """Every stage of the project's knowledge pipeline with what it holds now: ingested documents, chunks and
        embeddings, extracted concepts, the ontology check, the PostgreSQL links, the Neo4j copy, and evaluation."""
        repo, svc, _ = parts(request, project)
        st = state(request)
        srcs = repo.sources(project, limit=1_000_000)
        by_kind: dict[str, int] = {}
        for s in srcs:
            by_kind[s.kind] = by_kind.get(s.kind, 0) + 1
        chunks = repo.project_chunks(project)
        models: dict[str, int] = {}
        for c, _ in chunks:
            if c.embedding:
                models[c.embedding_model or "?"] = models.get(c.embedding_model or "?", 0) + 1
        audit = repo.audit_log(project, limit=100_000)

        def last(*actions: str) -> dict[str, Any] | None:
            """The newest audit record of these actions (the log is newest first)."""
            return next(
                ({"at": a.created_at.isoformat() + "Z", "action": a.action, "detail": a.detail} for a in audit if a.action in actions),
                None,
            )  # fmt: skip

        def count(rows: list[Any], key: Callable[[Any], str]) -> dict[str, int]:
            return {k: sum(1 for r in rows if key(r) == k) for k in sorted({key(r) for r in rows})}

        edges = repo.edges(project, limit=1_000_000)
        onto = for_project(repo, project)
        neo: dict[str, Any] = {"enabled": svc.graph is not None}
        if svc.graph is not None:
            try:
                neo.update(svc.graph.project_info(project) or {})
                neo["current"] = neo.get("marker") == repo.change_marker(project)
                neo["available"] = True
            except GraphUnavailable as e:
                neo.update({"available": False, "detail": str(e)})
        doc_kinds = (
            "brd",
            "requirement",
            "epic",
            "feature",
            "story",
            "ticket",
            "design",
            "doc",
            "code",
            "test",
        )
        return {
            "project": project,
            "ingest": {
                "documents": {k: v for k, v in by_kind.items() if k in doc_kinds},
                "states": count(srcs, lambda s: s.ingestion_state),
                "last": last("ingest.documents", "ingest.text", "ingest.repository", "ingest.jira_mock"),
                "review": review_items(repo, project),
            },
            "chunks": {
                "total": len(chunks), "embedded": sum(models.values()), "by_model": models,
                "vector_index": st.settings.knowledge_vector, "embedder": svc.embedder.name,
                "current_format": doc_model(svc.embedder), "current": models.get(doc_model(svc.embedder), 0),
            },
            "extraction": {
                "concepts": by_kind.get("concept", 0), "rules": by_kind.get("rule", 0),
                "last": last("extract.run"),
            },
            "linking": {
                "last": last("link.run"),
                "by_status": count([e for e in edges if e.origin == "extracted" and (e.evidence or "").startswith("inferred from")], lambda e: e.status),  # fmt: skip
            },
            "ontology": {
                "types": len(onto.types), "relations": len(onto.relations), "synonyms": onto.synonyms,
                **check_links(repo, project, onto, 5),
                **refused_links(audit, 200),
            },
            "graph": {
                "edges": len(edges), "by_status": count(edges, lambda e: e.status), "by_origin": count(edges, lambda e: e.origin),
                "by_type": count(edges, lambda e: e.type),
            },
            "neo4j": neo,
            "evaluation": {"retrieval": last("retrieval.evaluated"), "bundles_built": sum(1 for a in audit if a.action == "bundle.built")},
        }  # fmt: skip

    # ---- MCP over HTTP: the same tools as the stdio server, for harnesses that connect by URL ----------------------
    @r.post("/mcp", response_model=None)
    def mcp_http(request: Request, body: Annotated[Any, Body()], project: str | None = Query(None)) -> Any:
        """MCP Streamable HTTP (JSON responses): `initialize`, `tools/list`, `tools/call`, `ping`, one message or a batch.
        Same bearer token and project access list as the rest of the API; `?project=` fixes the scope (like
        FORGE_KNOWLEDGE_PROJECTS for the stdio server), otherwise every project the token may read."""
        repo, svc, actor = parts(request, project)
        if project:
            svc.projects = [project]
        msgs = body if isinstance(body, list) else [body]
        replies = []
        for m in msgs:
            if not isinstance(m, dict):
                replies.append(
                    {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "invalid request"}}
                )
                continue
            if m.get("method") == "tools/call":  # who asked for what: in the audit log like every other read
                p = (m.get("params") or {}).get("arguments", {}).get("project") or project or "-"
                repo.audit(str(p)[:64], actor, "mcp.called", "tool", str((m.get("params") or {}).get("name"))[:64], {"via": "http"})  # fmt: skip
            reply = mcp_handle(svc, m)
            if reply is not None:
                replies.append(reply)
        if not replies:
            return Response(status_code=202)  # only notifications
        return JSONResponse(replies if isinstance(body, list) else replies[0])

    @r.get("/mcp", response_model=None)
    def mcp_get() -> Any:
        return Response(
            status_code=405, headers={"Allow": "POST"}
        )  # no server-initiated stream: JSON responses only

    @r.get("/mcp/config")
    def mcp_config(request: Request, project: str = Query(...)):
        """Ready-to-paste MCP settings for common clients (no secrets: the token is a placeholder)."""
        _, svc, _ = parts(request, project)
        svc.projects = [
            project
        ]  # the URL and the snippets are scoped to this project, like the tools they list
        st = state(request)
        offered = sorted(tools.names(svc))
        url = str(request.base_url).rstrip("/") + f"/api/knowledge/mcp?project={urllib.parse.quote(project)}"
        needs_token = bool(
            st.settings.api_token or st.settings.knowledge_acl_map
        )  # either one makes a token required
        auth = {"Authorization": "Bearer <your FORGE_API_TOKEN>"} if needs_token else {}
        backend = str(Path(__file__).resolve().parents[2])
        py = str(Path(sys.executable))
        stdio = {"command": py, "args": ["-m", "forge.knowledge.mcp_server"], "cwd": backend, "env": {"FORGE_KNOWLEDGE_PROJECTS": project}}  # fmt: skip
        http = {"type": "http", "url": url, **({"headers": auth} if auth else {})}
        name = "forge-knowledge"
        return {
            "project": project, "url": url, "auth_required": bool(auth), "tools": offered,
            "clients": [
                {"client": "Claude Code", "file": ".mcp.json (project) or `claude mcp add`", "config": {"mcpServers": {name: http}},
                 "command": f"claude mcp add --transport http {name} \"{url}\"" + (' --header "Authorization: Bearer <your FORGE_API_TOKEN>"' if auth else "")},
                {"client": "Cursor", "file": ".cursor/mcp.json", "config": {"mcpServers": {name: {"url": url, **({"headers": auth} if auth else {})}}}},
                {"client": "VS Code (Copilot agent)", "file": ".vscode/mcp.json", "config": {"servers": {name: http}}},
                {"client": "Kiro", "file": ".kiro/settings/mcp.json", "config": {"mcpServers": {name: {**stdio, "autoApprove": offered}}}},
                {"client": "Forge (another Forge, or any stdio client)", "file": "mcp.json", "config": {"mcpServers": {name: {**stdio, "autoApprove": offered}}}},
            ],
            "note": "stdio servers read the database and keys from the Forge .env next to the backend, so the snippet holds no secrets; HTTP uses this server.",
        }  # fmt: skip

    @r.post("/rebuild", status_code=202)
    def rebuild(request: Request, project: str = Query(...)):
        """Re-chunk and re-embed every source of the project with the current chunking and embedding format (after an
        upgrade changed either). Versions, links and decisions stay; runs as a background job with progress."""
        repo, svc, actor = parts(request, project)

        def work(emit: Callable[..., None]) -> dict[str, Any]:
            out = Ingestor(
                repo, svc.embedder, actor, progress=lambda st_, msg, d: emit(st_, msg, **d)
            ).rebuild(project)
            return {"project": project, **out}

        return start_job("rebuild", project, "Rebuild chunks and embeddings", work)

    @r.post("/query")
    def query(body: QueryIn, request: Request):
        """Run a Cypher query on the project's graph in Neo4j, or have the model write one from a question. Read-only,
        project-scoped, time- and size-limited (see knowledge/query.py)."""
        st = state(request)
        repo, svc, actor = parts(request, body.project)
        if svc.graph is None:
            raise HTTPException(409, "Neo4j is off: set FORGE_GRAPH_ENABLED=true and NEO4J_PASSWORD")
        if not (body.cypher or "").strip() and not (body.question or "").strip():
            raise HTTPException(422, "Give a Cypher query or a question")
        out: dict[str, Any] = {"project": body.project, "explanation": None, "generated": False}
        try:
            svc.graph.ensure_synced(repo, body.project)  # query what the database holds now
            if body.question and not (body.cypher or "").strip():
                if st.settings.provider_problem():
                    raise HTTPException(503, st.settings.provider_problem())
                tr = nl_to_cypher(st.runner.provider_factory(), body.question)
                out.update(cypher=tr["cypher"], explanation=tr["explanation"], generated=True)
            else:
                out["cypher"] = (body.cypher or "").strip()
            if body.run:
                out["result"] = run_query(svc.graph, body.project, out["cypher"]).as_dict()
        except QueryRefused as e:
            raise HTTPException(422, str(e)) from None
        except GraphUnavailable as e:
            text = str(e)
            if "AccessMode" in text:
                raise HTTPException(
                    422, "This query tries to change the graph; queries here are read-only"
                ) from None
            if "ClientError" in text or "SyntaxError" in text:
                raise HTTPException(422, f"Neo4j could not run the query: {text[:400]}") from None
            raise HTTPException(422 if "timed out" in text else 503, text) from None
        except ValueError as e:  # the model's answer was not usable JSON
            raise HTTPException(502, f"Could not turn the question into a query: {e}") from None
        repo.audit(body.project, actor, "graph.queried", "project", body.project, {"cypher": out["cypher"][:1000], "question": (body.question or "")[:500], "records": (out.get("result") or {}).get("records")})  # fmt: skip
        return out

    @r.get("/graphdb")
    def graphdb_status(request: Request, project: str = Query(...)):
        """Is the Neo4j copy of this project's graph current? (FORGE_GRAPH_ENABLED + NEO4J_PASSWORD turn it on)"""
        repo, svc, _ = parts(request, project)
        if svc.graph is None:
            return {
                "enabled": False,
                "detail": "Neo4j is off: set FORGE_GRAPH_ENABLED=true and NEO4J_PASSWORD",
            }
        try:
            synced = svc.graph.marker(project)
        except GraphUnavailable as e:
            return {"enabled": True, "available": False, "detail": str(e)}
        return {"enabled": True, "available": True, "current": synced == repo.change_marker(project), "browser": "http://localhost:7474"}  # fmt: skip

    @r.post("/graphdb/sync")
    def graphdb_sync(request: Request, project: str = Query(...)):
        repo, svc, _ = parts(request, project)
        out = mirror(svc, repo, project)
        if not out.get("enabled"):
            raise HTTPException(409, "Neo4j is off: set FORGE_GRAPH_ENABLED=true and NEO4J_PASSWORD")
        if "error" in out:
            raise HTTPException(503, out["error"])
        return out

    @r.get("/metrics")
    def get_metrics(request: Request, project: str = Query(...)):
        repo, _, _ = parts(request, project)
        s = state(request).settings
        return metrics(repo, project, cost=lambda t: estimated_cost(t, s))

    @r.get("/audit")
    def audit(request: Request, project: str = Query(...), limit: int = Query(100, ge=1, le=1000)):
        repo, _, _ = parts(request, project)
        return {"project": project, "events": [
            {"at": a.created_at.isoformat() + "Z", "actor": a.actor, "action": a.action, "object": f"{a.object_type}:{a.object_id}", "detail": a.detail}
            for a in repo.audit_log(project, limit)
        ]}  # fmt: skip

    @r.get("/projects")
    def projects(request: Request):
        repo, _, _ = parts(request)
        _, allowed = principal(request)
        return {"projects": [p for p in repo.projects() if allowed is None or p["project"] in allowed]}

    @r.post("/documents")
    def add_document(body: DocumentIn, request: Request):
        """Hand over a BRD, user story, requirement, design or other document (pasted or uploaded): it is stored with
        embeddings, a BRD is split into its identified requirements, declared relationships are created and likely links
        are proposed (never verified)."""
        repo, svc, actor = parts(request, body.project)
        data = None
        if body.data_base64:
            try:
                data = base64.b64decode(body.data_base64, validate=True)
            except ValueError:
                raise HTTPException(422, "data_base64 is not valid base64") from None
        if not body.text and not data:
            raise HTTPException(422, "Give the document as text or as an uploaded file")

        def work(emit: Callable[..., None]) -> dict[str, Any]:
            before = tables(repo, body.project, emit)
            emit("upload", f"Received {body.filename or 'pasted text'} ({len(data) if data else len((body.text or '').encode())} bytes) as {body.kind}")  # fmt: skip
            res = Ingestor(repo, svc.embedder, actor, progress=lambda st_, msg, d: emit(st_, msg, **d)).ingest_text(
                body.project, kind=body.kind, filename=body.filename, text=body.text, data=data,
                external_id=body.external_id, title=body.title, links=body.links, scope=body.scope,
            )  # fmt: skip
            if res.failed and not res.ingested:
                raise ValueError("; ".join(res.failed))
            tables(repo, body.project, emit, before)
            return {"project": body.project, "embedding_model": svc.embedder.name, **res.as_dict(), "neo4j": mirror(svc, repo, body.project, emit)}  # fmt: skip

        if body.background:
            return start_job("upload", body.project, f"Add {body.filename or body.kind}", work)
        try:
            return work(lambda *a, **k: None)
        except ValueError as e:
            raise HTTPException(422, str(e)) from None

    @r.post("/extract", status_code=202)
    def start_extract(body: ExtractIn, request: Request):
        """Read the documents with the configured model and propose the concepts, actors, fields and rules they state
        (each with an exact quote, checked against the text) and the relationships between them. Runs in the background."""
        st = state(request)
        repo, svc, actor = parts(request, body.project)
        if st.settings.provider_problem():
            raise HTTPException(503, st.settings.provider_problem())
        if any(j["project"] == body.project and j["kind"] == "extract" and j["status"] == "running" for j in jobs.values()):  # fmt: skip
            raise HTTPException(409, "An extraction is already running for this project")

        def work(emit: Callable[..., None]) -> dict[str, Any]:
            rep = run_extraction(
                repo, Ingestor(repo, svc.embedder, actor, progress=lambda st_, msg, d: emit(st_, msg, **d)),
                st.runner.provider_factory, body.project, tuple(body.kinds), body.limit, reset=body.reset,
                on_event=lambda st_, msg, d: emit(st_, msg, **d),
            )  # fmt: skip
            refused = rep.rejected_entities + rep.rejected_relations
            emit("ontology", f"{rep.entities} concepts/rules and {rep.relations} relationships kept; {refused} proposals dropped (quote not in the document, or not allowed by the ontology)", kept=rep.entities + rep.relations, dropped=refused)  # fmt: skip
            return {**rep.as_dict(), "neo4j": mirror(svc, repo, body.project, emit)}

        return start_job("extract", body.project, "Extract concepts and rules", work)

    @r.get("/extract/{job_id}")
    def extract_status(job_id: str, request: Request):
        return get_job(job_id, request)

    @r.post("/link", status_code=202)
    def start_link(body: LinkIn, request: Request):
        """Read each document with the configured model and propose the links its text implies to other items (depends on,
        contradicts, clarifies, flags a gap in, uses a system). Every link needs an exact quote and stays a proposal until a
        person promotes it. Runs in the background."""
        st = state(request)
        repo, svc, actor = parts(request, body.project)
        if st.settings.provider_problem():
            raise HTTPException(503, st.settings.provider_problem())
        if any(
            j["project"] == body.project and j["kind"] == "link" and j["status"] == "running"
            for j in jobs.values()
        ):
            raise HTTPException(409, "Linking is already running for this project")

        def work(emit: Callable[..., None]) -> dict[str, Any]:
            rep = linking.run(repo, st.runner.provider_factory, body.project, actor, limit=body.limit, on_event=lambda st_, msg, d: emit(st_, msg, **d))  # fmt: skip
            emit("infer", f"{rep.proposed} links proposed; {sum(rep.rejected.values())} dropped ({', '.join(f'{k}: {v}' for k, v in rep.rejected.items()) or 'none'})", kept=rep.proposed, dropped=sum(rep.rejected.values()))  # fmt: skip
            return {**rep.as_dict(), "neo4j": mirror(svc, repo, body.project, emit)}

        return start_job("link", body.project, "Link documents", work)

    @r.get("/link/{job_id}")
    def link_status(job_id: str, request: Request):
        return get_job(job_id, request)

    @r.post("/export")
    def export_markdown(body: ExportIn, request: Request):
        """Write a readable, diffable Markdown copy of the project's knowledge (one file per source, front matter, wikilinks)
        to a folder. The database stays the master. Limited like ingest by FORGE_KNOWLEDGE_ROOTS; a folder with other files
        and no manifest is refused."""
        st = state(request)
        repo, _, _ = parts(request, body.project)
        folder = Path(body.path.strip().strip('"').strip("'").strip()).expanduser().resolve()
        roots = st.settings.knowledge_root_paths if st.settings.knowledge_roots.strip() else []
        if roots and not any(folder.is_relative_to(x) for x in roots):
            raise HTTPException(422, "The folder is outside FORGE_KNOWLEDGE_ROOTS")
        try:
            return markdown_export.export(repo, body.project, folder)
        except (ValueError, OSError) as e:
            raise HTTPException(422, str(e)) from None

    @r.get("/findings")
    def findings(request: Request, project: str = Query(...)):
        """Problems in the documents as the last ingestion of each folder found them: ids used twice (the second replaces
        the first), references to ids that do not exist, links the ontology refuses, unreadable or cut files, sources with
        identical text, renderings the backlog disagrees with."""
        repo, _, _ = parts(request, project)
        return findings_mod.latest(repo, project)

    @r.get("/coverage")
    def coverage(request: Request, project: str = Query(...), path: str | None = Query(None)):
        """Did everything reach the knowledge base? Every chunk covers its source's text, sources that should be linked and
        are not, and, with `path` (the input folder), every line and JSON value of the input files found in what is stored."""
        st = state(request)
        repo, _, _ = parts(request, project)
        folder = None
        if path:
            try:
                folder = Path(path.strip().strip('"').strip("'").strip()).expanduser().resolve()
                if st.settings.knowledge_roots.strip():
                    authorize_repo(str(folder), st.settings.knowledge_root_paths)
                elif not folder.is_dir():
                    raise PathViolation(f"Not found or not a directory: {path}")
            except PathViolation as e:
                raise HTTPException(422, str(e)) from None
        return coverage_audit(repo, project, folder)

    @r.get("/embeddings")
    def embeddings(request: Request, project: str = Query(...), limit: int = Query(1500, ge=10, le=5000)):
        """Every chunk of the project that has an embedding, placed on a 2-D map (principal components), so you can see
        which texts the model considers alike. Only the dominant embedding model is drawn (vectors of different models
        cannot be compared); the others are counted."""
        repo, _, _ = parts(request, project)
        rows = [(c, s) for c, s in repo.project_chunks(project) if c.embedding]
        by_model: dict[str, int] = {}
        for c, _ in rows:
            by_model[c.embedding_model or "?"] = by_model.get(c.embedding_model or "?", 0) + 1
        total_chunks = len(repo.project_chunks(project))
        if not rows:
            return {"project": project, "model": None, "dimensions": 0, "chunks": total_chunks, "embedded": 0, "by_model": by_model, "points": [], "truncated": False}  # fmt: skip
        model = max(by_model, key=lambda m: by_model[m])
        rows = [(c, s) for c, s in rows if (c.embedding_model or "?") == model]
        truncated = len(rows) > limit
        rows = rows[:limit]
        coords = pca2([c.embedding for c, _ in rows])  # type: ignore[misc]
        return {
            "project": project, "model": model, "dimensions": len(rows[0][0].embedding or []), "chunks": total_chunks,
            "embedded": sum(by_model.values()), "by_model": by_model, "truncated": truncated,
            "points": [
                {"id": c.id, "source_id": s.id, "external_id": s.external_id, "kind": s.kind, "heading": c.heading, "text": c.text[:400], "x": round(x, 4), "y": round(y, 4)}  # fmt: skip
                for (c, s), (x, y) in zip(rows, coords, strict=True)
            ],
        }  # fmt: skip

    @r.get("/chunks/{chunk_id}/neighbors")
    def chunk_neighbors(
        chunk_id: str, request: Request, project: str = Query(...), k: int = Query(6, ge=1, le=20)
    ):
        """The chunks whose embeddings are closest (cosine similarity) to this one: what the model thinks is alike."""
        repo, _, _ = parts(request, project)
        pool = repo.project_chunks(project)
        me = next(((c, s) for c, s in pool if c.id == chunk_id), None)
        if me is None or not me[0].embedding:
            raise HTTPException(404, "No such embedded chunk in this project")
        near = sorted(
            (
                (cosine(me[0].embedding, c.embedding), c, s)
                for c, s in pool
                if c.id != chunk_id and c.embedding and c.embedding_model == me[0].embedding_model and len(c.embedding) == len(me[0].embedding)
            ),
            key=lambda x: -x[0],
        )[:k]  # fmt: skip
        return {
            "chunk": {
                "id": me[0].id,
                "external_id": me[1].external_id,
                "kind": me[1].kind,
                "heading": me[0].heading,
                "text": me[0].text,
                "model": me[0].embedding_model,
                "dimensions": len(me[0].embedding),
                "vector_head": [round(x, 4) for x in me[0].embedding[:12]],
            },  # fmt: skip
            "neighbors": [
                {
                    "id": c.id,
                    "external_id": s.external_id,
                    "kind": s.kind,
                    "heading": c.heading,
                    "text": c.text[:300],
                    "similarity": round(sim, 4),
                }
                for sim, c, s in near
            ],  # fmt: skip
        }

    @r.get("/graph")
    def graph(request: Request, project: str = Query(...), bundle: str | None = None, only_bundle: bool = False, include_rejected: bool = False, view: Literal["documents", "concepts", "all"] = "documents"):  # fmt: skip
        """The project's knowledge graph (sources and relationships). With `bundle`, the sources and relationships that
        bundle contains are flagged: that is the task's context graph."""
        repo, _, _ = parts(request, project)
        inb_nodes: set[str] = set()
        inb_edges: set[tuple[str, str, str]] = set()
        if bundle:
            row = repo.get_bundle(bundle)
            if row is None or row.project != project:
                raise HTTPException(404, "No such bundle in this project")
            inb_nodes = {i["source_id"] for slot in row.content["slots"].values() for i in slot}
            inb_edges = {(e["from"], e["to"], e["type"]) for e in row.content["relationships"]}
        sources = repo.sources(project, limit=5000)
        names = {s.id: s.external_id for s in sources}
        by_id = {s.id: s for s in sources}
        is_content = lambda s: s.kind in ("concept", "rule")  # noqa: E731 - what the documents SAY (extracted), not the documents
        # view=documents: the documents and how they trace to each other; view=concepts: the facts extracted from inside
        # them; view=all: both, with the "mentions" links between them (what a graph database browser shows)
        if view != "all":
            sources = [s for s in sources if is_content(s) == (view == "concepts")]

        def subtype(s) -> str | None:  # noqa: ANN001 - the extracted kind of a concept (actor, rule, field, ...)
            v = repo.version(s.id) if is_content(s) else None
            return (v.meta or {}).get("concept_type") if v is not None else None

        nodes = [
            {"id": s.id, "kind": s.kind, "external_id": s.external_id, "title": s.title, "version": s.current_version, "origin": s.origin, "state": s.ingestion_state, "in_bundle": s.id in inb_nodes, "subtype": subtype(s)}  # fmt: skip
            for s in sources if not only_bundle or s.id in inb_nodes
        ]  # fmt: skip
        keep = {n["id"] for n in nodes}
        edges = []
        for e in repo.edges(project, limit=20000):
            if (
                e.src_id not in keep
                or e.dst_id not in keep
                or (e.status == "rejected" and not include_rejected)
            ):
                continue
            in_b = (names[e.src_id], names[e.dst_id], e.type) in inb_edges
            if only_bundle and not in_b:
                continue
            ends = (by_id.get(e.src_id), by_id.get(e.dst_id))
            # extracted concepts gain versions whenever another document adds evidence: that is not a change of meaning
            extracted = any(x and x.kind in ("concept", "rule") for x in ends)
            stale = bool(
                ends[0]
                and ends[1]
                and not extracted
                and (ends[0].current_version != e.src_version or ends[1].current_version != e.dst_version)
            )
            edges.append({"id": e.id, "from": e.src_id, "to": e.dst_id, "type": e.type, "status": e.status, "origin": e.origin, "evidence": e.evidence, "stale": stale, "in_bundle": in_b})  # fmt: skip
        return {"project": project, "bundle": bundle, "nodes": nodes, "edges": edges}

    def _task_for_eval(request: Request, task_id: str):
        st = state(request)
        try:
            task = st.store.get_task(task_id)
        except NotFound:
            raise HTTPException(404, "No such task") from None
        if not task.knowledge_project:
            raise HTTPException(422, "This task did not use a knowledge project")
        parts(request, task.knowledge_project)  # access
        return st, task

    @r.get("/tasks/{task_id}/evaluation")
    def task_evaluation(task_id: str, request: Request):
        st, task = _task_for_eval(request, task_id)
        _, svc, _ = parts(request, task.knowledge_project)
        return evaluate_task(
            KnowledgeRepo(st.store), task, st.store.verifications(task_id), task.report, svc.embedder
        )

    @r.post("/tasks/{task_id}/evaluation")
    def review_criterion(task_id: str, body: ReviewIn, request: Request):
        st, task = _task_for_eval(request, task_id)
        repo = KnowledgeRepo(st.store)
        valid = {c["id"] for c in evaluate_task(repo, task, [], task.report)["criteria"]}
        if body.criterion not in valid:
            raise HTTPException(422, "Unknown criterion for this task")
        _, _, actor = parts(request)
        record_review(
            repo, task.knowledge_project or "", task_id, body.criterion, body.verdict, body.note, actor
        )
        _, svc, _ = parts(request, task.knowledge_project)
        return evaluate_task(repo, task, st.store.verifications(task_id), task.report, svc.embedder)

    @r.post("/eval")
    def retrieval_eval(body: EvalIn, request: Request):
        """Hit rate and MRR of search over cases you supply, per mode (hybrid, vector, keyword)."""
        repo, svc, actor = parts(request, body.project)
        for c in body.cases:
            if (
                not isinstance(c.get("query"), str)
                or not isinstance(c.get("expect"), list)
                or not c["expect"]
            ):
                raise HTTPException(422, 'Each case needs "query" (text) and "expect" (list of ids)')
        weights = (body.keyword_weight, 1.0) if body.keyword_weight is not None else None
        out = evaluate(svc, body.project, body.cases, weights=weights)
        if body.bundles:
            out["bundles"] = evaluate_bundles(svc, body.project, body.cases)
        summary = {m: {k: v for k, v in r.items() if k != "misses"} for m, r in out["modes"].items()}
        if body.bundles:
            summary["bundles"] = {k: v for k, v in out["bundles"].items() if k != "misses"}
        repo.audit(body.project, actor, "retrieval.evaluated", "project", body.project, {"cases": len(body.cases), **summary})  # fmt: skip
        return {**out, "keyword_weight": weights[0] if weights else svc.weights[0]}

    return r
