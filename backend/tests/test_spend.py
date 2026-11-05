"""Spending is measured, never limited: cost and tokens are counted and reported, nothing is stopped for them."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from harnix.api.app import create_app
from harnix.config import Settings
from harnix.models.scripted import ScriptedProvider
from harnix.persistence.store import Store
from harnix.policies.budget import Budget

from .conftest import call, make_runner, new_task, step, wait_for, wait_terminal
from .test_apply import FIX

# 1000 USD per 1M tokens => 1000 tokens cost exactly $1: easy arithmetic
PRICES = {"price_input_per_mtok": 1000.0, "price_output_per_mtok": 1000.0}
# the limits Forge used to enforce; they are still accepted (an old .env keeps working) but stop nothing
OLD_LIMITS = {"spend_limit_usd": 0.5, "max_cost_per_task_usd": 0.5, "max_total_tokens": 100}


def costly(n_in=500, n_out=500):
    return {"text": "", "tool_calls": [call("list_files")], "usage": {"input": n_in, "output": n_out}}


def test_cost_math():
    s = Settings(_env_file=None, price_input_per_mtok=0.20, price_output_per_mtok=1.25)  # type: ignore[call-arg]
    assert s.cost_usd(1_000_000, 1_000_000) == pytest.approx(1.45)
    assert s.cost_usd(0, 0) == 0
    assert Settings(_env_file=None).cost_usd(5, 5) is None  # type: ignore[call-arg]


def test_the_budget_counts_cost_and_tokens_and_never_stops_for_them():
    b = Budget(10, 100, 10, price_in_per_mtok=1000.0, price_out_per_mtok=1000.0)
    for _ in range(5):
        b.next_iteration()
        b.add_usage(500, 500)  # $1.00 each, $5.00 in total
    assert b.cost_usd == pytest.approx(5.0) and b.tokens == 5000
    b.add_tokens(10**9)
    b.next_iteration()  # a billion tokens later it still runs: only iterations, time and tool calls stop a loop
    # without prices nothing is estimated and nothing is invented
    b2 = Budget(10, 100, 10)
    b2.add_usage(10**6, 10**6)
    assert b2.cost_usd == 0.0 and b2.tokens == 2 * 10**6


def test_a_task_runs_to_the_end_however_much_it_costs(settings: Settings, store: Store, repo: Path):
    s = settings.model_copy(update={**PRICES, **OLD_LIMITS})
    provider = ScriptedProvider([costly()] * 3 + [step("", FIX), step("fixed")])
    runner = make_runner(s, store, provider)
    task = new_task(store, repo, s)
    runner.start(task.id)
    t = wait_terminal(store, task.id)
    assert t.status == "completed" and t.outcome == "verified" and provider.calls == 5
    assert (
        t.input_tokens >= 1500 and t.output_tokens >= 1500
    )  # counted in full (the two plain steps add a little)
    assert t.report["usage"]["estimated_cost_usd"] >= 3.0  # and priced in full: six times the old $0.50 caps


def test_new_runs_are_never_refused_for_spend(settings: Settings, repo: Path):
    s = settings.model_copy(update={**PRICES, **OLD_LIMITS})
    provider = ScriptedProvider(
        [costly()] * 2 + [step("", FIX), step("fixed")] + [step("", FIX), step("fixed")]
    )
    app = create_app(s, provider_factory=lambda: provider, create_schema=True)
    with TestClient(app) as c:
        assert c.get("/api/health").json()["spend"] == {
            "spent_usd": 0.0
        }  # a number, no limit and no remainder
        first = c.post("/api/tasks", json={"repo_path": str(repo), "instructions": "x"}).json()
        assert c.post(f"/api/tasks/{first['id']}/run").status_code == 202
        wait_for(lambda: c.get(f"/api/tasks/{first['id']}").json()["status"] in ("completed", "failed"), 120)
        assert c.get("/api/health").json()["spend"]["spent_usd"] >= 2.0  # far above the old $0.50 limit
        second = c.post("/api/tasks", json={"repo_path": str(repo), "instructions": "y"}).json()
        r = c.post(f"/api/tasks/{second['id']}/run")
        assert r.status_code == 202, r.text  # used to be 402 spend_limit


def test_old_limits_are_accepted_without_prices_and_reported_as_ignored(
    capsys: pytest.CaptureFixture[str], settings: Settings
):
    old = Settings(_env_file=None, spend_limit_usd=3.0, max_total_tokens=1000)  # type: ignore[call-arg]  # no prices: fine now
    assert old.ignored_limits() == ["FORGE_SPEND_LIMIT_USD", "FORGE_MAX_TOTAL_TOKENS"]
    assert Settings(_env_file=None).ignored_limits() == []  # type: ignore[call-arg]
    create_app(
        settings.model_copy(update=OLD_LIMITS),
        provider_factory=lambda: ScriptedProvider([]),
        create_schema=True,
    )
    captured = capsys.readouterr()
    text = captured.out + captured.err
    assert "FORGE_SPEND_LIMIT_USD" in text and "no longer limit anything" in text


def test_spend_is_none_without_prices(settings: Settings, store: Store):
    assert make_runner(settings, store, ScriptedProvider([])).spend() is None


def test_api_health_spend_null_without_prices(settings: Settings):
    with TestClient(
        create_app(settings, provider_factory=lambda: ScriptedProvider([]), create_schema=True)
    ) as c:
        assert c.get("/api/health").json()["spend"] is None
