"""Applying a finished task's changes to the real repository: strictness is the feature."""

from __future__ import annotations

import os
import pathlib
import shutil
import stat
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from harnix.api.app import create_app
from harnix.config import Settings
from harnix.execution import git
from harnix.execution.apply import ApplyConflict, Change, apply_workspace_to_repo, unsafe_reason
from harnix.execution.workspace import WorkspaceManager
from harnix.models.scripted import ScriptedProvider

from .conftest import call, step, wait_for
from .test_policies import _dir_link

FIX = call("edit_file", path="mathutil.py", old_text="a - b", new_text="a + b")


@pytest.fixture
def pair(settings: Settings, repo: Path):
    ws = WorkspaceManager(settings.workspace_root_path).create("t1", repo)
    return ws, repo


def by_path(changes: list[Change]) -> dict[str, str]:
    return {c.path: c.status for c in changes}


def test_applies_add_modify_delete_with_exact_bytes(pair):
    ws, repo = pair
    (ws / "mathutil.py").write_bytes(b"def add(a, b):\r\n    return a + b\r\n")  # CRLF must survive exactly
    (ws / "new" / "deep").mkdir(parents=True)
    (ws / "new" / "deep" / "bin.dat").write_bytes(b"\x00\x01\xfe\xff binary \x00")
    (ws / "crlf.txt").write_bytes(b"one\r\ntwo\r\n")
    (ws / "AGENTS.md").unlink()
    changes = apply_workspace_to_repo(ws, repo)
    assert by_path(changes) == {
        "mathutil.py": "M", "new/deep/bin.dat": "A", "crlf.txt": "A", "AGENTS.md": "D",
    }  # fmt: skip
    assert (repo / "mathutil.py").read_bytes() == b"def add(a, b):\r\n    return a + b\r\n"
    assert (repo / "new" / "deep" / "bin.dat").read_bytes() == b"\x00\x01\xfe\xff binary \x00"
    assert (repo / "crlf.txt").read_bytes() == b"one\r\ntwo\r\n"
    assert not (repo / "AGENTS.md").exists()
    assert (repo / ".env").read_text().startswith("API_KEY=")  # untouched secret file stays untouched


def test_unchanged_workspace_changes_nothing(pair):
    ws, repo = pair
    assert apply_workspace_to_repo(ws, repo) == []


def test_dry_run_reports_plan_and_writes_nothing(pair):
    ws, repo = pair
    (ws / "mathutil.py").write_text("changed\n")
    (ws / "extra.txt").write_text("x")
    changes = apply_workspace_to_repo(ws, repo, dry_run=True)
    assert by_path(changes) == {"mathutil.py": "M", "extra.txt": "A"}
    assert (repo / "mathutil.py").read_text().startswith("def add") and not (repo / "extra.txt").exists()


def test_repository_changes_since_the_task_started_are_never_overwritten_and_nothing_is_written(pair):
    ws, repo = pair
    (ws / "mathutil.py").write_text("agent version\n")
    (ws / "test_mathutil.py").write_text("agent test version\n")
    (repo / "mathutil.py").write_text("the user edited this meanwhile\n")  # real conflict
    with pytest.raises(ApplyConflict) as e:
        apply_workspace_to_repo(ws, repo)
    assert any("mathutil.py" in c and "changed in your repository" in c for c in e.value.conflicts)
    assert (repo / "mathutil.py").read_text() == "the user edited this meanwhile\n"
    assert (repo / "test_mathutil.py").read_text().startswith("from mathutil")  # all-or-nothing


def test_added_file_that_now_exists_and_modified_file_that_vanished_conflict(pair):
    ws, repo = pair
    (ws / "brand_new.py").write_text("agent\n")
    (ws / "test_mathutil.py").write_text("agent edit\n")
    (repo / "brand_new.py").write_text("user created the same name\n")
    (repo / "test_mathutil.py").unlink()
    with pytest.raises(ApplyConflict) as e:
        apply_workspace_to_repo(ws, repo)
    text = " | ".join(e.value.conflicts)
    assert "brand_new.py: already exists" in text and "test_mathutil.py: no longer exists" in text
    assert (repo / "brand_new.py").read_text() == "user created the same name\n"


def test_deleting_something_the_user_already_deleted_is_fine(pair):
    ws, repo = pair
    (ws / "AGENTS.md").unlink()
    (repo / "AGENTS.md").unlink()
    assert by_path(apply_workspace_to_repo(ws, repo)) == {"AGENTS.md": "D"}


