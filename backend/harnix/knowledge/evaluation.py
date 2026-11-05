"""Evaluating what an agent did with the knowledge it was given.

Combines what the harness can establish itself (independent verification, whether code and tests changed, traceability from
each requirement to the code and tests linked to it, whether the agent cited its sources) with a human review of each
acceptance criterion, which is recorded in the audit log. Nothing here asks a model to grade a model.
"""

from __future__ import annotations

import re
from typing import Any

from harnix.graph.extract import is_test_path
from harnix.knowledge.repo import KnowledgeRepo
from harnix.persistence.models import Task

REVIEW_VERDICTS = ("pass", "fail", "unsure")
_DIFF_FILE = re.compile(r"^diff --git a/(.+?) b/", re.M)
_CRITERIA_HEAD = re.compile(r"(?im)^\s*(?:#{1,6}\s*|\*\*)?acceptance criteria[:*]*\s*$")
# the next section ends the criteria: a Markdown heading, a bold heading on its own line, or a rule
_SECTION_END = re.compile(r"^\s*(?:#|\*\*[^*]+\*\*\s*:?\s*$|---+\s*$)")
_TEST_FN = re.compile(r"^(\s*)(?:async\s+)?def\s+(test_\w+)\s*\(", re.M)
_JS_TEST = re.compile(r"""\b(?:it|test)\(\s*(['"`])(.+?)\1""")
_VEC_CACHE: dict[
    tuple[str, str], list[float]
] = {}  # opening the evaluation again does not re-embed the same texts
COVER_SIMILARITY = (
    0.5  # a criterion and a test this alike are shown as "likely covered" (a suggestion, not a proof)
)


def criteria_of(content: str) -> list[str]:
    """Bullets under an 'Acceptance criteria' heading, up to the next heading or blank-line-separated paragraph."""
    lines = content.split("\n")
    out: list[str] = []
    for i, line in enumerate(lines):
        if _CRITERIA_HEAD.match(line):
            for nxt in lines[i + 1 :]:
                if _SECTION_END.match(nxt):
                    break
                m = re.match(r"^\s*(?:[-*]|\d+[.)])\s+(.*\S)\s*$", nxt)
                if m:
                    out.append(m.group(1))
            break
    return out


def find_tests(path: str, text: str) -> list[dict[str, str]]:
    """The tests in one test file: Python `def test_*` (name, docstring and body) or JavaScript `it(...)/test(...)`."""
    out: list[dict[str, str]] = []
    if path.endswith(".py"):
        ms = list(_TEST_FN.finditer(text))
        for i, m in enumerate(ms):
            body = text[m.start() : ms[i + 1].start() if i + 1 < len(ms) else len(text)]
            out.append({"file": path, "name": m.group(2), "text": body[:1200]})
    else:
        for m in _JS_TEST.finditer(text):
            out.append({"file": path, "name": m.group(2)[:200], "text": text[m.start() : m.start() + 1200]})
    return out


def match_tests(embedder: Any, criteria: list[dict[str, Any]], tests: list[dict[str, str]]) -> None:
    """For each criterion, the test it most resembles (cosine of embeddings): a pointer for the reviewer, never a
    verdict. Readable test names count: `test_rejects_an_eleventh_favorite` matches "prevents adding another"."""
    if not criteria or not tests or embedder is None:
        return
    from harnix.knowledge.embeddings import cosine

    texts = [c["text"] for c in criteria] + [f"{t['name'].replace('_', ' ')}\n{t['text']}" for t in tests]
    missing = [t for t in dict.fromkeys(texts) if (embedder.name, t) not in _VEC_CACHE]
    try:
        if missing:
            if len(_VEC_CACHE) > 5000:
                _VEC_CACHE.clear()
            for t, v in zip(missing, embedder.embed(missing), strict=True):
                _VEC_CACHE[(embedder.name, t)] = v
    except Exception:  # noqa: BLE001 - no embeddings, no suggestion
        return
    vecs = [_VEC_CACHE[(embedder.name, t)] for t in texts]
    cv, tv = vecs[: len(criteria)], vecs[len(criteria) :]
    for c, v in zip(criteria, cv, strict=True):
        best = max(range(len(tests)), key=lambda j: cosine(v, tv[j]))
        sim = cosine(v, tv[best])
        c["test"] = {"file": tests[best]["file"], "name": tests[best]["name"], "similarity": round(sim, 3), "likely": sim >= COVER_SIMILARITY}  # fmt: skip


