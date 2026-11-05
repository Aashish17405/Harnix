"""Ingestion: documents, Jira tickets (mock data only for now), Git repositories and tests become versioned sources
with chunks, embeddings and declared relationships.

Idempotent: a source whose content hash did not change is not touched (nothing re-chunked or re-embedded unless a
previous run left chunks without embeddings). Partial failures are contained per source: a failing embedding call
leaves the source in state `partial` and the next ingest retries just that.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from harnix.graph.extract import extract_python, is_test_path, language_of
from harnix.graph.walker import walk
from harnix.harness.skills import parse_frontmatter
from harnix.knowledge import renderings
from harnix.knowledge.embeddings import Embedder, doc_model
from harnix.knowledge.fields import field_rows
from harnix.knowledge.ontology import OntologyViolation, default, domain_terms, for_project, norm
from harnix.knowledge.repo import EDGE_TYPES, KINDS, KnowledgeRepo
from harnix.knowledge.reports import dag_edges, is_report, parse_report
from harnix.knowledge.sections import (
    BACKLOG_PREFIXES,
    ID,
    clean_line,
    docx_to_markdown,
    epic_notes,
    first_title,
    plain,
    split_epics,
    split_sections,
    split_stories,
    story_notes,
)
from harnix.knowledge.taxonomy import is_taxonomy
from harnix.knowledge.taxonomy import parse as parse_taxonomy
from harnix.persistence.models import KnowledgeSource

log = logging.getLogger(__name__)
MAX_DOC_BYTES = 5_000_000  # FORGE_KNOWLEDGE_MAX_DOC_BYTES (see configure)
MAX_DOCX_BYTES = 20_000_000
MIN_CHUNK_CHARS = 120  # shorter pieces (a lone heading) are merged into the next one
MAX_CHUNKS = 5000  # FORGE_KNOWLEDGE_MAX_CHUNKS: more are cut AND reported (see Ingestor.add)
CHUNK_CHARS = 1000
OVERLAP_CHARS = (
    150  # the end of a chunk repeated at the start of the next one of the same section (not for tables)
)


def configure(settings: Any) -> None:
    """Take the size limits from the settings (called by the API before it ingests)."""
    global MAX_DOC_BYTES, MAX_CHUNKS
    MAX_DOC_BYTES, MAX_CHUNKS = settings.knowledge_max_doc_bytes, settings.knowledge_max_chunks


DIR_KINDS = {
    "brd": "brd", "brds": "brd", "requirements": "requirement", "requirement": "requirement", "stories": "story",
    "story": "story", "tickets": "ticket", "design": "design", "designs": "design", "docs": "doc", "domain": "doc",
}  # fmt: skip
FILE_KINDS = {
    "brd": "brd", "epic": "epic", "epics": "epic", "user_story": "story", "user_stories": "story", "story": "story",
    "stories": "story",
}  # fmt: skip
_SCOPE = re.compile(
    r"^([A-Za-z]+-\d+)"
)  # a folder named BRD-01-... is a namespace: its US-001 is not another folder's US-001
_ID_LIST = re.compile(r"(?i)\b(?:implements|satisfies)\b[:\s]+((?:[A-Za-z][A-Za-z0-9]*-\d+[\s,]*)+)")
_ID = re.compile(r"[A-Za-z][A-Za-z0-9]*-\d+")


@dataclass
class IngestResult:
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    skipped: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    partial: list[str] = field(default_factory=list)
    edges_created: int = 0
    unresolved: list[str] = field(default_factory=list)
    ingested: list[str] = field(default_factory=list)  # kind:external_id of every document taken in
    review: list[str] = field(
        default_factory=list
    )  # kept as `doc` because their kind is unknown: a person decides
    suggested: int = 0  # relationships proposed for a person to confirm
    # what was captured, for the live ingestion view (not in as_dict: it would bloat the audit log)
    entities: list[dict[str, Any]] = field(default_factory=list)
    links: list[dict[str, Any]] = field(default_factory=list)
    findings: list[dict[str, str]] = field(default_factory=list)  # problems in the documents themselves

    def as_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if k not in ("entities", "links")}


def merge_evidence(
    evidence: list[dict[str, str]], source: str, quote: str, keep: int = 20
) -> list[dict[str, str]]:
    """One concept shared by several documents (a role, a glossary term, a system) keeps one quote per document. A document
    that says it again replaces its own quote IN PLACE: moving it to the end would reorder the list each time another
    document is read, and every reprocessing would make a new version of the concept."""
    out = [{**e, "quote": quote} if e.get("source") == source else e for e in evidence]
    if not any(e.get("source") == source for e in out):
        out.append({"source": source, "quote": quote})
    return out[-keep:]


def review_kind(
    kind: str, meta: dict[str, Any] | None, external_id: str, res: IngestResult
) -> tuple[str, dict[str, Any]]:
    """An unknown kind (a typo, or a new one in front matter) is not a failure: the document is kept as `doc`, flagged for
    a person to accept or reclassify (GET and POST /api/knowledge/review), and reported in the ingest result."""
    meta = dict(meta or {})
    if kind in KINDS:
        return kind, meta
    meta.update({"needs_review": True, "requested_kind": str(kind)[:60]})
    res.review.append(f"{external_id}: kind {str(kind)[:60]!r} is not known")
    return "doc", meta


def normalize(text: str) -> str:
    t = text.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")
    return re.sub(r"\n{3,}", "\n\n", t).strip() + "\n"


_TABLE_ROW = re.compile(r"^\s*\|")
_TABLE_SEP = re.compile(r"^\s*\|?\s*:?-+:?\s*(?:\||$)")  # | --- |, |:--|, | :----: |


def _cut(text: str, limit: int) -> list[str]:
    """Prose longer than `limit` cut at a line or sentence end (else a space): every piece is the text's own wording."""
    out: list[str] = []
    while len(text) > limit:
        window = text[:limit]
        at = max(window.rfind("\n"), *(window.rfind(p) + 1 for p in (". ", "! ", "? ", "; ")))
        if at < limit // 3:
            at = window.rfind(" ")
        if at < limit // 3:
            at = limit
        if piece := text[:at].strip():
            out.append(piece)
        text = text[at:]
    if text.strip():
        out.append(text.strip())
    return out


def _split_block(block: str) -> list[str]:
    """A paragraph or table longer than CHUNK_CHARS in pieces that still make sense alone: a table by rows, with its
    header row repeated in every piece (a row of a field table says nothing without its column names), prose at a
    sentence end."""
    if len(block) <= CHUNK_CHARS:
        return [block]
    lines = block.strip("\n").split("\n")
    if not all(_TABLE_ROW.match(x) for x in lines if x.strip()):
        return _cut(block, CHUNK_CHARS - OVERLAP_CHARS)  # room for the overlap the next chunk starts with
    head = lines[:2] if len(lines) > 1 and _TABLE_SEP.match(lines[1]) else lines[:1]
    out: list[str] = []
    rows: list[str] = []
    for row in lines[len(head) :]:
        if rows and sum(len(x) + 1 for x in (*head, *rows, row)) > CHUNK_CHARS:
            out.append("\n".join(head + rows))
            rows = []
        rows.append(row)
    if rows:
        out.append("\n".join(head + rows))
    return out


def _tail(text: str) -> str:
    """The last sentence or so (about OVERLAP_CHARS) of a chunk, from a word or sentence start: what the next chunk of
    the same section begins with, so a fact cut at the boundary is whole in one of them. Never from a table."""
    t = text.strip()
    if len(t) <= OVERLAP_CHARS * 2 or "|" in t[-OVERLAP_CHARS:]:
        return ""
    tail = t[-OVERLAP_CHARS:]
    tail = tail[tail.find(" ") + 1 :] if " " in tail else tail
    cut = tail.find(". ")
    return (tail[cut + 2 :] if 0 <= cut < len(tail) - 30 else tail).strip()


def chunk_text(text: str, code: bool = False) -> list[tuple[str, str]]:
    """(heading, text) pieces of at most ~CHUNK_CHARS: Markdown by heading then paragraph, code by line window. The
    heading is the section's path ("5. Business Requirements > 5.2.1 Accounts List"), so a chunk keeps its context."""
    out: list[tuple[str, str]] = []
    if code:
        lines = text.split("\n")
        for i in range(0, len(lines), 40):
            body = "\n".join(lines[i : i + 40]).strip()
            if body:
                out.append((f"lines {i + 1}-{min(i + 40, len(lines))}", body))
        return out
    heading, buf = "", ""
    stack: list[
        tuple[int, str]
    ] = []  # the headings above the current block: "5. Requirements > 5.2.1 Accounts"

    def flush() -> None:
        nonlocal buf
        if buf.strip():
            out.append((heading, buf.strip()))
        buf = ""

    for block in re.split(r"\n\s*\n", text):
        m = re.match(r"^(#{1,6})\s+(.*)$", block.strip().split("\n")[0])
        if m and (title := clean_line(m.group(2))):  # an empty `#` line (exports have them) is no section
            flush()
            stack = [(lv, t) for lv, t in stack if lv < len(m.group(1))] + [(len(m.group(1)), title)]
            heading = " > ".join(t for _, t in stack)
        for piece in _split_block(block):
            if len(buf) + len(piece) > CHUNK_CHARS and buf:
                tail = _tail(buf)
                flush()
                buf = tail + "\n\n" if tail and len(tail) + len(piece) + 4 <= CHUNK_CHARS else ""
            buf += piece + "\n\n"
    flush()
    # a heading with (almost) nothing under it says little on its own: it joins the next piece
    merged: list[tuple[str, str]] = []
    carry = ""
    for i, (h, t) in enumerate(out):
        if len(t) < MIN_CHUNK_CHARS and i + 1 < len(out):
            carry += t + "\n\n"
            continue
        merged.append((h, carry + t))
        carry = ""
    return merged


_KIND_WORD = {
    "brd": "BRD",
    "requirement": "requirement",
    "epic": "epic",
    "feature": "feature",
    "story": "user story",
}