@pytest.mark.parametrize(
    "rel,expected",
    [
        ("src/a.py", None),
        (".env.example", None),
        (".env", "secret"),
        ("config/.env.production", "secret"),
        ("keys/id_rsa", "secret"),
        ("certs/server.pem", "secret"),
        (".ssh/config", "secret"),
        (".git/hooks/pre-commit", "protected"),
        ("a/.git/config", "protected"),
        ("../outside.txt", "escapes"),
        ("a/../../outside.txt", "escapes"),
        ("/etc/passwd", "escapes"),
        ("C:/Windows/x.txt", "escapes"),
        ("C:evil.txt", "escapes"),
        ("", "empty"),
    ],
)
def test_unsafe_paths_are_classified(rel, expected):
    why = unsafe_reason(rel)
    assert (why is None) if expected is None else (why is not None and expected in why)


def test_secret_files_created_by_the_agent_are_refused(pair):
    ws, repo = pair
    (ws / ".env.local").write_text("TOKEN=abc")
    (ws / "ok.txt").write_text("fine")
    with pytest.raises(ApplyConflict) as e:
        apply_workspace_to_repo(ws, repo)
    assert any(".env.local" in c and "secret" in c for c in e.value.conflicts)
    assert not (repo / "ok.txt").exists() and not (repo / ".env.local").exists()


def test_cannot_write_through_a_link_inside_the_repository(pair, tmp_path: Path):
    ws, repo = pair
    outside = tmp_path / "outside"
    outside.mkdir()
    _dir_link(repo / "linked", outside)  # repo has a link pointing outside; the workspace copy skipped it
    (ws / "linked").mkdir()
    (ws / "linked" / "payload.txt").write_text("should never be written outside")
    with pytest.raises(ApplyConflict) as e:
        apply_workspace_to_repo(ws, repo)
    assert any("outside the repository" in c for c in e.value.conflicts)
    assert list(outside.iterdir()) == []


def test_failed_write_is_rolled_back(pair, monkeypatch):
    ws, repo = pair
    (ws / "mathutil.py").write_text("agent version\n")
    (ws / "zz_new.txt").write_text("new file\n")
    orig = pathlib.Path.write_bytes

    def flaky(self, data):
        if self.name == "zz_new.txt":
            raise OSError("disk full (simulated)")
        return orig(self, data)

    monkeypatch.setattr(pathlib.Path, "write_bytes", flaky)
    with pytest.raises(ApplyConflict, match="rolled back"):
        apply_workspace_to_repo(ws, repo)
    monkeypatch.setattr(pathlib.Path, "write_bytes", orig)
    assert (repo / "mathutil.py").read_text().startswith("def add")  # first write was undone
    assert not (repo / "zz_new.txt").exists()


def test_baseline_stores_exact_bytes_even_with_eol_attributes(settings: Settings, repo: Path):
    (repo / ".gitattributes").write_text("* text=auto eol=lf\n")
    (repo / "windows.txt").write_bytes(b"a\r\nb\r\n")
    ws = WorkspaceManager(settings.workspace_root_path).create("eol", repo)
    assert git.baseline_bytes(ws, "windows.txt") == b"a\r\nb\r\n"  # not normalized to LF
    (ws / "windows.txt").write_bytes(b"a\r\nB\r\n")
    assert by_path([Change(p, s) for s, p in git.changed_paths_raw(ws)]) == {"windows.txt": "M"}
    apply_workspace_to_repo(ws, repo)
    assert (repo / "windows.txt").read_bytes() == b"a\r\nB\r\n"


def test_unicode_and_space_paths(pair):
    ws, repo = pair
    (ws / "dir with space").mkdir()
    (ws / "dir with space" / "naïve-файл.txt").write_text("héllo", encoding="utf-8")
    changes = apply_workspace_to_repo(ws, repo)
    assert by_path(changes) == {"dir with space/naïve-файл.txt": "A"}
    assert (repo / "dir with space" / "naïve-файл.txt").read_text(encoding="utf-8") == "héllo"


# ---- through the HTTP API ---------------------------------------------------------------------


def make_client(settings: Settings, steps) -> TestClient:
    provider = ScriptedProvider(steps)
    return TestClient(create_app(settings, provider_factory=lambda: provider, create_schema=True))


def finished_task(c: TestClient, repo: Path, steps_done: bool = True) -> str:
    t = c.post("/api/tasks", json={"repo_path": str(repo), "instructions": "fix it"}).json()
    assert c.post(f"/api/tasks/{t['id']}/run").status_code == 202
    wait_for(lambda: c.get(f"/api/tasks/{t['id']}").json()["status"] in ("completed", "failed"), 120)
    return t["id"]


