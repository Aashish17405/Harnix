"""Unit tests: state machine, path containment, command policy, authorization, budgets, redaction."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from harnix.config import Settings
from harnix.harness.states import TERMINAL, TRANSITIONS, InvalidTransition, TaskStatus, check_transition
from harnix.persistence.store import Store
from harnix.policies.budget import Budget, BudgetExceeded
from harnix.policies.commands import CommandSyntaxError, classify_command, parse_command
from harnix.policies.paths import PathViolation, WorkspacePaths, authorize_repo
from harnix.policies.policy import PolicyEngine
from harnix.policies.redaction import REDACTED, Redactor
from harnix.tools.schemas import (
    DeleteFileArgs,
    EditFileArgs,
    ReadFileArgs,
    RunCommandArgs,
    RunTestsArgs,
    WriteFileArgs,
)

from .conftest import new_task

# ---- state machine ------------------------------------------------------------------------------


def test_terminal_states_have_no_exits():
    for s in TERMINAL:
        assert TRANSITIONS[s] == frozenset()


def test_valid_and_invalid_transitions():
    check_transition(TaskStatus.QUEUED, TaskStatus.RUNNING)
    check_transition(TaskStatus.RUNNING, TaskStatus.AWAITING_APPROVAL)
    check_transition(TaskStatus.VERIFYING, TaskStatus.RUNNING)  # recovery loop
    for src, dst in [
        (TaskStatus.QUEUED, TaskStatus.COMPLETED),
        (TaskStatus.RUNNING, TaskStatus.COMPLETED),  # completion must pass through verifying
        (TaskStatus.COMPLETED, TaskStatus.RUNNING),
        (TaskStatus.CANCELLED, TaskStatus.RUNNING),
    ]:
        with pytest.raises(InvalidTransition):
            check_transition(src, dst)


def test_store_transition_is_compare_and_set(store: Store, settings: Settings, repo: Path):
    t = new_task(store, repo, settings)
    store.transition(t.id, TaskStatus.RUNNING)
    with pytest.raises(InvalidTransition):  # a duplicate start must fail, not silently succeed
        store.transition(t.id, TaskStatus.RUNNING)
    store.transition(t.id, TaskStatus.VERIFYING)
    store.transition(t.id, TaskStatus.COMPLETED)
    assert store.get_task(t.id).finished_at is not None
    types = [e.type for e in store.events_after(t.id)]
    assert types.count("task.state") == 3 and types[0] == "task.created"


# ---- paths -------------------------------------------------------------------------------------


@pytest.fixture
def ws(tmp_path: Path) -> WorkspacePaths:
    root = tmp_path / "workspace"
    (root / "src").mkdir(parents=True)
    (root / "src" / "a.py").write_text("x")
    (tmp_path / "outside.txt").write_text("secret")
    return WorkspacePaths(root)


def test_resolve_inside(ws: WorkspacePaths):
    assert ws.rel(ws.resolve("src/a.py")) == "src/a.py"
    assert ws.rel(ws.resolve(".")) == "."
    assert ws.rel(ws.resolve("src/../src/a.py")) == "src/a.py"


@pytest.mark.parametrize("bad", ["../outside.txt", "src/../../outside.txt", "..", "a/../../.."])
def test_traversal_rejected(ws: WorkspacePaths, bad: str):
    with pytest.raises(PathViolation):
        ws.resolve(bad)


def test_absolute_outside_rejected(ws: WorkspacePaths, tmp_path: Path):
    with pytest.raises(PathViolation):
        ws.resolve(str(tmp_path / "outside.txt"))
    with pytest.raises(PathViolation):
        ws.resolve("C:\\Windows\\win.ini" if os.name == "nt" else "/etc/passwd")
    assert ws.resolve(str(ws.root / "src" / "a.py")).name == "a.py"  # absolute but inside is fine


def test_nul_byte_rejected(ws: WorkspacePaths):
    with pytest.raises(PathViolation):
        ws.resolve("a\x00b")


def _dir_link(link: Path, target: Path) -> None:
    """Directory symlink; on Windows without symlink privilege fall back to a junction (also followed by resolve)."""
    try:
        link.symlink_to(target, target_is_directory=True)
    except (OSError, NotImplementedError):
        if os.name != "nt":
            pytest.skip("symlinks not permitted on this system")
        subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], check=True, capture_output=True)


def test_dir_link_escape_rejected(ws: WorkspacePaths, tmp_path: Path):
    _dir_link(ws.root / "dlink", tmp_path)
    with pytest.raises(PathViolation):
        ws.resolve("dlink/outside.txt")
    with pytest.raises(PathViolation):  # writing a new file through an escaping directory link
        ws.resolve("dlink/new.txt")
    inner = ws.root / "inner"
    inner.mkdir()
    _dir_link(ws.root / "ok", inner)  # links that stay inside the workspace are fine
    assert ws.resolve("ok").name == "inner"


def test_file_symlink_escape_rejected(ws: WorkspacePaths, tmp_path: Path):
    try:
        (ws.root / "link.txt").symlink_to(tmp_path / "outside.txt")
    except (OSError, NotImplementedError):
        pytest.skip("file symlinks need privilege on this system (directory links are covered above)")
    with pytest.raises(PathViolation):
        ws.resolve("link.txt")


def test_secret_and_protected(ws: WorkspacePaths):
    for name in [".env", ".env.production", "keys/id_rsa", "server.pem", ".ssh/config"]:
        assert ws.is_secret(ws.root / name), name
    assert not ws.is_secret(ws.root / ".env.example")  # placeholder files are not secrets
    assert not ws.is_secret(ws.root / "src" / "a.py")
    assert ws.is_protected(ws.root / ".git" / "config")
    assert not ws.is_protected(ws.root / "src")


def test_authorize_repo(tmp_path: Path):
    root = tmp_path / "repos"
    (root / "a").mkdir(parents=True)
    (tmp_path / "other").mkdir()
    assert authorize_repo(str(root / "a"), [root]) == (root / "a").resolve()
    with pytest.raises(PathViolation):
        authorize_repo(str(tmp_path / "other"), [root])
    with pytest.raises(PathViolation):
        authorize_repo(str(root / "a"), [])  # nothing authorized -> nothing allowed
    with pytest.raises(PathViolation):
        authorize_repo(str(root / "missing"), [root])
    with pytest.raises(PathViolation):  # sibling directory sharing a prefix must not match
        (tmp_path / "repos-evil").mkdir()
        authorize_repo(str(tmp_path / "repos-evil"), [root])


def test_authorize_repo_any_folder_is_an_explicit_setting_and_never_a_drive_root(tmp_path: Path):
    from harnix.config import Settings
    from harnix.policies.paths import ANY_ROOT

    roots = Settings(allowed_repo_roots="*", _env_file=None).repo_roots  # type: ignore[call-arg]
    assert roots == [ANY_ROOT]
    assert authorize_repo(str(tmp_path), roots) == tmp_path.resolve()
    with pytest.raises(PathViolation):
        authorize_repo(str(tmp_path / "missing"), roots)  # still has to be a directory
    with pytest.raises(PathViolation):
        authorize_repo(tmp_path.anchor, roots)  # C:\ or /
    assert Settings(allowed_repo_roots="", _env_file=None).repo_roots == []  # unset stays closed  # type: ignore[call-arg]


# ---- commands ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "cmd,kind",
    [
        ("python -m pytest -q", "allow"),
        ("pytest tests/", "allow"),
        ("python -m ruff check .", "allow"),
        ("npm test", "allow"),
        ("npm run lint", "allow"),
        ("git status", "allow"),
        ("git diff --stat", "allow"),
        ("python -c 'print(1)'", "approve"),
        ("python script.py", "approve"),
        ("npm install left-pad", "approve"),
        ("git push origin main", "approve"),
        ("git commit -am x", "approve"),
        ("git -c core.pager=evil log", "approve"),
        ("git diff --output=/tmp/x", "approve"),
        ("rm -rf .", "approve"),
        ("del file.txt", "approve"),
        ("curl http://example.com", "approve"),
        ("./run.sh", "approve"),
        ("sudo ls", "deny"),
        ("C:/Windows/System32/shutdown.exe /s", "deny"),
    ],
)
def test_classify(cmd: str, kind: str):
    assert classify_command(parse_command(cmd)).kind == kind


@pytest.mark.parametrize("cmd", ["ls | grep x", "a && b", "echo hi > f", "x; y", ""])
def test_shell_syntax_rejected(cmd: str):
    with pytest.raises(CommandSyntaxError):
        parse_command(cmd)


def test_unbalanced_quote_rejected():
    with pytest.raises(CommandSyntaxError):
        parse_command("python -m 'pytest")


# ---- authorization -----------------------------------------------------------------------------


def engine(settings: Settings, root: Path, **kw) -> PolicyEngine:
    return PolicyEngine(settings.model_copy(update=kw), WorkspacePaths(root), trusted_commands=["make check"])


def test_authorize_matrix(settings: Settings, tmp_path: Path):
    root = tmp_path / "w"
    root.mkdir()
    p = engine(settings, root)
    assert p.authorize("read_file", ReadFileArgs(path="a.py")).kind == "allow"
    assert p.authorize("read_file", ReadFileArgs(path="../x")).kind == "deny"
    assert p.authorize("read_file", ReadFileArgs(path=".env")).kind == "deny"
    assert p.authorize("write_file", WriteFileArgs(path="a.py", content="x")).kind == "allow"
    assert p.authorize("write_file", WriteFileArgs(path=".git/config", content="x")).kind == "deny"
    assert p.authorize("write_file", WriteFileArgs(path="../evil", content="x")).kind == "deny"
    assert p.authorize("edit_file", EditFileArgs(path="a.py", old_text="a", new_text="b")).kind == "allow"
    assert p.authorize("delete_file", DeleteFileArgs(path="a.py")).kind == "approve"
    assert p.authorize("run_command", RunCommandArgs(command="rm -rf .")).kind == "approve"
    assert p.authorize("run_command", RunCommandArgs(command="a | b")).kind == "deny"
    assert p.authorize("run_command", RunCommandArgs(command="pytest", workdir="..")).kind == "deny"
    assert p.authorize("run_command", RunCommandArgs(command="make check")).kind == "allow"  # trusted check
    assert p.authorize("run_tests", RunTestsArgs()).kind == "allow"
    assert p.authorize("run_tests", RunTestsArgs(command="rm -rf .")).kind == "approve"
    assert p.authorize("nope", RunTestsArgs()).kind == "deny"


def test_policy_modes(settings: Settings, tmp_path: Path):
    root = tmp_path / "w"
    root.mkdir()
    strict = engine(settings, root, approval_policy="strict")
    assert strict.authorize("write_file", WriteFileArgs(path="a", content="x")).kind == "approve"
    deny = engine(settings, root, approval_policy="deny_sensitive")
    assert deny.authorize("delete_file", DeleteFileArgs(path="a")).kind == "deny"
    assert deny.authorize("run_command", RunCommandArgs(command="curl x")).kind == "deny"
    assert deny.authorize("read_file", ReadFileArgs(path="a")).kind == "allow"


# ---- budgets -----------------------------------------------------------------------------------


def test_budget_iterations_tools_time_and_tokens_only_counted():
    now = [0.0]
    b = Budget(2, 10, 2, clock=lambda: now[0])
    assert b.next_iteration() == 1 and b.next_iteration() == 2
    with pytest.raises(BudgetExceeded) as e:
        b.next_iteration()
    assert e.value.kind == "iterations"
    b.add_tool_call()
    b.add_tool_call()
    with pytest.raises(BudgetExceeded) as e:
        b.add_tool_call()
    assert e.value.kind == "tool_calls"
    b2 = Budget(10, 10, 10, clock=lambda: now[0])
    b2.add_tokens(10**9)
    assert b2.next_iteration() == 1 and b2.tokens == 10**9  # tokens are measured, never a reason to stop
    b3 = Budget(10, 10, 10, clock=lambda: now[0])
    now[0] = 11.0
    with pytest.raises(BudgetExceeded) as e:
        b3.check_time()
    assert e.value.kind == "duration"


def test_budget_pause_excludes_waiting():
    now = [0.0]
    b = Budget(10, 10, 10, clock=lambda: now[0])
    now[0] = 50.0
    b.pause(45.0)  # 45s waiting for a human does not count
    b.check_time()
    assert abs(b.elapsed() - 5.0) < 1e-9


# ---- redaction ---------------------------------------------------------------------------------


def test_redaction_patterns_and_exact_secrets():
    r = Redactor(["my-exact-secret-value"])
    text = (
        "key sk-ant-api03-abcdefghijklmnop123 and AKIAABCDEFGHIJKLMNOP and ghp_" + "a" * 36 + "\n"
        "export DB_PASSWORD=hunter2hunter2\napi_key: 'abcd1234efgh'\nAuthorization: Bearer abcdefghijkl\n"
        "my-exact-secret-value\n-----BEGIN RSA PRIVATE KEY-----\nMIIabc\n-----END RSA PRIVATE KEY-----"
    )
    out = r.redact(text)
    for leaked in [
        "sk-ant-api03",
        "AKIAABCDEFGHIJKLMNOP",
        "ghp_aaaa",
        "hunter2",
        "abcd1234efgh",
        "abcdefghijkl",
        "my-exact-secret",
        "MIIabc",
    ]:
        assert leaked not in out, leaked
    assert REDACTED in out
    assert r.redact("nothing secret here: 42") == "nothing secret here: 42"


def test_redaction_leaves_ordinary_code_alone():
    r = Redactor()
    code = (
        "max_tokens=16000\ntoken = request.token\npassword = None\nsecret_key = os.environ['X']\n"
        "TOKEN_LIMIT = 100\napi_key = settings.api_key\nuser_password: str = field()\n"
    )
    assert r.redact(code) == code
    assert r.redact_obj({"a": ["token=abcdefgh1234", 3]}) == {"a": [f"token={REDACTED}", 3]}
