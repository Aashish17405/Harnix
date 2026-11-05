"""Skills, sub-agents, memory, thinking and context management inside real task runs.

The model is the scripted mock (labelled as such everywhere): these tests prove the harness wiring, policy and
accounting, not that a real model uses these features well.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from harnix.api.app import create_app
from harnix.config import Settings
from harnix.models.base import Message, ModelResponse, ProviderError, TextBlock, ToolSpec
from harnix.models.scripted import ScriptedProvider
from harnix.persistence.store import Store
from harnix.policies.paths import repo_key

from .conftest import call, make_runner, new_task, step, wait_for, wait_terminal

FIX = call("edit_file", path="mathutil.py", old_text="a - b", new_text="a + b")
CHILD = "You are a sub-agent of Forge"
SUMMARY = (
    "## Objective\n- fix add\n## Work State\n### Completed\n- read files\n## Next Move\n1. edit mathutil.py"
)


class Router(ScriptedProvider):
    """Scripted mock that also answers compaction requests (recognized by their transcript) and records requests."""

    def __init__(self, steps: list[Any]):
        super().__init__(steps)
        self.seen: list[dict[str, Any]] = []

    def complete(self, *, system: str, messages: list[Message], tools: list[ToolSpec], max_tokens: int, timeout: float,
                 thinking: str | None = None) -> ModelResponse:  # fmt: skip
        text = "\n".join(b.text for b in messages[0].content if isinstance(b, TextBlock))
        self.seen.append({"system": system, "first": text, "tools": [t.name for t in tools], "thinking": thinking,
                          "summary": "<transcript>" in text, "messages": messages})  # fmt: skip
        if "<transcript>" in text:
            return ModelResponse(SUMMARY, [], "end_turn", message=Message("assistant", [TextBlock(SUMMARY)]))
        idx = self.calls
        if idx < len(self.steps) and isinstance(self.steps[idx], dict) and self.steps[idx].get("overflow"):
            self.calls += 1
            raise ProviderError("too long", kind="context_overflow")
        return super().complete(
            system=system,
            messages=messages,
            tools=tools,
            max_tokens=max_tokens,
            timeout=timeout,
            thinking=thinking,
        )


def run(settings: Settings, store: Store, repo: Path, steps: list[Any], **task_kw):
    provider = Router(steps)
    runner = make_runner(settings, store, provider)
    task = new_task(store, repo, settings, **task_kw)
    runner.start(task.id)
    return provider, task, wait_terminal(store, task.id)


def of(store: Store, task_id: str, type_: str) -> list[dict[str, Any]]:
    return [e.data for e in store.events_after(task_id, 0, 5000) if e.type == type_]


def main_calls(p: Router) -> list[dict[str, Any]]:
    return [s for s in p.seen if not s["summary"] and not s["system"].startswith(CHILD)]


# ---- thinking --------------------------------------------------------------------------------------------


def test_thinking_level_reaches_the_model_and_is_recorded(settings: Settings, store: Store, repo: Path):
    p, task, done = run(settings, store, repo, [step("", FIX), step("fixed")], thinking="xhigh")
    assert done.status == "completed" and done.thinking == "xhigh"
    assert {s["thinking"] for s in p.seen} == {"xhigh"}
    assert of(store, task.id, "model.response")[0]["thinking"]["requested"] == "xhigh"
    p2, _, _ = run(settings, store, repo, [step("", FIX), step("fixed")])
    assert {s["thinking"] for s in p2.seen} == {"medium"}  # FORGE_THINKING_LEVEL default


# ---- skills ----------------------------------------------------------------------------------------------


@pytest.fixture
def skilled(settings: Settings, tmp_path: Path) -> Settings:
    d = tmp_path / "skills"
    (d / "pytest-fix" / "reference").mkdir(parents=True)
    (d / "pytest-fix" / "SKILL.md").write_text(
        "---\nname: Fix failing tests\ndescription: How to fix a failing pytest test\n---\nAlways run the test first.\n"
    )
    (d / "pytest-fix" / "reference" / "tips.md").write_text("Read the assertion values.")
    (d / "deploy").mkdir()
    (d / "deploy" / "SKILL.md").write_text(
        "---\ndescription: deploy\ndisable-model-invocation: true\n---\nnever automatic\n"
    )
    return settings.model_copy(update={"skills_dirs": str(d)})


def test_skills_are_listed_loaded_and_preloaded(skilled: Settings, store: Store, repo: Path):
    steps = [
        step("", call("load_skill", id="pytest-fix", file="reference/tips.md")),
        step("", FIX),
        step("fixed"),
    ]
    p, task, done = run(skilled, store, repo, steps, skills=["deploy", "nope"])
    assert done.status == "completed"
    first = main_calls(p)[0]
    assert (
        '<skill id="pytest-fix">' in first["system"] and "deploy" not in first["system"]
    )  # opted out of listing
    assert "load_skill" in first["tools"]
    assert (
        '<skill_content id="deploy"' in first["first"] and "never automatic" in first["first"]
    )  # preloaded by the task
    loaded = [r for r in of(store, task.id, "tool.result") if r["tool"] == "load_skill"][0]
    assert loaded["ok"] and "Read the assertion values." in loaded["data"]["content"]
    assert of(store, task.id, "skill.loaded") == [{"id": "deploy", "how": "preloaded", "source": "operator"}]
    assert of(store, task.id, "skill.missing")[0]["id"] == "nope"


def test_without_skills_the_tool_is_not_offered(settings: Settings, store: Store, repo: Path):
    p, task, _ = run(settings, store, repo, [step("", FIX), step("fixed")])
    assert (
        "load_skill" not in main_calls(p)[0]["tools"] and "available_skills" not in main_calls(p)[0]["system"]
    )
    assert not of(store, task.id, "skills.available")


# ---- sub-agents ------------------------------------------------------------------------------------------


def test_explore_subagent_runs_read_only_with_a_fresh_context(settings: Settings, store: Store, repo: Path):
    steps = [
        step(
            "",
            call(
                "spawn_agent",
                agent="explore",
                description="find add",
                prompt="Where is add defined and what is wrong?",
            ),
        ),
        step("", call("read_file", path="mathutil.py")),  # sub-agent
        step("", FIX),  # sub-agent tries to edit: not its tool
        step("add in mathutil.py subtracts instead of adding (line 2)"),  # sub-agent report
        step("", FIX),  # main
        step("fixed"),
    ]
    p, task, done = run(settings, store, repo, steps)
    assert done.status == "completed" and done.outcome == "verified"
    started = of(store, task.id, "subagent.started")[0]
    assert (
        started["agent"] == "explore-1"
        and started["thinking"] == "low"
        and "edit_file" not in started["tools"]
    )
    child = [s for s in p.seen if s["system"].startswith(CHILD)]
    assert len(child) == 3 and "spawn_agent" not in child[0]["tools"] and "edit_file" not in child[0]["tools"]
    assert (
        "Where is add defined" in child[0]["first"] and "Fix the bug" not in child[0]["first"]
    )  # fresh context
    denied = [
        r
        for r in of(store, task.id, "tool.result")
        if r.get("agent") == "explore-1" and r["tool"] == "edit_file"
    ][0]
    assert not denied["ok"] and "not available to this agent" in denied["error"]
    finished = of(store, task.id, "subagent.finished")[0]
    assert (finished["reason"], finished["steps"]) == ("done", 3)
    report = [r for r in of(store, task.id, "tool.result") if r["tool"] == "spawn_agent"][0]
    assert report["ok"] and "subtracts" in report["data"]["report"]
    assert done.iterations == 6  # the sub-agent's steps count against the task's budget
    assert (
        repo / "mathutil.py"
    ).read_text() == "def add(a, b):\n    return a - b\n"  # untouched: workspace copy


def test_subagent_step_limit_and_unknown_agent(settings: Settings, store: Store, repo: Path):
    s = settings.model_copy(update={"subagent_max_iterations": 2})
    steps = [
        step("", call("spawn_agent", agent="ghost", description="x", prompt="y")),
        step("", call("spawn_agent", agent="general", description="loop", prompt="read forever")),
        step("", call("list_files")), step("", call("list_files", path=".")),  # sub-agent: hits its 2-step limit
        step("", FIX), step("fixed"),
    ]  # fmt: skip
    _, task, done = run(s, store, repo, steps)
    assert done.status == "completed"
    spawned = [r for r in of(store, task.id, "tool.result") if r["tool"] == "spawn_agent"]
    assert (
        not spawned[0]["ok"]
        and "Unknown agent 'ghost'" in spawned[0]["error"]
        and "explore" in spawned[0]["error"]
    )
    assert not spawned[1]["ok"] and "max_steps" in spawned[1]["error"]
    assert of(store, task.id, "subagent.finished")[0]["reason"] == "max_steps"


def test_subagents_cannot_spawn_or_remember(settings: Settings, store: Store, repo: Path):
    s = settings.model_copy(update={"memory_write": "allow"})
    steps = [
        step("", call("spawn_agent", agent="general", description="nest", prompt="try")),
        step("", call("spawn_agent", agent="explore", description="deeper", prompt="x")),
        step("", call("remember", note="sub-agent fact")),
        step("could not"),
        step("", FIX), step("fixed"),
    ]  # fmt: skip
    _, task, _ = run(s, store, repo, steps)
    child = [r for r in of(store, task.id, "tool.result") if r.get("agent") == "general-1"]
    assert [r["ok"] for r in child] == [False, False]
    assert all("not available to this agent" in r["error"] for r in child)
    assert store.memories(repo_key(repo)) == []


# ---- memory -----------------------------------------------------------------------------------------------


def test_memory_is_shown_and_written_under_policy(settings: Settings, store: Store, repo: Path):
    scope = repo_key(repo)
    store.add_memory(scope, "Use pytest -q for tests", "user")
    s = settings.model_copy(update={"memory_write": "allow"})
    steps = [
        step("", call("remember", note="mathutil.add is covered by test_mathutil.py")),
        step("", call("remember", note="mathutil.add is covered by test_mathutil.py")),  # duplicate
        step("", call("remember", note="token=ab12cd34ef56gh78 is the key")),  # secret
        step("", FIX), step("fixed"),
    ]  # fmt: skip
    p, task, done = run(s, store, repo, steps)
    assert done.status == "completed"
    first = main_calls(p)[0]
    assert "<project_memory" in first["first"] and "- [user] Use pytest -q for tests" in first["first"]
    assert "remember" in first["tools"]
    results = [r for r in of(store, task.id, "tool.result") if r["tool"] == "remember"]
    assert results[0]["ok"] and results[0]["data"]["saved"] is True
    assert results[1]["ok"] and results[1]["data"]["saved"] is False
    assert not results[2]["ok"] and "secret" in results[2]["error"]
    notes = store.memories(scope)
    assert [(n.source, n.content) for n in notes] == [
        ("user", "Use pytest -q for tests"), ("agent", "mathutil.add is covered by test_mathutil.py"),
    ]  # fmt: skip
    assert notes[1].task_id == task.id
    # the next task on the same repository sees both notes, the agent one labelled as such
    p2, _, _ = run(s, store, repo, [step("", FIX), step("fixed")])
    assert "- [agent] mathutil.add is covered" in main_calls(p2)[0]["first"]


def test_memory_writes_need_approval_by_default(settings: Settings, store: Store, repo: Path):
    provider = Router([step("", call("remember", note="needs approval")), step("", FIX), step("fixed")])
    runner = make_runner(settings, store, provider)
    task = new_task(store, repo, settings)
    runner.start(task.id)
    pending = wait_for(lambda: [a for a in store.approvals(task.id) if a.status == "pending"])[0]
    assert pending.tool == "remember" and "persists into future tasks" in pending.reason
    store.decide_approval(pending.id, "approved")
    wait_terminal(store, task.id)
    assert [n.content for n in store.memories(repo_key(repo))] == ["needs approval"]


def test_memory_off_or_denied(settings: Settings, store: Store, repo: Path):
    off = settings.model_copy(update={"memory_enabled": False})
    store.add_memory(repo_key(repo), "hidden", "user")
    p, _, _ = run(off, store, repo, [step("", FIX), step("fixed")])
    assert "project_memory" not in main_calls(p)[0]["first"] and "remember" not in main_calls(p)[0]["tools"]
    deny = settings.model_copy(update={"memory_write": "deny"})
    p2, task, _ = run(deny, store, repo, [step("", call("remember", note="x")), step("", FIX), step("fixed")])
    assert "remember" not in main_calls(p2)[0]["tools"]
    r = [x for x in of(store, task.id, "tool.result") if x["tool"] == "remember"][0]
    assert not r["ok"] and "Denied by policy" in r["error"]


# ---- context management ----------------------------------------------------------------------------------


@pytest.fixture
def big_repo(repo: Path) -> Path:
    (repo / "big.py").write_text("".join(f"x_{i} = '{'y' * 70}'\n" for i in range(400)))  # ~32 KB per read
    return repo


def test_pruning_comes_first_and_is_often_enough(settings: Settings, store: Store, big_repo: Path):
    s = settings.model_copy(update={
        "context_window_tokens": 30_000, "compaction_keep_tokens": 9_000, "prune_protect_tokens": 8_000,
        "prune_minimum_tokens": 4_000, "max_iterations": 20,
    })  # fmt: skip
    assert s.compaction_threshold_tokens == 15_000
    reads = [step("", call("read_file", path="big.py", start_line=1 + i)) for i in range(5)]
    p, task, done = run(s, store, big_repo, [*reads, step("", FIX), step("fixed")])
    assert done.status == "completed" and done.outcome == "verified"
    pruned = of(store, task.id, "context.pruned")
    assert len(pruned) >= 2 and all(x["tokens_reclaimed"] >= 4_000 for x in pruned)
    assert all(
        x["tokens_after"] < s.compaction_threshold_tokens <= x["tokens_before"] + 10_000 for x in pruned
    )
    assert not of(
        store, task.id, "context.compacted"
    )  # pruning kept it under the threshold: no model call spent
    assert not [x for x in p.seen if x["summary"]]
    last = main_calls(p)[-1]["messages"]
    results = [b.content for m in last for b in m.content if hasattr(b, "tool_use_id")]
    assert results[-1].startswith('{"ok": true') and not results[-1].startswith(
        "[pruned"
    )  # the newest is intact


def test_compaction_when_pruning_cannot_reclaim_enough(settings: Settings, store: Store, big_repo: Path):
    s = settings.model_copy(update={
        "context_window_tokens": 30_000, "compaction_keep_tokens": 9_000, "prune_minimum_tokens": 10**6,
        "max_iterations": 20,
    })  # fmt: skip
    reads = [step("", call("read_file", path="big.py", start_line=1 + i)) for i in range(4)]
    p, task, done = run(s, store, big_repo, [*reads, step("", FIX), step("fixed")])
    assert done.status == "completed" and done.outcome == "verified"
    assert not of(store, task.id, "context.pruned")
    compacted = of(store, task.id, "context.compacted")
    assert compacted and compacted[0]["reason"] == "threshold"
    assert (
        compacted[0]["tokens_after"] < compacted[0]["tokens_before"]
        and compacted[0]["summarized_messages"] >= 2
    )
    assert "## Objective" in compacted[0]["summary"]
    summary_req = [x for x in p.seen if x["summary"]][0]
    assert summary_req["tools"] == [] and summary_req["thinking"] == "low"  # no tools, cheap
    after = main_calls(p)[-1]
    assert (
        "<conversation-checkpoint>" in after["first"] and "# Task" in after["first"]
    )  # the task stays, plus the summary
    assert (
        after["messages"][1].role == "assistant"
    )  # the kept tail starts at an assistant turn (pairs intact)
    assert store.get_task(task.id).model_calls == len(
        p.seen
    )  # the summary call is accounted like any model call


def test_overflow_from_the_provider_triggers_one_compaction_and_a_retry(
    settings: Settings, store: Store, big_repo: Path
):
    steps = [step("", call("read_file", path="big.py")), step("", call("read_file", path="mathutil.py")),
             step("", call("list_files")), {"overflow": True}, step("", FIX), step("fixed")]  # fmt: skip
    p, task, done = run(settings, store, big_repo, steps)
    assert done.status == "completed"
    compacted = of(store, task.id, "context.compacted")
    assert [c["reason"] for c in compacted] == ["overflow"]


def test_overflow_with_nothing_to_compact_fails_honestly(settings: Settings, store: Store, repo: Path):
    _, task, done = run(settings, store, repo, [{"overflow": True}])
    assert done.status == "failed" and "too long" in (done.error or "")
    assert of(store, task.id, "context.compaction_failed")[0]["reason"] == "overflow"


def test_context_management_can_be_switched_off(settings: Settings, store: Store, big_repo: Path):
    s = settings.model_copy(
        update={
            "compaction_enabled": False,
            "context_window_tokens": 20_000,
            "prune_protect_tokens": 100,
            "prune_minimum_tokens": 10,
        }
    )
    reads = [step("", call("read_file", path="big.py", start_line=1 + i)) for i in range(3)]
    _, task, _ = run(s, store, big_repo, [*reads, step("", FIX), step("fixed")])
    assert not of(store, task.id, "context.pruned") and not of(store, task.id, "context.compacted")


# ---- API ----------------------------------------------------------------------------------------------------


def client(settings: Settings) -> TestClient:
    return TestClient(create_app(settings, provider_factory=lambda: ScriptedProvider([]), create_schema=True))


def test_api_thinking_skills_agents(skilled: Settings, repo: Path, tmp_path: Path):
    agents = tmp_path / "agents"
    agents.mkdir()
    (agents / "reviewer.md").write_text("---\ndescription: Reviews\ntools: [read_file]\n---\nstrict")
    s = skilled.model_copy(update={"agents_dir": str(agents)})
    with client(s) as c:
        sk = c.get("/api/skills").json()
        assert [k["id"] for k in sk["items"]] == ["deploy", "pytest-fix"] and sk["items"][0][
            "listed"
        ] is False
        ag = c.get("/api/agents").json()
        assert [a["id"] for a in ag["items"]] == ["explore", "general", "reviewer"] and ag["items"][2][
            "tools"
        ] == ["read_file"]
        body = {"repo_path": str(repo), "instructions": "fix", "thinking": "high", "skills": ["pytest-fix"]}
        t = c.post("/api/tasks", json=body).json()
        assert t["thinking"] == "high" and t["skills"] == ["pytest-fix"]
        bad = c.post("/api/tasks", json={**body, "skills": ["nope"]})
        assert bad.status_code == 422 and "Unknown skill(s): nope" in bad.json()["error"]["message"]
        assert c.post("/api/tasks", json={**body, "thinking": "max"}).status_code == 422
        assert c.post("/api/tasks", json={**body, "skills": ["../x"]}).status_code == 422


def test_api_memory_crud_and_scoping(settings: Settings, repo: Path):
    other = settings.repo_roots[0] / "other"
    other.mkdir()
    with client(settings) as c:
        r = c.post("/api/memory", json={"repo_path": str(repo), "content": "run  tests\nwith -q"})
        assert (
            r.status_code == 201
            and r.json()["content"] == "run tests with -q"
            and r.json()["source"] == "user"
        )
        mid = r.json()["id"]
        assert [m["id"] for m in c.get("/api/memory", params={"repo_path": str(repo)}).json()] == [mid]
        assert c.get("/api/memory", params={"repo_path": str(other)}).json() == []  # per repository
        assert (
            c.delete(f"/api/memory/{mid}", params={"repo_path": str(other)}).status_code == 404
        )  # wrong scope
        secret = c.post("/api/memory", json={"repo_path": str(repo), "content": "password=hunter2abc123"})
        assert secret.status_code == 422 and "secret" in secret.json()["error"]["message"]
        outside = c.post(
            "/api/memory", json={"repo_path": str(settings.repo_roots[0].parent), "content": "x"}
        )
        assert outside.status_code == 422
        assert c.delete(f"/api/memory/{mid}", params={"repo_path": str(repo)}).json() == {"deleted": mid}
        assert c.get("/api/memory", params={"repo_path": str(repo)}).json() == []
    with client(settings.model_copy(update={"memory_enabled": False})) as c:
        r = c.get("/api/memory", params={"repo_path": str(repo)})
        assert r.status_code == 503 and "FORGE_MEMORY_ENABLED" in r.json()["error"]["message"]
