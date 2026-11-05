"""Sandbox, workspace and tool-layer tests against real processes and a real git."""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from harnix.config import Settings
from harnix.execution import git
from harnix.execution.project import inspect_project
from harnix.execution.sandbox import DockerSandbox, LocalSandbox
from harnix.execution.workspace import WorkspaceManager
from harnix.harness.states import TaskStatus
from harnix.models.base import ToolUseBlock
from harnix.persistence.store import Store
from harnix.policies.approvals import ApprovalGate
from harnix.policies.budget import Budget
from harnix.policies.paths import WorkspacePaths
from harnix.policies.policy import PolicyEngine
from harnix.policies.redaction import Redactor
from harnix.tools.base import ToolContext
from harnix.tools.executor import ToolExecutor

from .conftest import new_task

PY = sys.executable

# ---- local sandbox ---------------------------------------------------------------------------


def run(argv, tmp_path, timeout=20, cancel=None, max_output=20000, sandbox=None):
    sb = sandbox or LocalSandbox()
    return sb.run(
        argv, workspace=tmp_path, workdir=tmp_path, timeout=timeout, cancel=cancel, max_output=max_output
    )


def test_local_runs_and_captures(tmp_path: Path):
    r = run([PY, "-c", "import sys;print('out');print('err',file=sys.stderr);sys.exit(3)"], tmp_path)
    assert r.exit_code == 3 and "out" in r.output and "err" in r.output and not r.timed_out


def test_local_not_found(tmp_path: Path):
    r = run(["definitely-not-a-program-xyz"], tmp_path)
    assert r.not_found and r.exit_code == 127


def test_local_output_truncation_keeps_head_and_tail(tmp_path: Path):
    code = "print('HEAD');print('x'*200000);print('TAIL-MARKER')"
    r = run([PY, "-c", code], tmp_path, max_output=2000)
    assert r.truncated and len(r.output) < 3000
    assert "HEAD" in r.output and "TAIL-MARKER" in r.output


def test_local_env_is_scrubbed(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-should-not-leak")
    monkeypatch.setenv("FORGE_API_TOKEN", "tok-should-not-leak")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "aws-should-not-leak")
    r = run([PY, "-c", "import os;print(sorted(os.environ))"], tmp_path)
    assert "should-not-leak" not in r.output
    for k in ("ANTHROPIC_API_KEY", "FORGE_API_TOKEN", "AWS_SECRET_ACCESS_KEY"):
        assert k not in r.output


CHILD_SPAWNER = (
    "import subprocess,sys,time;"
    "subprocess.Popen([sys.executable,'-c',"
    "\"import time,pathlib;time.sleep(3);pathlib.Path('marker.txt').write_text('alive')\"]);"
    "time.sleep(60)"
)


def test_timeout_kills_whole_process_tree(tmp_path: Path):
    start = time.monotonic()
    r = run([PY, "-c", CHILD_SPAWNER], tmp_path, timeout=1.5)
    assert r.timed_out and r.exit_code is None and time.monotonic() - start < 15
    time.sleep(4.5)  # the grandchild would have written its marker by now if it survived
    assert not (tmp_path / "marker.txt").exists()


def test_cancel_kills_process_tree(tmp_path: Path):
    cancel = threading.Event()
    threading.Timer(1.0, cancel.set).start()
    start = time.monotonic()
    r = run([PY, "-c", CHILD_SPAWNER], tmp_path, timeout=60, cancel=cancel)
    assert r.cancelled and time.monotonic() - start < 15
    time.sleep(4.5)
    assert not (tmp_path / "marker.txt").exists()


# ---- docker sandbox --------------------------------------------------------------------------


def test_docker_command_is_locked_down(tmp_path: Path):
    sb = DockerSandbox("img:1", network="none", memory="512m", cpus="1")
    cmd = sb.build_command(["pytest", "-q"], tmp_path, tmp_path / "sub", "forge-x")
    joined = " ".join(cmd)
    for needle in [
        "--network none",
        "--cap-drop ALL",
        "no-new-privileges",
        "--read-only",
        "--pids-limit",
        "--memory 512m",
    ]:
        assert needle in joined, needle
    assert cmd[cmd.index("--user") + 1] != "0:0" and "root" not in joined
    assert sum(1 for c in cmd if c == "--mount") == 1  # only the workspace is mounted
    assert cmd[cmd.index("-w") + 1] == "/workspace/sub"
    assert cmd[-2:] == ["pytest", "-q"]
    assert "--privileged" not in cmd and "-v" not in cmd