def cited_ids(repo: KnowledgeRepo, project: str, text: str, given: set[str]) -> dict[str, list[str]]:
    """Ids the agent's final message names, sorted into: given in its context, in the knowledge base but not given,
    and unknown (possibly invented). Only id styles the project uses count (US-, BRD-, FEAT-...), so UTF-8 is not one."""
    from harnix.knowledge.context import ID_RE

    known = {s.external_id for s in repo.sources(project, limit=100_000)}
    short = {k.split("/", 1)[1]: k for k in known if "/" in k}
    prefixes = {k.split("/")[-1].split("-")[0] for k in known if "-" in k.split("/")[-1]}
    out: dict[str, list[str]] = {"given": [], "known": [], "unknown": []}
    for x in dict.fromkeys(ID_RE.findall(text)):
        if x.split("/")[-1].split("-")[0] not in prefixes:
            continue
        full = x if x in known else short.get(x)
        given_ids = {g for g in given} | {g.split("/", 1)[1] for g in given if "/" in g}
        out["given" if x in given_ids or full in given else "known" if full else "unknown"].append(x)
    return out


def changed_files(task: Task) -> list[str]:
    return list(dict.fromkeys(_DIFF_FILE.findall(task.final_diff or "")))


def latest_reviews(repo: KnowledgeRepo, project: str, task_id: str) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for a in reversed(repo.audit_log(project, limit=5000)):  # oldest first, so the last review wins
        if a.action == "evaluation.reviewed" and a.object_id == task_id:
            d = a.detail or {}
            out[str(d.get("criterion"))] = {"verdict": d.get("verdict"), "note": d.get("note", ""), "by": a.actor, "at": a.created_at.isoformat() + "Z"}  # fmt: skip
    return out


def record_review(repo: KnowledgeRepo, project: str, task_id: str, criterion: str, verdict: str, note: str, actor: str) -> None:  # fmt: skip
    if verdict not in REVIEW_VERDICTS:
        raise ValueError(f"verdict must be one of: {', '.join(REVIEW_VERDICTS)}")
    repo.audit(project, actor, "evaluation.reviewed", "task", task_id, {"criterion": criterion[:200], "verdict": verdict, "note": note[:1000]})  # fmt: skip


