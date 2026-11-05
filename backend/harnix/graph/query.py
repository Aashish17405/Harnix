"""Read-side of the knowledge graph for one task: the tool operations and the context block.

Everything here is a fixed, bounded query (see store.py). The model supplies only *values* (a name, a path);
it can never supply Cypher, labels or relationship types.

All text that comes out of the graph originates in the repository (names, signatures, one docstring line), so it
is cleaned, size-capped and handed to the model as untrusted data.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from typing import Any

from harnix.graph.extract import clean
from harnix.graph.store import GraphStore

OPS = (
    "search_symbols",
    "find_symbol",
    "callers",
    "callees",
    "hierarchy",
    "file_info",
    "dependents",
    "tests_for",
)
_PATH = re.compile(
    r"(?<![\w/.\-])((?:[\w.\-]+/)+[\w.\-]+\.\w{1,6}|[\w\-]+\.(?:py|js|jsx|ts|tsx|go|rs|java|kt|cs|rb|php|json|ya?ml|toml|md))\b"
)
_TRACE = re.compile(r"""(?:^|[\s"'(])((?:[\w.\-]+/)*[\w.\-]+\.py)(?:::|:\d+|", line)""")
_IDENT = re.compile(r"`([A-Za-z_][\w.]{2,80})`|\b([A-Za-z_][A-Za-z0-9_]{3,60})\b")
_COMMON = frozenset(
    "this that with from into have been when then than them they their what which will would should could about "
    "after before there where while using used uses make made test tests file files code function class method "
    "return returns value values data name names none true false error errors fail fails failing failed fix fixed "
    "bug issue please want need needs update change changes add adds run runs check checks work works task".split()
)  # fmt: skip


def norm_path(p: str) -> str | None:
    """A workspace-relative posix path, or None if it is absolute, climbs out, or empty."""
    p = p.strip().replace("\\", "/").removeprefix("./")
    if not p or p.startswith("/") or re.match(r"^[A-Za-z]:", p) or ".." in p.split("/") or "\x00" in p:
        return None
    return p


def _uniq(items: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(items))


class RepoGraph:
    """The graph of one repository as seen by one task (read-only)."""

    def __init__(
        self, store: GraphStore, repo_id: str, *, version: int, complete: bool, max_results: int,
        touched: Callable[[], set[str]] | None = None,
    ):  # fmt: skip
        self.store, self.repo_id, self.version, self.complete = store, repo_id, version, complete
        self.max_results = max_results
        self._touched = touched or (lambda: set())

    # ---- tool operations --------------------------------------------------------------------------
    def op(self, op: str, target: str, limit: int | None = None, depth: int = 2) -> dict[str, Any]:
        n = max(1, min(limit or self.max_results, self.max_results))
        target = target.strip()
        if op not in OPS:
            raise ValueError(f"Unknown operation {op!r}. Available: {', '.join(OPS)}")
        if not target:
            raise ValueError("target is required")
        fn = getattr(self, f"_op_{op}")
        data: dict[str, Any] = fn(target, n, depth)
        data["op"], data["target"] = op, target
        data["snapshot"] = self._snapshot()
        return data

    def _snapshot(self) -> dict[str, Any]:
        s: dict[str, Any] = {
            "indexed_at_task_start": True, "version": self.version, "index_complete": self.complete,
            "accuracy": "static analysis, name-based; calls through instance attributes (obj.method()) are not resolved, "
            "so callers/callees are a lower bound",
        }  # fmt: skip
        touched = sorted(self._touched())
        if touched:
            s["note"] = "Files you changed since the task started are not reflected: " + ", ".join(
                touched[:10]
            )
        try:
            cur = (self.store.repo_state(self.repo_id) or {}).get("version")
            if isinstance(cur, int) and cur > self.version:
                s["stale"] = "the repository was re-indexed by another run since this task started"
        except Exception:  # noqa: BLE001 - the staleness hint is optional
            pass
        return s

    def _names(self, target: str) -> tuple[str, str | None]:
        """`Class.method` -> ('method', 'Class.method'); `name` -> ('name', None)."""
        return (target.rsplit(".", 1)[-1], target) if "." in target else (target, None)

    def _op_search_symbols(self, target: str, n: int, _: int) -> dict[str, Any]:
        rows = self.store.search_symbols(self.repo_id, target, n + 1)
        return {"results": [self._sym(r) for r in rows[:n]], "truncated": len(rows) > n}

    def _op_find_symbol(self, target: str, n: int, _: int) -> dict[str, Any]:
        name, qual = self._names(target)
        rows = [
            r
            for r in self.store.symbols_named(self.repo_id, name, 50)
            if qual is None or r["qualname"] == qual
        ]
        return {
            "results": [self._sym(r) | {"doc": clean(r.get("doc") or "", 160)} for r in rows[:n]],
            "truncated": len(rows) > n,
        }

    def _op_callers(self, target: str, n: int, _: int) -> dict[str, Any]:
        name, qual = self._names(target)
        rows = [r for r in self.store.callers(self.repo_id, name, 200) if qual is None or r["target"] == qual]
        return {
            "results": [{"caller": clean(r["caller"], 120), "path": r["caller_path"], "line": r["line"], "calls": clean(r["target"], 120), "in": r["target_path"]} for r in rows[:n]],
            "truncated": len(rows) > n,
        }  # fmt: skip

    def _op_callees(self, target: str, n: int, _: int) -> dict[str, Any]:
        name, qual = self._names(target)
        rows = [r for r in self.store.callees(self.repo_id, name, 200) if qual is None or r["caller"] == qual]
        return {
            "results": [{"caller": clean(r["caller"], 120), "calls": clean(r["target"], 120), "path": r["target_path"], "line": r["line"]} for r in rows[:n]],
            "truncated": len(rows) > n,
        }  # fmt: skip

    def _op_hierarchy(self, target: str, n: int, _: int) -> dict[str, Any]:
        name, qual = self._names(target)
        h = self.store.hierarchy(self.repo_id, name, n)
        up = [r for r in h["bases"] if qual is None or r["cls"] == qual]
        down = [r for r in h["subclasses"] if qual is None or r["cls"] == qual]
        return {"bases": [{"class": clean(r["cls"], 100), "base": clean(r["base"], 100), "path": r["path"]} for r in up],
                "subclasses": [{"class": clean(r["cls"], 100), "subclass": clean(r["sub"], 100), "path": r["path"]} for r in down]}  # fmt: skip

    def _path(self, target: str) -> str:
        p = norm_path(target)
        if p is None:
            raise ValueError("target must be a path relative to the repository root")
        return p

    def _op_file_info(self, target: str, n: int, _: int) -> dict[str, Any]:
        p = self._path(target)
        prof = self.store.file_profile(self.repo_id, p, n)
        if prof is None:
            near = self.store.paths_named(self.repo_id, p.rsplit("/", 1)[-1], 5)
            return {"found": False, "similar_paths": near}
        prof["symbols"] = [
            {**s, "signature": clean(s["signature"] or "", 160), "qualname": clean(s["qualname"], 100)}
            for s in prof["symbols"]
        ]
        prof["error"] = clean(prof["error"] or "", 160) or None
        return {"found": True, **prof}

    def _dependents(
        self, path: str, depth: int, limit: int, *, tests_only: bool = False
    ) -> tuple[list[dict[str, Any]], bool]:
        """Breadth-first over IMPORTS, one bounded query per level (no combinatorial path expansion)."""
        seen, frontier, out = {path}, [path], []
        for d in range(1, max(1, min(depth, 3)) + 1):
            nxt: list[str] = []
            for r in self.store.importers_of(self.repo_id, frontier, limit=limit * 4):
                if r["path"] in seen:
                    continue
                seen.add(r["path"])
                hit = {"path": r["path"], "is_test": r["is_test"], "distance": d}
                if not tests_only:
                    out.append(hit)
                    nxt.append(r["path"])
                elif r["is_test"]:
                    out.append(hit)  # tests are leaves: nothing that matters imports a test
                else:
                    nxt.append(r["path"])  # keep climbing through non-test files to reach tests
            if not nxt or len(out) > limit:
                break
            frontier = nxt
        return out[:limit], len(out) > limit

    def _op_dependents(self, target: str, n: int, depth: int) -> dict[str, Any]:
        p = self._path(target)
        rows, trunc = self._dependents(p, depth, n)
        return {"depth": max(1, min(depth, 3)), "results": rows, "truncated": trunc,
                "meaning": "files that import this file, directly (distance 1) or through other files"}  # fmt: skip

    def _op_tests_for(self, target: str, n: int, depth: int) -> dict[str, Any]:
        p = self._path(target)
        rows, trunc = self._dependents(p, 3, n, tests_only=True)
        return {
            "results": rows,
            "truncated": trunc,
            "meaning": "test files that import this file, directly or transitively",
        }

    @staticmethod
    def _sym(r: dict[str, Any]) -> dict[str, Any]:
        return {
            "kind": r["kind"],
            "name": clean(r["qualname"], 120),
            "path": r["path"],
            "line": r["start"],
            "signature": clean(r.get("signature") or "", 160),
        }

    # ---- context block ----------------------------------------------------------------------------
    def seeds(self, instructions: str, ref_paths: list[str], failing_output: str) -> dict[str, list[str]]:
        """Where to look first: files the user attached, files named in the task, files in failing tracebacks,
        and identifiers in the task that match few symbols."""
        wanted = [p for p in (norm_path(x) for x in ref_paths) if p]
        for m in _PATH.finditer(instructions):
            if (p := norm_path(m.group(1))) and p not in wanted:
                wanted.append(p)
        failing: list[str] = []
        for m in _TRACE.finditer(failing_output):
            if (p := norm_path(m.group(1))) and p not in failing:
                failing.append(p)
        candidates = _uniq([*wanted, *failing])[:40]
        exact = self.store.existing_paths(self.repo_id, candidates)
        files, tests = [], []
        for p in candidates:
            resolved = p if p in exact else None
            if resolved is None and "/" not in p:
                named = self.store.paths_named(self.repo_id, p, 2)
                resolved = named[0] if len(named) == 1 else None
            if resolved is None:
                continue
            (tests if p in failing and p not in wanted else files).append(resolved)
        idents: list[str] = []
        for m in _IDENT.finditer(instructions):
            tok = (m.group(1) or m.group(2)).rsplit(".", 1)[-1]
            looks_like_code = (
                bool(m.group(1)) or "_" in tok or (tok != tok.lower() and tok != tok.capitalize())
            )
            if looks_like_code and tok.lower() not in _COMMON and tok not in idents:
                idents.append(tok)
        counts = self.store.symbol_name_counts(self.repo_id, idents[:40]) if idents else {}
        symbols = [t for t in idents if 1 <= counts.get(t, 0) <= 3][:5]
        return {"files": _uniq(files)[:6], "tests": _uniq(tests)[:4], "symbols": symbols}

    def context_block(
        self, instructions: str, ref_paths: list[str], failing_output: str, max_chars: int
    ) -> tuple[str, dict[str, Any]]:
        ov = self.store.overview(self.repo_id, 8)
        langs = ", ".join(f"{n} {c}" for n, c in ov["languages"][:5])
        total = sum(c for _, c in ov["languages"])
        lines = [
            "Static, name-based index of the repository as it was when this task started. It can be incomplete or "
            "approximate (see the graph_query tool's snapshot notes). Verify with the file tools before relying on it.",
            f"- {total} indexed files ({langs}); index {'complete' if self.complete else 'INCOMPLETE (time budget)'}",
            "- main areas: " + ", ".join(f"{clean(d, 40)} ({c})" for d, c in ov["top_dirs"][:6]),
        ]
        if ov["central"]:
            lines.append(
                "- most depended-on files: "
                + ", ".join(f"{c['path']} ({c['dependents']})" for c in ov["central"][:8])
            )
        seeds = self.seeds(instructions, ref_paths, failing_output)
        stats = {
            "seed_files": len(seeds["files"]),
            "seed_tests": len(seeds["tests"]),
            "seed_symbols": len(seeds["symbols"]),
        }
        focus: list[str] = []
        for t in seeds["tests"]:
            prof = self.store.file_profile(self.repo_id, t, 8)
            if prof:
                focus.append(
                    f"- {t} (a test that fails before your changes) imports: "
                    + (", ".join(prof["imports"]) or "nothing internal")
                )
        for f in seeds["files"]:
            prof = self.store.file_profile(self.repo_id, f, 8)
            if not prof:
                continue
            focus.append(
                f"- {f} [{prof['language']}, {prof['lines']} lines, {prof['dependents']} dependents]"
            )
            if prof["symbols"]:
                focus.append(
                    "    defines: "
                    + "; ".join(
                        f"{s['kind']} {clean(s['qualname'], 60)} L{s['start']}-{s['end']}"
                        for s in prof["symbols"][:8]
                    )
                )
            if prof["imports"]:
                focus.append("    imports: " + ", ".join(prof["imports"][:6]))
            if prof["imported_by"]:
                focus.append(
                    "    imported by: "
                    + ", ".join(
                        f"{d['path']}{' (test)' if d['is_test'] else ''}" for d in prof["imported_by"][:6]
                    )
                )
        for s in seeds["symbols"]:
            defs = self.store.symbols_named(self.repo_id, s, 3)
            for d in defs[:2]:
                callers = [r for r in self.store.callers(self.repo_id, s, 6) if r["target_path"] == d["path"]]
                callees = [r for r in self.store.callees(self.repo_id, s, 6) if r["caller_path"] == d["path"]]
                parts = [f"- {d['kind']} {clean(d['qualname'], 80)} at {d['path']}:{d['start']}"]
                if callers:
                    parts.append(
                        "callers: "
                        + ", ".join(f"{clean(r['caller'], 50)} ({r['caller_path']})" for r in callers[:5])
                    )
                if callees:
                    parts.append(
                        "calls: "
                        + ", ".join(f"{clean(r['target'], 50)} ({r['target_path']})" for r in callees[:5])
                    )
                focus.append("; ".join(parts))
        if focus:
            lines.append("Focus (from your task, attached files and failing tests):")
            lines += focus
        lines.append(
            "More: graph_query ops search_symbols, find_symbol, callers, callees, hierarchy, file_info, dependents, tests_for."
        )
        text = "\n".join(lines)
        if len(text) > max_chars:
            text = text[: max_chars - 40].rsplit("\n", 1)[0] + "\n[graph context cut to fit the size limit]"
        stats["chars"] = len(text)
        return text, stats
