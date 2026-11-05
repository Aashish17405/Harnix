"""Did everything the input files say reach the knowledge base? A coverage audit that does not trust the ingestion code.

It reads the files again (own reader, no ingestion function) and lists every datapoint: each line of a Markdown/text/Word
document (front matter aside), each leaf value of a JSON file. A datapoint is `mapped` when its text is found in what the
project stores (the current version of any source), `missing` when it is not. A value too short to identify itself
("P1", "Yes") has to be found right after its key ("priority p1"), so a stray "yes" elsewhere does not count.

Three more checks on what is stored: every line of every source lies in one of its chunks (chunking drops nothing), and the
sources that should be linked but have no link at all (orphans).

Mapped means present, not correct: a value can be stored under the wrong item. That needs a comparison against hand-labelled
expected links.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from harnix.harness.skills import parse_frontmatter
from harnix.knowledge import ingest, renderings
from harnix.knowledge.ingest import docx_to_markdown
from harnix.knowledge.repo import KnowledgeRepo
from harnix.knowledge.sections import plain as norm

SUFFIXES = (".md", ".txt", ".docx", ".json", *renderings.SUFFIXES)
SHORT = 6  # a normalised value shorter than this must sit right after its key
LINKED_KINDS = ("brd", "requirement", "epic", "feature", "story", "ticket", "design", "rule")
_IDENTIFIER = re.compile(r"[a-z].*\d|\d.*[a-z]")
_SEPARATOR = re.compile(r"^[\s|:\-+=*_#>]*$")


def _lines(text: str) -> list[str]:
    return [ln for ln in text.splitlines() if len(norm(ln)) >= 3 and not _SEPARATOR.match(ln)]


def _leaves(
    node: Any, pointer: str = "", key: str = "", listed: bool = False
) -> list[tuple[str, str, str, bool]]:
    """(pointer, key, text, in_list) of every leaf value; each item of a list of plain values is its own datapoint."""
    out: list[tuple[str, str, str, bool]] = []
    if isinstance(node, dict):
        for k, v in node.items():
            out += _leaves(v, f"{pointer}/{k}", str(k))
    elif isinstance(node, list):
        for i, x in enumerate(node):
            out += _leaves(x, f"{pointer}[{i}]", key, listed=True)
    elif node not in (None, ""):
        out.append((pointer, key, str(node), listed))
    return out


def datapoints(path: Path) -> list[dict[str, str]]:
    """Every datapoint of one input file: {"pointer", "key", "text"}."""
    suffix = path.suffix.lower()
    if suffix == ".json":
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        return [{"pointer": p, "key": "" if listed else k, "text": t} for p, k, t, listed in _leaves(data)]
    if suffix in renderings.SUFFIXES:
        return [{"pointer": f"{r['sheet']} row {i}".strip(), "key": h, "text": v, "fuzzy": "1"} for i, r in enumerate(renderings.read(path), 2) for h, v in r["cells"].items()]  # fmt: skip
    if suffix == ".docx":
        body = docx_to_markdown(path)
    else:
        _, body = parse_frontmatter(path.read_text(encoding="utf-8", errors="replace"))
    return [{"pointer": f"line {i}", "key": "", "text": ln} for i, ln in enumerate(_lines(body), 1)]


def _present(d: dict[str, str], corpus: str) -> bool:
    value = norm(d["text"])
    if not value:
        return True  # nothing to find
    if d.get("fuzzy") and len(value) >= SHORT:  # an export cuts long cells ("...") and joins lists with " | "
        return renderings._known(d["text"], corpus)
    if len(value) >= SHORT or not d["key"] or _IDENTIFIER.search(value):  # an id identifies itself
        return value in corpus
    return f"{norm(d['key'])} {value}" in corpus


def _stored(s: Any, v: Any) -> str:
    """Everything kept for one source: its title, its text and its metadata (`key value` pairs, so a short value is found
    next to its key)."""
    meta = " ".join(norm(f"{k} {t}") for _, k, t, _ in _leaves(v.meta or {}))
    return norm(f"{s.title} {v.content}") + " " + meta


def audit_inputs(repo: KnowledgeRepo, project: str, folder: Path, max_missing: int = 200) -> dict[str, Any]:
    """The input folder against the project's stored text."""
    sources = repo.sources(project, limit=100_000)
    versions = {s.id: repo.version(s.id) for s in sources}
    # a rendering's own note lists the cells the backlog lacks: it must not make them look stored
    corpus = " ".join(_stored(s, v) for s in sources if (v := versions[s.id]) is not None and not (v.meta or {}).get("rendering"))  # fmt: skip
    files: dict[str, dict[str, Any]] = {}
    missing: list[dict[str, str]] = []
    for p in sorted(x for x in folder.rglob("*") if x.is_file() and x.suffix.lower() in SUFFIXES):
        rel = p.relative_to(folder).as_posix()
        if p.stat().st_size > ingest.MAX_DOC_BYTES:
            files[rel] = {"datapoints": 0, "mapped": 0, "error": "larger than the ingestion size limit"}
            continue
        try:
            points = datapoints(p)
        except Exception as e:  # noqa: BLE001 - a corrupt file (bad zip, bad XML, bad JSON) is a finding, not a crash
            files[rel] = {"datapoints": 0, "mapped": 0, "error": f"{type(e).__name__}: {e}"[:160]}
            continue
        lost = [d for d in points if not _present(d, corpus)]
        files[rel] = {"datapoints": len(points), "mapped": len(points) - len(lost)}
        missing += [{"file": rel, "where": d["pointer"], "text": d["text"][:200]} for d in lost]
    total = sum(f["datapoints"] for f in files.values())
    mapped = sum(f["mapped"] for f in files.values())
    return {
        "datapoints": total, "mapped": mapped, "score": round(mapped / total, 4) if total else None,
        "missing": missing[:max_missing], "missing_total": len(missing), "files": files,
    }  # fmt: skip