def evaluate_task(
    repo: KnowledgeRepo,
    task: Task,
    verifications: list[Any],
    report: dict[str, Any] | None,
    embedder: Any = None,
) -> dict[str, Any]:
    project = task.knowledge_project or ""
    row = repo.bundle_for_task(task.id)
    bundle = row.content if row else None
    files = changed_files(task)
    code_changed = [f for f in files if not is_test_path(f)]
    tests_changed = [f for f in files if is_test_path(f)]
    final = [v for v in verifications if v.phase == "final"]
    checks: list[dict[str, str]] = []

    def check(name: str, status: str, detail: str) -> None:
        checks.append({"name": name, "status": status, "detail": detail})

    check("Independent verification", "pass" if task.outcome == "verified" else "fail" if task.outcome else "warn",
          f"outcome: {task.outcome or 'not finished'}; " + ", ".join(f"{v.kind} {v.status}" for v in final[-6:]))  # fmt: skip
    if bundle is None:
        check("Context bundle", "fail", "this task has no stored context bundle")
    else:
        suff = bundle["sufficiency"]
        check("Context bundle", "pass" if bundle["status"] == "sufficient" else "fail", f"{bundle['status']}; id {bundle['bundle_id'][:12]}" + (f"; missing: {'; '.join(suff['missing'])}" if suff["missing"] else ""))  # fmt: skip
    check(
        "Code changed",
        "pass" if code_changed else "warn",
        ", ".join(code_changed[:6]) or "no non-test file changed",
    )
    check(
        "Tests added or changed",
        "pass" if tests_changed else "warn",
        ", ".join(tests_changed[:6]) or "no test file changed",
    )

    trace: list[dict[str, Any]] = []
    criteria: list[dict[str, Any]] = []
    if bundle:
        by_ext = {i["external_id"]: i for slot in bundle["slots"].values() for i in slot}
        links: dict[str, list[dict[str, Any]]] = {}
        for e in bundle["relationships"]:
            for a, b in ((e["from"], e["to"]), (e["to"], e["from"])):
                other = by_ext.get(b)
                if a in by_ext and other and other["kind"] in ("code", "test"):
                    links.setdefault(a, []).append({"external_id": b, "kind": other["kind"], "type": e["type"], "status": e["status"], "changed": b in files})  # fmt: skip
        for it in bundle["slots"]["requirements"]:
            lk = links.get(it["external_id"], [])
            trace.append({"requirement": it["external_id"], "kind": it["kind"], "title": it["title"], "links": lk,
                          "code_changed": any(x["changed"] and x["kind"] == "code" for x in lk),
                          "test_linked": any(x["kind"] == "test" for x in lk)})  # fmt: skip
        # the criteria to sign off are the target's (what the task is about), read from the exact version the agent was
        # given, not from the (possibly cut) excerpt; other stories in the context were background, not the assignment
        reqs = bundle["slots"]["requirements"]
        leaves = [it for it in reqs if it["kind"] in ("story", "ticket", "requirement")]
        targets = [it for it in leaves if it.get("target")] or [it for it in leaves if "id" in (it.get("found_by") or [])] or leaves[:1]  # fmt: skip

        def criteria_from(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
            out: list[dict[str, Any]] = []
            for it in items:
                ver = repo.version(it["source_id"], it["version"])
                text = ver.content if ver is not None else it["content"]
                out += [{"id": f"{it['external_id']}#{n}", "source": it["external_id"], "source_version": it["version"], "text": t} for n, t in enumerate(criteria_of(text), 1)]  # fmt: skip
            return out

        # a target inherits the criteria of what it implements or derives from (a ticket -> its story); a requirement with
        # none of its own takes those of the stories that derive from it
        names = {t["external_id"] for t in targets}
        up = {e["to"] for e in bundle["relationships"] if e["from"] in names and e["type"] in ("implements", "derives_from", "refines")}  # fmt: skip
        criteria = criteria_from(targets + [it for it in leaves if it["external_id"] in up and it["external_id"] not in names])  # fmt: skip
        if not criteria:
            down = {e["from"] for e in bundle["relationships"] if e["to"] in names}
            criteria = criteria_from([it for it in leaves if it["external_id"] in down])
        untouched = [
            t["requirement"] for t in trace if t["links"] and not t["code_changed"] and t["kind"] != "brd"
        ]
        check("Linked code was changed", "pass" if not untouched else "warn", "every requirement with linked code had that code changed" if not untouched else "no change to the code linked to: " + ", ".join(untouched[:6]))  # fmt: skip
        final_msg = ((report or {}).get("agent") or {}).get("final_message") or ""
        ids = cited_ids(repo, project, final_msg, set(by_ext))
        if ids["unknown"]:
            check("Agent cited its sources", "fail", "names ids that are not in the knowledge base (possibly invented): " + ", ".join(ids["unknown"][:8]))  # fmt: skip
        else:
            detail = ", ".join(ids["given"][:8]) or "the final message names no source id"
            if ids["known"]:
                detail += "; also names (not in its context): " + ", ".join(ids["known"][:6])
            check("Agent cited its sources", "pass" if ids["given"] else "warn", detail)
        # which test most likely covers each criterion (from the tests the task changed, read from its workspace)
        if task.workspace_path and tests_changed:
            from pathlib import Path

            found: list[dict[str, str]] = []
            for f in tests_changed[:20]:
                p = Path(task.workspace_path) / f
                try:
                    if p.is_file() and p.stat().st_size < 400_000:
                        found += find_tests(f, p.read_text(encoding="utf-8", errors="replace"))
                except OSError:
                    continue
            match_tests(embedder, criteria, found[:200])
    pending = [e for e in repo.edges(project, "proposed") if e.task_id == task.id]
    check("Relationships proposed", "info", f"{len(pending)} waiting for a person to promote or reject")
    for e in (
        pending
    ):  # what this task is suggested to implement or test, shown beside the requirement (as proposed)
        src, dst = repo.get_source(e.src_id), repo.get_source(e.dst_id)
        row = next((t for t in trace if dst and t["requirement"] == dst.external_id), None)
        if src is not None and row is not None and src.kind in ("code", "test"):
            row["links"].append({"external_id": src.external_id, "kind": src.kind, "type": e.type, "status": "proposed", "changed": src.external_id in files})  # fmt: skip
            row["code_changed"] = row["code_changed"] or (src.kind == "code" and src.external_id in files)
            row["test_linked"] = row["test_linked"] or src.kind == "test"

    reviews = latest_reviews(repo, project, task.id)
    for c in criteria:
        c["review"] = reviews.get(c["id"])
    counts = {v: sum(1 for c in criteria if (c["review"] or {}).get("verdict") == v) for v in REVIEW_VERDICTS}
    counts["unreviewed"] = sum(1 for c in criteria if not c["review"])
    auto_ok = all(c["status"] != "fail" for c in checks)
    verdict = (
        "fail"
        if (not auto_ok or counts["fail"])
        else "pass"
        if (criteria and counts["pass"] == len(criteria))
        else "needs review"
    )
    return {
        "task_id": task.id, "project": project, "bundle_id": (bundle or {}).get("bundle_id"), "changed_files": files,
        "checks": checks, "traceability": trace, "criteria": criteria, "criteria_summary": counts, "verdict": verdict,
        "note": "pass = independent checks have no failure and a person passed every acceptance criterion; the automatic checks alone never say pass",  # fmt: skip
    }  # fmt: skip
