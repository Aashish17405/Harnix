"""CLI: python -m forge.evaluations run|compare ..."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from harnix.config import ConfigError, Settings
from harnix.evaluations.runner import CASES_DIR, load_cases, run_evaluation


def _print(report: dict) -> None:
    if report.get("notice"):
        print(f"*** {report['notice']} ***\n")
    print(f"provider={report['provider']} model={report['model']}\n")
    print(
        f"{'case':22} {'status':10} {'outcome':20} {'truth':6} {'claim':6} {'denied':6} {'retries':7} {'secs':>6} {'cost $':>8}"
    )
    for r in report["results"]:
        cost = "n/a" if r["estimated_cost_usd"] is None else f"{r['estimated_cost_usd']:.4f}"
        print(
            f"{r['case']:22} {r['status']:10} {str(r['outcome']):20} {str(r['ground_truth_pass']):6} "
            f"{('FALSE' if r['false_claim'] else 'ok'):6} {r['policy_denials']:<6} {r['retries']:<7} {r['duration_seconds']:>6} {cost:>8}"
        )
    if report.get("skipped_for_budget"):
        print(
            f"\nSTOPPED: budget ${report['budget_usd']} reached; not run: {', '.join(report['skipped_for_budget'])}"
        )
    print("\nsummary:", json.dumps(report["summary"], indent=2))
    if report.get("selftest_mismatches"):
        print("\nSELF-TEST MISMATCHES:", json.dumps(report["selftest_mismatches"], indent=2))


def cmd_run(a: argparse.Namespace) -> int:
    overrides: dict = {"execution_mode": a.execution_mode, "approval_policy": a.approval_policy}
    if a.max_iterations:
        overrides["max_iterations"] = a.max_iterations
    if a.max_recovery is not None:
        overrides["max_recovery_attempts"] = a.max_recovery
    for flag, field in (
        ("model", "model"),
        ("price_input", "price_input_per_mtok"),
        ("price_output", "price_output_per_mtok"),
    ):
        if getattr(a, flag) is not None:
            overrides[field] = getattr(a, flag)
    try:
        settings = Settings(**overrides).model_copy(update={"provider": a.provider, "scripted_file": "n/a"})
        cases = load_cases(Path(a.cases), a.only)
        if not cases:
            print("no cases matched", file=sys.stderr)
            return 2
        report = run_evaluation(settings, a.provider, cases, a.label, a.budget_usd)
    except ConfigError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    _print(report)
    if a.out:
        Path(a.out).write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"\nwrote {a.out}")
    return 1 if report.get("selftest_mismatches") else 0


def cmd_compare(a: argparse.Namespace) -> int:
    reports = [json.loads(Path(p).read_text(encoding="utf-8")) for p in a.reports]
    keys = list(reports[0]["summary"])
    print(f"{'metric':36}" + "".join(f"{r['label'][:20]:>22}" for r in reports))
    print(f"{'provider':36}" + "".join(f"{r['provider']:>22}" for r in reports))
    for k in keys:
        print(
            f"{k:36}"
            + "".join(
                f"{str(round(r['summary'][k], 3) if isinstance(r['summary'][k], float) else r['summary'][k]):>22}"
                for r in reports
            )
        )
    if any(not r["valid_for_llm_claims"] for r in reports):
        print("\nNote: scripted-mock reports are harness self-tests and say nothing about LLM quality.")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(prog="python -m forge.evaluations")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="run evaluation cases")
    r.add_argument("--provider", choices=["anthropic", "openai", "scripted"], default="scripted")
    r.add_argument("--model", help="override FORGE_MODEL for this run")
    r.add_argument(
        "--price-input", type=float, help="USD per 1M input tokens (overrides FORGE_PRICE_INPUT_PER_MTOK)"
    )
    r.add_argument(
        "--price-output", type=float, help="USD per 1M output tokens (overrides FORGE_PRICE_OUTPUT_PER_MTOK)"
    )
    r.add_argument(
        "--budget-usd", type=float, help="stop before the next case once this estimated spend is reached"
    )
    r.add_argument("--cases", default=str(CASES_DIR))
    r.add_argument("--only", nargs="*")
    r.add_argument("--execution-mode", choices=["local", "docker"], default="local")
    r.add_argument(
        "--approval-policy", choices=["sensitive", "strict", "deny_sensitive"], default="deny_sensitive"
    )
    r.add_argument("--max-iterations", type=int)
    r.add_argument("--max-recovery", type=int)
    r.add_argument("--label", default="")
    r.add_argument("--out")
    r.set_defaults(fn=cmd_run)
    c = sub.add_parser("compare", help="compare result files from different configurations")
    c.add_argument("reports", nargs="+")
    c.set_defaults(fn=cmd_compare)
    a = p.parse_args()
    return a.fn(a)


if __name__ == "__main__":
    raise SystemExit(main())
