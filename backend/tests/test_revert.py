"""Rolling back an applied task, and listing a repository's task history."""

from __future__ import annotations

from pathlib import Path

import pytest

from harnix.config import Settings
from harnix.execution.apply import ApplyConflict, apply_workspace_to_repo, revert_applied
from harnix.execution.workspace import WorkspaceManager

from .conftest import step
from .test_apply import FIX, finished_task, make_client


@pytest.fixture
def pair(settings: Settings, repo: Path):
    return WorkspaceManager(settings.workspace_root_path).create("r1", repo), repo


def applied(ws: Path, repo: Path):
    (ws / "mathutil.py").write_bytes(b"def add(a, b):\r\n    return a + b\r\n")
    (ws / "added.txt").write_text("new\n")
    (ws / "AGENTS.md").unlink()
    ch = apply_workspace_to_repo(ws, repo)
    return [(c.status, c.path) for c in ch]


def test_revert_restores_modified_added_and_deleted_files_exactly(pair):
    ws, repo = pair
    before = (repo / "mathutil.py").read_bytes()
    agents = (repo / "AGENTS.md").read_bytes()
    files = applied(ws, repo)
    assert not (repo / "AGENTS.md").exists() and (repo / "added.txt").exists()
    revert_applied(ws, repo, files)
    assert (repo / "mathutil.py").read_bytes() == before
    assert (repo / "AGENTS.md").read_bytes() == agents
    assert not (repo / "added.txt").exists()


def test_revert_refuses_everything_if_you_edited_a_file_since(pair):
    ws, repo = pair
    files = applied(ws, repo)
    (repo / "mathutil.py").write_text("my own edit\n")
    with pytest.raises(ApplyConflict) as e:
        revert_applied(ws, repo, files)
    assert any("mathutil.py: changed in your repository" in c for c in e.value.conflicts)
    assert (repo / "added.txt").exists() and (
        repo / "mathutil.py"
    ).read_text() == "my own edit\n"  # nothing touched


def test_revert_never_overwrites_a_recreated_deleted_file_and_dry_run_writes_nothing(pair):
    ws, repo = pair
    files = applied(ws, repo)
    plan = revert_applied(ws, repo, files, dry_run=True)
    assert {c.path for c in plan} == {"mathutil.py", "added.txt", "AGENTS.md"} and (
        repo / "added.txt"
    ).exists()
    (repo / "AGENTS.md").write_text("new file\n")
    with pytest.raises(ApplyConflict, match="exists again"):
        revert_applied(ws, repo, files)


def test_revert_refuses_unsafe_paths(pair):
    ws, repo = pair
    with pytest.raises(ApplyConflict, match="refused"):
        revert_applied(ws, repo, [("M", "../outside.txt")])
    with pytest.raises(ApplyConflict, match="refused"):
        revert_applied(ws, repo, [("M", ".env")])


def test_revert_through_the_api_and_apply_again(settings: Settings, repo: Path):
    with make_client(settings, [step("", FIX), step("fixed")]) as c:
        tid = finished_task(c, repo)
        assert (
            c.post(f"/api/tasks/{tid}/revert", json={"confirm": True}).status_code == 409
        )  # not applied yet
        assert c.get(f"/api/tasks/{tid}").json()["apply_state"] is None
        assert c.post(f"/api/tasks/{tid}/apply", json={"confirm": True}).status_code == 200
        assert "a + b" in (repo / "mathutil.py").read_text()
        assert c.get(f"/api/tasks/{tid}").json()["apply_state"] == "applied"
        assert c.post(f"/api/tasks/{tid}/revert", json={}).status_code == 422  # needs confirm or dry_run
        prev = c.post(f"/api/tasks/{tid}/revert", json={"dry_run": True}).json()
        assert prev["dry_run"] and [f["path"] for f in prev["files"]] == ["mathutil.py"]
        assert "a + b" in (repo / "mathutil.py").read_text()  # a preview writes nothing
        done = c.post(f"/api/tasks/{tid}/revert", json={"confirm": True})
        assert done.status_code == 200 and "a - b" in (repo / "mathutil.py").read_text()
        assert c.get(f"/api/tasks/{tid}").json()["apply_state"] == "reverted"
        assert c.post(f"/api/tasks/{tid}/revert", json={"confirm": True}).status_code == 409  # only once
        assert (
            c.post(f"/api/tasks/{tid}/apply", json={"confirm": True}).status_code == 200
        )  # apply is possible again
        assert c.get(f"/api/tasks/{tid}").json()["apply_state"] == "applied"


