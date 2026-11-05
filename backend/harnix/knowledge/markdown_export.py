"""A readable copy of the knowledge graph as Markdown files: one file per source, YAML front matter, `[[wikilinks]]`.

The database stays the master; this is a copy people can read, diff in git and review in a pull request. The output is
deterministic (no timestamps, everything sorted), so exporting twice gives identical files and a change in the knowledge shows
as a small diff. Each file holds the source's current text, its identity and version, and its links (rejected ones left out),
outgoing as front matter and `[[wikilinks]]`, incoming as a list. `manifest.json` maps every `kind/id` to its file.

Safety: file names are built only from letters, digits and `._-` (an id such as `../../x` cannot leave the folder), two ids
never share a file, and a folder that already holds other files and no manifest is refused rather than overwritten.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from harnix.knowledge.repo import KnowledgeRepo

MANIFEST = "manifest.json"
_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


def _slug(external_id: str) -> str:
    s = _UNSAFE.sub("_", external_id.replace("/", "__")).strip("._-")[:100]
    return s or "item"


def _q(value: Any) -> str:
    """A YAML scalar: JSON strings are valid YAML, so quoting is one call and nothing in a title can break the header."""
    return json.dumps(value, ensure_ascii=False)


def plan(repo: KnowledgeRepo, project: str) -> dict[str, str]:
    """{source id: relative file path}: `<kind>/<slug>.md`, with a short hash added when two ids would share a name."""
    taken: dict[str, str] = {}
    out: dict[str, str] = {}
    for s in sorted(repo.sources(project, limit=100_000), key=lambda s: (s.kind, s.external_id, s.id)):
        path = f"{_slug(s.kind)}/{_slug(s.external_id)}.md"
        if path.lower() in taken and taken[path.lower()] != s.id:
            path = f"{_slug(s.kind)}/{_slug(s.external_id)}-{hashlib.sha1(s.id.encode()).hexdigest()[:6]}.md"
        taken[path.lower()] = s.id
        out[s.id] = path
    return out


def render(repo: KnowledgeRepo, project: str, files: dict[str, str]) -> dict[str, str]:
    """{relative path: file text} for every source of the project."""
    sources = {s.id: s for s in repo.sources(project, limit=100_000)}
    outgoing: dict[str, list[Any]] = {}
    incoming: dict[str, list[Any]] = {}
    for e in repo.edges(project, limit=1_000_000):
        if e.status != "rejected" and e.src_id in sources and e.dst_id in sources:
            outgoing.setdefault(e.src_id, []).append(e)
            incoming.setdefault(e.dst_id, []).append(e)
    out: dict[str, str] = {}
    for sid, path in files.items():
        s = sources[sid]
        v = repo.version(sid)
        if v is None:
            continue
        links = sorted(outgoing.get(sid, []), key=lambda e: (e.type, sources[e.dst_id].external_id))
        head = ["---", f"id: {_q(s.external_id)}", f"kind: {_q(s.kind)}", f"title: {_q(s.title)}", f"project: {_q(project)}", f"version: {v.version}", f"content_hash: {_q(v.content_hash[:16])}", f"origin: {_q(s.origin)}"]  # fmt: skip
        by_type: dict[str, list[Any]] = {}
        for e in links:
            by_type.setdefault(e.type, []).append(e)
        if by_type:
            head.append("links:")
            for t, es in by_type.items():
                head.append(f"  {t}:")
                head += [
                    f"    - id: {_q(sources[e.dst_id].external_id)}\n      status: {e.status}" for e in es
                ]
        head.append("---")
        body = [v.content.rstrip("\n"), ""]
        if links:
            body += (
                ["## Links", ""]
                + [f"- {e.type} [[{sources[e.dst_id].external_id}]] ({e.status})" for e in links]
                + [""]
            )
        back = sorted(incoming.get(sid, []), key=lambda e: (e.type, sources[e.src_id].external_id))
        if back:
            body += (
                ["## Linked from", ""]
                + [f"- [[{sources[e.src_id].external_id}]] {e.type} this ({e.status})" for e in back]
                + [""]
            )
        out[path] = "\n".join(head) + "\n\n" + "\n".join(body)
    return out


def export(repo: KnowledgeRepo, project: str, folder: Path) -> dict[str, Any]:
    """Write the project's Markdown copy under `folder`; only files whose text changed are written, and files of items that
    left the knowledge base are removed (only those a previous export listed in its manifest)."""
    folder = folder.resolve()
    manifest_path = folder / MANIFEST
    previous: dict[str, str] = {}
    if manifest_path.exists():
        try:
            previous = json.loads(manifest_path.read_text(encoding="utf-8")).get("files", {})
        except (OSError, ValueError):
            previous = {}
        if not isinstance(previous, dict):
            previous = {}
    elif folder.exists() and any(folder.iterdir()):
        raise ValueError(f"{folder} has files and no {MANIFEST}: choose an empty or new folder")
    files = plan(repo, project)
    texts = render(repo, project, files)
    written = unchanged = 0
    for rel, text in texts.items():
        target = (folder / rel).resolve()
        if not target.is_relative_to(folder):  # cannot happen with the slugs above; checked, not assumed
            raise ValueError(f"refusing to write outside the folder: {rel}")
        if target.exists() and target.read_text(encoding="utf-8") == text:
            unchanged += 1
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8", newline="\n")
        written += 1
    removed = 0
    for rel in sorted(set(previous.values()) - set(texts)):
        stale = (folder / rel).resolve()
        if stale.is_relative_to(folder) and stale.is_file():
            stale.unlink()
            removed += 1
    by_id = {s.id: f"{s.kind}/{s.external_id}" for s in repo.sources(project, limit=100_000)}
    manifest = {
        "project": project,
        "files": {by_id[i]: rel for i, rel in sorted(files.items(), key=lambda kv: by_id[kv[0]])},
    }
    folder.mkdir(parents=True, exist_ok=True)
    text = json.dumps(manifest, indent=1, ensure_ascii=False) + "\n"
    if not manifest_path.exists() or manifest_path.read_text(encoding="utf-8") != text:
        manifest_path.write_text(text, encoding="utf-8", newline="\n")
    return {
        "project": project,
        "folder": str(folder),
        "files": len(texts),
        "written": written,
        "unchanged": unchanged,
        "removed": removed,
    }
