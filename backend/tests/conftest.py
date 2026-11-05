from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from harnix.config import Settings
from harnix.execution.sandbox import LocalSandbox
from harnix.execution.workspace import WorkspaceManager
from harnix.harness.runner import TaskRunner
from harnix.harness.states import TERMINAL, TaskStatus
from harnix.persistence.db import make_engine
from harnix.persistence.models import Base
from harnix.persistence.store import Store
from harnix.policies.redaction import Redactor

BUGGY = "def add(a, b):\n    return a - b\n"
TEST = "from mathutil import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n"


# Set FORGE_TEST_DATABASE_URL (e.g. postgresql+psycopg://forge:forge@127.0.0.1:55432/forge) to run the whole
# suite against PostgreSQL instead of the default per-test SQLite file. Tables are dropped after each test.
PG_URL = os.environ.get("FORGE_TEST_DATABASE_URL")


@pytest.fixture(autouse=True)
def _clean_external_db():
    yield
    if PG_URL:
        engine = make_engine(PG_URL)
        Base.metadata.drop_all(engine)
        engine.dispose()


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    repos = tmp_path / "repos"
    repos.mkdir()
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        provider="scripted",
        scripted_file="unused.json",
        execution_mode="local",
        database_url=PG_URL or f"sqlite:///{tmp_path / 'forge.db'}",
        workspace_root=str(tmp_path / "ws"),
        allowed_repo_roots=str(repos),
        max_iterations=12,
        max_duration_seconds=120,
        tool_timeout_seconds=60,
        approval_timeout_seconds=20,
        max_recovery_attempts=2,
    )


@pytest.fixture
def repo(settings: Settings) -> Path:
    r = settings.repo_roots[0] / "calc"
    r.mkdir()
    (r / "mathutil.py").write_text(BUGGY)
    (r / "test_mathutil.py").write_text(TEST)
    (r / "AGENTS.md").write_text("Run pytest before finishing.\n")
    (r / ".env").write_text("API_KEY=supersecretvalue123\n")
    return r


@pytest.fixture
def store(settings: Settings) -> Store:
    s = Store(make_engine(settings.database_url), Redactor())
    s.create_schema()
    return s


def make_runner(settings: Settings, store: Store, provider, graph=None) -> TaskRunner:
    return TaskRunner(
        settings,
        store,
        lambda: provider,
        LocalSandbox(),
        WorkspaceManager(settings.workspace_root_path),
        Redactor(),
        graph,
    )


# ---- Neo4j (integration tests skip when it is not reachable; start it with `docker compose up -d neo4j`) -----
# Test data lives under random repo ids and is deleted afterwards, so the development graph is left untouched.


def _neo4j_settings() -> Settings | None:
    try:
        s = Settings()  # reads .env like the real backend (NEO4J_PASSWORD, FORGE_NEO4J_URI)
    except Exception:
        return None
    return s if s.neo4j_password else None


@pytest.fixture(scope="session")
def neo4j_settings() -> Settings:
    s = _neo4j_settings()
    if s is None:
        pytest.skip("NEO4J_PASSWORD is not configured")
    from harnix.graph.store import GraphStore

    probe = GraphStore(s.neo4j_uri, s.neo4j_user, s.neo4j_password or "", s.neo4j_database)
    try:
        ok, detail = probe.health()
    finally:
        probe.close()
    if not ok:
        pytest.skip(f"Neo4j is not reachable: {detail}")
    return s


@pytest.fixture
def neo(neo4j_settings: Settings):
    """A GraphStore on the real database plus a list of repo ids to clean up afterwards."""
    from harnix.graph.store import GraphStore

    s = neo4j_settings
    store = GraphStore(s.neo4j_uri, s.neo4j_user, s.neo4j_password or "", s.neo4j_database, query_timeout=15)
    created: list[str] = []

    def new_repo_id() -> str:
        rid = f"test-{os.urandom(6).hex()}"
        created.append(rid)
        return rid

    store.new_repo_id = new_repo_id  # type: ignore[attr-defined]
    yield store
    for rid in created:
        store.drop_repo(rid)
    store.close()


def new_task(store: Store, repo: Path, settings: Settings, **kw):
    fields = dict(
        title="fix add",
        repo_path=str(repo),
        instructions="Fix the bug in mathutil.add",
        acceptance_criteria=[],
        verification_commands=[],
        max_iterations=settings.max_iterations,
        max_duration_seconds=settings.max_duration_seconds,
    )
    fields.update(kw)
    return store.create_task(**fields)


def wait_terminal(store: Store, task_id: str, timeout: float = 90):
    end = time.time() + timeout
    while time.time() < end:
        t = store.get_task(task_id)
        if TaskStatus(t.status) in TERMINAL:
            return t
        time.sleep(0.1)
    raise AssertionError(f"task {task_id} did not finish: {store.get_task(task_id).status}")


def wait_for(cond, timeout: float = 30):
    end = time.time() + timeout
    while time.time() < end:
        v = cond()
        if v:
            return v
        time.sleep(0.05)
    raise AssertionError("condition not met in time")


def call(name: str, **inp) -> dict:
    return {"name": name, "input": inp}


def step(text: str = "", *calls: dict) -> dict:
    return {"text": text, "tool_calls": list(calls)}