def embed_text(src: Any, heading: str, text: str) -> str:
    """What is embedded for one chunk: the source it belongs to (kind, id, title), its section, then its text.
    Without this, a section like "2.2 Objective" or "Acceptance criteria" looks the same in every document."""
    head = f"[{_KIND_WORD.get(src.kind, src.kind)} {src.external_id}] {src.title}"
    section = f"\nSection: {heading}" if heading and heading.strip().lower() not in src.title.lower() else ""
    return f"{head}{section}\n{text}"


EMBED_BATCH = 64  # chunks per embedding call: bounded requests, and progress the dashboard can show

Progress = Callable[[str, str, dict[str, Any]], None]  # (stage, message, data)


class Ingestor:
    def __init__(self, repo: KnowledgeRepo, embedder: Embedder, actor: str = "ingest", progress: Progress | None = None):  # fmt: skip
        self.repo, self.embedder, self.actor = repo, embedder, actor
        self._pending: dict[str, str] = {}
        self._seen: dict[tuple[str, str], tuple[str, str]] = {}  # (kind, id) -> (hash, uri) of this run
        self._extras: dict[str, dict[str, str]] = {}  # tracker columns of renderings, by item id
        self._records: dict[str, list[dict[str, Any]] | None] = {}  # parsed renderings by file
        self._corpus: str | None = None
        self.progress = progress  # live reporting for the dashboard's pipeline; None = silent

    def emit(self, stage: str, message: str, **data: Any) -> None:
        if self.progress is not None:
            try:
                self.progress(stage, message, data)
            except Exception:  # noqa: BLE001 - reporting must never break ingestion
                log.debug("progress callback failed", exc_info=True)

    # ---- one source ---------------------------------------------------------------------------------
    def add(
        self, project: str, kind: str, external_id: str, title: str, text: str, *, uri: str | None = None,
        origin: str = "ingest", meta: dict[str, Any] | None = None, res: IngestResult | None = None, code: bool = False, defer: bool = False,
    ) -> None:  # fmt: skip
        res = res if res is not None else IngestResult()
        kind, meta = review_kind(kind, meta, external_id, res)
        body = normalize(text)
        if len(body.encode()) > MAX_DOC_BYTES:
            res.skipped.append(f"{external_id}: larger than {MAX_DOC_BYTES} bytes")
            return
        h = hashlib.sha256(body.encode()).hexdigest()
        if (
            kind != "concept"
        ):  # an id used twice in one run with different content: the second replaces the first
            earlier = self._seen.get((kind, external_id))
            if earlier is not None and earlier[0] != h:
                res.findings.append({"kind": "duplicate_id", "where": external_id, "message": f"{kind} id {external_id} is used twice with different content ({earlier[1] or '?'} and {uri or '?'}); the second replaces the first"})  # fmt: skip
            self._seen[(kind, external_id)] = (h, uri or "")
        try:
            src, ver, changed = self.repo.upsert_version(
                project, kind, external_id, title, uri, origin, body, h, meta or {}
            )
        except Exception as e:  # noqa: BLE001 - one bad source must not stop the batch
            log.exception("ingest %s failed", external_id)
            res.failed.append(f"{external_id}: {type(e).__name__}")
            return
        pieces = chunk_text(body, code) if ver is not None and changed else []
        if len(pieces) > MAX_CHUNKS:
            res.skipped.append(
                f"{external_id}: only the first {MAX_CHUNKS} of {len(pieces)} chunks are indexed (FORGE_KNOWLEDGE_MAX_CHUNKS)"
            )
            pieces = pieces[:MAX_CHUNKS]
        res.entities.append({
            "kind": kind, "external_id": external_id, "title": title[:200], "chars": len(body),
            "status": "unchanged" if ver is None else ("created" if ver.version == 1 else "updated"),
            "version": src.current_version, "chunks": len(pieces), "sections": [h for h, _ in pieces if h][:8],
            "declares": {t: list(v)[:10] for t, v in ((meta or {}).get("declared") or {}).items()},
        })  # fmt: skip
        if ver is not None:
            if ver.version == 1:
                res.created += 1
            else:
                res.updated += 1
            if changed:
                self.repo.add_chunks(src, ver.id, pieces)
            self.repo.audit(project, self.actor, "source.ingested", "source", src.id, {"external_id": external_id, "kind": kind, "version": ver.version, "hash": h[:12], "origin": origin, "content_changed": changed})  # fmt: skip
        else:
            res.unchanged += 1
        if defer:  # a batch embeds everything it ingested in a few calls (see flush_embeddings)
            self._pending[src.id] = external_id
        else:
            self._embed(src.id, external_id, res)

    def flush_embeddings(self, res: IngestResult) -> None:
        """Embed the chunks of every source added with defer=True in one go. A failed call leaves those sources
        `partial`; the next ingest (nothing changed) retries just them."""
        pending, self._pending = self._pending, {}
        model = doc_model(self.embedder)
        todo = [(sid, ext, c) for sid, ext in pending.items() for c in self.repo.chunks(sid) if c.embedding is None or c.embedding_model != model]  # fmt: skip
        srcs = {sid: self.repo.get_source(sid) for sid in {sid for sid, _, _ in todo}}
        ok_sources = set(pending) - {sid for sid, _, _ in todo}
        if todo:
            self.emit(
                "embed", f"Embedding {len(todo)} chunks with {self.embedder.name}", done=0, total=len(todo)
            )
        failed: set[str] = set()
        for i in range(0, len(todo), EMBED_BATCH):
            batch = todo[i : i + EMBED_BATCH]
            try:
                texts = [embed_text(srcs[sid], c.heading, c.text) for sid, _, c in batch]
                vecs = self.embedder.embed(texts)
                if len(vecs) != len(batch):
                    raise ValueError("embedding count mismatch")
                self.repo.set_embeddings({c.id: v for (_, _, c), v in zip(batch, vecs, strict=True)}, model)
                if i == 0:  # one worked example: exactly what was embedded and what came back
                    self.emit("embed", f"{len(vecs[0])}-dimensional vectors from {self.embedder.name}, stored per chunk", model=model, dims=len(vecs[0]), sample={"source": batch[0][1], "heading": batch[0][2].heading, "embedded_text": texts[0][:700], "vector_head": [round(float(x), 4) for x in vecs[0][:8]]})  # fmt: skip
                self.emit("embed", f"Embedded {min(i + EMBED_BATCH, len(todo))}/{len(todo)} chunks", done=min(i + EMBED_BATCH, len(todo)), total=len(todo))  # fmt: skip
            except Exception as e:  # noqa: BLE001
                log.warning("embedding batch failed: %s", type(e).__name__)
                self.emit(
                    "embed",
                    f"Embedding failed for {len(batch)} chunks: {type(e).__name__}",
                    failed=len(batch),
                )
                for sid in {sid for sid, _, _ in batch}:
                    self.repo.set_state(sid, "partial", f"{type(e).__name__}: {e}")
                    failed.add(sid)
        for sid in failed:
            res.partial.append(pending[sid])
        ok_sources |= {sid for sid, _, _ in todo} - failed
        for sid in ok_sources:
            self.repo.set_state(sid, "ok")

    def _embed(self, source_id: str, external_id: str, res: IngestResult) -> None:
        self._pending[source_id] = external_id
        self.flush_embeddings(res)

    def rebuild(self, project: str) -> dict[str, int]:
        """Re-chunk and re-embed every current source of a project with the current chunking and embedding format
        (after either changed). Versions, links and decisions are untouched; only chunks and vectors are rewritten."""
        res = IngestResult()
        sources = self.repo.sources(project, limit=100_000)
        self.emit("scan", f"Rebuilding chunks and embeddings of {len(sources)} sources", total=len(sources))
        chunks = 0
        for n, s in enumerate(sources, 1):
            v = self.repo.version(s.id)
            if v is None:
                continue
            rows = self.repo.replace_chunks(
                s, v.id, chunk_text(v.content, code=s.kind in ("code", "test"))[:MAX_CHUNKS]
            )
            chunks += len(rows)
            self._pending[s.id] = s.external_id
            if n % 50 == 0 or n == len(sources):
                self.emit(
                    "read",
                    f"Re-chunked {n}/{len(sources)} sources ({chunks} chunks)",
                    done=n,
                    total=len(sources),
                )
        self.flush_embeddings(res)
        self.repo.audit(project, self.actor, "ingest.rebuild", "project", project, {"sources": len(sources), "chunks": chunks, "partial": res.partial[:20]})  # fmt: skip
        return {"sources": len(sources), "chunks": chunks, "partial": len(res.partial)}

    # ---- relationships -------------------------------------------------------------------------------
    def resolve_declared(self, project: str, res: IngestResult) -> None:
        """Create edges for relationships that documents declare (`implements: [REQ-1]`, `Implements: REQ-1` in a code
        comment). Reads every current version, so the order of ingestion does not matter and a re-run is a no-op."""
        for src in self.repo.sources(project, limit=5000):
            ver = self.repo.version(src.id)
            declared = (ver.meta or {}).get("declared") if ver else None
            if not isinstance(declared, dict):
                continue
            for etype, refs in declared.items():
                if etype not in EDGE_TYPES or not isinstance(refs, list):
                    continue
                for ref in refs:
                    dst = self.repo.resolve(project, str(ref))
                    if dst is None or dst.id == src.id:
                        key = f"{src.external_id} -{etype}-> {ref}"
                        if key not in res.unresolved and dst is None:
                            res.unresolved.append(key)
                        continue
                    try:
                        _, created = self.repo.upsert_edge(project, src, dst, etype, "verified", "source", f"declared in {src.external_id}")  # fmt: skip
                    except (
                        OntologyViolation
                    ) as e:  # the document declares a link the ontology does not allow: say so
                        res.unresolved.append(f"{src.external_id} -{etype}-> {ref}: {e}"[:300])
                        continue
                    res.edges_created += int(created)
                    if created:
                        res.links.append({"from": src.external_id, "type": etype, "to": dst.external_id, "status": "verified", "how": f"declared in {src.external_id}"})  # fmt: skip
        self.resolve_cited(project, res)
        self.resolve_tags(project, res)
        self.resolve_dag(project, res)
        self.resolve_reports(project, res)

    def _lookup(
        self, by_ext: dict[str, KnowledgeSource], src: KnowledgeSource, ref: str
    ) -> KnowledgeSource | None:
        """An id named in `src`'s text: in its own namespace first (a pack's US-001), else as written."""
        scope = src.external_id.split("/", 1)[0] + "/" if "/" in src.external_id else ""
        return by_ext.get(scope + ref) or by_ext.get(ref)

    def resolve_dag(self, project: str, res: IngestResult) -> None:
        """A dependency graph (Mermaid `US-001 --> US-002`; dotted `-.->` is provisional) says the right item cannot start
        before the left one: `US-002 depends_on US-001`, verified (the document draws it; the arrow is in the evidence)."""
        sources = self.repo.sources(project, limit=100_000)
        by_ext = {s.external_id: s for s in sources}
        for src in sources:
            ver = self.repo.version(src.id) if src.kind in ("doc", "design") else None
            for before, after, firm in dag_edges(ver.content) if ver is not None else []:
                a, b = self._lookup(by_ext, src, before), self._lookup(by_ext, src, after)
                if a is None or b is None or a.id == b.id:
                    continue
                arrow = "-->" if firm else "-.->"
                try:
                    _, created = self.repo.upsert_edge(project, b, a, "depends_on", "verified", "source", f"dependency graph {src.external_id}: {before} {arrow} {after} ({'firm' if firm else 'provisional'})")  # fmt: skip
                except OntologyViolation:
                    continue
                res.edges_created += int(created)
                if created:
                    res.links.append({"from": b.external_id, "type": "depends_on", "to": a.external_id, "status": "verified", "how": f"dependency graph {src.external_id}"})  # fmt: skip

    def resolve_reports(self, project: str, res: IngestResult) -> None:
        """A gate or review report that names an item on a line reporting a finding (WARN, BLOCK, gap, failed) `flags_gap`
        to it. Proposed: a line naming two items does not say which one the finding is about."""
        sources = self.repo.sources(project, limit=100_000)
        by_ext = {s.external_id: s for s in sources}
        for src in sources:
            ver = self.repo.version(src.id) if src.kind in ("doc", "design") else None
            report = (ver.meta or {}).get("report") if ver is not None else None
            for f in report.get("flagged", []) if isinstance(report, dict) else []:
                dst = self._lookup(by_ext, src, str(f.get("id", "")))
                if dst is None or dst.id == src.id:
                    continue
                try:
                    _, created = self.repo.upsert_edge(project, src, dst, "flags_gap", "proposed", "source", f"flagged in {src.external_id}: {f.get('quote', '')}"[:300])  # fmt: skip
                except OntologyViolation:
                    continue
                res.edges_created += int(created)

    def resolve_tags(self, project: str, res: IngestResult) -> None:
        """Every item whose text uses the name of a taxonomy's leaf tag is `tagged` with it. Verified: the name is in the
        text (a string match, like a glossary mention); a pair already linked, in either direction, is left alone."""
        sources = self.repo.sources(project, limit=100_000)
        tags = []
        for s in sources:
            v = self.repo.version(s.id) if s.kind == "concept" and s.origin == "taxonomy" else None
            name = str((v.meta or {}).get("name") or s.title) if v is not None else ""
            if v is not None and (v.meta or {}).get("leaf") and len(norm(name)) >= 4:
                tags.append((s, (v.meta or {}).get("taxonomy"), re.compile(rf"(?i)(?<![\w-]){re.escape(name)}{'' if name.endswith('s') else 's?'}(?![\w-])")))  # fmt: skip
        if not tags:
            return
        linked = {frozenset((e.src_id, e.dst_id)) for e in self.repo.edges(project, limit=1_000_000)}
        for src in sources:
            ver = self.repo.version(src.id) if src.kind in _CITING_KINDS else None
            if ver is None:
                continue
            for tag, taxonomy, pattern in tags:
                m = pattern.search(ver.content)
                if m is None or src.external_id == taxonomy or frozenset((src.id, tag.id)) in linked:
                    continue
                try:
                    _, created = self.repo.upsert_edge(project, src, tag, "tagged", "verified", "source", f"tagged in {src.external_id}: {_line_at(ver.content, m.start(), m.end())}"[:300])  # fmt: skip
                except OntologyViolation:
                    continue
                linked.add(frozenset((src.id, tag.id)))
                res.edges_created += int(created)
                if created:
                    res.links.append({"from": src.external_id, "type": "tagged", "to": tag.external_id, "status": "verified", "how": f"tag name in {src.external_id}"})  # fmt: skip

    def resolve_cited(self, project: str, res: IngestResult) -> None:
        """Links for identifiers a document names in its own text ("Builds on the summary in US-001", "see BF-AS-VA-02",
        `story_id: US-4` in a JSON record). Only to sources that exist, never where the two are already linked (either
        way, any status: a person's rejection stands), typed by the words just before the id (see cited_ids), and
        `relates_to` when the ontology does not allow that type between the two. Verified: the document names it."""
        sources = self.repo.sources(project, limit=100_000)
        by_ext = {s.external_id: s for s in sources}
        linked = {frozenset((e.src_id, e.dst_id)) for e in self.repo.edges(project, limit=1_000_000)}
        actors = []  # roles a backlog declared: every document naming one mentions it
        for s in sources:
            if s.kind == "concept" and s.origin == "backlog" and (v := self.repo.version(s.id)) is not None:
                name = str((v.meta or {}).get("name") or s.title)
                actors.append((s, re.compile(rf"(?i)(?<![\w-]){re.escape(name)}s?(?![\w-])")))

        def link(src: KnowledgeSource, dst: KnowledgeSource, etype: str, quote: str) -> None:
            try:
                _, created = self.repo.upsert_edge(project, src, dst, etype, "verified", "source", f"cited in {src.external_id}: {quote}"[:300])  # fmt: skip
            except OntologyViolation:
                return
            linked.add(frozenset((src.id, dst.id)))
            res.edges_created += int(created)
            if created:
                res.links.append({"from": src.external_id, "type": etype, "to": dst.external_id, "status": "verified", "how": f"cited in {src.external_id}"})  # fmt: skip

        for src in sources:
            if src.kind not in _CITING_KINDS:
                continue
            ver = self.repo.version(src.id)
            if ver is None:
                continue
            scope = src.external_id.split("/", 1)[0] + "/" if "/" in src.external_id else ""
            for ref, etype, quote in cited_ids(ver.content):
                dst = by_ext.get(scope + ref) or by_ext.get(ref)  # a namespaced pack's own US-001 first
                if dst is None or dst.id == src.id or frozenset((src.id, dst.id)) in linked:
                    continue
                if not default().allowed(etype, self.repo.node_type(src), self.repo.node_type(dst)):
                    etype = "relates_to"
                link(src, dst, etype, quote)
            for actor, pattern in actors:
                if frozenset((src.id, actor.id)) not in linked and (m := pattern.search(ver.content)):
                    link(src, actor, "mentions", _line_at(ver.content, m.start(), m.end()))

    # ---- batches -------------------------------------------------------------------------------------
    def add_document(
        self, project: str, rel: str, front: dict[str, Any], body: str, res: IngestResult, *,
        kind: str | None = None, ext: str | None = None, title: str | None = None, links: dict[str, list[str]] | None = None,
        folders: tuple[str, ...] = (), scope: str | None = None,
    ) -> str:  # fmt: skip
        """One parsed document (front matter already split off) becomes a source, plus one requirement source per
        identified section when it is a BRD. Explicit arguments win over front matter, which wins over the folder name."""
        parts = (
            *folders,
            *PurePosixPath(rel).parts,
        )  # the chosen folder's own name counts too (requirements/, stories/)
        stem = PurePosixPath(rel).stem
        kind = str(kind or front.get("kind") or FILE_KINDS.get(stem.lower()) or next((DIR_KINDS[x.lower()] for x in reversed(parts[:-1]) if x.lower() in DIR_KINDS), "doc"))  # fmt: skip
        if scope is None:  # the nearest folder that looks like BRD-01 / PACK-2 names the namespace
            scope = next((m.group(1) for x in reversed(parts[:-1]) if (m := _SCOPE.match(x))), None)
        ns = (lambda i: f"{scope}/{i}") if scope else (lambda i: i)
        ext = str(
            ext or front.get("id") or (scope if (kind == "brd" and stem.lower() == "brd" and scope) else stem)
        )
        title = str(title or front.get("title") or first_title(body) or ext)
        meta: dict[str, Any] = {
            k: v for k, v in front.items() if k not in EDGE_TYPES and k not in ("id", "kind", "title")
        }
        kind, meta = review_kind(kind, meta, ext, res)
        declared = {
            t: ([front[t]] if isinstance(front[t], str) else front[t])
            for t in EDGE_TYPES
            if t in front and front[t]
        }
        for t, refs in (links or {}).items():
            if t in EDGE_TYPES and refs:
                declared[t] = sorted({*declared.get(t, []), *refs})
        if declared:
            meta["declared"] = declared
        if kind in ("doc", "design") and is_report(stem, front):
            meta["report"] = parse_report(body, stem)
        if kind in ("epic", "story") and self._add_backlog(
            project, kind, rel, body, ns, ext_brd=scope, res=res
        ):
            return ext  # the file is stored as its epics / stories, not once more as a whole
        self.add(project, kind, ext, title, body, uri=rel, meta=meta, res=res, defer=True)
        res.ingested.append(f"{kind}:{ext}")
        if kind == "doc":
            self.add_domain_terms(project, ext, body)
            if is_taxonomy(front, stem, title):
                self.add_taxonomy(project, ext, title, body)
        if kind == "brd" and front.get("split") not in ("false", False):
            for sec in split_sections(
                body, skip_prefixes=BACKLOG_PREFIXES
            ):  # one requirement source per identified section of the BRD
                link = {"refines": [sec.refines]} if sec.refines else {"derives_from": [ext]}
                label = (
                    sec.title if sec.refines else f"{sec.id}: {sec.title}"
                )  # a detail table is named for what it is
                self.add(
                    project, "requirement", sec.id, label[:300], sec.text, uri=f"{rel}#{sec.id}",
                    meta={"section_of": ext, "references": list(sec.references), "declared": link}, res=res, defer=True,
                )  # fmt: skip
                res.ingested.append(f"requirement:{sec.id}")
                self.add_fields(project, sec.id, sec.title, sec.text)
        return ext

    def add_fields(self, project: str, section: str, title: str, text: str) -> int:
        """Every row of a data-field table in a requirements section as a `field` concept (type, mandatory, handling as
        attributes), mentioned by the section: the row is the document's own wording, so the link is verified. Idempotent."""
        rows = field_rows(text)
        src = self.repo.find_source(project, "requirement", section)
        if src is None or not rows:
            return 0
        seen: set[str] = set()
        for r in rows:
            key = f"{plain(section)}/{plain(r['name'])}"
            if key in seen or not plain(r["name"]):
                continue
            seen.add(key)
            cext = f"concept:field/{key}"
            meta = {"concept_type": "field", "name": r["name"], "section": section, "group": r["group"], "attributes": r["attributes"], "evidence": [{"source": section, "quote": r["quote"]}]}  # fmt: skip
            lines = [f"# {r['name']}", "Type: field", f"Section: {title}", *([f"Group: {r['group']}"] if r["group"] else []), *[f"{h}: {v}" for h, v in r["attributes"].items()], "", "Stated in:", f"- {section}: “{r['quote']}”"]  # fmt: skip
            self.add(project, "concept", cext, f"{r['name']} [{section}]", chr(10).join(lines) + chr(10), origin="fields", meta=meta, defer=True)  # fmt: skip
            c = self.repo.find_source(project, "concept", cext)
            if c is not None:
                self.repo.upsert_edge(project, src, c, "mentions", "verified", "source", r["quote"][:300])
        return len(seen)

    def add_taxonomy(self, project: str, ext: str, title: str, body: str) -> int:
        """A taxonomy document (categories as headings, tags as bullets or table rows) becomes `tag` concepts, each under
        its category (`part_of`), and the document mentions every one: its own wording, so no model is involved. Items that
        use a tag's name are linked in resolve_tags. Idempotent."""
        src = self.repo.find_source(project, "doc", ext)
        tags = parse_taxonomy(body, title)
        if src is None or not tags:
            return 0
        made: dict[str, KnowledgeSource] = {}
        for t in tags:
            path = [*t["path"], t["name"]]
            key = "/".join(norm(x) for x in path)
            cext = f"concept:tag/{key}"
            meta = {"concept_type": "tag", "name": t["name"], "path": t["path"], "leaf": t["leaf"], "description": t["description"], "taxonomy": ext, "evidence": [{"source": ext, "quote": t["quote"]}]}  # fmt: skip
            text = chr(10).join([f"# {t['name']}", "Type: tag", " > ".join(path), t["description"], "", "Stated in:", f"- {ext}: “{t['quote']}”"])  # fmt: skip
            self.add(project, "concept", cext, t["name"], text, origin="taxonomy", meta=meta, defer=True)
            c = self.repo.find_source(project, "concept", cext)
            if c is None:
                continue
            made[key] = c
            parent = made.get("/".join(norm(x) for x in t["path"])) if t["path"] else None
            if parent is not None:
                self.repo.upsert_edge(project, c, parent, "part_of", "verified", "source", t["quote"][:300])
            self.repo.upsert_edge(project, src, c, "mentions", "verified", "source", t["quote"][:300])
        return len(tags)

    def add_domain_terms(self, project: str, ext: str, body: str) -> int:
        """The glossary of a document ("Term. Definition", or a | Term | Definition | table) becomes `domain` concepts: the
        entry is the document's own wording, so the link document -> term is verified, no model involved. Idempotent: a
        term already known (from another document or from extraction) only gains this document as evidence."""
        src = self.repo.find_source(project, "doc", ext)
        terms = domain_terms(body)
        if src is None or not terms:
            return 0
        onto = for_project(self.repo, project)
        for name, desc, quote in terms:
            cext = f"concept:{onto.canonical(norm(name))}"
            prev = self.repo.find_source(project, "concept", cext)
            old = self.repo.version(prev.id) if prev else None
            meta = dict((old.meta if old else None) or {})
            evidence = merge_evidence(meta.get("evidence", []), ext, quote)
            desc = meta.get("description") or desc
            meta.update({"concept_type": "domain", "name": meta.get("name") or name, "description": desc, "evidence": evidence, "extracted": True})  # fmt: skip
            lines = [f"# {meta['name']}", "Type: domain", desc, "", "Stated in:"]
            lines += [f"- {e['source']}: “{e['quote']}”" for e in meta["evidence"]]
            text = "\n".join(lines) + "\n"
            self.add(project, "concept", cext, meta["name"], text, origin="glossary", meta=meta, defer=True)
            c = self.repo.find_source(project, "concept", cext)
            if c is not None:
                self.repo.upsert_edge(project, src, c, "mentions", "verified", "source", quote[:300])
        return len(terms)

    def add_actor(
        self, project: str, onto: Any, name: str, capabilities: str, uri: str, res: IngestResult
    ) -> None:
        """A role a backlog names (`role_capabilities`) as an actor concept with what it can do. Merged like glossary
        terms: a concept already known under that name keeps its other evidence (and stays `domain` if a glossary
        defined it). Documents naming the role are linked to it in resolve_cited."""
        cext = f"concept:{onto.canonical(norm(name))}"
        prev = self.repo.find_source(project, "concept", cext)
        old = self.repo.version(prev.id) if prev else None
        meta = dict((old.meta if old else None) or {})
        evidence = merge_evidence(meta.get("evidence", []), uri, f"{name}: {capabilities}"[:400])
        ctype = "domain" if meta.get("concept_type") == "domain" else "actor"
        meta.update({"concept_type": ctype, "name": meta.get("name") or name, "description": meta.get("description") or capabilities, "evidence": evidence, "role": True})  # the first definition stays: the others are in the evidence  # fmt: skip
        lines = [f"# {meta['name']}", f"Type: {ctype}", meta["description"], "", "Stated in:"]
        lines += [f"- {e['source']}: “{e['quote']}”" for e in meta["evidence"]]
        self.add(project, "concept", cext, meta["name"], "\n".join(lines) + "\n", origin="backlog", meta=meta, res=res, defer=True)  # fmt: skip
        res.ingested.append(f"concept:{cext}")

    def _backlog_notes(self, project: str, rel: str, notes: str, ns, res: IngestResult) -> None:  # noqa: ANN001
        """What a Markdown backlog file says outside its epics/stories (the file's title and source line, an assumptions
        register) as one searchable note, like the notes of a JSON backlog."""
        if not any(len(re.sub(r"[^0-9A-Za-z]", "", ln)) >= 3 for ln in notes.split(chr(10))):
            return
        stem = PurePosixPath(rel).stem.replace(" ", "-")
        self.add(
            project,
            "doc",
            ns(f"{stem}-notes"),
            f"Backlog notes: {rel}"[:300],
            notes + chr(10),
            uri=rel,
            res=res,
            defer=True,
        )
        res.ingested.append(f"doc:{ns(f'{stem}-notes')}")

    def _add_backlog(
        self, project: str, kind: str, rel: str, body: str, ns, ext_brd: str | None, res: IngestResult
    ) -> bool:  # noqa: ANN001
        """Epics (with their features) or user stories of one backlog file, linked to each other and to the BRD of the same
        pack: story -> feature -> epic -> BRD. Returns False when the file holds none (it is then stored whole)."""
        if kind == "epic":
            epics = split_epics(body)
            for e in epics:
                self.add(
                    project, "epic", ns(e.id), f"{e.id}: {e.title}"[:300], e.text, uri=f"{rel}#{e.id}", res=res, defer=True,
                    meta={"declared": {"derives_from": [ext_brd]}} if ext_brd else {},
                )  # fmt: skip
                res.ingested.append(f"epic:{ns(e.id)}")
                for fid, ftitle in e.features:
                    self.add(
                        project, "feature", ns(fid), f"{fid}: {ftitle}"[:300], f"{fid} {ftitle}\nPart of {e.id}: {e.title}", uri=f"{rel}#{fid}",
                        res=res, defer=True, meta={"declared": {"derives_from": [ns(e.id)]}},
                    )  # fmt: skip
                    res.ingested.append(f"feature:{ns(fid)}")
            self._backlog_notes(project, rel, epic_notes(body) if epics else "", ns, res)
            return bool(epics)
        stories = split_stories(body)
        for st in stories:
            parent = ns(st.feature) if st.feature else (ns(st.epic) if st.epic else None)
            meta: dict[str, Any] = {"epic": st.epic, "feature": st.feature, "traces": list(st.traces), "priority": st.priority, "channel": st.channel}  # fmt: skip
            if parent:
                meta["declared"] = {"derives_from": [parent]}
            self.add(project, "story", ns(st.id), f"{st.id}: {st.title}"[:300], st.text, uri=f"{rel}#{st.id}", res=res, defer=True, meta=meta)  # fmt: skip
            res.ingested.append(f"story:{ns(st.id)}")
        self._backlog_notes(project, rel, story_notes(body) if stories else "", ns, res)
        return bool(stories)

    def ingest_text(
        self, project: str, *, kind: str, filename: str = "", text: str | None = None, data: bytes | None = None,
        external_id: str | None = None, title: str | None = None, links: dict[str, list[str]] | None = None,
        scope: str | None = None,
    ) -> IngestResult:  # fmt: skip
        """A document handed over directly (pasted text, or an uploaded .md/.txt/.docx): ingest it, embed it, resolve
        the relationships it declares, and propose links for the rest (see suggest_links)."""
        res = IngestResult()
        name = (filename or "document").replace("\\", "/").rsplit("/", 1)[-1] or "document"
        try:
            if name.lower().endswith(".docx"):
                if data is None or len(data) > MAX_DOCX_BYTES:
                    raise ValueError("a .docx upload is required and must be under 20 MB")
                front, body = {"format": "docx"}, docx_to_markdown(io.BytesIO(data))
            elif name.lower().endswith(".json"):
                raw = text if text is not None else (data or b"").decode("utf-8", "replace")
                if len(raw.encode()) > MAX_DOC_BYTES:
                    raise ValueError(f"larger than {MAX_DOC_BYTES} bytes")
                parsed = json.loads(raw)
                ns = (lambda i: f"{scope}/{i}") if scope else (lambda i: i)
                self._add_json_data(
                    project, name, parsed, ns, scope, res, kind=kind if kind in KINDS else "doc"
                )
                return self._finish_text(project, name, res)
            else:
                raw = text if text is not None else (data or b"").decode("utf-8", "replace")
                if len(raw.encode()) > MAX_DOC_BYTES:
                    raise ValueError(f"larger than {MAX_DOC_BYTES} bytes")
                front, body = parse_frontmatter(raw)
        except ValueError as e:  # json.JSONDecodeError is a ValueError
            res.failed.append(f"{name}: {e}"[:200])
            return res
        stem = PurePosixPath(name).stem
        ext = (
            external_id
            or front.get("id")
            or (stem if kind in ("brd", "doc", "design") and stem != "document" else None)
        )
        if not ext:
            ext = f"{ {'story': 'US', 'requirement': 'REQ', 'ticket': 'TKT'}.get(kind, kind.upper()) }-{hashlib.sha256(body.encode()).hexdigest()[:6]}"  # fmt: skip
        self.add_document(
            project,
            f"{name}",
            front,
            body,
            res,
            kind=kind,
            ext=str(ext),
            title=title,
            links=links,
            scope=scope,
        )
        return self._finish_text(project, name, res, str(ext))

    def _finish_text(
        self, project: str, name: str, res: IngestResult, ext: str | None = None
    ) -> IngestResult:
        """What follows once a handed-over document is stored: report it, embed, link, and propose links."""
        kinds: dict[str, int] = {}
        for i in res.ingested:
            kinds[i.split(":", 1)[0]] = kinds.get(i.split(":", 1)[0], 0) + 1
        self.emit("read", f"{name}: {', '.join(f'{v} {k}' for k, v in kinds.items()) or 'nothing'}", file=name, done=1, total=1, items=kinds, created=res.created, updated=res.updated, unchanged=res.unchanged, entities=res.entities[:80])  # fmt: skip
        self.flush_embeddings(res)
        self.resolve_declared(project, res)
        self.report_links(res)
        self.suggest_links(project, res)
        self.emit("suggest", f"{res.suggested} links proposed for you to confirm (search and embeddings agree)", proposed=res.suggested, links=[x for x in res.links if x["status"] == "proposed"][:100])  # fmt: skip
        self.repo.audit(project, self.actor, "ingest.text", "document", ext or name, res.as_dict())
        return res

    # what a new source most plausibly relates to, by kind: (kinds to search, edge type from the new source)
    _SUGGEST = {
        "story": (("requirement", "brd"), "derives_from"), "ticket": (("story", "requirement"), "implements"),
        "requirement": (("brd",), "derives_from"), "design": (("requirement", "story"), "designs"),
    }  # fmt: skip

    def suggest_links(self, project: str, res: IngestResult) -> None:
        """For documents ingested just now that declare no relationship of the expected kind, PROPOSE links to the
        best-matching existing sources. Only where keyword and vector search agree. Never verified: a person decides."""
        from harnix.knowledge.service import KnowledgeService

        svc = KnowledgeService(self.repo, self.embedder, [project])
        for key in res.ingested:
            kind, ext = key.split(":", 1)
            if kind not in self._SUGGEST:
                continue
            src = self.repo.find_source(project, kind, ext)
            ver = self.repo.version(src.id) if src else None
            if src is None or ver is None:
                continue
            targets, etype = self._SUGGEST[kind]
            if (ver.meta or {}).get("declared"):  # the document already says what it relates to
                continue
            hits = svc.search(project, f"{src.title}\n{ver.content[:500]}", kinds=list(targets), top_k=5)[
                "hits"
            ]
            for h in [
                h for h in hits if h["source_id"] != src.id and {"keyword", "vector"} <= set(h["matched_by"])
            ][:2]:
                dst = self.repo.get_source(h["source_id"])
                if dst is not None and default().allowed(
                    etype, self.repo.node_type(src), self.repo.node_type(dst)
                ):
                    _, created = self.repo.upsert_edge(project, src, dst, etype, "proposed", "agent", "suggested at intake: keyword and vector search agree")  # fmt: skip
                    res.suggested += int(created)
                    if created:
                        res.links.append({"from": src.external_id, "type": etype, "to": dst.external_id, "status": "proposed", "how": "keyword and vector search agree"})  # fmt: skip

    def ingest_documents(self, project: str, root: Path, res: IngestResult | None = None) -> IngestResult:
        res = res or IngestResult()
        suffixes = (".md", ".txt", ".docx", ".json", *renderings.SUFFIXES)
        files = sorted((p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in suffixes), key=lambda p: (p.suffix.lower() in renderings.SUFFIXES, p))  # renderings last: they are checked against what is stored  # fmt: skip
        self._seen, self._extras, self._records, self._corpus = {}, {}, {}, None
        for p in files:
            if p.suffix.lower() in renderings.SUFFIXES:
                self._prescan_rendering(root, p, p.relative_to(root).as_posix())
        self.emit("scan", f"Found {len(files)} document files in {root.name}", total=len(files), files=[p.relative_to(root).as_posix() for p in files][:200])  # fmt: skip
        for n, p in enumerate(files, 1):
            rel = p.relative_to(root).as_posix()
            before, seen = len(res.ingested), len(res.entities)
            self._file(project, root, p, rel, res)
            got = res.ingested[before:]
            kinds: dict[str, int] = {}
            for i in got:
                kinds[i.split(":", 1)[0]] = kinds.get(i.split(":", 1)[0], 0) + 1
            what = ", ".join(f"{v} {k}" for k, v in kinds.items()) or "nothing (skipped or failed)"
            self.emit("read", f"{rel}: {what}", file=rel, done=n, total=len(files), items=kinds, created=res.created, updated=res.updated, unchanged=res.unchanged, entities=res.entities[seen:][:80])  # fmt: skip
        self.flush_embeddings(res)
        self.resolve_declared(project, res)
        self.report_links(res)
        self.collect_findings(project, res)
        self.repo.audit(
            project,
            self.actor,
            "ingest.findings",
            "batch",
            root.name,
            {"root": root.name, "findings": res.findings[:500]},
        )
        self.repo.audit(project, self.actor, "ingest.documents", "batch", root.name, res.as_dict())
        return res

    def collect_findings(self, project: str, res: IngestResult) -> None:
        """Problems in the documents (not in this code): references to ids that do not exist, links the ontology refuses,
        files that could not be read or were cut, ids used twice, and different sources with identical text."""
        for u in res.unresolved:
            kind = "ontology_refused" if "not allowed by the ontology" in u else "unresolved_reference"
            res.findings.append({"kind": kind, "where": u.split(" ")[0], "message": u})
        res.findings += [{"kind": "skipped", "where": x.split(":")[0], "message": x} for x in res.skipped]
        res.findings += [{"kind": "failed", "where": x.split(":")[0], "message": x} for x in res.failed]
        res.findings += [{"kind": "unknown_kind", "where": x.split(":")[0], "message": x} for x in res.review]
        by_hash: dict[str, list[str]] = {}
        for s in self.repo.sources(project, limit=100_000):
            v = self.repo.version(s.id)
            if v is not None and s.kind not in ("concept", "rule", "code", "test") and len(v.content) >= 200 and not (v.meta or {}).get("rendering"):  # fmt: skip
                by_hash.setdefault(v.content_hash, []).append(s.external_id)
        for ids in by_hash.values():
            if len(ids) > 1:
                res.findings.append({"kind": "duplicate_content", "where": ids[0], "message": f"{', '.join(ids[:4])} have identical text"})  # fmt: skip

    def _scope_of(self, root: Path, rel: str) -> str | None:
        parts = (root.name, *PurePosixPath(rel).parts)
        return next((m.group(1) for x in reversed(parts[:-1]) if (m := _SCOPE.match(x))), None)

    def _prescan_rendering(self, root: Path, p: Path, rel: str) -> None:
        """Read a rendering before the backlog JSON is added, so the tracker columns (status, sprint, ...) are in the
        items' text and metadata from their first version."""
        try:
            if p.stat().st_size > MAX_DOC_BYTES * 4:
                raise ValueError("too large")
            records = renderings.read(p)
        except Exception as e:  # noqa: BLE001 - a corrupt export is a finding, reported when its turn comes
            log.debug("rendering %s unreadable: %s", rel, e)
            self._records[rel] = None
            return
        self._records[rel] = records
        scope = self._scope_of(root, rel)
        for rid, found in renderings.extras(records).items():
            self._extras.setdefault(f"{scope}/{rid}" if scope else rid, {}).update(found)

    def _add_rendering(self, project: str, root: Path, rel: str, res: IngestResult) -> None:
        """One note per rendering: how many of its cells the backlog already says, the cells it does not (a finding), and the
        tracker fields it adds (which are attached to their items)."""
        records = self._records.get(rel)
        if records is None:
            res.failed.append(f"{rel}: could not be read as a rendering (corrupt or too large)")
            return
        if self._corpus is None:
            self._corpus = " ".join(plain(f"{s.title} {v.content}") for s in self.repo.sources(project, limit=100_000) if (v := self.repo.version(s.id)) is not None and not (v.meta or {}).get("rendering"))  # fmt: skip
        cmp = renderings.compare(records, self._corpus)
        tracked = renderings.extras(records)
        scope = self._scope_of(root, rel)
        ns = (lambda i: f"{scope}/{i}") if scope else (lambda i: i)
        diffs = cmp["differences"]
        lines = [f"# Rendering: {rel}", "", f"Rows: {cmp['rows']}. Cells checked: {cmp['cells']}. Found in the backlog: {cmp['matched']}. Not found: {len(diffs)}.", ""]  # fmt: skip
        if diffs:
            lines += ["## Cells not found in the backlog", "", *[f"- {d}" for d in diffs[:100]], ""]
        if tracked:
            lines += ["## Fields only this file has", "", *[f"- {rid}: {_tracking_text(v)}" for rid, v in list(tracked.items())[:200]], ""]  # fmt: skip
        stem = PurePosixPath(rel).stem.replace(" ", "-")
        ext = ns(f"{stem}-rendering")
        self.add(project, "doc", ext, f"Rendering: {rel}"[:300], chr(10).join(lines), uri=rel, meta={"format": PurePosixPath(rel).suffix.lower(), "rendering": True, "rows": cmp["rows"], "cells": cmp["cells"], "matched": cmp["matched"], "differences": len(diffs)}, res=res, defer=True)  # fmt: skip
        res.ingested.append(f"doc:{ext}")
        if diffs:
            res.findings.append({"kind": "rendering_differs", "where": rel, "message": f"{len(diffs)} of {cmp['cells']} cells are not in the backlog (first: {diffs[0]})"})  # fmt: skip

    def report_links(self, res: IngestResult) -> None:
        refused = [u for u in res.unresolved if "not allowed by the ontology" in u]
        missing = [u for u in res.unresolved if u not in refused]
        self.emit("link", f"{res.edges_created} links created, {len(missing)} references not found", created=res.edges_created, unresolved=len(missing), links=[x for x in res.links if x["status"] == "verified"][:150], missing=missing[:10])  # fmt: skip
        self.emit("ontology", f"{len(refused)} declared links refused by the ontology" if refused else "every declared link is allowed by the ontology", refused=len(refused), examples=refused[:3])  # fmt: skip

    def _file(self, project: str, root: Path, p: Path, rel: str, res: IngestResult) -> None:
        """One file of a folder: a JSON backlog, a rendering of one (.csv / .xlsx / .jsonl), or a document (.md / .txt / .docx)."""
        if p.suffix.lower() in renderings.SUFFIXES:
            self._add_rendering(project, root, rel, res)
            return
        if p.suffix.lower() == ".json":
            self._add_json_file(project, root, p, rel, res)
            return
        try:
            if p.suffix.lower() == ".docx":
                front, body = {"format": "docx"}, docx_to_markdown(p)
            else:
                raw = p.read_bytes()
                if len(raw) > MAX_DOC_BYTES:
                    res.skipped.append(f"{rel}: too large")
                    return
                front, body = parse_frontmatter(raw.decode("utf-8", "replace"))
        except (OSError, ValueError) as e:
            res.failed.append(f"{rel}: {type(e).__name__}: {e}"[:200])
            return
        self.add_document(project, rel, front, body, res, folders=(root.name,))

    # ---- JSON backlogs -------------------------------------------------------------------------------
    def _add_json_file(self, project: str, root: Path, p: Path, rel: str, res: IngestResult) -> None:
        """One JSON file of a folder. A backlog (`{"epics": [...]}` and/or `{"user_stories": [...]}`, plus
        `validation_rules`) becomes epics, features, stories and rules; a Jira-style export (`{"issues": [...]}`)
        becomes tickets; any other JSON becomes one readable document (see json_to_markdown)."""
        if p.stat().st_size > MAX_DOC_BYTES:
            res.skipped.append(f"{rel}: too large")
            return
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            res.failed.append(f"{rel}: {type(e).__name__}: {e}"[:200])
            return
        parts = (root.name, *PurePosixPath(rel).parts)
        scope = next((m.group(1) for x in reversed(parts[:-1]) if (m := _SCOPE.match(x))), None)
        ns = (lambda i: f"{scope}/{i}") if scope else (lambda i: i)
        # the BRD of the same backlog: the folder's namespace, or a BRD file next to this one
        brd = scope or next((q.stem for q in p.parent.iterdir() if q.is_file() and FILE_KINDS.get(q.stem.lower()) == "brd"), None)  # fmt: skip
        self._add_json_data(project, rel, data, ns, brd, res)

    def _add_json_data(self, project: str, rel: str, data: Any, ns: Any, brd: str | None, res: IngestResult, kind: str = "doc") -> None:  # fmt: skip
        if isinstance(data, dict) and {"epics", "user_stories"} & set(data):
            raw_info = data.get("document_info")
            info: dict[str, Any] = raw_info if isinstance(raw_info, dict) else {}
            add_json_backlog(self, project, rel, data, ns, brd, info, res)
        elif isinstance(data, dict) and _is_jira(data):
            self._add_jira_issues(project, data["issues"], res)
        elif data in (None, "", [], {}) or isinstance(data, (bool, int, float)):
            res.skipped.append(f"{rel}: JSON with nothing to read")
        else:
            # lists of identified records (`"requirements": [{"id": "REQ-1", ...}]`) become one source each, so each is
            # found, versioned and linked on its own; whatever else the file holds stays one readable document
            groups = (
                {k: g for k, v in data.items() if (g := json_records(k, v))}
                if isinstance(data, dict)
                else ({"": g} if (g := json_records(PurePosixPath(rel).stem, data)) else {})
            )
            for rkind, records in groups.values():
                for rid, rec in records:
                    label = _record_label(rec, rid)
                    head = f"{rid}: {label}" if label else rid
                    text = "\n".join([f"# {head}", "", *_flat(rec)]) + "\n"
                    self.add(project, rkind, ns(rid), head[:300], text, uri=f"{rel}#{rid}", meta={"format": "json", "file": rel}, res=res, defer=True)  # fmt: skip
                    res.ingested.append(f"{rkind}:{ns(rid)}")
            rest = (
                {k: v for k, v in data.items() if k not in groups}
                if isinstance(data, dict)
                else ([] if groups else data)
            )
            if rest in ({}, []):
                return
            title = _json_title(data) or rel
            ext = ns(rel)
            text = json_to_markdown(title, rest)
            self.add(
                project, kind, ext, title[:300], text, uri=rel, meta={"format": "json"}, res=res, defer=True
            )
            res.ingested.append(f"{kind}:{ext}")

    def _add_jira_issues(self, project: str, issues: list[Any], res: IngestResult) -> None:
        """Tickets of a Jira-shaped export (MOCK data only: a live Jira client needs credentials and is not built)."""
        for issue in issues:
            if not isinstance(issue, dict) or "key" not in issue:
                continue
            key = str(issue["key"])
            ac = issue.get("acceptance_criteria") or []
            text = f"# {key}: {issue.get('summary', '')}\n\nType: {issue.get('type', '')}  Status: {issue.get('status', '')}\n\n{issue.get('description', '')}\n"  # fmt: skip
            if ac:
                text += "\n## Acceptance criteria\n" + "\n".join(f"- {a}" for a in ac) + "\n"
            declared: dict[str, list[str]] = {}
            for link in issue.get("links", []):
                t = {"implements": "implements", "derives from": "derives_from", "relates to": "relates_to", "blocks": "depends_on", "tests": "tests"}.get(str(link.get("type", "")).lower(), "relates_to")  # fmt: skip
                declared.setdefault(t, []).append(str(link["key"]))
            meta = {"jira_type": issue.get("type"), "status": issue.get("status"), "mock": True, "open_questions": issue.get("open_questions", [])}  # fmt: skip
            if declared:
                meta["declared"] = declared
            self.add(project, "ticket", key, f"{key}: {issue.get('summary', '')}", text, uri=f"mock-jira://{key}", origin="mock", meta=meta, res=res, defer=True)  # fmt: skip
            res.ingested.append(f"ticket:{key}")

    def ingest_jira_mock(self, project: str, file: Path, res: IngestResult | None = None) -> IngestResult:
        """MOCK data only: a JSON export shaped like Jira issues. A live Jira client needs credentials and is not built."""
        res = res or IngestResult()
        data = json.loads(file.read_text(encoding="utf-8"))
        self._add_jira_issues(project, data.get("issues", []), res)
        self.flush_embeddings(res)
        self.resolve_declared(project, res)
        self.repo.audit(project, self.actor, "ingest.jira_mock", "batch", file.name, res.as_dict())
        return res

    def ingest_repository(
        self, project: str, root: Path, max_files: int = 2000, res: IngestResult | None = None,
        only: set[str] | None = None, origin: str = "ingest", extra_meta: dict[str, Any] | None = None,
    ) -> IngestResult:  # fmt: skip
        """Source files and tests of a Git repository (secret files, .git, dependencies and symlinks are skipped by the
        shared walker). Test -> code edges come from Python imports; `Implements: REQ-1` comments declare requirements."""
        res = res or IngestResult()
        files = [c for c in walk(root, max_files).candidates if language_of(c.path) not in (None, "markdown", "json", "yaml", "toml", "ini", "text", "html", "css") and c.size <= MAX_DOC_BYTES]  # fmt: skip
        modules: dict[str, str] = {}
        for c in files:
            if c.path.endswith(".py"):
                stem = c.path[:-3].replace("/", ".")
                modules[stem] = c.path
                modules.setdefault(stem.rsplit(".", 1)[-1], c.path)  # flat layouts import by file name
        for c in files:
            if only is not None and c.path not in only:
                continue
            try:
                text = (root / c.path).read_text(encoding="utf-8", errors="replace")
            except OSError as e:
                res.failed.append(f"{c.path}: {type(e).__name__}")
                continue
            is_test = is_test_path(c.path)
            declared: dict[str, list[str]] = {}
            ids = [i for m in _ID_LIST.finditer(text) for i in _ID.findall(m.group(1))]
            if ids:
                declared["implements" if not is_test else "tests"] = sorted(set(ids))
            if c.path.endswith(".py"):
                imports, _ = extract_python(text)
                targets: set[str] = set()
                for imp in imports:
                    names = [imp[1]] if imp[0] == "i" else [imp[1]] + [f"{imp[1]}.{n[0]}" for n in imp[3]]
                    for n in names:
                        if n in modules and modules[n] != c.path:
                            targets.add(modules[n])
                if targets:
                    declared.setdefault("tests" if is_test else "depends_on", [])
                    declared["tests" if is_test else "depends_on"] += sorted(targets)
            meta: dict[str, Any] = {"language": language_of(c.path), **(extra_meta or {})}
            if declared:
                meta["declared"] = {k: sorted(set(v)) for k, v in declared.items()}
            self.add(
                project,
                "test" if is_test else "code",
                c.path,
                c.path,
                text,
                uri=c.path,
                meta=meta,
                res=res,
                code=True,
                defer=True,
                origin=origin,
            )
        self.flush_embeddings(res)
        self.resolve_declared(project, res)
        self.repo.audit(project, self.actor, "ingest.repository", "batch", root.name, res.as_dict())
        return res


