"""Problems in the documents themselves, as the last ingestion of each folder found them.

Ingestion records them (Ingestor.collect_findings) in the audit log: ids used twice with different content (the second
silently replaces the first), references to ids that do not exist, links the ontology refuses, files that could not be read
or were cut, sources with identical text, renderings whose cells the backlog does not say. This reads the newest record of
each folder, so fixing a document and ingesting again clears its findings.
"""

from __future__ import annotations

from typing import Any

from harnix.knowledge.repo import KnowledgeRepo


def latest(repo: KnowledgeRepo, project: str) -> dict[str, Any]:
    """{"count", "by_kind", "findings": [{kind, where, message, root, at}]} from the newest ingestion of each folder."""
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for a in repo.audit_log(project, limit=5000):  # newest first
        if a.action != "ingest.findings" or a.object_id in seen:
            continue
        seen.add(a.object_id)
        out += [
            {**f, "root": a.object_id, "at": a.created_at.isoformat() + "Z"}
            for f in (a.detail or {}).get("findings", [])
        ]
    by_kind: dict[str, int] = {}
    for f in out:
        by_kind[f["kind"]] = by_kind.get(f["kind"], 0) + 1
    return {"project": project, "count": len(out), "by_kind": by_kind, "findings": out}
