"""The `forge` CLI against a real HTTP server (uvicorn in a thread); only the model is scripted."""

from __future__ import annotations

import argparse
import socket
import threading
import time
from pathlib import Path

import pytest
import uvicorn

from harnix.api.app import create_app
from harnix.cli import (
    EXIT_CANCELLED,
    EXIT_FAILED,
    EXIT_OK,
    EXIT_SETUP,
    EXIT_UNVERIFIED,
    EXIT_USAGE,
    Style,
    cmd_serve,
    combined_roots,
    extract_references,
    main,
    render_diff,
    render_event,
    run_task,
    short_args,
    tail,
)
from harnix.client import ForgeClient, ForgeError, parse_sse
from harnix.config import Settings
from harnix.models.scripted import ScriptedProvider

from .conftest import call, step

FIX = call("edit_file", path="mathutil.py", old_text="a - b", new_text="a + b")
APPROVE_CMD = call("run_command", command="python -c print(1)")  # needs a human decision
PLAIN = Style(False)


@pytest.fixture
def serve(settings: Settings):
    started: list[tuple[uvicorn.Server, threading.Thread]] = []

    def start(steps) -> str:
        provider = ScriptedProvider(steps)
        app = create_app(settings, provider_factory=lambda: provider, create_schema=True)
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
        t = threading.Thread(target=server.run, daemon=True)
        t.start()
        end = time.time() + 20
        while not server.started and time.time() < end:
            time.sleep(0.05)
        assert server.started, "server did not start"
        started.append((server, t))
        return f"http://127.0.0.1:{port}"

    yield start
    for server, t in started:
        server.should_exit = True
        t.join(15)


def answers(approve="y", apply="y"):
    asked: list[str] = []

    def ask(prompt: str) -> str:
        asked.append(prompt)
        return approve if "Approve exactly" in prompt else apply

    ask.asked = asked  # type: ignore[attr-defined]
    return ask


def go(url, repo, ask, *, prompt="fix add", **kw):
    lines: list[str] = []
    code = run_task(ForgeClient(url), repo=repo, prompt=prompt, out=lines.append, ask=ask, style=PLAIN, **kw)
    return code, "\n".join(lines)


# ---- full sessions ----------------------------------------------------------------------------


def test_session_with_approval_diff_and_apply(serve, repo: Path):
    url = serve(
        [step("Looking.", FIX), step("", APPROVE_CMD), step("", call("run_tests")), step("Fixed add().")]
    )
    ask = answers("y", "y")
    code, out = go(url, repo, ask)
    assert code == EXIT_OK, out
    for needle in [
        "approval required", "python -c print(1)", "▶ edit_file", "approved: approved by human",
        "verification: verified", "completed, verified by independent checks", "1 file(s) changed",
        "+    return a + b", "applied 1 file(s) to your repository: M mathutil.py", "Fixed add().",
    ]:  # fmt: skip
        assert needle in out, needle
    assert "a + b" in (repo / "mathutil.py").read_text()
    assert (
        sum("Approve exactly" in p for p in ask.asked) == 1
        and sum("Apply these changes" in p for p in ask.asked) == 1
    )


def test_declining_approval_and_apply_changes_nothing_then_apply_later(serve, repo: Path):
    url = serve([step("", FIX), step("", APPROVE_CMD), step("done")])
    code, out = go(url, repo, answers("n", "n"))
    assert code == EXIT_OK
    assert "not approved" in out and "not applied. Apply later with:  forge apply " in out
    assert "a - b" in (repo / "mathutil.py").read_text()
    tid = out.split("forge apply ")[1].split()[0]
    lines: list[str] = []
    assert main(["apply", tid, "--url", url, "--yes"], out=lines.append, ask=lambda p: "y") == EXIT_OK
    assert "would change 1 file(s)" in "\n".join(lines) and "applied 1 file(s)" in "\n".join(lines)
    assert "a + b" in (repo / "mathutil.py").read_text()
    again: list[str] = []
    assert main(["apply", tid, "--url", url, "--yes"], out=again.append) == EXIT_FAILED
    assert "already applied" in "\n".join(again)


def test_non_interactive_session_rejects_approvals_and_applies_nothing(serve, repo: Path):
    url = serve([step("", FIX), step("", APPROVE_CMD), step("done")])

    def no_tty(prompt):
        raise EOFError

    code, out = go(url, repo, no_tty)
    assert code == EXIT_OK and "not approved" in out and "not applied" in out
    assert "a - b" in (repo / "mathutil.py").read_text()