_CITING_KINDS = {"brd", "requirement", "epic", "feature", "story", "ticket", "design", "doc", "rule"}
_CITE = re.compile(rf"(?<![\w/.-]){ID}(?![\w-])")
# the words just before a cited id say what the link is; the phrase nearest the id wins. No `contradicts`: a
# conflict read out of prose is a claim for a person to make, not a verified link. Matched on lower case with
# `_` read as a space, so JSON keys (`depends_on[1].story_id: US-3`, `parent_id: E-1`) type their ids too.
_CITE_TYPES = (
    ("depends_on", r"depends? on|dependent on|dependenc|builds? on|relies on|\brequires?\b|prerequisite|blocked by"),
    ("derives_from", r"derived? from|derives? from|traces? (?:to|back)|traceab|brd ref|\bparent\b|part of|\b(?:epic|feature)(?: id)?\s*:"),
    ("implements", r"implement(?:s|ed|ing)?\b(?! by)"),
    ("satisfies", r"satisf|fulfil|\btraces\s*:"),  # a story's "Traces: ITEM-002" is the scope item it delivers
    ("tests", r"\btest(?:s|ed)?\b|\bverif(?:y|ies)\b"),
    ("refines", r"\brefines?\b|details? of|elaborat"),
)  # fmt: skip
_LIST_GAP = re.compile(r"^[\s,;/&]*(?:and|or)?[\s,;/&]*$")


