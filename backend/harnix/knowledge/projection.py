"""A 2-D picture of embeddings: principal components found by power iteration (standard library only).

The components are fitted on a sample of at most `fit` vectors (that bounds the cost: about a second for 1536 dimensions),
then every vector is projected onto them. Deterministic: the same vectors give the same picture. Distances in the picture
only roughly follow distances in the full space; the neighbour list in the UI uses the real cosine similarity.
"""

from __future__ import annotations

import math
from operator import mul


def _dot(a: list[float], b: list[float]) -> float:
    return sum(map(mul, a, b))


def _unit(v: list[float]) -> list[float]:
    n = math.sqrt(_dot(v, v)) or 1.0
    return [x / n for x in v]


def _top_component(rows: list[list[float]], iters: int, seed: int) -> list[float]:
    d = len(rows[0])
    v = _unit([math.sin(seed * 12.9898 + i * 78.233) for i in range(d)])  # a fixed, non-degenerate start
    for _ in range(iters):
        scores = [_dot(r, v) for r in rows]  # X v
        w = [0.0] * d
        for s, r in zip(scores, rows, strict=True):  # X^T (X v)
            if s:
                w = [a + s * b for a, b in zip(w, r, strict=True)]
        nv = _unit(w)
        if _dot(nv, v) > 0.999999:
            return nv
        v = nv
    return v


def pca2(vectors: list[list[float]], fit: int = 200, iters: int = 25) -> list[tuple[float, float]]:
    """(x, y) in [0, 1] for each vector. Fewer than two vectors: the centre."""
    n = len(vectors)
    if n < 2:
        return [(0.5, 0.5)] * n
    d = len(vectors[0])
    mean = [sum(col) / n for col in zip(*vectors, strict=True)]
    centred = [[a - m for a, m in zip(v, mean, strict=True)] for v in vectors]
    step = max(n // fit, 1)
    sample = centred[::step][:fit]
    c1 = _top_component(sample, iters, 1)
    proj1 = [_dot(r, c1) for r in sample]
    deflated = [[a - p * b for a, b in zip(r, c1, strict=True)] for r, p in zip(sample, proj1, strict=True)]
    c2 = _top_component(deflated, iters, 2) if d > 1 else [0.0] * d
    xs, ys = [_dot(r, c1) for r in centred], [_dot(r, c2) for r in centred]

    # one scale for both axes: the picture keeps the proportions of the data (a weak second component stays narrow)
    span = max(max(xs) - min(xs), max(ys) - min(ys)) or 1.0

    def scale(vs: list[float]) -> list[float]:
        mid = (max(vs) + min(vs)) / 2
        return [0.5 + 0.92 * (x - mid) / span for x in vs]

    return list(zip(scale(xs), scale(ys), strict=True))
