"""The opt-in host terminal: real processes on this machine, approvals, ownership, and that a started server really answers.

The model is the scripted mock; the processes (a stock `python -m http.server`) are real.
"""

from __future__ import annotations

import socket
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from harnix.api.app import create_app
from harnix.config import Settings
from harnix.execution.terminal import ProcessManager, host_env, probe
from harnix.persistence.store import Store

from .conftest import call, make_runner, new_task, step, wait_for, wait_terminal
from .test_agentic_harness import FIX, Router, of

PY = Path(sys.executable).as_posix()  # forward slashes: commands are split like POSIX argv
PYQ = f'"{PY}"'  # quoted: the path contains spaces


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def pm():
    m = ProcessManager(max_running=2)
    yield m
    m.stop_all()


def pending(store: Store, task_id: str):
    return wait_for(lambda: [a for a in store.approvals(task_id) if a.status == "pending"] or None)[0]


# ---- units -----------------------------------------------------------------------------------------------


def test_host_env_drops_secrets_and_forge_settings(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-leak-1234567890")
    monkeypatch.setenv("FORGE_API_TOKEN", "t0ken-value")
    monkeypatch.setenv("MY_PLAIN_SETTING", "keep")
    monkeypatch.setenv("INNOCENT_NAME", "exactsecretvalue99")
    env = host_env(["exactsecretvalue99"])
    assert "OPENAI_API_KEY" not in env and "FORGE_API_TOKEN" not in env and "INNOCENT_NAME" not in env
    assert env["MY_PLAIN_SETTING"] == "keep" and "PATH" in env


def test_probe_only_contacts_this_machine():
    ok, status, note = probe("http://example.com/")
    assert not ok and status is None and "localhost" in note
    assert not probe("file:///etc/passwd")[0]
    assert probe(f"http://127.0.0.1:{free_port()}/")[0] is False  # nothing listening


def test_start_wait_output_stop_and_ownership(pm: ProcessManager, tmp_path: Path):
    (tmp_path / "hello.txt").write_text("hi")
    port = free_port()
    mp = pm.start(
        [PY, "-m", "http.server", str(port), "--bind", "127.0.0.1"], cwd=tmp_path, owner="s1", task_id="t1"
    )
    reachable, status, _ = pm.wait_ready(mp, f"http://localhost:{port}/hello.txt", 20)
    assert reachable and status == 200 and mp.running
    wait_for(lambda: "GET /hello.txt" in mp.output(2000))  # its own log shows the request
    assert pm.get(mp.id, "someone-else") is None and not pm.stop(mp.id, "someone-else") and mp.running
    assert pm.stop(mp.id, "s1") and not mp.running
    assert probe(f"http://127.0.0.1:{port}/")[0] is False


def test_a_process_that_dies_is_reported_not_called_reachable(pm: ProcessManager, tmp_path: Path):
    mp = pm.start([PY, "-c", "import sys; print('boom'); sys.exit(3)"], cwd=tmp_path, owner="s", task_id="t")
    reachable, _, note = pm.wait_ready(mp, f"http://localhost:{free_port()}/", 20)
    assert reachable is False and "exited with code 3" in note
    wait_for(lambda: "boom" in mp.output(100))


def test_limit_on_running_processes_and_missing_program(pm: ProcessManager, tmp_path: Path):
    for _ in range(2):
        pm.start([PY, "-c", "import time; time.sleep(60)"], cwd=tmp_path, owner="s", task_id="t")
    with pytest.raises(RuntimeError, match="already running"):
        pm.start([PY, "-c", "pass"], cwd=tmp_path, owner="s", task_id="t")
    with pytest.raises(FileNotFoundError):
        ProcessManager().start(["definitely-not-a-program-xyz"], cwd=tmp_path, owner="s", task_id="t")


# ---- inside tasks ----------------------------------------------------------------------------------------


def host(settings: Settings, **kw) -> Settings:
    return settings.model_copy(update={"terminal": "host", **kw})


def run_task(settings: Settings, store: Store, repo: Path, steps, wait=True):
    provider = Router(steps)
    runner = make_runner(settings, store, provider)
    task = new_task(store, repo, settings)
    runner.start(task.id)
    return runner, provider, task, (wait_terminal(store, task.id) if wait else None)


def test_tools_are_only_offered_when_enabled(settings: Settings, store: Store, repo: Path):
    runner, p, _, _ = run_task(settings, store, repo, [step("", FIX), step("done")])
    assert not any(t.startswith("terminal_") for c in p.seen for t in c["tools"])
    runner2, p2, _, _ = run_task(host(settings), store, repo, [step("", FIX), step("done")])
    try:
        assert {"terminal_run", "terminal_start", "terminal_output", "terminal_stop"} <= set(
            p2.seen[0]["tools"]
        )
    finally:
        runner2.processes.stop_all()


def test_server_started_by_a_task_answers_and_outlives_it(settings: Settings, store: Store, repo: Path):
    port = free_port()
    url = f"http://localhost:{port}/mathutil.py"
    steps = [
        step(
            "",
            call(
                "terminal_start",
                command=f"{PYQ} -m http.server {port} --bind 127.0.0.1",
                wait_for_url=url,
                wait_seconds=30,
            ),
        ),
        step("", FIX),
        step("done"),
    ]
    runner, _, task, _ = run_task(host(settings), store, repo, steps, wait=False)
    try:
        a = pending(store, task.id)
        assert a.tool == "terminal_start" and "outside the sandbox" in a.reason
        store.decide_approval(a.id, "approved")
        done = wait_terminal(store, task.id)
        assert done.status == "completed"
        res = [r for r in of(store, task.id, "tool.result") if r["tool"] == "terminal_start"][0]
        assert res["ok"] and res["data"]["reachable"] is True and res["data"]["http_status"] == 200, res
        # the task is over, the server is still there (the point of the feature)
        assert probe(url)[0] is True
        [info] = runner.processes.list()
        assert info["running"] and info["task_id"] == task.id
        assert runner.processes.stop(info["id"]) and probe(url)[0] is False
    finally:
        runner.processes.stop_all()


def test_terminal_run_executes_on_the_host_after_approval(settings: Settings, store: Store, repo: Path):
    steps = [step("", call("terminal_run", command=f"{PYQ} -c print(6*7)")), step("", FIX), step("done")]
    runner, _, task, _ = run_task(host(settings), store, repo, steps, wait=False)
    a = pending(store, task.id)
    assert "host terminal" in a.reason
    store.decide_approval(a.id, "approved")
    wait_terminal(store, task.id)
    res = [r for r in of(store, task.id, "tool.result") if r["tool"] == "terminal_run"][0]
    assert res["ok"] and "42" in res["data"]["output"] and res["data"]["where"] == "host"


def test_rejected_start_never_runs_and_hard_denied_commands(settings: Settings, store: Store, repo: Path):
    port = free_port()
    steps = [
        step("", call("terminal_start", command=f"{PYQ} -m http.server {port} --bind 127.0.0.1")),
        step("", call("terminal_run", command="sudo rm -rf /")),
        step("", FIX),
        step("done"),
    ]
    runner, _, task, _ = run_task(host(settings), store, repo, steps, wait=False)
    store.decide_approval(pending(store, task.id).id, "rejected", "no")
    wait_terminal(store, task.id)
    results = {r["tool"]: r for r in of(store, task.id, "tool.result") if r["tool"].startswith("terminal_")}
    assert "Not approved" in results["terminal_start"]["error"] and runner.processes.list() == []
    assert "never permitted" in results["terminal_run"]["error"]


def test_deny_sensitive_refuses_start_and_workdir_cannot_escape(settings: Settings, store: Store, repo: Path):
    s = host(settings, approval_policy="deny_sensitive")
    steps = [
        step("", call("terminal_start", command=f"{PYQ} -c pass")),
        step("", call("terminal_run", command=f"{PYQ} --version", workdir="..")),
        step("", FIX),
        step("done"),
    ]
    runner, _, task, _ = run_task(s, store, repo, steps)
    res = {r["tool"]: r for r in of(store, task.id, "tool.result") if r["tool"].startswith("terminal_")}
    assert "Denied by policy" in res["terminal_start"]["error"] and runner.processes.list() == []
    assert "Denied" in res["terminal_run"]["error"]  # outside the workspace


def test_tools_cannot_touch_other_conversations_processes(
    settings: Settings, store: Store, repo: Path, tmp_path: Path
):
    s = host(settings)
    steps = [step("", call("terminal_stop", process_id="p1")), step("", FIX), step("done")]
    runner, _, task, _ = run_task(s, store, repo, steps, wait=False)
    other = runner.processes.start(
        [PY, "-c", "import time; time.sleep(60)"], cwd=tmp_path, owner="another-session", task_id="x"
    )
    try:
        wait_terminal(store, task.id)
        res = [r for r in of(store, task.id, "tool.result") if r["tool"] == "terminal_stop"][0]
        assert not res["ok"] and other.running
    finally:
        runner.processes.stop_all()


def test_api_lists_and_stops_processes(settings: Settings, tmp_path: Path):
    with TestClient(create_app(host(settings), create_schema=True)) as c:
        pm = c.app.state.forge.runner.processes  # type: ignore[attr-defined]
        mp = pm.start([PY, "-c", "import time; time.sleep(60)"], cwd=tmp_path, owner="s", task_id="t")
        items = c.get("/api/processes").json()["items"]
        assert items[0]["id"] == mp.id and items[0]["running"]
        assert c.post(f"/api/processes/{mp.id}/stop").status_code == 200 and not mp.running
        assert c.post("/api/processes/nope/stop").status_code == 404
    assert not mp.running


def test_cli_ps_and_stop_reach_the_backend(settings: Settings, tmp_path: Path):
    """`forge ps` / `forge stop` are subcommands, not task prompts (they must be known to the argument pre-parser)."""
    import threading
    import time

    import uvicorn

    from harnix.cli import EXIT_OK, main

    app = create_app(host(settings), create_schema=True)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    while not server.started:
        time.sleep(0.05)
    try:
        url, lines = f"http://127.0.0.1:{port}", []
        assert main(["ps", "--url", url], out=lines.append) == EXIT_OK and lines == [
            "No background processes."
        ]
        mp = app.state.forge.runner.processes.start(
            [PY, "-c", "import time; time.sleep(60)"], cwd=tmp_path, owner="s", task_id="t"
        )
        lines.clear()
        assert (
            main(["ps", "--url", url], out=lines.append) == EXIT_OK
            and lines[0].startswith(f"{mp.id} ")
            and "running" in lines[0]
        )
        assert main(["stop", mp.id, "--url", url], out=lines.append) == EXIT_OK and not mp.running
        assert main(["stop", "nope", "--url", url], out=lines.append) != EXIT_OK
    finally:
        app.state.forge.runner.processes.stop_all()
        server.should_exit = True
        t.join(15)