def _line_at(text: str, start: int, end: int) -> str:
    """The text around a match, within its line: the quote a link carries as evidence."""
    a = text.rfind("\n", 0, start) + 1
    b = text.find("\n", end)
    line = text[a : b if b >= 0 else len(text)]
    lo = max(0, start - a - 140)
    return line[lo : lo + 280].strip()


def cited_ids(text: str) -> list[tuple[str, str, str]]:
    """(id, link type, quote) for each identifier a text names, once per id. The type comes from the phrase in the
    ~80 characters before it in the same sentence ("builds on US-001" -> depends_on); ids listed after one share its
    type ("depends on US-1, US-2 and US-3"); with no such phrase it is `relates_to`."""
    out: dict[str, tuple[str, str]] = {}
    prev_end, prev_type, prev_line = 0, "relates_to", -1
    for m in _CITE.finditer(text):
        line_start = text.rfind("\n", 0, m.start()) + 1
        gap_from = prev_end if prev_line == line_start else line_start
        gap = text[gap_from : m.start()]
        if prev_line == line_start and _LIST_GAP.match(gap):
            etype = prev_type
        else:
            window = re.split(r"[.;!?]\s", gap[-80:])[-1].lower().replace("_", " ")  # this sentence only
            best = (-1, "relates_to")
            for t, pat in _CITE_TYPES:
                for w in re.finditer(pat, window):
                    best = max(best, (w.end(), t))
            etype = best[1]
        prev_end, prev_type, prev_line = m.end(), etype, line_start
        ref = m.group(0)
        if ref not in out or (out[ref][0] == "relates_to" and etype != "relates_to"):
            out[ref] = (etype, _line_at(text, m.start(), m.end()))
    return [(ref, t, q) for ref, (t, q) in out.items()]