def test_revert_conflict_through_the_api_writes_nothing(settings: Settings, repo: Path):
    with make_client(settings, [step("", FIX), step("fixed")]) as c:
        tid = finished_task(c, repo)
        c.post(f"/api/tasks/{tid}/apply", json={"confirm": True})
        (repo / "mathutil.py").write_text("changed by me\n")
        r = c.post(f"/api/tasks/{tid}/revert", json={"confirm": True})
        assert r.status_code == 409 and "Nothing was written" in r.json()["error"]["message"]
        assert (repo / "mathutil.py").read_text() == "changed by me\n"


def test_history_is_filtered_by_repository_and_shows_apply_state(settings: Settings, repo: Path):
    other = settings.repo_roots[0] / "other"
    other.mkdir()
    (other / "x.py").write_text("x = 1\n")
    with make_client(settings, [step("", FIX), step("fixed"), step("nothing to do")]) as c:
        a = finished_task(c, repo)
        c.post(f"/api/tasks/{a}/apply", json={"confirm": True})
        b = finished_task(c, other)
        mine = c.get("/api/tasks", params={"repo_path": str(repo)}).json()
        assert [t["id"] for t in mine["items"]] == [a] and mine["total"] == 1
        assert mine["items"][0]["apply_state"] == "applied"
        both = {t["id"]: t["apply_state"] for t in c.get("/api/tasks").json()["items"]}
        assert both == {a: "applied", b: None}


def test_history_filter_accepts_another_spelling_of_the_same_folder(settings: Settings, repo: Path):
    with make_client(settings, [step("", FIX), step("fixed")]) as c:
        a = finished_task(c, repo)
        alt = str(repo / "." / ".." / repo.name)  # same folder, different spelling
        assert [t["id"] for t in c.get("/api/tasks", params={"repo_path": alt}).json()["items"]] == [a]
        outside = c.get(
            "/api/tasks", params={"repo_path": str(repo.parent.parent)}
        ).json()  # not an authorized folder
        assert outside["items"] == [] and outside["total"] == 0


def test_file_versions_for_side_by_side_review_even_after_apply(settings: Settings, repo: Path):
    with make_client(settings, [step("", FIX), step("fixed")]) as c:
        tid = finished_task(c, repo)
        c.post(
            f"/api/tasks/{tid}/apply", json={"confirm": True}
        )  # your file now equals the agent's: the baseline still differs
        base = c.get(f"/api/tasks/{tid}/file", params={"path": "mathutil.py", "version": "baseline"}).json()
        final = c.get(f"/api/tasks/{tid}/file", params={"path": "mathutil.py", "version": "final"}).json()
        assert base["exists"] and "a - b" in base["content"] and "a + b" not in base["content"]
        assert final["exists"] and "a + b" in final["content"]
        absent = c.get(f"/api/tasks/{tid}/file", params={"path": "nope.py", "version": "baseline"}).json()
        assert absent["exists"] is False and absent["content"] == ""


def test_file_versions_refuse_secrets_and_escapes(settings: Settings, repo: Path):
    with make_client(settings, [step("", FIX), step("fixed")]) as c:
        tid = finished_task(c, repo)
        for bad in (".env", "../outside.txt", "/etc/passwd", ".git/config"):
            r = c.get(f"/api/tasks/{tid}/file", params={"path": bad, "version": "final"})
            assert r.status_code == 422, (bad, r.status_code)
        assert c.get(f"/api/tasks/{tid}/file", params={"path": "x", "version": "other"}).status_code == 422


def test_binary_files_report_their_size_without_content(settings: Settings, repo: Path):
    (repo / "logo.bin").write_bytes(b"\x00\x01\x02binary" * 10)
    with make_client(settings, [step("", FIX), step("fixed")]) as c:
        tid = finished_task(c, repo)
        r = c.get(f"/api/tasks/{tid}/file", params={"path": "logo.bin", "version": "baseline"}).json()
        assert r["binary"] is True and r["content"] == "" and r["size"] == 90
        t = c.get(f"/api/tasks/{tid}/file", params={"path": "mathutil.py", "version": "final"}).json()
        assert t["binary"] is False and t["size"] == len(t["content"].encode())
