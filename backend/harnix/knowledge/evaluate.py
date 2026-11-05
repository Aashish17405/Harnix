"""Retrieval evaluation: does search put the right sources first? Cases are `{query, expect: [external ids]}` (any one of
the expected sources counts as a hit). Reports hit rate at 1/3/5 and mean reciprocal rank for each search mode, so a
change to ranking is judged on data. It measures retrieval only, not whether an agent then does a good job."""

from __future__ import annotations

from typing import Any

from harnix.knowledge.embeddings import Embedder
from harnix.knowledge.service import KnowledgeService

MODES = ("hybrid", "vector", "keyword")


class _Memo:
    """Each query is embedded once however many modes and weights are tried (each call costs time and money)."""

    def __init__(self, inner: Embedder):
        self.inner, self.name, self.cache = inner, inner.name, {}

    def embed(self, texts: list[str]) -> list[list[float]]:
        missing = [t for t in texts if t not in self.cache]
        if missing:
            self.cache.update(zip(missing, self.inner.embed(missing), strict=True))
        return [self.cache[t] for t in texts]


def evaluate(service: KnowledgeService, project: str, cases: list[dict[str, Any]], modes=MODES, weights: tuple[float, float] | None = None) -> dict[str, Any]:  # fmt: skip
    service = KnowledgeService(
        service.repo, _Memo(service.embedder), service.projects, service.index, service.weights
    )
    out: dict[str, Any] = {
        "project": project,
        "cases": len(cases),
        "embedding_model": service.embedder.name,
        "modes": {},
    }
    for mode in modes:
        ranks: list[int | None] = []
        detail = []
        for c in cases:
            hits = service.search(project, c["query"], top_k=10, mode=mode, weights=weights)["hits"]
            ids = [h["external_id"] for h in hits]
            rank = next((i + 1 for i, x in enumerate(ids) if x in c["expect"]), None)
            ranks.append(rank)
            detail.append({"query": c["query"], "expect": c["expect"], "rank": rank, "top3": ids[:3]})
        n = len(cases) or 1
        out["modes"][mode] = {
            "hit@1": round(sum(1 for r in ranks if r == 1) / n, 3),
            "hit@3": round(sum(1 for r in ranks if r is not None and r <= 3) / n, 3),
            "hit@5": round(sum(1 for r in ranks if r is not None and r <= 5) / n, 3),
            "mrr": round(sum(1 / r for r in ranks if r) / n, 3),
            "misses": [d for d in detail if d["rank"] is None or d["rank"] > 3],
        }
    return out


def evaluate_bundles(service: KnowledgeService, project: str, cases: list[dict[str, Any]]) -> dict[str, Any]:
    """The context the harness would hand an agent, per case (the first expected id is the target):
    * target_in_bundle / mrr: is the target among the requirements, and how high;
    * rules_included: of the rules the target mentions (content graph), how many came along;
    * same_backlog: of the requirement items, how many share the target's backlog (BRD-02/...); noise otherwise;
    * target_complete: of the target's acceptance criteria (read from the full source), how many reach the agent;
    * context_chars: size of the rendered context the agent receives;
    * wrongly_insufficient: share of these (answerable) requests the context builder still called insufficient;
    * seconds: time to build one bundle."""
    import time

    from harnix.knowledge.context import build_bundle, render
    from harnix.knowledge.evaluation import criteria_of

    found = rr = rule_hit = rule_tot = same = reqs_n = crit_hit = crit_tot = 0
    chars: list[int] = []
    blocked = 0  # on-topic requests the context builder refused (a relevance floor set too high shows here)
    secs: list[float] = []
    misses: list[dict[str, Any]] = []
    for c in cases:
        target = c["expect"][0]
        t = time.time()
        b = build_bundle(service, project, c["query"], [])
        secs.append(time.time() - t)
        reqs = [i["external_id"] for i in b.content["slots"]["requirements"]]
        items = {i["external_id"] for v in b.content["slots"].values() for i in v}
        chars.append(len(render(b.content)))
        blocked += int(b.status != "sufficient")
        if target in reqs:
            found += 1
            rr += 1 / (reqs.index(target) + 1)
        else:
            misses.append({"query": c["query"], "expect": target, "got": reqs[:3]})
        if "/" in target:
            same += sum(1 for r in reqs if r.split("/", 1)[0] == target.split("/", 1)[0])
            reqs_n += len(reqs)
        src = service.repo.resolve(project, target)
        if src is not None:
            full = service.repo.version(src.id)
            given = next(
                (i["content"] for i in b.content["slots"]["requirements"] if i["external_id"] == target), ""
            )
            norm = lambda t: " ".join(t.lower().split())  # noqa: E731
            for crit in criteria_of(full.content if full else ""):
                crit_tot += 1
                crit_hit += int(norm(crit)[:120] in norm(given))
            rel = service.relations(project, src.id, 1, ["mentions"])
            want = {n["external_id"] for n in rel["nodes"] if n["kind"] == "rule"}
            rule_tot += len(want)
            rule_hit += len(want & items)
    n = len(cases) or 1
    return {
        "cases": len(cases),
        "target_in_bundle": round(found / n, 3),
        "mrr": round(rr / n, 3),
        "rules_included": round(rule_hit / rule_tot, 3) if rule_tot else None,
        "same_backlog": round(same / reqs_n, 3) if reqs_n else None,
        "target_complete": round(crit_hit / crit_tot, 3) if crit_tot else None,
        "context_chars": round(sum(chars) / n),
        "wrongly_insufficient": round(blocked / n, 3),
        "seconds_per_bundle": round(sum(secs) / n, 2),
        "misses": misses,
    }


def evaluate_offtopic(service: KnowledgeService, project: str, cases: list[dict[str, Any]]) -> dict[str, Any]:
    """Requests the knowledge base does not cover: how often the context builder still says "sufficient" (it should
    say the knowledge has nothing relevant instead of handing over the nearest unrelated stories)."""
    from harnix.knowledge.context import build_bundle

    wrong: list[str] = []
    for c in cases:
        b = build_bundle(service, project, c["query"], [])
        if b.status == "sufficient":
            wrong.append(c["query"])
    n = len(cases) or 1
    return {"cases": len(cases), "wrongly_sufficient": round(len(wrong) / n, 3), "examples": wrong[:5]}