def _is_jira(data: dict[str, Any]) -> bool:
    issues = data.get("issues")
    return isinstance(issues, list) and any(isinstance(i, dict) and "key" in i for i in issues)


_JSON_LABELS = ("id", "key", "story_id", "name", "title", "summary", "label")
_RECORD_KINDS = {
    **DIR_KINDS, **FILE_KINDS, "features": "feature", "feature": "feature", "rules": "rule", "rule": "rule",
    "validation_rules": "rule", "business_rules": "rule", "issues": "ticket", "requirement": "requirement",
}  # fmt: skip


def json_records(key: str, v: Any) -> tuple[str, list[tuple[str, dict[str, Any]]]] | None:
    """(kind, [(id, record)]) when `v` is a list of records that each carry their own identifier (REQ-1, US-002) and
    the list's name says what they are (`requirements`, `user_stories`, `rules`, ...). None otherwise: a list we cannot
    name, or records without unique ids, stay part of the document."""
    kind = _RECORD_KINDS.get(key.lower().replace(" ", "_").replace("-", "_"))
    if kind is None or not isinstance(v, list) or not v:
        return None
    keys = ("id", "key", f"{key.lower().rstrip('s')}_id", f"{kind}_id")
    out: list[tuple[str, dict[str, Any]]] = []
    for x in v:
        rid = next((str(x[k]) for k in keys if isinstance(x, dict) and isinstance(x.get(k), (str, int))), "")
        if not re.fullmatch(ID, rid):
            return None
        out.append((rid, x))
    return (kind, out) if len({r for r, _ in out}) == len(out) else None