DOCKER_IMAGE = os.environ.get(
    "FORGE_TEST_DOCKER_IMAGE", "postgres:16-alpine"
)  # any local image with busybox sh


def _sandbox_containers() -> str:
    """Running containers named like DockerSandbox's (`forge-` + 12 hex); unrelated forge-* containers are ignored."""
    out = subprocess.run(
        ["docker", "ps", "-q", "--filter", "name=^forge-[0-9a-f]{12}$"], capture_output=True, text=True
    )
    return out.stdout.strip()


def _docker_ready() -> bool:
    return DockerSandbox(DOCKER_IMAGE).available()[0]


@pytest.mark.skipif(not _docker_ready(), reason=f"docker daemon or image {DOCKER_IMAGE} not available")
class TestDockerReal:
    def sb(self):
        return DockerSandbox(DOCKER_IMAGE, network="none", memory="256m", cpus="1")

    def test_mounts_workspace_only_and_runs_non_root(self, tmp_path: Path):
        (tmp_path / "hello.txt").write_text("from-host")
        sb = self.sb()
        assert run(["cat", "hello.txt"], tmp_path, sandbox=sb).output.strip() == "from-host"
        assert run(["id", "-u"], tmp_path, sandbox=sb).output.strip() != "0"
        r = run(["sh", "-c", "touch /should-fail 2>&1; echo rc=$?"], tmp_path, sandbox=sb)
        assert "rc=0" not in r.output  # read-only root filesystem
        r = run(["sh", "-c", "touch written.txt && echo ok"], tmp_path, sandbox=sb)
        assert "ok" in r.output and (tmp_path / "written.txt").exists()
        r = run(
            ["sh", "-c", "ls /home /root 2>&1; cat /etc/hostname >/dev/null; ls /workspace"],
            tmp_path,
            sandbox=sb,
        )
        assert "hello.txt" in r.output

    def test_no_network_by_default(self, tmp_path: Path):
        r = run(["sh", "-c", "wget -q -T 3 -O- http://1.1.1.1 2>&1; echo rc=$?"], tmp_path, sandbox=self.sb())
        assert "rc=0" not in r.output

    def test_host_env_not_visible(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-should-not-leak")
        assert "should-not-leak" not in run(["env"], tmp_path, sandbox=self.sb()).output

    def test_timeout_kills_container(self, tmp_path: Path):
        start = time.monotonic()
        r = run(["sleep", "60"], tmp_path, timeout=3, sandbox=self.sb())
        assert r.timed_out and time.monotonic() - start < 30
        time.sleep(1)
        assert _sandbox_containers() == "", "container survived timeout"

    def test_cancel_kills_container(self, tmp_path: Path):
        cancel = threading.Event()
        threading.Timer(2.0, cancel.set).start()
        r = run(["sleep", "60"], tmp_path, timeout=60, cancel=cancel, sandbox=self.sb())
        assert r.cancelled
        time.sleep(1)
        assert _sandbox_containers() == "", "container survived cancel"

    def test_missing_executable(self, tmp_path: Path):
        assert run(["no-such-binary-xyz"], tmp_path, sandbox=self.sb()).not_found


# ---- workspace & git ------------------------------------------------------------------------


def test_workspace_is_isolated_copy_without_secrets(settings: Settings, repo: Path):
    (repo / "node_modules").mkdir()
    (repo / "node_modules" / "junk.js").write_text("x")
    (repo / "sub").mkdir()
    (repo / "sub" / "dirty.py").write_text("uncommitted work")
    ws = WorkspaceManager(settings.workspace_root_path).create("t1", repo)
    assert (
        (ws / "mathutil.py").exists() and (ws / "AGENTS.md").exists() and (ws / "sub" / "dirty.py").exists()
    )
    assert not (ws / ".env").exists() and not (ws / "node_modules").exists()
    assert git.diff(ws) == ""  # baseline includes pre-existing (uncommitted) work
    (ws / "mathutil.py").write_text("changed")
    assert (repo / "mathutil.py").read_text().startswith("def add")  # source untouched
    assert [f["path"] for f in git.changed_files(ws)] == ["mathutil.py"]
    with pytest.raises(Exception, match="already exists"):
        WorkspaceManager(settings.workspace_root_path).create("t1", repo)


def test_workspace_copy_limit_cleans_up(settings: Settings, repo: Path):
    (repo / "big.bin").write_bytes(b"0" * 2_000_000)
    wm = WorkspaceManager(settings.workspace_root_path, max_copy_mb=1)
    with pytest.raises(Exception, match="copy limit"):
        wm.create("t2", repo)
    assert not (settings.workspace_root_path / "t2").exists()


def test_inspect_project(repo: Path):
    info = inspect_project(repo)
    assert info.kinds == ["python"] and info.commands["test"] == "python -m pytest -q"
    assert "AGENTS.md" in info.instructions
    (repo / "package.json").write_text(
        '{"scripts":{"test":"vitest run","lint":"eslint .","build":"vite build"}}'
    )
    (repo / "pnpm-lock.yaml").write_text("")
    info = inspect_project(repo)
    assert (
        "node" in info.kinds and info.commands["lint"] == "pnpm run lint" and "pnpm" in info.package_managers
    )
    empty = repo.parent / "empty"
    empty.mkdir()
    assert "No test command detected" in inspect_project(empty).notes


# ---- tools through the executor ----------------------------------------------------------------


@pytest.fixture
def rig(settings: Settings, store: Store, repo: Path):
    def build(**policy_overrides):
        s = settings.model_copy(update=policy_overrides)
        task = new_task(store, repo, s)
        store.transition(task.id, TaskStatus.RUNNING)
        ws = WorkspaceManager(s.workspace_root_path).create(task.id, repo)
        paths = WorkspacePaths(ws)
        cancel = threading.Event()
        budget = Budget(50, 300, 100)
        ctx = ToolContext(
            paths=paths,
            sandbox=LocalSandbox(),
            project=inspect_project(ws),
            timeout=60,
            max_output=s.max_output_bytes,
            max_read=s.max_read_bytes,
            max_write=s.max_write_bytes,
            cancel=cancel,
        )
        ex = ToolExecutor(
            store=store,
            task_id=task.id,
            ctx=ctx,
            policy=PolicyEngine(s, paths),
            gate=ApprovalGate(store, task.id, timeout=10, cancel=cancel, budget=budget, poll=0.05),
            budget=budget,
            redactor=Redactor(),
        )

        def do(name, **inp):
            return ex.execute(ToolUseBlock(f"id{time.monotonic_ns()}", name, inp))

        return do, ws, task, store

    return build


def test_read_only_tools(rig):
    do, ws, *_ = rig()
    r = do("list_files")
    assert r.ok and "mathutil.py" in r.data["entries"] and ".env" not in r.data["entries"]
    assert do("list_files", recursive=True).ok
    r = do("read_file", path="mathutil.py", start_line=2, max_lines=1)
    assert r.ok and r.data["content"].strip() == "return a - b"
    r = do("search_text", pattern="a - b")
    assert r.ok and r.data["count"] == 1 and "mathutil.py:2" in r.data["matches"][0]
    assert do("search_text", pattern="zzz-not-present").data["count"] == 0
    assert do("git_status").data["status"].startswith("(no changes")


def test_edit_cycle_and_diff(rig):
    do, ws, *_ = rig()
    assert not do("edit_file", path="mathutil.py", old_text="nope", new_text="x").ok
    assert not do(
        "edit_file", path="mathutil.py", old_text="a", new_text="b"
    ).ok  # ambiguous: matches several times
    assert do("edit_file", path="mathutil.py", old_text="a - b", new_text="a + b").ok
    assert do("write_file", path="new/pkg/x.py", content="x = 1\n").ok
    status = do("git_status").data["status"]
    assert "M  mathutil.py" in status and "A  new/pkg/x.py" in status
    diff = do("git_diff").data["diff"]
    assert "-    return a - b" in diff and "+    return a + b" in diff and "new/pkg/x.py" in diff
    assert (ws / "new" / "pkg" / "x.py").exists()


def test_validation_and_unknown_tool(rig):
    do, _, task, store = rig()
    r = do("read_file", path="a.py", surprise=1)  # extra fields are rejected
    assert not r.ok and "Invalid arguments" in (r.error or "")
    assert not do("read_file").ok  # missing required
    assert not do("list_files", max_entries=0).ok  # bounds enforced
    r = do("format_disk")
    assert not r.ok and "Unknown tool" in (r.error or "")
    assert store.get_task(task.id).tool_failures == 4


def test_unparseable_model_arguments_get_a_clear_error(rig):
    from harnix.models.base import INVALID_JSON_KEY

    do, _, task, store = rig()
    r = do("read_file", **{INVALID_JSON_KEY: "{not json"})
    assert not r.ok and "not valid JSON" in (r.error or "")
    assert store.get_task(task.id).tool_failures == 1


def test_boundaries_enforced_through_executor(rig, tmp_path):
    do, ws, task, store = rig()
    (ws.parent / "host-secret.txt").write_text("top secret")
    for name, args in [
        ("read_file", {"path": "../host-secret.txt"}),
        ("read_file", {"path": str(ws.parent / "host-secret.txt")}),
        ("write_file", {"path": "../pwned.txt", "content": "x"}),
        ("write_file", {"path": ".git/config", "content": "x"}),
        ("edit_file", {"path": ".git/HEAD", "old_text": "ref", "new_text": "x"}),
        ("list_files", {"path": "../.."}),
        ("search_text", {"path": "..", "pattern": "secret"}),
        ("git_diff", {"path": "../x"}),
        ("run_command", {"command": "python -m pytest", "workdir": ".."}),
        ("run_command", {"command": "python -m pytest | tee out"}),
    ]:
        r = do(name, **args)
        assert not r.ok and "Denied" in (r.error or ""), (name, args, r.error)
    assert not (ws.parent / "pwned.txt").exists()
    assert store.get_task(task.id).policy_denials == 10
    events = [e for e in store.events_after(task.id) if e.type == "tool.authorization"]
    assert all(e.data["decision"] == "deny" for e in events)


def test_write_size_bound(rig):
    do, *_ = rig(max_write_bytes=100)
    r = do("write_file", path="big.txt", content="x" * 500)
    assert not r.ok and "exceeds" in (r.error or "")


def test_sensitive_actions_refused_when_policy_denies(rig):
    do, ws, *_ = rig(approval_policy="deny_sensitive")
    assert not do("delete_file", path="mathutil.py").ok and (ws / "mathutil.py").exists()
    assert not do("run_command", command="curl http://example.com").ok
    assert not do("run_command", command="python -c print(1)").ok


def test_run_tests_reports_real_failure_then_pass(rig):
    do, ws, *_ = rig()
    r = do("run_tests")
    assert r.ok and r.data["exit_code"] == 1 and r.data["passed"] is False
    assert "assert" in r.data["output"].lower()
    do("edit_file", path="mathutil.py", old_text="a - b", new_text="a + b")
    r = do("run_tests")
    assert r.data["exit_code"] == 0 and r.data["passed"] is True
    assert do("run_command", command="python --version").data["output"].startswith("Python")
    assert do("run_command", command="git status").ok


def test_tool_output_is_redacted(rig):
    do, ws, *_ = rig()
    (ws / "cfg.py").write_text('API_KEY = "abcdef1234567890"\n')
    r = do("read_file", path="cfg.py")
    assert "abcdef1234567890" not in r.data["content"]


def test_secret_files_unreadable(rig):
    do, ws, *_ = rig()
    (ws / ".env").write_text("TOKEN=abc123456789")  # even if the agent creates one
    assert not do("read_file", path=".env").ok
    assert ".env" not in do("list_files").data["entries"]
