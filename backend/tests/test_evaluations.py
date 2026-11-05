"""The evaluation harness itself: scoring logic, labelling and refusal to fake real-model runs."""

from __future__ import annotations

import json

import pytest

from harnix.config import ConfigError, Settings
from harnix.evaluations.__main__ import main
from harnix.evaluations.runner import load_cases, run_evaluation, summarize


def test_cases_load_and_have_ground_truth():
    cases = load_cases()
    assert {c.id for c in cases} >= {"off_by_one", "wrong_operator", "missing_feature", "injection_attempt"}
    for c in cases:
        assert c.repo_dir.is_dir() and c.ground_truth and c.scripted_steps


def test_scripted_selftest_catches_lies_and_blocks_injection(settings: Settings):
    s = settings.model_copy(update={"approval_policy": "deny_sensitive"})
    cases = load_cases(only=["off_by_one", "claims_without_fix", "injection_attempt"])
    rep = run_evaluation(s, "scripted", cases, "unit")
    assert rep["provider"] == "scripted-mock" and rep["valid_for_llm_claims"] is False
    assert "NOT LLM performance" in rep["notice"]
    assert rep["selftest_mismatches"] == {}
    by = {r["case"]: r for r in rep["results"]}
    assert by["off_by_one"]["ground_truth_pass"] and by["off_by_one"]["outcome"] == "verified"
    lie = by["claims_without_fix"]
    assert lie["false_claim"] and lie["status"] == "failed" and not lie["ground_truth_pass"]
    assert by["injection_attempt"]["policy_denials"] >= 3 and by["injection_attempt"]["ground_truth_pass"]
    sm = rep["summary"]
    assert sm["false_success_claims"] == 1 and sm["harness_false_accepts"] == 0
    assert sm["completion_rate"] == pytest.approx(2 / 3)


def test_real_provider_run_refuses_without_key(settings: Settings):
    s = settings.model_copy(update={"anthropic_api_key": None})
    with pytest.raises(ConfigError, match="ANTHROPIC_API_KEY"):
        run_evaluation(s, "anthropic", load_cases(only=["off_by_one"]))


def test_summarize_math():
    base = dict(
        status="completed", outcome="verified", ground_truth_pass=True, false_claim=False, harness_false_accept=False,
        tool_failures=1, retries=0, policy_denials=0, duration_seconds=2.0, input_tokens=10, output_tokens=5,
        estimated_cost_usd=0.5,
    )  # fmt: skip
    bad = {
        **base,
        "status": "failed",
        "outcome": "verification_failed",
        "ground_truth_pass": False,
        "false_claim": True,
        "estimated_cost_usd": None,
    }
    s = summarize([base, bad])
    assert s["completion_rate"] == 0.5 and s["false_success_claims"] == 1 and s["estimated_cost_usd"] == 0.5
    assert s["tool_failures"] == 2 and s["mean_duration_seconds"] == 2.0


def test_cli_compare_labels_mock(tmp_path, capsys, monkeypatch):
    f = tmp_path / "r.json"
    f.write_text(
        json.dumps(
            {
                "label": "a",
                "provider": "scripted-mock",
                "valid_for_llm_claims": False,
                "summary": {"cases": 1},
            }
        )
    )
    monkeypatch.setattr("sys.argv", ["x", "compare", str(f), str(f)])
    assert main() == 0
    assert "say nothing about LLM quality" in capsys.readouterr().out
