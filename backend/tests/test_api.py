"""API integration tests: HTTP -> runner -> real tools/verification, with only the model scripted."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from harnix.api.app import create_app
from harnix.config import ConfigError, Settings
from harnix.models.scripted import ScriptedProvider

from .conftest import call, step, wait_for

FIX = call("edit_file", path="mathutil.py", old_text="a - b", new_text="a + b")


def make_client(settings: Settings, steps=None, **kw) -> TestClient:
    provider = ScriptedProvider(steps or [])
    app = create_app(settings, provider_factory=lambda: provider, create_schema=True, **kw)
    return TestClient(app)


def create(client: TestClient, repo: Path, **kw) -> dict:
    body = {"repo_path": str(repo), "instructions": "Fix the bug in mathutil.add", **kw}
    r = client.post("/api/tasks", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def poll(client: TestClient, task_id: str, until=("completed", "failed", "cancelled"), timeout=90) -> dict:
    return wait_for(
        lambda: (t := client.get(f"/api/tasks/{task_id}").json())["status"] in until and t, timeout
    )


def test_health_and_config(settings: Settings):
    with make_client(settings) as c:
        h = c.get("/api/health").json()
        assert h["status"] == "ok" and h["execution_mode"] == "local"
        assert h["sandbox"]["available"] and h["provider"]["configured"] and h["mock_provider"] is True
        cfg = c.get("/api/config").json()
        assert cfg["allowed_repo_roots"] == [str(settings.repo_roots[0])]
        assert "api_key" not in json.dumps(cfg).lower()


def test_unconfigured_provider_gives_guidance(settings: Settings, repo: Path):
    s = settings.model_copy(update={"provider": "anthropic", "anthropic_api_key": None})
    with make_client(s) as c:
        h = c.get("/api/health").json()
        assert h["provider"]["configured"] is False and "ANTHROPIC_API_KEY" in h["provider"]["problem"]
        t = create(c, repo)
        r = c.post(f"/api/tasks/{t['id']}/run")
        assert r.status_code == 503 and r.json()["error"]["code"] == "not_configured"
        assert "ANTHROPIC_API_KEY" in r.json()["error"]["message"]
        assert c.get(f"/api/tasks/{t['id']}").json()["status"] == "queued"


def test_missing_schema_fails_early(settings: Settings):
    with pytest.raises(ConfigError, match="alembic upgrade head"):
        create_app(settings)


def test_app_factory_exits_with_one_line_on_config_error(settings: Settings, monkeypatch):
    from harnix.api import app as app_module

    monkeypatch.setattr(app_module, "get_settings", lambda: settings)  # points at an unmigrated database
    with pytest.raises(SystemExit, match="Forge configuration error: .*alembic upgrade head"):
        app_module.app_factory()


def test_create_validation(settings: Settings, repo: Path, tmp_path: Path):
    with make_client(settings) as c:
        r = c.post("/api/tasks", json={"repo_path": str(repo)})
        assert r.status_code == 422 and r.json()["error"]["code"] == "validation_error"
        assert r.json()["error"]["details"][0]["field"] == "instructions"
        assert (
            c.post("/api/tasks", json={"repo_path": str(repo), "instructions": "x", "evil": 1}).status_code
            == 422
        )
        bad_cmd = {"repo_path": str(repo), "instructions": "x", "verification_commands": ["pytest | tee x"]}
        assert c.post("/api/tasks", json=bad_cmd).status_code == 422
        other = tmp_path / "not-authorized"
        other.mkdir()
        r = c.post("/api/tasks", json={"repo_path": str(other), "instructions": "x"})
        assert r.status_code == 422 and "authorized" in r.json()["error"]["message"]
        r = c.post("/api/tasks", json={"repo_path": str(repo / ".." / ".." / "x"), "instructions": "x"})
        assert r.status_code == 422
        r = c.post("/api/tasks", json={"repo_path": str(repo), "instructions": "x", "max_iterations": 999})
        assert r.status_code == 422 and "server limit" in r.json()["error"]["message"]


def test_create_get_list_and_404(settings: Settings, repo: Path):
    with make_client(settings) as c:
        t = create(c, repo, title="my task", acceptance_criteria=["works"], max_iterations=5)
        assert t["status"] == "queued" and t["title"] == "my task" and t["max_iterations"] == 5
        assert t["created_at"].endswith("Z") and t["started_at"] is None
        auto = create(c, repo, instructions="First line title\nmore")
        assert auto["title"] == "First line title"
        assert c.get(f"/api/tasks/{t['id']}").json()["acceptance_criteria"] == ["works"]
        lst = c.get("/api/tasks?limit=1").json()
        assert lst["total"] == 2 and len(lst["items"]) == 1 and lst["limit"] == 1
        assert c.get("/api/tasks?limit=1&offset=1").json()["items"][0]["id"] != lst["items"][0]["id"]
        assert c.get("/api/tasks?status=queued").json()["total"] == 2
        assert c.get("/api/tasks?status=completed").json()["total"] == 0
        assert c.get("/api/tasks?status=bogus").status_code == 422
        assert c.get("/api/tasks?limit=0").status_code == 422
        for url in [
            "/api/tasks/nope",
            "/api/tasks/nope/events",
            "/api/tasks/nope/diff",
            "/api/tasks/nope/approvals",
        ]:
            r = c.get(url)
            assert r.status_code == 404 and r.json()["error"]["code"] == "not_found", url
        assert c.post("/api/tasks/nope/run").status_code == 404
        assert c.post("/api/tasks/nope/cancel").status_code == 404
        assert c.get(f"/api/tasks/{t['id']}/report").status_code == 409  # not finished yet


def test_full_run_through_api(settings: Settings, repo: Path):
    steps = [step("fix", FIX), step("testing", call("run_tests")), step("Fixed.")]
    with make_client(settings, steps) as c:
        t = create(c, repo)
        r = c.post(f"/api/tasks/{t['id']}/run")
        assert r.status_code == 202 and r.json()["status"] == "running"
        assert c.post(f"/api/tasks/{t['id']}/run").status_code == 409  # duplicate execution request
        done = poll(c, t["id"])
        assert done["status"] == "completed" and done["outcome"] == "verified"
        assert (
            done["duration_seconds"] > 0 and done["tool_calls"] == 2 and done["provider"] == "scripted-mock"
        )
        assert done["report"]["changed_files"][0]["path"] == "mathutil.py"

        assert c.post(f"/api/tasks/{t['id']}/run").status_code == 409
        assert c.post(f"/api/tasks/{t['id']}/cancel").status_code == 409  # already terminal

        diff = c.get(f"/api/tasks/{t['id']}/diff").json()
        assert (
            diff["live"] is False
            and "+    return a + b" in diff["diff"]
            and diff["files"][0]["additions"] == 1
        )

        v = c.get(f"/api/tasks/{t['id']}/verifications").json()
        assert [(x["phase"], x["status"]) for x in v if x["kind"] == "test"] == [
            ("baseline", "failed"),
            ("final", "passed"),
        ]
        assert [(x["phase"], x["status"]) for x in v if x["kind"] == "syntax"] == [
            ("final", "passed")
        ]  # changed .py file

        ev = c.get(f"/api/tasks/{t['id']}/events").json()
        ids = [e["id"] for e in ev]
        assert ids == sorted(ids) and ev[0]["type"] == "task.created" and ev[-1]["type"] == "task.finished"
        later = c.get(f"/api/tasks/{t['id']}/events?after={ids[3]}").json()
        assert [e["id"] for e in later] == ids[4:]

        rep = c.get(f"/api/tasks/{t['id']}/report").json()
        assert rep["outcome"] == "verified"
        md = c.get(f"/api/tasks/{t['id']}/report?format=md")
        assert (
            md.headers["content-type"].startswith("text/markdown")
            and "## Independent verification" in md.text
        )
        assert c.get(f"/api/tasks/{t['id']}/report?format=xml").status_code == 422

        rerun = c.post(f"/api/tasks/{t['id']}/rerun")
        assert (
            rerun.status_code == 201
            and rerun.json()["parent_task_id"] == t["id"]
            and rerun.json()["status"] == "queued"
        )
        assert c.post(f"/api/tasks/{rerun.json()['id']}/rerun").status_code == 409  # only finished tasks


def test_sse_stream_replays_and_ends(settings: Settings, repo: Path):
    with make_client(settings, [step("fix", FIX), step("done")]) as c:
        t = create(c, repo)
        c.post(f"/api/tasks/{t['id']}/run")
        poll(c, t["id"])
        events, ended = [], False
        with c.stream("GET", f"/api/tasks/{t['id']}/events/stream") as r:
            assert r.headers["content-type"].startswith("text/event-stream")
            etype = None
            for line in r.iter_lines():
                if line.startswith("event:"):
                    etype = line.split(":", 1)[1].strip()
                    ended = ended or etype == "end"
                elif line.startswith("data:") and etype != "end":
                    events.append(json.loads(line[5:]))
        assert ended and events[0]["type"] == "task.created" and events[-1]["type"] == "task.finished"
        # resume support
        mid = events[5]["id"]
        with c.stream("GET", f"/api/tasks/{t['id']}/events/stream?after={mid}") as r:
            resumed = [
                json.loads(ln[5:]) for ln in r.iter_lines() if ln.startswith("data:") and ln != "data: {}"
            ]
        assert [e["id"] for e in resumed] == [e["id"] for e in events if e["id"] > mid]


def test_live_approval_over_http(settings: Settings, repo: Path):
    steps = [
        step("", FIX),
        step("", call("delete_file", path="AGENTS.md")),
        step("", call("run_tests")),
        step("done"),
    ]
    with make_client(settings, steps) as c:
        t = create(c, repo)
        c.post(f"/api/tasks/{t['id']}/run")
        poll(c, t["id"], until=("awaiting_approval",))
        pend = c.get(f"/api/tasks/{t['id']}/approvals").json()
        assert (
            len(pend) == 1
            and pend[0]["status"] == "pending"
            and pend[0]["arguments"] == {"path": "AGENTS.md"}
        )
        live = c.get(f"/api/tasks/{t['id']}/diff").json()  # the diff is available while the task is blocked
        assert live["live"] is True and "+    return a + b" in live["diff"]
        # invalid decision payloads / unknown ids
        assert (
            c.post(f"/api/approvals/{pend[0]['id']}/decision", json={"decision": "maybe"}).status_code == 422
        )
        assert c.post("/api/approvals/unknown/decision", json={"decision": "approve"}).status_code == 404
        ok = c.post(f"/api/approvals/{pend[0]['id']}/decision", json={"decision": "approve", "note": "fine"})
        assert ok.status_code == 200 and ok.json()["status"] == "approved"
        again = c.post(f"/api/approvals/{pend[0]['id']}/decision", json={"decision": "reject"})
        assert again.status_code == 409  # exactly one decision per action
        done = poll(c, t["id"])
        assert done["status"] == "completed" and done["outcome"] == "verified"
        types = [e["type"] for e in c.get(f"/api/tasks/{t['id']}/events").json()]
        assert "approval.requested" in types and "approval.decided" in types


def test_cancel_over_http_while_waiting(settings: Settings, repo: Path):
    with make_client(settings, [step("", call("delete_file", path="AGENTS.md")), step("no")]) as c:
        t = create(c, repo)
        c.post(f"/api/tasks/{t['id']}/run")
        poll(c, t["id"], until=("awaiting_approval",))
        r = c.post(f"/api/tasks/{t['id']}/cancel")
        assert r.status_code == 202 and r.json()["cancel_requested"] is True
        done = poll(c, t["id"])
        assert done["status"] == "cancelled" and done["error"] == "Cancelled by user"
        assert c.get(f"/api/tasks/{t['id']}/approvals").json()[0]["status"] == "rejected"


def test_cancel_queued_task(settings: Settings, repo: Path):
    with make_client(settings) as c:
        t = create(c, repo)
        assert c.post(f"/api/tasks/{t['id']}/cancel").status_code == 202
        assert c.get(f"/api/tasks/{t['id']}").json()["status"] == "cancelled"
        assert c.post(f"/api/tasks/{t['id']}/run").status_code == 409  # cannot start a cancelled task


def test_capacity_returns_429(settings: Settings, repo: Path):
    s = settings.model_copy(update={"max_concurrent_tasks": 1})
    with make_client(s, [step("", call("delete_file", path="AGENTS.md"))]) as c:
        a, b = create(c, repo), create(c, repo)
        c.post(f"/api/tasks/{a['id']}/run")
        poll(c, a["id"], until=("awaiting_approval",))
        assert c.post(f"/api/tasks/{b['id']}/run").status_code == 429
        c.post(f"/api/tasks/{a['id']}/cancel")
        poll(c, a["id"])


def test_api_token_auth(settings: Settings, repo: Path):
    s = settings.model_copy(update={"api_token": "s3cret-token-value"})
    with make_client(s, [step("done")]) as c:
        assert c.get("/api/health").status_code == 200  # health stays open for probes
        assert c.get("/api/tasks").status_code == 401
        assert c.get("/api/tasks", headers={"Authorization": "Bearer wrong"}).status_code == 401
        h = {"Authorization": "Bearer s3cret-token-value"}
        assert c.get("/api/tasks", headers=h).status_code == 200
        t = c.post("/api/tasks", headers=h, json={"repo_path": str(repo), "instructions": "x"}).json()
        c.post(f"/api/tasks/{t['id']}/cancel", headers=h)  # terminal task => the stream ends by itself
        with c.stream("GET", f"/api/tasks/{t['id']}/events/stream?token=s3cret-token-value") as r:
            assert r.status_code == 200  # EventSource cannot send headers, so ?token= is accepted
        assert c.get(f"/api/tasks/{t['id']}/events/stream").status_code == 401
        assert c.post("/api/approvals/x/decision", json={"decision": "approve"}).status_code == 401
        assert "s3cret-token-value" not in c.get("/api/health").text


def test_secrets_not_exposed_in_responses(settings: Settings, repo: Path):
    s = settings.model_copy(update={"anthropic_api_key": "sk-ant-api03-topsecretkeyvalue1234"})
    with make_client(
        s, [step("here is sk-ant-api03-topsecretkeyvalue1234", call("list_files")), step("done")]
    ) as c:
        t = create(c, repo)
        c.post(f"/api/tasks/{t['id']}/run")
        poll(c, t["id"])
        blob = json.dumps(
            [
                c.get(f"/api/tasks/{t['id']}").json(),
                c.get(f"/api/tasks/{t['id']}/events").json(),
                c.get("/api/config").json(),
            ]
        )
        assert "topsecretkeyvalue" not in blob


def test_restart_marks_interrupted_tasks_failed(settings: Settings, repo: Path):
    from datetime import timedelta

    from sqlalchemy import update

    from harnix.harness.states import TaskStatus
    from harnix.persistence.models import Event, utcnow
    from harnix.persistence.store import Store

    with make_client(settings) as c:
        dead, live = create(c, repo), create(c, repo)
        store: Store = c.app.state.forge.store  # type: ignore[attr-defined]
        store.transition(dead["id"], TaskStatus.RUNNING)
        store.transition(live["id"], TaskStatus.RUNNING)
        with store.sessions.begin() as s:  # the dead one's process went quiet an hour ago
            s.execute(
                update(Event).where(Event.task_id == dead["id"]).values(ts=utcnow() - timedelta(hours=1))
            )
    time.sleep(0.2)
    app2 = create_app(settings, provider_factory=lambda: ScriptedProvider([]))
    with TestClient(app2) as c2:  # lifespan startup runs orphan recovery
        got = c2.get(f"/api/tasks/{dead['id']}").json()
        assert got["status"] == "failed" and "restarted" in got["error"]
        # active a moment ago: may belong to another backend on the same database (or a --reload restart): left alone
        assert c2.get(f"/api/tasks/{live['id']}").json()["status"] == "running"


def test_openapi_documents_endpoints(settings: Settings):
    with make_client(settings) as c:
        spec = c.get("/openapi.json").json()
        for p in ["/api/tasks", "/api/tasks/{task_id}/run", "/api/tasks/{task_id}/cancel", "/api/tasks/{task_id}/events",
                  "/api/tasks/{task_id}/events/stream", "/api/tasks/{task_id}/diff", "/api/tasks/{task_id}/approvals",
                  "/api/approvals/{approval_id}/decision", "/api/health"]:  # fmt: skip
            assert p in spec["paths"], p
