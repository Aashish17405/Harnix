"""Process-wide owner of the graph connection: namespacing, locking, background indexing, health.

One GraphManager per backend process. Failure to reach Neo4j never fails a task: callers get GraphUnavailable
and the runner records a `graph.unavailable` event and continues without the graph.
"""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from harnix.config import Settings
from harnix.graph.indexer import IndexReport, RepoIndexer
from harnix.graph.query import RepoGraph
from harnix.graph.store import GraphStore, GraphUnavailable
from harnix.policies.paths import repo_key

log = logging.getLogger("forge.graph")
HEALTH_TTL = 5.0


def repo_id_for(repo: Path) -> str:
    """Stable namespace of a repository: a hash of its resolved absolute path (no path is stored in the graph)."""
    return repo_key(repo)


class GraphManager:
    def __init__(self, settings: Settings, store: GraphStore | None = None):
        self.settings = settings
        self.store = store or GraphStore(
            settings.neo4j_uri, settings.neo4j_user, settings.neo4j_password or "", settings.neo4j_database,
            query_timeout=settings.graph_query_timeout_seconds,
        )  # fmt: skip
        self.indexer = RepoIndexer(
            self.store, max_files=settings.graph_max_files, max_parse_bytes=settings.graph_max_parse_bytes
        )
        self._locks: dict[str, threading.Lock] = {}
        self._guard = threading.Lock()
        self._jobs: dict[str, dict[str, Any]] = {}
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="forge-graph")
        self._health: tuple[float, dict[str, Any]] | None = None

    def close(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)
        self.store.close()

    def _lock(self, repo_id: str) -> threading.Lock:
        with self._guard:
            return self._locks.setdefault(repo_id, threading.Lock())

    # ---- health ------------------------------------------------------------------------------------
    def health(self) -> dict[str, Any]:
        now = time.monotonic()
        if self._health and now - self._health[0] < HEALTH_TTL:
            return self._health[1]
        ok, detail = self.store.health()
        h = {"enabled": True, "available": ok, "detail": detail}
        self._health = (now, h)
        return h

    # ---- indexing for a task -----------------------------------------------------------------------
    def index_for_task(
        self, workspace: Path, repo: Path, cancel: threading.Event, touched: Any = None
    ) -> tuple[IndexReport | None, RepoGraph, str]:
        """Index the task's workspace copy (secret-free, a faithful copy of the working tree) within the time budget.

        Returns (report or None if another index run held the lock, the view for this task, a note).
        """
        rid = repo_id_for(repo)
        lock = self._lock(rid)
        report: IndexReport | None = None
        note = ""
        if lock.acquire(timeout=self.settings.graph_index_timeout_seconds):
            try:
                report = self.indexer.index(
                    workspace,
                    rid,
                    repo.name,
                    time_budget=self.settings.graph_index_timeout_seconds,
                    cancel=cancel,
                )
            finally:
                lock.release()
        else:
            note = "another index run for this repository was still in progress; using the stored graph"
        state = self.store.repo_state(rid) or {}
        view = RepoGraph(
            self.store, rid, version=int(state.get("version", 0)), complete=bool(state.get("complete")),
            max_results=self.settings.graph_query_max_results, touched=touched,
        )  # fmt: skip
        return report, view, note

    # ---- background pre-indexing (large repositories) ---------------------------------------------
    def start_index(self, repo: Path) -> dict[str, Any]:
        rid = repo_id_for(repo)
        with self._guard:
            job = self._jobs.get(rid)
            if job and job["state"] == "running":
                return job
            job = {
                "repo_id": rid,
                "state": "running",
                "started": time.time(),
                "stage": "starting",
                "progress": 0,
            }
            self._jobs[rid] = job
        self._pool.submit(self._run_job, rid, repo, job)
        return job

    def _run_job(self, rid: str, repo: Path, job: dict[str, Any]) -> None:
        def progress(stage: str, n: int) -> None:
            job["stage"], job["progress"] = stage, n

        try:
            with self._lock(rid):
                rep = self.indexer.index(
                    repo,
                    rid,
                    repo.name,
                    time_budget=self.settings.graph_background_index_timeout_seconds,
                    progress=progress,
                )
            job.update(state="done", report=rep.event_data(), finished=time.time())
        except GraphUnavailable as e:
            job.update(state="failed", error=str(e), finished=time.time())
        except Exception as e:  # noqa: BLE001 - a job must always end in a visible state
            log.exception("background graph index failed")
            job.update(state="failed", error=f"{type(e).__name__}: {str(e)[:200]}", finished=time.time())

    def status(self, repo: Path) -> dict[str, Any]:
        rid = repo_id_for(repo)
        out: dict[str, Any] = {"enabled": True, "repo_id": rid, **self.health()}
        if out["available"]:
            state = self.store.repo_state(rid)
            out["indexed"] = state is not None
            if state:
                out["graph"] = {
                    k: state.get(k)
                    for k in (
                        "version",
                        "complete",
                        "files",
                        "symbols",
                        "imports",
                        "calls",
                        "indexed_at",
                        "notes",
                    )
                }
        if rid in self._jobs:
            out["job"] = self._jobs[rid]
        return out

    def drop(self, repo: Path) -> int:
        rid = repo_id_for(repo)
        with self._lock(rid):
            return self.store.drop_repo(rid)