def _record_label(rec: dict[str, Any], rid: str) -> str:
    return next((str(rec[k]).strip()[:200] for k in ("title", "name", "summary", "label", "text", "description") if isinstance(rec.get(k), str) and rec[k].strip() and rec[k] != rid), "")  # fmt: skip


def _json_title(data: Any) -> str:
    """A name for a JSON document: its own title/name field, else None (the caller uses the file path)."""
    if isinstance(data, dict):
        for k in ("title", "name", "document_name"):
            if isinstance(data.get(k), str) and data[k].strip():
                return data[k].strip()
        info = data.get("info") or data.get("document_info")
        if isinstance(info, dict):
            return _json_title(info)
    return ""


def _flat(d: dict[str, Any], prefix: str = "", depth: int = 0) -> list[str]:
    """`key: value` lines for a JSON object; nested objects become `a.b: value`, lists of objects `a[1].b: value`."""
    out: list[str] = []
    for k, v in d.items():
        name = f"{prefix}{k}"
        if v in (None, "", [], {}):
            continue
        if isinstance(v, dict) and depth < 5:
            out += _flat(v, f"{name}.", depth + 1)
        elif isinstance(v, list) and v and all(isinstance(x, dict) for x in v) and depth < 5:
            for i, x in enumerate(v, 1):
                out += _flat(x, f"{name}[{i}].", depth + 1)
        elif isinstance(v, list) and all(not isinstance(x, (dict, list)) for x in v):
            out.append(f"{name}: " + "; ".join(str(x) for x in v))
        elif isinstance(v, (dict, list)):
            out.append(f"{name}: {json.dumps(v, ensure_ascii=False)}")
        else:
            out.append(f"{name}: {v}")
    return out


