"""Alembic migrations run for real, in a separate process against a throwaway SQLite file."""

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
_FILL = {"INTEGER": 0, "FLOAT": 0.0, "BOOLEAN": 0, "DATETIME": "2026-01-01 00:00:00", "JSON": "[]"}


def _alembic(db: Path, *args: str) -> None:
    env = {
        **os.environ,
        "FORGE_DATABASE_URL": f"sqlite:///{db.as_posix()}",
        "FORGE_PROVIDER": "scripted",
        "FORGE_SCRIPTED_FILE": str(BACKEND / "examples" / "mock-script.json"),
        "FORGE_ALLOWED_REPO_ROOTS": str(db.parent),
        "FORGE_WORKSPACE_ROOT": str(db.parent / "ws"),
        "FORGE_KNOWLEDGE_VECTOR": "python",  # a SQLite file, whatever the developer's .env chose
    }
    done = subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=BACKEND,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert done.returncode == 0, done.stdout + done.stderr


def _columns(db: Path, table: str) -> dict[str, tuple[str, bool, object]]:
    with sqlite3.connect(db) as con:
        return {r[1]: (r[2].upper(), bool(r[3]), r[4]) for r in con.execute(f"PRAGMA table_info({table})")}


def _insert_minimal_task(db: Path) -> None:
    """A row as an older version would have stored it: every required column, nothing else."""
    cols = _columns(db, "tasks")
    values = {
        n: _FILL.get(t.split("(")[0], "x")
        for n, (t, required, default) in cols.items()
        if required and default is None
    }
    with sqlite3.connect(db) as con:
        con.execute(
            f"INSERT INTO tasks ({', '.join(values)}) VALUES ({', '.join('?' * len(values))})",
            list(values.values()),
        )


def test_references_column_is_added_keeps_old_rows_and_can_be_removed(tmp_path: Path):
    db = tmp_path / "m.db"
    _alembic(db, "upgrade", "0001")
    assert "references" not in _columns(db, "tasks")
    _insert_minimal_task(db)

    _alembic(db, "upgrade", "head")
    cols = _columns(db, "tasks")
    assert "references" in cols and cols["references"][1] is False  # nullable: old rows stay valid
    with sqlite3.connect(db) as con:
        assert con.execute('SELECT COUNT(*), COUNT("references") FROM tasks').fetchone() == (1, 0)

    _alembic(db, "downgrade", "0001")
    assert "references" not in _columns(db, "tasks")
    with sqlite3.connect(db) as con:
        assert con.execute("SELECT COUNT(*) FROM tasks").fetchone() == (
            1,
        )  # the row survived both directions


def test_skills_thinking_and_memory_tables_upgrade_and_downgrade(tmp_path: Path):
    db = tmp_path / "m3.db"
    _alembic(db, "upgrade", "0002")
    _insert_minimal_task(db)
    _alembic(db, "upgrade", "head")
    cols = _columns(db, "tasks")
    assert {"thinking", "skills"} <= set(cols) and not cols["thinking"][1] and not cols["skills"][1]
    mem = _columns(db, "memories")
    assert {"id", "scope", "content", "source", "task_id", "created_at"} == set(mem)
    with sqlite3.connect(db) as con:
        assert con.execute("SELECT COUNT(*), COUNT(thinking) FROM tasks").fetchone() == (
            1,
            0,
        )  # old rows stay valid
        idx = [r[1] for r in con.execute("PRAGMA index_list(memories)")]
        assert "ix_memories_scope_created" in idx
    _alembic(db, "downgrade", "0002")
    assert "thinking" not in _columns(db, "tasks")
    with sqlite3.connect(db) as con:
        assert con.execute("SELECT name FROM sqlite_master WHERE name='memories'").fetchone() is None
        assert con.execute("SELECT COUNT(*) FROM tasks").fetchone() == (1,)


def test_sessions_migration_gives_every_old_task_a_session_and_is_reversible(tmp_path: Path):
    db = tmp_path / "s.db"
    _alembic(db, "upgrade", "0004")
    assert "session_id" not in _columns(db, "tasks")
    _insert_minimal_task(db)
    with sqlite3.connect(db) as con:
        con.execute("UPDATE tasks SET id = 'older'")
    _insert_minimal_task(db)  # two old tasks

    _alembic(db, "upgrade", "head")
    cols = _columns(db, "tasks")
    assert {"session_id", "turn", "discarded", "base_ref", "checkpoint", "messages"} <= set(cols)
    with sqlite3.connect(db) as con:
        rows = con.execute("SELECT id, session_id, turn, discarded FROM tasks").fetchall()
        sessions = {r[0] for r in con.execute("SELECT id FROM chat_sessions")}
    assert len(rows) == 2 and all(
        r[1] in sessions and r[2] == 1 and not r[3] for r in rows
    )  # one session per old task
    assert len({r[1] for r in rows}) == 2

    _alembic(db, "downgrade", "0004")
    assert "session_id" not in _columns(db, "tasks")
    with sqlite3.connect(db) as con:
        assert con.execute("SELECT count(*) FROM tasks").fetchone()[0] == 2  # nothing lost going back
        assert not con.execute("SELECT name FROM sqlite_master WHERE name = 'chat_sessions'").fetchall()
    _alembic(db, "upgrade", "head")


def test_knowledge_migration_adds_tables_and_task_column_and_is_reversible(tmp_path: Path):
    db = tmp_path / "k.db"
    _alembic(db, "upgrade", "0005")
    assert "knowledge_project" not in _columns(db, "tasks")
    _insert_minimal_task(db)
    _alembic(db, "upgrade", "head")
    tables = {"knowledge_sources", "knowledge_versions", "knowledge_chunks", "knowledge_edges", "knowledge_audit", "context_bundles"}  # fmt: skip
    with sqlite3.connect(db) as con:
        assert tables <= {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        assert con.execute("SELECT knowledge_project FROM tasks").fetchall() == [
            (None,)
        ]  # old rows keep working
    assert "knowledge_project" in _columns(db, "tasks")
    assert _columns(db, "knowledge_edges")["status"][1]  # NOT NULL

    _alembic(db, "downgrade", "0005")
    assert "knowledge_project" not in _columns(db, "tasks")
    with sqlite3.connect(db) as con:
        assert con.execute("SELECT count(*) FROM tasks").fetchone()[0] == 1
        assert not tables & {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    _alembic(db, "upgrade", "head")
