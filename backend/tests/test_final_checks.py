"""Final verification adds syntax checks and discovers tests the task created, without overstating them."""

from __future__ import annotations

from pathlib import Path

from harnix.execution.project import ProjectInfo
from harnix.verification.engine import Check, CheckResult, aggregate, plan_final_checks

NO_TESTS = Check("tests", "test", None, "No test command detected and no verification command supplied")


def pi(**commands: str) -> ProjectInfo:
    p = ProjectInfo()
    p.commands.update(commands)
    return p


def test_syntax_checks_cover_changed_source_files_only():
    checks = plan_final_checks(
        [NO_TESTS], pi(), ["a.py", "pkg/b.py", "c.js", "notes.md", "dir with space/d.py"], False
    )
    syntax = [c for c in checks if c.kind == "syntax"]
    assert [c.command for c in syntax] == ["python -m py_compile a.py pkg/b.py", "node --check c.js"]
    assert any(c.command is None for c in checks)  # still no tests: the honest placeholder stays


def test_tests_the_task_created_run_as_agent_tests():
    checks = plan_final_checks([NO_TESTS], pi(test="python -m pytest -q"), ["a.py"], False)
    kinds = {c.command: c.kind for c in checks}
    assert kinds["python -m pytest -q"] == "agent_test" and not any(c.command is None for c in checks)


def test_existing_tests_stay_blocking_and_are_not_duplicated():
    base = [Check("python -m pytest -q", "test", "python -m pytest -q")]
    checks = plan_final_checks(base, pi(test="python -m pytest -q"), [], True)
    assert [c.kind for c in checks] == ["test"]


def test_agent_tests_and_syntax_alone_never_verify_but_are_reported():
    ok = lambda k, n: CheckResult(n, k, n, "passed", 0)  # noqa: E731
    v = aggregate([], [ok("syntax", "python -m py_compile a.py"), ok("agent_test", "python -m pytest -q")])
    assert v.outcome == "unverified"
    assert "agent-written tests" in v.reasons[0] and "not independent evidence" in v.reasons[0]


def test_a_failing_agent_test_or_syntax_error_blocks():
    bad = CheckResult("x", "syntax", "python -m py_compile a.py", "failed", 1)
    assert aggregate([], [bad]).outcome == "verification_failed"
    bad2 = CheckResult("y", "agent_test", "python -m pytest -q", "failed", 1)
    assert aggregate([], [bad2]).outcome == "verification_failed"


# ---- in a task: a repository with no tests -----------------------------------------------------------------

from harnix.models.scripted import ScriptedProvider  # noqa: E402

from .conftest import call, make_runner, new_task, step, wait_terminal  # noqa: E402


def _bare_repo(settings) -> Path:
    r = settings.repo_roots[0] / "bare"
    r.mkdir()
    (r / "dedupe.py").write_text("def dedupe(xs):\n    return xs\n")
    return r


def _checks(store, tid):
    return [
        e.data
        for e in store.events_after(tid, 0, 5000)
        if e.type == "verification.check" and e.data["phase"] == "final"
    ]


def test_no_tests_still_runs_a_syntax_check_and_reports_it(settings, store):
    repo = _bare_repo(settings)
    p = ScriptedProvider([
        step("", call("edit_file", path="dedupe.py", old_text="return xs", new_text="return list(dict.fromkeys(xs))")),
        step("done"),
    ])  # fmt: skip
    runner = make_runner(settings, store, p)
    t = new_task(store, repo, settings)
    runner.start(t.id)
    done = wait_terminal(store, t.id)
    assert done.status == "completed" and done.outcome == "unverified"  # honest: nothing independent
    syn = [c for c in _checks(store, t.id) if c["kind"] == "syntax"]
    assert syn and syn[0]["status"] == "passed" and "py_compile dedupe.py" in syn[0]["command"]


def test_a_syntax_error_in_the_changed_file_fails_verification(settings, store):
    repo = _bare_repo(settings)
    p = ScriptedProvider([
        step("", call("edit_file", path="dedupe.py", old_text="return xs", new_text="return (")),
        step("done"), step("still done"), step("final"),
    ])  # fmt: skip
    runner = make_runner(settings, store, p)
    t = new_task(store, repo, settings)
    runner.start(t.id)
    done = wait_terminal(store, t.id)
    assert done.outcome == "verification_failed"
    assert any(c["kind"] == "syntax" and c["status"] == "failed" for c in _checks(store, t.id))


def test_tests_the_agent_wrote_are_run_but_do_not_verify(settings, store):
    repo = _bare_repo(settings)
    test = "from dedupe import dedupe\n\n\ndef test_d():\n    assert dedupe([1, 1, 2]) == [1, 2]\n"
    p = ScriptedProvider([
        step("", call("edit_file", path="dedupe.py", old_text="return xs", new_text="return list(dict.fromkeys(xs))")),
        step("", call("write_file", path="test_dedupe.py", content=test)),
        step("added a test"),
    ])  # fmt: skip
    runner = make_runner(settings, store, p)
    t = new_task(store, repo, settings)
    runner.start(t.id)
    done = wait_terminal(store, t.id)
    ch = _checks(store, t.id)
    assert any(c["kind"] == "agent_test" and c["status"] == "passed" for c in ch)
    assert done.outcome == "unverified"