def _section(v: Any) -> list[str]:
    if isinstance(v, dict):
        return _flat(v)
    if isinstance(v, list):
        out: list[str] = []
        for i, x in enumerate(v, 1):
            if isinstance(x, dict):
                label = next((str(x[k]) for k in _JSON_LABELS if isinstance(x.get(k), (str, int)) and str(x[k]).strip()), str(i))  # fmt: skip
                out += ["", f"### {label[:120]}", "", *_flat(x)]
            else:
                out.append(f"- {x}")
        return out
    return [str(v)]


def json_to_markdown(title: str, data: Any) -> str:
    """Readable text for a JSON file so it can be chunked by section and embedded: top-level fields first, then one
    `##` section per object/array field and one `###` per object in an array, each as `key: value` lines (nested
    objects as `a.b`). Nothing is dropped except empty values."""
    lines = [f"# {title}", ""]
    if isinstance(data, dict):
        flat = {k: v for k, v in data.items() if not isinstance(v, (dict, list))}
        lines += [f"- {k}: {v}" for k, v in flat.items() if v not in (None, "")]
        for k, v in data.items():
            if k not in flat and v not in (None, [], {}):
                lines += ["", f"## {k}", "", *_section(v)]
    else:
        lines += _section(data)
    return "\n".join(lines) + "\n"


_BRD_REF = re.compile(r"\b[A-Z]{2,}(?:-[A-Z0-9]+)+-\d+\b")  # BF-AS-VA-01: a BRD requirement section id


def _txt(v: Any) -> str:
    return (
        v if isinstance(v, str) else json.dumps(v, ensure_ascii=False) if v not in (None, "", [], {}) else ""
    )


def _bullets(title: str, items: Any, numbered: bool = False) -> list[str]:
    if not isinstance(items, list) or not items:
        return []
    rows = [
        (f"{x['id']}: {x.get('text', '')}" if x.get("id") else str(x.get("text")))
        if isinstance(x, dict)
        else str(x)
        for x in items
    ]  # an item's id stays with it: stories trace to ITEM-002 and a search for it must find this line
    return ["", f"**{title}**", "", *[f"{i}. {r}" if numbered else f"- {r}" for i, r in enumerate(rows, 1)]]


_TRACKING_LABELS = {
    "status": "Status",
    "sprint": "Sprint",
    "story_points": "Story points",
    "labels": "Labels",
    "issue_type": "Issue type",
}


def _tracking_text(found: dict[str, str]) -> str:
    return " | ".join(f"{_TRACKING_LABELS.get(k, k)}: {v}" for k, v in found.items())


def _tracked(ing: Ingestor, key: str) -> list[str]:
    """The tracker fields a rendering gave this item (status, sprint, story points, labels), as a line of its text."""
    found = ing._extras.get(key)
    return ["", f"**Tracking**: {_tracking_text(found)}"] if found else []


