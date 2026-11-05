"""Sessions: consecutive messages keep one conversation, one workspace and a checkpoint per response."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import pytest

from harnix.config import Settings
from harnix.harness.transcript import dump_messages, load_messages
from harnix.models.base import ImageBlock, Message, TextBlock, ToolResultBlock, ToolUseBlock

from .conftest import call, step, wait_for
from .test_apply import FIX, make_client

NOTES = call("write_file", path="notes.txt", content="hello from turn 2\n")
MORE = call("write_file", path="more.txt", content="third turn\n")


def turn(c, repo: Path, text: str, session_id: str | None = None, timeout: int = 120) -> dict[str, Any]:
    body: dict[str, Any] = {"repo_path": str(repo), "instructions": text}
    if session_id:
        body["session_id"] = session_id
    r = c.post("/api/tasks", json=body)
    assert r.status_code == 201, r.text
    t = r.json()
    assert c.post(f"/api/tasks/{t['id']}/run").status_code == 202
    wait_for(
        lambda: c.get(f"/api/tasks/{t['id']}").json()["status"] in ("completed", "failed", "cancelled"),
        timeout,
    )
    return c.get(f"/api/tasks/{t['id']}").json()


def texts(messages: list[Message]) -> list[tuple[str, str]]:
    return [(m.role, "\n".join(b.text for b in m.content if isinstance(b, TextBlock))) for m in messages]


class Seen:
    """Records the conversation the model is shown at the start of each turn."""

    def __init__(self) -> None:
        self.first: list[list[Message]] = []

    def at_start(self, reply: dict[str, Any]):
        def fn(messages: list[Message]) -> dict[str, Any]:
            self.first.append(list(messages))
            return reply

        return fn


def workspace_of(c, task: dict) -> Path:
    return Path(c.get(f"/api/tasks/{task['id']}").json()["workspace_path"])


# ---- the conversation and the workspace carry over -------------------------------------------------


def test_the_second_message_continues_the_conversation_and_the_workspace(settings: Settings, repo: Path):
    seen = Seen()
    steps = [
        seen.at_start(step("", FIX)),
        step("Fixed add() in mathutil.py."),
        seen.at_start(step("", NOTES)),
        step("Wrote notes.txt."),
    ]
    with make_client(settings, steps) as c:
        t1 = turn(c, repo, "fix the bug in add")
        t2 = turn(c, repo, "now also write a notes file", session_id=t1["session_id"])
        assert (t1["turn"], t2["turn"]) == (1, 2) and t1["session_id"] == t2["session_id"]
        # the model is shown the whole earlier exchange, then the new request
        convo = texts(seen.first[1])
        assert any("fix the bug in add" in x for r, x in convo if r == "user")
        assert any("Fixed add() in mathutil.py." in x for r, x in convo if r == "assistant")
        assert (
            convo[-1][0] == "user"
            and "# New request (turn 2 of this session)" in convo[-1][1]
            and "now also write a notes file" in convo[-1][1]
        )
        assert "mathutil.py" in convo[-1][1]  # it is told what is already changed
        # a fresh task would have seen none of that
        assert not any("Fixed add()" in x for _, x in texts(seen.first[0]))
        # the workspace was reused: turn 1's edit is there although nothing was applied
        assert workspace_of(c, t1) == workspace_of(c, t2)
        assert (
            "a + b" in (workspace_of(c, t2) / "mathutil.py").read_text()
            and "a - b" in (repo / "mathutil.py").read_text()
        )
        events = [e["type"] for e in c.get(f"/api/tasks/{t2['id']}/events", params={"after": 0}).json()]
        assert (
            "workspace.reused" in events
            and "session.continued" in events
            and "workspace.created" not in events
        )


def test_each_turn_shows_only_its_own_changes(settings: Settings, repo: Path):
    steps = [step("", FIX), step("fixed"), step("", NOTES), step("notes")]
    with make_client(settings, steps) as c:
        t1 = turn(c, repo, "fix add")
        t2 = turn(c, repo, "add notes", session_id=t1["session_id"])
        files1 = [f["path"] for f in c.get(f"/api/tasks/{t1['id']}/diff").json()["files"]]
        files2 = [f["path"] for f in c.get(f"/api/tasks/{t2['id']}/diff").json()["files"]]
        assert files1 == ["mathutil.py"] and files2 == ["notes.txt"]  # not cumulative
        # side-by-side: turn 2 left mathutil.py the way turn 1 did, and notes.txt did not exist before turn 2
        assert not c.get(
            f"/api/tasks/{t2['id']}/file", params={"path": "notes.txt", "version": "baseline"}
        ).json()["exists"]
        assert (
            "a + b"
            in c.get(
                f"/api/tasks/{t2['id']}/file", params={"path": "mathutil.py", "version": "baseline"}
            ).json()["content"]
        )


def test_a_new_task_without_a_session_starts_a_fresh_one(settings: Settings, repo: Path):
    seen = Seen()
    steps = [seen.at_start(step("", FIX)), step("fixed"), seen.at_start(step("nothing to do"))]
    with make_client(settings, steps) as c:
        a = turn(c, repo, "first conversation")
        b = turn(c, repo, "second conversation")
        assert a["session_id"] != b["session_id"] and b["turn"] == 1
        assert not any("first conversation" in x for _, x in texts(seen.first[1]))


# ---- applying and reverting turn by turn -------------------------------------------------------------


def test_apply_and_revert_follow_the_turns(settings: Settings, repo: Path):
    steps = [step("", FIX), step("fixed"), step("", NOTES), step("notes")]
    with make_client(settings, steps) as c:
        t1 = turn(c, repo, "fix add")
        t2 = turn(c, repo, "add notes", session_id=t1["session_id"])
        assert c.post(f"/api/tasks/{t1['id']}/apply", json={"confirm": True}).status_code == 200
        preview = c.post(f"/api/tasks/{t2['id']}/apply", json={"dry_run": True}).json()
        assert [f["path"] for f in preview["files"]] == [
            "notes.txt"
        ]  # only what turn 1 did not already deliver
        assert c.post(f"/api/tasks/{t2['id']}/apply", json={"confirm": True}).status_code == 200
        assert "a + b" in (repo / "mathutil.py").read_text() and (repo / "notes.txt").exists()
        # an earlier turn cannot be reverted underneath a later one
        r = c.post(f"/api/tasks/{t1['id']}/revert", json={"confirm": True})
        assert r.status_code == 409 and "later turn" in r.json()["error"]["message"]
        assert c.post(f"/api/tasks/{t2['id']}/revert", json={"confirm": True}).status_code == 200
        assert not (repo / "notes.txt").exists() and "a + b" in (repo / "mathutil.py").read_text()
        assert c.post(f"/api/tasks/{t1['id']}/revert", json={"confirm": True}).status_code == 200
        assert "a - b" in (repo / "mathutil.py").read_text()  # back to the original


def test_applying_a_later_turn_delivers_everything_not_yet_applied(settings: Settings, repo: Path):
    steps = [step("", FIX), step("fixed"), step("", NOTES), step("notes")]
    with make_client(settings, steps) as c:
        t1 = turn(c, repo, "fix add")
        t2 = turn(c, repo, "add notes", session_id=t1["session_id"])
        done = c.post(f"/api/tasks/{t2['id']}/apply", json={"confirm": True}).json()
        assert sorted(f["path"] for f in done["files"]) == ["mathutil.py", "notes.txt"]
        assert "a + b" in (repo / "mathutil.py").read_text() and (repo / "notes.txt").exists()
        # turn 1 is now already in the repository: applying it again would change nothing
        again = c.post(f"/api/tasks/{t1['id']}/apply", json={"dry_run": True})
        assert again.status_code in (200, 409)


def test_apply_still_refuses_files_you_edited_yourself(settings: Settings, repo: Path):
    steps = [step("", FIX), step("fixed"), step("", NOTES), step("notes")]
    with make_client(settings, steps) as c:
        t1 = turn(c, repo, "fix add")
        t2 = turn(c, repo, "add notes", session_id=t1["session_id"])
        assert c.post(f"/api/tasks/{t1['id']}/apply", json={"confirm": True}).status_code == 200
        (repo / "notes.txt").write_text(
            "my own notes\n"
        )  # you created the file the agent is about to deliver
        r = c.post(f"/api/tasks/{t2['id']}/apply", json={"confirm": True})
        assert r.status_code == 409 and "already exists" in r.json()["error"]["message"]
        assert (repo / "notes.txt").read_text() == "my own notes\n"


# ---- going back to an earlier response -----------------------------------------------------------------


def test_restore_returns_to_an_earlier_response_and_the_conversation_follows(settings: Settings, repo: Path):
    seen = Seen()
    steps = [
        step("", FIX), step("fixed"),
        step("", NOTES), step("notes"),
        step("", MORE), step("more"),
        seen.at_start(step("", MORE)), step("redone"),
    ]  # fmt: skip
    with make_client(settings, steps) as c:
        t1 = turn(c, repo, "fix add")
        sid = t1["session_id"]
        t2 = turn(c, repo, "write the notes", session_id=sid)
        t3 = turn(c, repo, "write more", session_id=sid)
        ws = workspace_of(c, t1)
        assert (ws / "notes.txt").exists() and (ws / "more.txt").exists()
        assert (
            c.post(f"/api/sessions/{sid}/restore", json={"turn": 1}).status_code == 422
        )  # needs confirm or dry_run
        preview = c.post(f"/api/sessions/{sid}/restore", json={"turn": 1, "dry_run": True}).json()
        assert not preview["restored"] and sorted(f["path"] for f in preview["files"]) == [
            "more.txt",
            "notes.txt",
        ]
        assert (ws / "notes.txt").exists()  # a preview changes nothing
        done = c.post(f"/api/sessions/{sid}/restore", json={"turn": 1, "confirm": True}).json()
        assert done["restored"] and sorted(done["discarded_tasks"]) == sorted([t2["id"], t3["id"]])
        assert (
            not (ws / "notes.txt").exists()
            and not (ws / "more.txt").exists()
            and "a + b" in (ws / "mathutil.py").read_text()
        )
        detail = c.get(f"/api/sessions/{sid}").json()
        assert [(t["turn"], t["discarded"]) for t in detail["items"]] == [(1, False), (2, True), (3, True)]
        assert detail["turns"] == 1
        # the next message continues from turn 1: the model has never heard of turns 2 and 3
        t4 = turn(c, repo, "try again differently", session_id=sid)
        assert t4["turn"] == 4
        convo = " ".join(x for _, x in texts(seen.first[0]))
        assert "fix add" in convo and "write the notes" not in convo and "write more" not in convo
        assert (
            c.post(f"/api/tasks/{t2['id']}/apply", json={"confirm": True}).status_code == 409
        )  # a rolled-back turn
        # and all the way back to the start
        c.post(f"/api/sessions/{sid}/restore", json={"turn": 0, "confirm": True})
        assert "a - b" in (ws / "mathutil.py").read_text() and not (ws / "more.txt").exists()
        assert c.post(f"/api/sessions/{sid}/restore", json={"turn": 9, "dry_run": True}).status_code == 404


# ---- guards ---------------------------------------------------------------------------------------------


def test_session_guards(settings: Settings, repo: Path, tmp_path: Path):
    gate, started = threading.Event(), threading.Event()

    def block(_messages):
        started.set()
        gate.wait(30)
        return step("done")

    other = settings.repo_roots[0] / "other"
    other.mkdir()
    (other / "x.py").write_text("x = 1\n")
    with make_client(settings, [step("", FIX), step("fixed"), block, step("late")]) as c:
        t1 = turn(c, repo, "fix add")
        sid = t1["session_id"]
        assert (
            c.post(
                "/api/tasks", json={"repo_path": str(repo), "instructions": "x", "session_id": "0" * 16}
            ).status_code
            == 404
        )
        wrong = c.post("/api/tasks", json={"repo_path": str(other), "instructions": "x", "session_id": sid})
        assert wrong.status_code == 422 and "another repository" in wrong.json()["error"]["message"]
        t2 = c.post(
            "/api/tasks", json={"repo_path": str(repo), "instructions": "slow one", "session_id": sid}
        ).json()
        assert c.post(f"/api/tasks/{t2['id']}/run").status_code == 202
        assert started.wait(30)
        busy = c.post(
            "/api/tasks", json={"repo_path": str(repo), "instructions": "impatient", "session_id": sid}
        )
        assert busy.status_code == 409 and "previous message" in busy.json()["error"]["message"]
        assert c.post(f"/api/sessions/{sid}/restore", json={"turn": 0, "confirm": True}).status_code == 409
        gate.set()
        wait_for(lambda: c.get(f"/api/tasks/{t2['id']}").json()["status"] in ("completed", "failed"), 60)


def test_a_turn_that_ended_early_is_summarized_for_the_next_one(settings: Settings, repo: Path):
    seen = Seen()
    steps = [{"error": "provider exploded", "retryable": False}, seen.at_start(step("", FIX)), step("fixed")]
    with make_client(settings, steps) as c:
        t1 = turn(c, repo, "first attempt")
        assert t1["status"] == "failed"
        t2 = turn(c, repo, "please continue", session_id=t1["session_id"])
        assert t2["status"] == "completed"
        convo = texts(seen.first[0])
        assert any("first attempt" in x for r, x in convo if r == "user")
        assert any("Turn 1 ended early" in x for r, x in convo if r == "assistant")


# ---- the sessions API -----------------------------------------------------------------------------------


def test_sessions_list_detail_rename_and_archive(settings: Settings, repo: Path):
    other = settings.repo_roots[0] / "other"
    other.mkdir()
    (other / "x.py").write_text("x = 1\n")
    steps = [step("", FIX), step("fixed"), step("", NOTES), step("notes"), step("nothing")]
    with make_client(settings, steps) as c:
        t1 = turn(c, repo, "fix add\nwith a second line")
        turn(c, repo, "add notes", session_id=t1["session_id"])
        turn(c, other, "look around")
        mine = c.get("/api/sessions", params={"repo_path": str(repo)}).json()
        assert mine["total"] == 1
        s = mine["items"][0]
        assert (
            s["id"] == t1["session_id"]
            and s["turns"] == 2
            and s["last_prompt"] == "add notes"
            and s["last_status"] == "completed"
        )
        assert c.get("/api/sessions").json()["total"] == 2
        detail = c.get(f"/api/sessions/{s['id']}").json()
        assert [t["turn"] for t in detail["items"]] == [1, 2] and detail["items"][0][
            "instructions"
        ].startswith("fix add")
        assert c.get("/api/sessions/" + "0" * 16).status_code == 404
        assert c.patch(f"/api/sessions/{s['id']}", json={"title": ""}).status_code == 422
        renamed = c.patch(f"/api/sessions/{s['id']}", json={"title": "Fixing add()"}).json()
        assert renamed["title"] == "Fixing add()"
        c.patch(f"/api/sessions/{s['id']}", json={"archived": True})
        assert c.get("/api/sessions", params={"repo_path": str(repo)}).json()["total"] == 0
        assert c.get("/api/sessions", params={"repo_path": str(repo), "archived": True}).json()["total"] == 1
        assert (
            c.get("/api/sessions", params={"repo_path": str(repo.parent.parent)}).json()["total"] == 0
        )  # not authorized


# ---- saving a conversation ---------------------------------------------------------------------------------


def test_a_saved_conversation_keeps_what_the_model_saw_and_drops_provider_payloads():
    img = ImageBlock("image/png", "QUJD", "a.png", "high", ref="abc:1")
    history = [
        Message("user", [TextBlock("task"), img]),
        Message(
            "assistant",
            [TextBlock("looking"), ToolUseBlock("t1", "read_file", {"path": "x"})],
            raw=[object()],
        ),
        Message("user", [ToolResultBlock("t1", "contents", False)]),
        Message("assistant", [TextBlock("done")]),
    ]
    saved = dump_messages(history)
    import json

    json.dumps(saved)  # plain JSON: raw SDK objects are not in it
    assert saved[0]["content"][1] == {
        "t": "image",
        "ref": "abc:1",
        "name": "a.png",
        "detail": "high",
    }  # a reference, not bytes
    loaded = load_messages(saved, lambda ref: ImageBlock("image/png", "QUJD", "a.png", "high", ref=ref))
    assert [m.role for m in loaded] == ["user", "assistant", "user", "assistant"]
    assert isinstance(loaded[0].content[1], ImageBlock) and loaded[0].content[1].data == "QUJD"
    assert isinstance(loaded[1].content[1], ToolUseBlock) and loaded[1].raw is None
    assert isinstance(loaded[2].content[0], ToolResultBlock) and loaded[2].content[0].content == "contents"
    gone = load_messages(saved, lambda ref: None)  # the image is no longer stored: the text says so
    assert "no longer available" in gone[0].content[1].text  # type: ignore[union-attr]


def test_an_unsaveable_image_without_a_reference_leaves_a_note():
    saved = dump_messages([Message("user", [ImageBlock("image/png", "QUJD", "loose.png")])])
    assert saved[0]["content"][0]["t"] == "text" and "loose.png" in saved[0]["content"][0]["text"]


@pytest.mark.parametrize(
    "bad", [None, [], [{"role": "system", "content": []}], [{"role": "user", "content": [{"t": "???"}]}]]
)
def test_loading_tolerates_garbage(bad):
    assert load_messages(bad, lambda r: None) == []  # type: ignore[arg-type]