def test_apply_flow_over_http(settings: Settings, repo: Path):
    with make_client(settings, [step("fix", FIX), step("done")]) as c:
        tid = finished_task(c, repo)
        assert c.get(f"/api/tasks/{tid}").json()["outcome"] == "verified"
        r = c.post(f"/api/tasks/{tid}/apply", json={})
        assert r.status_code == 422 and "confirm=true" in r.json()["error"]["message"]
        assert (repo / "mathutil.py").read_text().count("a - b") == 1  # nothing happened

        dry = c.post(f"/api/tasks/{tid}/apply", json={"dry_run": True}).json()
        assert dry == {
            "applied": False,
            "dry_run": True,
            "files": [{"path": "mathutil.py", "status": "M"}],
            "verified": True,
        }
        assert (repo / "mathutil.py").read_text().count("a - b") == 1

        done = c.post(f"/api/tasks/{tid}/apply", json={"confirm": True})
        assert done.status_code == 200 and done.json()["applied"] is True
        assert "a + b" in (repo / "mathutil.py").read_text()
        events = [e["type"] for e in c.get(f"/api/tasks/{tid}/events").json()]
        assert events[-1] == "task.applied"

        again = c.post(f"/api/tasks/{tid}/apply", json={"confirm": True})
        assert again.status_code == 409 and "already applied" in again.json()["error"]["message"]


def test_apply_conflict_leaves_the_repository_untouched(settings: Settings, repo: Path):
    with make_client(settings, [step("fix", FIX), step("done")]) as c:
        tid = finished_task(c, repo)
        (repo / "mathutil.py").write_text("def add(a, b):\n    return 42  # user's own edit\n")
        r = c.post(f"/api/tasks/{tid}/apply", json={"confirm": True})
        assert r.status_code == 409 and "Nothing was written" in r.json()["error"]["message"]
        assert "user's own edit" in (repo / "mathutil.py").read_text()
        assert "task.applied" not in [e["type"] for e in c.get(f"/api/tasks/{tid}/events").json()]


def test_only_completed_tasks_can_be_applied(settings: Settings, repo: Path):
    with make_client(settings, [{"error": "invalid api key"}]) as c:
        queued = c.post("/api/tasks", json={"repo_path": str(repo), "instructions": "x"}).json()["id"]
        assert c.post(f"/api/tasks/{queued}/apply", json={"confirm": True}).status_code == 409
        failed = finished_task(c, repo)
        r = c.post(f"/api/tasks/{failed}/apply", json={"confirm": True})
        assert r.status_code == 409 and "failed" in r.json()["error"]["message"]
        assert c.post("/api/tasks/nope/apply", json={"confirm": True}).status_code == 404


def test_unverified_result_is_flagged(settings: Settings, repo: Path):
    (repo / "test_mathutil.py").unlink()  # nothing to verify with
    with make_client(settings, [step("fix", FIX), step("done")]) as c:
        tid = finished_task(c, repo)
        assert c.get(f"/api/tasks/{tid}").json()["outcome"] == "unverified"
        r = c.post(f"/api/tasks/{tid}/apply", json={"confirm": True})
        assert r.status_code == 200 and r.json()["verified"] is False


def _rmtree(path: Path) -> None:
    """rmtree that also removes the read-only git object files (Windows)."""

    def onerror(func, p, _exc):
        os.chmod(p, stat.S_IWRITE)
        func(p)

    shutil.rmtree(path, onerror=onerror)


def test_apply_refuses_when_the_workspace_is_gone(settings: Settings, repo: Path):
    with make_client(settings, [step("fix", FIX), step("done")]) as c:
        tid = finished_task(c, repo)
        _rmtree(Path(c.get(f"/api/tasks/{tid}").json()["workspace_path"]))  # the session's workspace
        r = c.post(f"/api/tasks/{tid}/apply", json={"confirm": True})
        assert r.status_code == 409 and "workspace no longer exists" in r.json()["error"]["message"]
        assert "a - b" in (repo / "mathutil.py").read_text()


def test_apply_refuses_a_damaged_workspace_with_a_plain_message(settings: Settings, repo: Path):
    with make_client(settings, [step("fix", FIX), step("done")]) as c:
        tid = finished_task(c, repo)
        _rmtree(
            Path(c.get(f"/api/tasks/{tid}").json()["workspace_path"]) / ".git"
        )  # directory still there, repository destroyed
        r = c.post(f"/api/tasks/{tid}/apply", json={"confirm": True})
        assert r.status_code == 409
        assert (
            "workspace is unusable" in r.json()["error"]["message"]
            and "nothing was written" in r.json()["error"]["message"]
        )
        assert "a - b" in (repo / "mathutil.py").read_text()


def test_apply_rechecks_that_the_repository_is_still_authorized(
    settings: Settings, repo: Path, tmp_path: Path
):
    with make_client(settings, [step("fix", FIX), step("done")]) as c:
        tid = finished_task(c, repo)
    elsewhere = tmp_path / "other-root"
    elsewhere.mkdir()
    narrowed = settings.model_copy(
        update={"allowed_repo_roots": str(elsewhere)}
    )  # operator changed the allowlist
    with TestClient(create_app(narrowed, provider_factory=lambda: ScriptedProvider([]))) as c2:
        r = c2.post(f"/api/tasks/{tid}/apply", json={"confirm": True})
        assert r.status_code == 422 and "authorized" in r.json()["error"]["message"]
    assert "a - b" in (repo / "mathutil.py").read_text()