def add_json_backlog(ing: Ingestor, project: str, rel: str, data: dict[str, Any], ns: Any, brd: str | None, info: dict[str, Any], res: IngestResult) -> None:  # fmt: skip
    """Epics, features, stories and validation rules from a JSON backlog, stored and linked exactly like the Markdown
    backlogs (story -> feature -> epic -> BRD), plus links to the BRD requirement sections they cite (BF-AS-VA-01) and
    to the validation rules (V-1) a story depends on. Also, so nothing a story hangs on is lost in a notes file:
    a feature's in-scope items become requirements (ITEM-002 -> FEAT-001) that the stories tracing them satisfy, the
    assumptions register is attached to its stories, reports to their features, boundary items become constraints
    (BND-001) and the roles of `role_capabilities` actor concepts."""
    scalars = {**info, "schema_version": data.get("schema_version")}
    common = {k: v for k, v in scalars.items() if v not in (None, "", [], {}) and not isinstance(v, dict) and (not isinstance(v, list) or all(not isinstance(x, (dict, list)) for x in v))}  # fmt: skip
    assumptions: dict[str, list[str]] = {}
    for a in data.get("assumptions_register") or []:
        if isinstance(a, dict) and a.get("story_id") and _txt(a.get("assumption_text")):
            assumptions.setdefault(str(a["story_id"]), []).append(_txt(a["assumption_text"]))
    reports: dict[str, list[str]] = {}
    for r in data.get("reporting_requirements") or []:
        if isinstance(r, dict) and _txt(r.get("report_type")):
            line = _txt(r["report_type"]) + (f" ({_txt(r['formats'])})" if _txt(r.get("formats")) else "")
            for fid in re.findall(ID, _txt(r.get("features"))):
                reports.setdefault(fid, []).append(line)
    for b in data.get("boundary_items") or []:
        if not isinstance(b, dict) or not b.get("id") or not _txt(b.get("text")):
            continue
        bid = str(b["id"])
        lines = [f"# {bid}: scope boundary", "", _txt(b["text"])]
        lines += [f"{k.capitalize()}: {_txt(b[k])}" for k in ("kind", "routing") if _txt(b.get(k))]
        ing.add(project, "rule", ns(bid), f"{bid}: {_txt(b['text'])}"[:300], "\n".join(lines) + "\n", uri=f"{rel}#{bid}", res=res, defer=True, meta={**common, "concept_type": "constraint", "boundary": True, "boundary_kind": b.get("kind"), "routing": b.get("routing")})  # fmt: skip
        res.ingested.append(f"rule:{ns(bid)}")
    roles = [r for r in data.get("role_capabilities") or [] if isinstance(r, dict) and _txt(r.get("role"))]
    if roles:
        onto = for_project(ing.repo, project)
        for r in roles:
            ing.add_actor(project, onto, _txt(r["role"]), _txt(r.get("capabilities")), rel, res)
    for r in data.get("validation_rules") or []:
        if not isinstance(r, dict) or not r.get("rule_id"):
            continue
        rid = str(r["rule_id"])
        text = f"# {rid}: {r.get('rule_name', '')}\n\n{r.get('description', '')}\n"
        ing.add(project, "rule", ns(rid), f"{rid}: {r.get('rule_name', '')}"[:300], text, uri=f"{rel}#{rid}", res=res, defer=True, meta={**common, "concept_type": "rule", "validation_rule": True})  # fmt: skip
        res.ingested.append(f"rule:{ns(rid)}")
    for e in data.get("epics") or []:
        if not isinstance(e, dict) or not e.get("id"):
            continue
        eid = str(e["id"])
        refs = sorted(set(_BRD_REF.findall(_txt(e.get("brd_traceability")))))
        number = [f"Epic number: {e['epic_number']}"] if _txt(e.get("epic_number")) else []
        text = "\n".join([f"# {eid}: {e.get('name', '')}", *number, "", _txt(e.get("description")), "", f"BRD traceability: {_txt(e.get('brd_traceability'))}", *_tracked(ing, ns(eid))])  # fmt: skip
        track = ing._extras.get(ns(eid))
        parents = ([brd] if brd else []) + refs
        ing.add(project, "epic", ns(eid), f"{eid}: {e.get('name', '')}"[:300], text, uri=f"{rel}#{eid}", res=res, defer=True, meta={**common, **({"tracking": track} if track else {}), **({"declared": {"derives_from": parents}} if parents else {})})  # fmt: skip
        res.ingested.append(f"epic:{ns(eid)}")
        for f in e.get("features") or []:
            if not isinstance(f, dict) or not f.get("id"):
                continue
            fid = str(f["id"])
            lines = [
                f"# {fid}: {f.get('name', '')}",
                f"Part of {eid}: {e.get('name', '')}",
                *([f"Feature number: {f['feature_number']}"] if _txt(f.get("feature_number")) else []),
                "",
                _txt(f.get("description")),
            ]
            lines += _bullets("In scope", f.get("in_scope_items"))
            for k, v in f.items():  # anything else the file says about the feature, kept readable
                if k not in ("id", "name", "description", "in_scope_items", "feature_number") and _txt(v):
                    lines += ["", f"**{k.replace('_', ' ').capitalize()}**: {_txt(v)}"]
            lines += _bullets("Reports", reports.get(fid))
            lines += _tracked(ing, ns(fid))
            track = ing._extras.get(ns(fid))
            frefs = sorted(set(_BRD_REF.findall(_txt(f.get("brd_reference")))))
            ing.add(project, "feature", ns(fid), f"{fid}: {f.get('name', '')}"[:300], "\n".join(lines) + "\n", uri=f"{rel}#{fid}", res=res, defer=True, meta={**common, **({"tracking": track} if track else {}), "declared": {"derives_from": [ns(eid), *frefs]}})  # fmt: skip
            res.ingested.append(f"feature:{ns(fid)}")
            for it in (
                f.get("in_scope_items") or []
            ):  # what the feature promises, one by one: stories trace to these
                if not isinstance(it, dict) or not it.get("id") or not _txt(it.get("text")):
                    continue
                iid = str(it["id"])
                text = f"# {iid}: in scope of {fid} {f.get('name', '')}\n\n{_txt(it['text'])}\n"
                ing.add(project, "requirement", ns(iid), f"{iid}: {_txt(it['text'])}"[:300], text, uri=f"{rel}#{iid}", res=res, defer=True, meta={**common, "scope_item_of": fid, "declared": {"derives_from": [ns(fid)]}})  # fmt: skip
                res.ingested.append(f"requirement:{ns(iid)}")
    for st in data.get("user_stories") or []:
        if not isinstance(st, dict) or not st.get("story_id"):
            continue
        sid = str(st["story_id"])
        lines = [f"## {sid}: {st.get('title', '')}", "", f"- Epic: {st.get('epic_id', '-')} | Feature: {st.get('feature_id', '-')} | Traces: {', '.join(map(str, st.get('traces') or []))}", f"- Priority: {st.get('priority', '-')} | Channel: {st.get('channel', '-')}", "", _txt(st.get("description"))]  # fmt: skip
        if st.get("dependencies"):
            lines += ["", f"**Dependencies**: {_txt(st['dependencies'])}"]
        if st.get("business_validation_rules"):
            ids = ", ".join(map(str, st.get("business_validation_rule_ids") or []))
            lines += ["", f"**Business validation rules** ({ids}): {_txt(st['business_validation_rules'])}"]
        lines += _bullets("Acceptance Criteria", st.get("acceptance_criteria"), numbered=True)
        lines += _bullets("Additional scenarios", st.get("additional_scenarios"))
        lines += _bullets("Requirements (EARS)", st.get("ears_requirements"))
        if st.get("brd_reference"):
            lines += ["", f"**BRD reference**: {_txt(st['brd_reference'])}"]
        if st.get("ba_comments"):
            lines += ["", f"**BA comments**: {_txt(st['ba_comments'])}"]
        lines += _bullets("Assumptions", assumptions.get(sid))
        lines += _tracked(ing, ns(sid))
        track = ing._extras.get(ns(sid))
        parent = st.get("feature_id") or st.get("epic_id")
        declared: dict[str, list[str]] = {}
        derives = ([ns(str(parent))] if parent else []) + sorted(
            set(_BRD_REF.findall(_txt(st.get("brd_reference"))))
        )
        if derives:
            declared["derives_from"] = derives
        traces = [str(t) for t in st.get("traces") or [] if re.fullmatch(ID, str(t))]
        if traces:  # the feature's in-scope items this story delivers
            declared["satisfies"] = sorted({ns(t) for t in traces})
        # depends_on is a list of ids or of {story_id, reason}: the link is to the story, the reason stays readable
        dep_items = [(str(x.get("story_id") or x.get("id") or ""), str(x.get("reason") or "")) if isinstance(x, dict) else (str(x), "") for x in st.get("depends_on") or []]  # fmt: skip
        dep_origin = {str(x.get("story_id") or x.get("id")): _txt(x.get("origin")) for x in st.get("depends_on") or [] if isinstance(x, dict)}  # fmt: skip
        dep_items = [(i, r) for i, r in dep_items if i]
        if dep_items:
            lines += ["", "**Depends on**", "", *[f"- {i}{f': {r}' if r else ''}{f' (origin: {dep_origin[i]})' if dep_origin.get(i) else ''}" for i, r in dep_items]]  # fmt: skip
        deps = [ns(i) for i, _ in dep_items] + [
            ns(str(x)) for x in st.get("business_validation_rule_ids") or []
        ]
        if deps:
            declared["depends_on"] = sorted(set(deps))
        meta = {**common, "epic": st.get("epic_id"), "feature": st.get("feature_id"), "traces": list(st.get("traces") or []), "priority": st.get("priority"), "channel": st.get("channel"), **({"assumptions": assumptions[sid]} if sid in assumptions else {}), **({"tracking": track} if track else {}), **({"declared": declared} if declared else {})}  # fmt: skip
        ing.add(project, "story", ns(sid), f"{sid}: {st.get('title', '')}"[:300], "\n".join(lines) + "\n", uri=f"{rel}#{sid}", res=res, defer=True, meta=meta)  # fmt: skip
        res.ingested.append(f"story:{ns(sid)}")
    # what else the file holds (the assumptions register and reports as a whole, sign-offs): one searchable note
    rest = {k: v for k, v in data.items() if k not in ("schema_version", "document_info", "epics", "user_stories", "validation_rules", "boundary_items", "role_capabilities") and v}  # fmt: skip
    if rest:
        stem = PurePosixPath(rel).stem.replace(" ", "-")
        body = [f"# Backlog notes ({rel})", ""]
        for k, v in rest.items():
            body += [f"## {k.replace('_', ' ').capitalize()}", ""]
            body += _section(v) if isinstance(v, (list, dict)) else [_txt(v)]
            body.append("")
        ing.add(project, "doc", ns(f"{stem}-notes"), f"Backlog notes: {rel}"[:300], "\n".join(body), uri=rel, res=res, defer=True, meta=common)  # fmt: skip
        res.ingested.append(f"doc:{ns(f'{stem}-notes')}")