WINDOW = 8  # words; chunks overlap by more than this, so a window of any line lies whole in one chunk


def _windows_covered(line: str, pieces: list[str]) -> bool:
    """A line that is cut across chunks (they overlap by a sentence) is covered when every run of WINDOW words is in a chunk."""
    words = line.split()
    runs = [" ".join(words[i : i + WINDOW]) for i in range(max(1, len(words) - WINDOW + 1))]
    return all(any(r in p for p in pieces) for r in runs)


def audit_chunks(repo: KnowledgeRepo, project: str, max_examples: int = 50) -> dict[str, Any]:
    """Every line of every current source must be inside one of its chunks (or its section path)."""
    sources = repo.sources(project, limit=100_000)
    chunks: dict[str, list[Any]] = {}
    for c in sorted(repo.chunks_of(project, [s.id for s in sources]), key=lambda c: (c.source_id, c.ordinal)):
        chunks.setdefault(c.source_id, []).append(c)
    total = lost = 0
    examples: list[dict[str, str]] = []
    for s in sources:
        v = repo.version(s.id)
        if v is None:
            continue
        # a long paragraph cut at sentence ends is whole again once the chunk texts are joined in order; headings live in
        # the chunks' section paths
        stored = norm(" ".join(c.text for c in chunks.get(s.id, [])) + " " + " ".join(c.heading or "" for c in chunks.get(s.id, [])))  # fmt: skip
        pieces = [norm(c.text) for c in chunks.get(s.id, [])]
        for ln in _lines(v.content):
            total += 1
            n = norm(ln)
            if n in stored or _windows_covered(n, pieces):
                continue
            lost += 1
            if len(examples) < max_examples:
                examples.append({"source": s.external_id, "text": ln[:200]})
    return {"lines": total, "uncovered": lost, "examples": examples}


def audit_orphans(repo: KnowledgeRepo, project: str, max_examples: int = 50) -> dict[str, Any]:
    """Sources that should be linked to something and have no link in either direction."""
    linked: set[str] = set()
    for e in repo.edges(project, limit=1_000_000):
        linked.update((e.src_id, e.dst_id))
    orphans = [
        s for s in repo.sources(project, limit=100_000) if s.kind in LINKED_KINDS and s.id not in linked
    ]
    by_kind: dict[str, int] = {}
    for s in orphans:
        by_kind[s.kind] = by_kind.get(s.kind, 0) + 1
    return {
        "count": len(orphans),
        "by_kind": by_kind,
        "examples": [s.external_id for s in orphans[:max_examples]],
    }


def audit(repo: KnowledgeRepo, project: str, folder: Path | None = None) -> dict[str, Any]:
    report: dict[str, Any] = {"project": project, "chunks": audit_chunks(repo, project), "orphans": audit_orphans(repo, project)}  # fmt: skip
    if folder is not None:
        report["inputs"] = audit_inputs(repo, project, folder)
    return report
