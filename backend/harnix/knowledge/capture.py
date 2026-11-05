"""Knowledge updates after a task: what a VERIFIED task suggests becomes `proposed` relationships. Nothing the agent
produces is authoritative until a person promotes it (promote/reject here, behind the access list)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from harnix.knowledge.ontology import default
from harnix.knowledge.repo import KnowledgeRepo
from harnix.knowledge.service import AccessDenied

MAX_PROPOSALS = 20


def capture(repo: KnowledgeRepo, project: str, task_id: str, bundle: dict[str, Any] | None, changed_files: list[dict[str, str]]) -> dict[str, Any]:  # fmt: skip
    """Propose `file implements requirement` / `test tests requirement` for the top requirement-class items of the
    bundle and the files the verified task changed. Only for files that already exist as sources; never rewrites a
    source (the workspace is not applied yet). Idempotent: a repeated proposal is the same edge."""
    result: dict[str, Any] = {"proposed": 0, "existing": 0, "files_without_source": [], "requirements": []}
    if not bundle:
        return result
    # a BRD is too broad to link code to: only requirements, stories and tickets. The ones the task named by id are
    # the target; otherwise only the best-ranked one (the bundle is ordered by relevance), not every loose match
    reqs = [
        i
        for i in bundle["slots"].get("requirements", [])
        if i["matched"] and i.get("kind") in ("requirement", "story", "ticket")
    ]
    reqs = [i for i in reqs if "id" in (i.get("found_by") or [])] or reqs[:1]
    result["requirements"] = [r["external_id"] for r in reqs]
    for f in changed_files:
        if f["status"] == "D":
            continue
        src = next(
            (s for k in ("code", "test") if (s := repo.find_source(project, k, f["path"])) is not None), None
        )
        if src is None:
            result["files_without_source"].append(f["path"])
            continue
        for r in reqs:
            if result["proposed"] + result["existing"] >= MAX_PROPOSALS:
                break
            dst = repo.get_source(r["source_id"])
            if dst is None:
                continue
            etype = "tests" if src.kind == "test" else "implements"
            if not default().allowed(etype, src.kind, repo.node_type(dst)):
                continue  # e.g. code "implements" a whole BRD: not a link the ontology knows
            _, created = repo.upsert_edge(
                project, src, dst, etype, "proposed", "agent",
                evidence=f"task {task_id} changed {f['path']} and passed independent verification; heuristic: it was the closest match to {r['external_id']}",
                task_id=task_id,
            )  # fmt: skip
            result["proposed" if created else "existing"] += 1
    repo.audit(project, f"task:{task_id}", "edges.proposed", "task", task_id, result)
    return result


def decide(repo: KnowledgeRepo, edge_id: str, approve: bool, actor: str, projects: list[str] | None) -> str:
    """Promote (verified) or reject a proposed edge. Returns the new status. Raises KeyError/AccessDenied/ValueError."""
    e = repo.get_edge(edge_id)
    if e is None:
        raise KeyError(edge_id)
    if projects is not None and e.project not in projects:
        raise AccessDenied(f"No access to project '{e.project}'")
    status = "verified" if approve else "rejected"
    if not repo.decide_edge(edge_id, status, actor):
        raise ValueError(f"Edge {edge_id} is already {repo.get_edge(edge_id).status}")  # type: ignore[union-attr]
    repo.audit(
        e.project,
        actor,
        f"edge.{status}",
        "edge",
        edge_id,
        {"type": e.type, "src": e.src_id, "dst": e.dst_id},
    )
    return status


def task_metrics(repo: KnowledgeRepo, project: str, cost: Callable[[Any], float | None] | None = None) -> dict[str, Any]:  # fmt: skip
    """The delivery side of the loop for tasks built with this project's knowledge: validation success rate (the
    independent verification, not the model's claim), repair cycles, latency, tokens and estimated cost."""
    from sqlalchemy import select

    from harnix.persistence.models import Task

    with repo.sessions() as s:
        tasks = list(s.scalars(select(Task).where(Task.knowledge_project == project)))
    done = [t for t in tasks if t.status in ("completed", "failed", "cancelled")]
    secs = sorted(
        (t.finished_at - t.started_at).total_seconds() for t in done if t.started_at and t.finished_at
    )
    costs = [c for t in done if cost and (c := cost(t)) is not None]
    return {
        "total": len(tasks), "finished": len(done),
        "verified": sum(1 for t in done if t.outcome == "verified"),
        "validation_success_rate": round(sum(1 for t in done if t.outcome == "verified") / len(done), 3) if done else None,
        "stopped_for_clarification": sum(1 for t in done if "clarification" in (t.error or "").lower()),
        "repair_cycles": {"total": sum(t.recovery_attempts for t in done), "tasks_needing_repair": sum(1 for t in done if t.recovery_attempts)},
        "latency_seconds": {"median": secs[len(secs) // 2] if secs else None, "max": secs[-1] if secs else None},
        "tokens": {"input": sum(t.input_tokens for t in done), "output": sum(t.output_tokens for t in done)},
        "estimated_cost_usd": round(sum(costs), 4) if costs else None,
    }  # fmt: skip


def metrics(
    repo: KnowledgeRepo, project: str, cost: Callable[[Any], float | None] | None = None
) -> dict[str, Any]:
    """Counts for evaluating the knowledge loop (nothing here claims the answers are good, only what happened):
    the knowledge, the proposals and their review, the tasks built with it, and the latest retrieval evaluation."""
    srcs = repo.sources(project, limit=100000)
    edges = repo.edges(project, limit=100000)
    audit = repo.audit_log(project, limit=100000)
    by = lambda rows, key: {k: sum(1 for r in rows if key(r) == k) for k in sorted({key(r) for r in rows})}  # noqa: E731
    proposals = [e for e in edges if e.origin == "agent"]
    decided = [e for e in proposals if e.status != "proposed"]
    return {
        "project": project,
        "sources": {"total": len(srcs), "by_kind": by(srcs, lambda s: s.kind), "by_state": by(srcs, lambda s: s.ingestion_state)},
        "edges": {"total": len(edges), "by_status": by(edges, lambda e: e.status), "by_origin": by(edges, lambda e: e.origin)},
        "agent_proposals": {
            "total": len(proposals), "pending": len(proposals) - len(decided),
            "promoted": sum(1 for e in decided if e.status == "verified"), "rejected": sum(1 for e in decided if e.status == "rejected"),
            "promotion_rate": (round(sum(1 for e in decided if e.status == "verified") / len(decided), 3) if decided else None),
        },
        "bundles": by([a for a in audit if a.action == "bundle.built"], lambda a: a.detail.get("status")),
        "tasks": task_metrics(repo, project, cost),
        "retrieval": next(({"at": a.created_at.isoformat() + "Z", **a.detail} for a in audit if a.action == "retrieval.evaluated"), None),
        "audit_events": len(audit),
    }  # fmt: skip
