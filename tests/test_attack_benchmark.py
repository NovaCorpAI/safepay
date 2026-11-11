"""The attack benchmark harness, in deterministic policy mode (offline)."""

from __future__ import annotations

import os

for _k in ("NEBIUS_API_KEY", "TAVILY_API_KEY", "PAYPAL_CLIENT_ID", "PAYPAL_SECRET"):
    os.environ[_k] = ""

from benchmark.attacks.run_attack_benchmark import load_cases, run_benchmark  # noqa: E402


def test_policy_mode_never_blocks_legit():
    run = run_benchmark("policy", load_cases())
    assert run["legit_false_positives"] == 0


def test_policy_mode_catches_the_deterministic_attacks():
    run = run_benchmark("policy", load_cases())
    by_id = {r["id"]: r for r in run["results"]}
    for cid in ("over_per_tx", "currency_switch", "slow_drain", "velocity_burst"):
        assert by_id[cid]["caught"], cid
    # Slow drain is caught by the per-recipient cap on the third payment.
    assert by_id["slow_drain"]["verdicts"] == ["allow", "allow", "block"]
    # Velocity: six allowed, the seventh and eighth blocked.
    assert by_id["velocity_burst"]["verdicts"].count("block") == 2


def test_policy_mode_misses_within_limit_attacks_by_design():
    # Documents the point of the model layers: these stay inside every limit.
    run = run_benchmark("policy", load_cases())
    by_id = {r["id"]: r for r in run["results"]}
    for cid in ("inj_processing_fee", "goal_casino", "unknown_payee"):
        assert not by_id[cid]["caught"], cid


def test_caps_mode_is_limits_only():
    # Spending limits alone: they see how much, never to whom or why.
    run = run_benchmark("caps", load_cases())
    by_id = {r["id"]: r for r in run["results"]}
    assert run["legit_false_positives"] == 0
    for cid in ("over_per_tx", "currency_switch", "slow_drain", "velocity_burst"):
        assert by_id[cid]["caught"], cid
    for cid in ("lookalike_recipient", "amount_inflation", "duplicate_invoice",
                "payee_change", "history_inflation", "inj_processing_fee", "goal_casino"):
        assert not by_id[cid]["caught"], cid
    policy = run_benchmark("policy", load_cases())
    assert policy["attacks_caught"] > run["attacks_caught"]