def test_ctrl_c_cancels_the_task_and_exits_130(serve, repo: Path):
    url = serve([step("", APPROVE_CMD), step("never reached")])

    def interrupt(prompt):
        raise KeyboardInterrupt

    code, out = go(url, repo, interrupt)
    assert code == EXIT_CANCELLED and "cancelling the task" in out and "cancelled" in out
    assert "a - b" in (repo / "mathutil.py").read_text()
    t = ForgeClient(url).list_tasks()["items"][0]
    assert t["status"] == "cancelled"


def test_auto_apply_only_when_verified(serve, repo: Path):
    url = serve([step("", FIX), step("done")])
    never = answers()
    code, out = go(url, repo, never, auto_apply=True)
    assert code == EXIT_OK and "applied 1 file(s)" in out
    assert not any("Apply these changes" in p for p in never.asked)  # --apply never asks
    assert "a + b" in (repo / "mathutil.py").read_text()


def test_unverified_result_gets_exit_code_4_and_is_never_auto_applied(serve, repo: Path):
    (repo / "test_mathutil.py").unlink()
    url = serve([step("", FIX), step("done")])
    code, out = go(url, repo, answers(), auto_apply=True)
    assert code == EXIT_UNVERIFIED
    assert "NOT verified" in out and "--apply skipped" in out
    assert "a - b" in (repo / "mathutil.py").read_text()


def test_unverified_prompt_warns_loudly(serve, repo: Path):
    (repo / "test_mathutil.py").unlink()
    url = serve([step("", FIX), step("done")])
    ask = answers("n", "n")
    go(url, repo, ask)
    assert any("NOT verified!" in p for p in ask.asked)


def test_failed_task_exit_code(serve, repo: Path):
    url = serve([{"error": "invalid api key"}])
    code, out = go(url, repo, answers())
    assert code == EXIT_FAILED and "failed" in out and "invalid api key" in out


def test_shorthand_prompt_and_options_via_main(serve, repo: Path):
    url = serve([step("", FIX), step("done")])
    lines: list[str] = []
    code = main(
        ["fix", "the", "bug", "--repo", str(repo), "--url", url, "--no-diff", "--apply"],
        out=lines.append,
        ask=answers(),
    )
    out = "\n".join(lines)
    assert code == EXIT_OK and "applied 1 file(s)" in out and "+    return a + b" not in out  # --no-diff


# ---- setup problems are explained ---------------------------------------------------------------


def test_backend_not_running_says_how_to_start_it(repo: Path):
    code, out = go("http://127.0.0.1:9", repo, answers())
    assert code == EXIT_SETUP and "Cannot reach the Forge backend" in out and "forge serve --allow" in out


def test_unauthorized_repository_suggests_allow(serve, tmp_path: Path):
    url = serve([])
    other = tmp_path / "somewhere-else"
    other.mkdir()
    code, out = go(url, other, answers())
    assert code == EXIT_USAGE and "authorized" in out and "forge serve --allow" in out


def test_unconfigured_provider_is_reported_without_creating_a_task(
    settings: Settings, tmp_path: Path, repo: Path
):
    bad = settings.model_copy(update={"provider": "openai", "openai_api_key": None})
    app = create_app(bad, create_schema=True)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    while not server.started:
        time.sleep(0.05)
    try:
        code, out = go(f"http://127.0.0.1:{port}", repo, answers())
        assert code == EXIT_SETUP and "OPENAI_API_KEY is not set" in out
        assert ForgeClient(f"http://127.0.0.1:{port}").list_tasks()["total"] == 0
    finally:
        server.should_exit = True
        t.join(15)


def test_doctor_and_tasks_commands(serve, repo: Path):
    url = serve([step("", FIX), step("done")])
    go(url, repo, answers("n", "n"))
    lines: list[str] = []
    assert main(["doctor", "--url", url], out=lines.append) == EXIT_OK
    text = "\n".join(lines)
    assert "scripted" in text and "local:" in text and str(repo.parent) in text
    tasks: list[str] = []
    assert main(["tasks", "--url", url], out=tasks.append) == EXIT_OK and "completed" in tasks[0]


def test_serve_refuses_to_listen_publicly_without_a_token(monkeypatch):
    monkeypatch.setenv("FORGE_API_TOKEN", "")
    args = argparse.Namespace(host="0.0.0.0", port=0, allow=[], no_migrate=True, insecure=False)
    lines: list[str] = []
    assert cmd_serve(args, out=lines.append) == EXIT_USAGE and "FORGE_API_TOKEN" in lines[0]


