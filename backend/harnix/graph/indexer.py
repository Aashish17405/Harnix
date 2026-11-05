"""Incremental, time-budgeted, resumable indexing of one repository into the graph.

Steady state is cheap: a file whose (size, mtime) match the stored record is not read at all; a file whose
content hash is unchanged is not rewritten; only changed files are re-parsed, and only the files that can be
affected by a change (the changed files, their importers, importers of newly added modules) are re-resolved.

An interrupted run (time budget, cancel, crash) is safe: files are written one batch at a time, the repository
is marked incomplete, and the next run continues where this one stopped and then re-resolves everything once.
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from harnix.graph.extract import FileFacts, facts_for, is_test_path
from harnix.graph.resolve import FileResolution, ModuleMap, resolve_imports, resolve_ref
from harnix.graph.store import GraphStore
from harnix.graph.walker import walk

READ_BATCH = 200
RESOLVE_BATCH = 200


@dataclass
class IndexReport:
    repo_id: str
    files_seen: int = 0
    files_parsed: int = 0  # read and (re)written this run
    files_unchanged: int = 0
    files_removed: int = 0
    files_resolved: int = 0
    truncated: bool = False  # more files than FORGE_GRAPH_MAX_FILES
    complete: bool = True
    changed: bool = False
    version: int = 0
    seconds: float = 0.0
    notes: list[str] = field(default_factory=list)
    symbols: int = 0
    files: int = 0

    def event_data(self) -> dict[str, Any]:
        return {k: getattr(self, k) for k in (
            "files_seen", "files_parsed", "files_unchanged", "files_removed", "files_resolved", "truncated",
            "complete", "changed", "version", "files", "symbols", "notes")} | {"seconds": round(self.seconds, 2)}  # fmt: skip


class RepoIndexer:
    def __init__(self, store: GraphStore, *, max_files: int, max_parse_bytes: int):
        self.store, self.max_files, self.max_parse_bytes = store, max_files, max_parse_bytes

    def index(
        self, root: Path, repo_id: str, name: str, *, time_budget: float, cancel: threading.Event | None = None,
        progress: Callable[[str, int], None] | None = None,
    ) -> IndexReport:  # fmt: skip
        t0 = time.monotonic()
        deadline = t0 + time_budget
        rep = IndexReport(repo_id)
        stop = lambda: (cancel is not None and cancel.is_set()) or time.monotonic() > deadline  # noqa: E731
        say = progress or (lambda *_: None)

        self.store.ensure_schema()
        state = self.store.repo_state(repo_id)
        was_complete = bool(state and state.get("complete"))
        walked = walk(root, self.max_files)
        rep.files_seen, rep.truncated = len(walked.candidates), walked.truncated
        if walked.truncated:
            rep.notes.append(
                f"more than {self.max_files} indexable files: the rest were skipped (FORGE_GRAPH_MAX_FILES)"
            )
        known = self.store.file_records(repo_id)
        present = {c.path for c in walked.candidates}
        # with a truncated walk, files beyond the cap are unseen rather than deleted: never remove on that basis
        removed = [] if walked.truncated else sorted(set(known) - present)
        todo = [
            c
            for c in walked.candidates
            if (known.get(c.path) or (None, None, None))[:2] != (c.size, c.mtime_ns)
        ]
        rep.files_unchanged = len(walked.candidates) - len(todo)

        # importers of files that are about to disappear must be found while the edges still exist
        orphaned_importers = [d["path"] for d in self.store.importers_of(repo_id, removed)] if removed else []
        if removed:
            self.store.delete_files(repo_id, removed)
            rep.files_removed, rep.changed = len(removed), True

        changed_existing: list[str] = []
        new_paths: list[str] = []
        done_all = True
        for i in range(0, len(todo), READ_BATCH):
            if stop():
                done_all = False
                rep.notes.append("time budget reached: the rest will be indexed on the next run")
                break
            batch: list[FileFacts] = []
            for c in todo[i : i + READ_BATCH]:
                try:
                    data = b"" if c.size > self.max_parse_bytes else (root / c.path).read_bytes()
                except OSError:
                    continue
                facts = facts_for(c.path, data, c.size, c.mtime_ns, self.max_parse_bytes)
                prev = known.get(c.path)
                if prev and facts.sha256 is not None and prev[2] == facts.sha256 and prev[0] == facts.size:
                    rep.files_unchanged += 1  # touched but identical
                    continue
                batch.append(facts)
                (changed_existing if prev else new_paths).append(c.path)
            if batch:
                self.store.upsert_files(repo_id, batch)
                rep.files_parsed += len(batch)
                rep.changed = True
                say("indexed", rep.files_parsed)

        # ---- resolution -------------------------------------------------------------------------
        py_paths = [p for p in self.store.python_paths(repo_id) if p.endswith(".py")]
        mm = ModuleMap(py_paths)
        if not was_complete or state is None:
            affected = set(py_paths)  # first run, or the previous run was cut short: resolve everything once
        else:
            affected = {p for p in [*changed_existing, *new_paths] if p.endswith(".py")}
            if changed_existing:
                affected |= {d["path"] for d in self.store.importers_of(repo_id, changed_existing)}
            affected |= set(orphaned_importers)
            new_py = [p for p in new_paths if p.endswith(".py")]
            if new_py:  # importers that could not resolve a module before it existed
                names = sorted({n for p in new_py for n in mm.module_names(p)})
                affected |= set(self.store.files_importing_modules(repo_id, names))
        affected &= set(py_paths)
        ordered = sorted(affected)
        resolved_all = True
        for i in range(0, len(ordered), RESOLVE_BATCH):
            if stop():
                resolved_all = False
                rep.notes.append(
                    "time budget reached during resolution: edges will be completed on the next run"
                )
                break
            self._resolve(repo_id, ordered[i : i + RESOLVE_BATCH], mm)
            rep.files_resolved += min(RESOLVE_BATCH, len(ordered) - i)
            rep.changed = True
            say("resolved", rep.files_resolved)

        # `complete` means "this run finished its work" (drives the full re-resolve after an interrupted run);
        # a file-cap truncation is reported separately in the notes and does not make every run a full run
        rep.complete = done_all and resolved_all
        st = self.store.finish_index(
            repo_id, name, complete=rep.complete, changed=rep.changed or state is None, notes=rep.notes
        )
        rep.version = int(st.get("version", 0))
        rep.files, rep.symbols = int(st.get("files", 0) or 0), int(st.get("symbols", 0) or 0)
        rep.seconds = time.monotonic() - t0
        return rep

    def _resolve(self, repo_id: str, paths: list[str], mm: ModuleMap) -> None:
        self.store.clear_edges(repo_id, paths)
        imports: list[tuple[str, str]] = []
        externals: list[tuple[str, str]] = []
        tests: list[tuple[str, str]] = []
        calls: list[dict[str, Any]] = []
        inherits: list[dict[str, Any]] = []
        for raw in self.store.load_raw(repo_id, paths):
            path = raw["path"]
            res = resolve_imports(path, json.loads(raw["imports"] or "[]"), mm)
            imports += [(path, t) for t in sorted(res.imports)]
            externals += [(path, n) for n in sorted(res.externals)]
            if raw["is_test"]:
                tests += [(path, t) for t in sorted(res.imports) if not is_test_path(t)]
            symbols = raw["symbols"]
            local_top = {s["name"] for s in symbols if s["top"]}
            for s in symbols:
                self._refs(path, s, local_top, res, json.loads(s["calls"] or "[]"), calls)
                if s["kind"] == "class":
                    bases = [["a" if "." in b else "n", b] for b in json.loads(s["bases"] or "[]")]
                    self._refs(path, s, local_top, res, bases, inherits)
        self.store.write_edges(
            repo_id, imports=imports, externals=externals, tests=tests, calls=calls, inherits=inherits
        )

    @staticmethod
    def _refs(
        path: str,
        s: dict[str, Any],
        local_top: set[str],
        res: FileResolution,
        refs: list[list[str]],
        out: list[dict[str, Any]],
    ) -> None:
        for kind, text in refs:
            t = resolve_ref(kind, text, path, s["qualname"], local_top, res)
            if t:
                out.append({"src": s["uid"], "path": t[0], "name": t[1], "qual": t[2]})


__all__ = ["IndexReport", "RepoIndexer"]