def test_allow_adds_to_existing_roots_without_duplicates(tmp_path: Path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    roots = combined_roots([a.resolve()], [str(a), str(b)])
    assert roots == [str(a.resolve()), str(b.resolve())]


# ---- rendering and parsing units --------------------------------------------------------------


def ev(type_, **data):
    return {"id": 1, "type": type_, "data": data}


def test_render_event_shapes():
    s = PLAIN
    assert render_event(
        ev("tool.proposed", tool="edit_file", arguments={"path": "a.py", "new_text": "x"}), s
    ) == ["  ▶ edit_file path=a.py, new_text=x"]
    assert render_event(ev("tool.authorization", decision="allow", reason="ok", tool="t"), s) == []
    assert (
        "refused by policy: nope"
        in render_event(ev("tool.authorization", decision="deny", reason="nope", tool="t"), s)[0]
    )
    assert (
        "needs your approval"
        in render_event(ev("tool.authorization", decision="approve", reason="r", tool="t"), s)[0]
    )
    failing = render_event(
        ev(
            "tool.result",
            ok=True,
            tool="run_tests",
            data={"exit_code": 1, "duration_seconds": 1.2, "output": "x\nFAILED test_a\n"},
        ),
        s,
    )
    assert failing[0].strip() == "exit 1 in 1.2s" and "FAILED test_a" in failing[-1]
    assert render_event(
        ev(
            "tool.result",
            ok=True,
            tool="run_tests",
            data={"exit_code": 0, "duration_seconds": 1, "output": "noise"},
        ),
        s,
    ) == ["    exit 0 in 1s"]
    assert "✗ boom" in render_event(ev("tool.result", ok=False, tool="t", error="boom"), s)[0]
    assert (
        render_event(ev("tool.result", ok=False, tool="t", error="Not approved: rejected by human"), s) == []
    )  # no duplicate line
    assert (
        "[failed] test: pytest"
        in render_event(
            ev("verification.check", status="failed", kind="test", command="pytest", phase="final"), s
        )[0]
    )
    assert (
        render_event(ev("model.request", iteration=1), s) == [] and render_event(ev("task.created"), s) == []
    )
    assert "Looking" in "".join(render_event(ev("model.response", text="Looking at it", tool_calls=[]), s))


def test_at_mentions_become_references_and_other_at_text_is_untouched(repo: Path):
    (repo / "src").mkdir()
    (repo / "src" / "app.py").write_text("x = 1\n")
    text, refs = extract_references(
        "explain @src/app.py#1-2, fix @mathutil.py. ping @alice (a@b.com) read @../etc/passwd and @nope.py and @src/app.py#1-2",
        repo,
    )
    assert refs == [{"path": "src/app.py", "start_line": 1, "end_line": 2}, {"path": "mathutil.py"}]
    assert (
        text
        == "explain src/app.py (lines 1-2), fix mathutil.py. ping @alice (a@b.com) read @../etc/passwd and @nope.py and src/app.py (lines 1-2)"
    )
    assert extract_references("single line @mathutil.py#2", repo)[1] == [
        {"path": "mathutil.py", "start_line": 2, "end_line": 2}
    ]
    assert extract_references("no mentions here", repo) == ("no mentions here", [])
    (repo / "dir").mkdir()
    assert extract_references("a directory @dir is not a file", repo)[1] == []


def test_references_flow_through_the_cli_to_the_model(serve, repo: Path):
    seen: list[str] = []

    def spy(messages):
        seen.append(messages[0].content[0].text)
        return step("", FIX)  # fix the bug so the task verifies and the exit code is meaningful

    url = serve([spy, step("done")])
    lines: list[str] = []
    code = main(
        ["explain @mathutil.py#1-2 and @.env", "--repo", str(repo), "--url", url],
        out=lines.append,
        ask=answers("n", "n"),
    )
    out = "\n".join(lines)
    assert code == EXIT_OK, out
    assert (
        'path="mathutil.py"' in seen[0] and "return a - b" in seen[0] and "supersecretvalue123" not in seen[0]
    )
    assert "attached 1 file(s): mathutil.py" in out
    assert "could not attach .env: looks like a secret or protected file" in out


def test_long_arguments_and_output_are_trimmed():
    assert render_event(ev("tool.proposed", tool="git_status", arguments={}), PLAIN) == ["  ▶ git_status"]
    assert len(short_args({"content": "x" * 500})) <= 110
    assert tail("\n".join(str(i) for i in range(30)), 3) == ["    …", "    27", "    28", "    29"]
    assert tail("", 3) == []


def test_render_diff_and_style():
    on = Style(True)
    lines = render_diff("diff --git a/x b/x\n@@ -1 +1 @@\n-old\n+new\n ctx", on)
    assert "\x1b[31m-old" in lines[2] and "\x1b[32m+new" in lines[3] and lines[4] == " ctx"
    assert Style(False).red("x") == "x"


def test_parse_sse_handles_comments_multiline_bad_json_and_end():
    raw = [
        b": keep-alive\n",
        b"\n",
        b"id: 1\n",
        b'data: {"id": 1, "type": "a"}\n',
        b"\n",
        b"data: not json\n",
        b"\n",
        b"event: end\n",
        b"data: {}\n",
        b"\n",
    ]
    got = list(parse_sse(iter(raw)))
    assert got == [("message", {"id": 1, "type": "a"}), ("message", None), ("end", {})]


def test_client_error_shapes(serve):
    url = serve([])
    c = ForgeClient(url)
    with pytest.raises(ForgeError) as e:
        c.get_task("nope")
    assert e.value.status == 404 and e.value.code == "not_found" and not e.value.unreachable
    with pytest.raises(ForgeError) as e2:
        ForgeClient("http://127.0.0.1:9").health()
    assert e2.value.unreachable
    bad = ForgeClient(url, token="wrong")
    assert bad.health()["status"] == "ok"  # health is open; other routes need the token only when configured


def test_prompt_can_follow_options():
    from harnix.cli import _has_bare_prompt

    assert _has_bare_prompt(["fix it"])
    assert _has_bare_prompt(["--thinking", "high", "--skill", "x", "fix it"])
    assert _has_bare_prompt(["--url=http://h:1", "--no-diff", "fix it"])
    assert not _has_bare_prompt(["--url", "http://h:1"])  # options only: interactive session
    assert not _has_bare_prompt(["--url", "http://h:1", "doctor"])
    assert not _has_bare_prompt(["memory", "add", "note"])
    assert not _has_bare_prompt([])


def test_image_flag_attaches_files_and_refuses_bad_ones(serve, repo: Path, tmp_path: Path):
    import base64

    from harnix.cli import load_images

    from .test_images import png

    good = tmp_path / "shot.png"
    good.write_bytes(png())
    (tmp_path / "fake.png").write_bytes(b"this is not an image")
    imgs = load_images([str(good)])
    assert imgs == [{"name": "shot.png", "media_type": "image/png", "data": base64.b64encode(png()).decode()}]
    with pytest.raises(ValueError, match="not a PNG, JPEG, GIF or WebP"):
        load_images([str(tmp_path / "fake.png")])
    with pytest.raises(ValueError, match="no such file"):
        load_images([str(tmp_path / "missing.png")])

    seen = []
    url = serve([lambda m: (seen.append(m[0]), step("", FIX))[1], step("Fixed, and I saw the picture.")])
    code, out = go(url, repo, answers("y", "n"), images=imgs)
    assert code == EXIT_OK, out
    from harnix.models.base import ImageBlock

    assert [b for b in seen[0].content if isinstance(b, ImageBlock)], "the model never received the image"
    # the shorthand reaches the same path
    assert (
        main(["--image", str(tmp_path / "missing.png"), "-c", "x", "do it"], out=lambda *_: None)
        == EXIT_USAGE
    )


def test_a_conversation_continues_across_prompts_and_across_commands(serve, repo: Path):
    seen: list = []
    url = serve([
        lambda m: (seen.append(list(m)), step("", FIX))[1], step("Fixed add()."),
        lambda m: (seen.append(list(m)), step("Yes, I remember: I fixed add()."))[1],
        lambda m: (seen.append(list(m)), step("fresh start"))[1],
    ])  # fmt: skip
    chat: dict = {"id": None}
    code, out = go(url, repo, answers("y", "n"), prompt="fix add", conversation=chat)
    assert code == EXIT_OK and chat["id"], out
    assert f"session {chat['id']} message 1" in out
    code, out = go(url, repo, answers("y", "n"), prompt="what did you just do?", conversation=chat)
    assert f"session {chat['id']} message 2" in out
    convo = " ".join(b.text for m in seen[1] for b in m.content if hasattr(b, "text"))
    assert "fix add" in convo and "Fixed add()." in convo  # the second prompt saw the first exchange
    sid = chat["id"]
    chat["id"] = None  # what /new does
    go(url, repo, answers("y", "n"), prompt="something unrelated", conversation=chat)
    convo3 = " ".join(b.text for m in seen[2] for b in m.content if hasattr(b, "text"))
    assert chat["id"] != sid and "Fixed add()." not in convo3
