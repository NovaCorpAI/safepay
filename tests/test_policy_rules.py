"""Tests for the per-recipient cap and velocity policy rules."""

from __future__ import annotations

from safepay.models import PaymentIntent, PolicyConfig
from safepay.policy import SpendLedger, check_policy


def _intent(amount, recipient="v@x.example", agent="a1"):
    return PaymentIntent(recipient=recipient, amount=amount, agent_id=agent)


def test_per_recipient_cap_blocks_slow_drain():
    policy = PolicyConfig(per_tx_limit=500, daily_limit=10000, per_recipient_limit=300)
    ledger = SpendLedger()
    # Two payments of 200 to the same payee: under per-tx each, but 400 > 300.
    r1 = check_policy(_intent(200), policy, ledger)
    assert r1.passed
    ledger.record("a1", 200, "v@x.example")
    r2 = check_policy(_intent(200), policy, ledger)
    assert not r2.passed and "per-recipient" in r2.detail
    # A different payee is unaffected.
    assert check_policy(_intent(200, recipient="other@x.example"), policy, ledger).passed


def test_velocity_limit_blocks_bursts():
    policy = PolicyConfig(per_tx_limit=500, daily_limit=10000,
                          max_payments_per_window=2, velocity_window_seconds=3600)
    ledger = SpendLedger()
    assert check_policy(_intent(10), policy, ledger).passed
    ledger.record("a1", 10, "v@x.example")
    assert check_policy(_intent(10), policy, ledger).passed
    ledger.record("a1", 10, "v@x.example")
    # Third within the window trips the velocity rule.
    r3 = check_policy(_intent(10), policy, ledger)
    assert not r3.passed and "velocity" in r3.detail


def test_defaults_disable_new_rules():
    # With the default policy (limits 0 / 0) the new rules never fire.
    policy = PolicyConfig(per_tx_limit=500, daily_limit=10000)
    ledger = SpendLedger()
    for _ in range(5):
        assert check_policy(_intent(100), policy, ledger).passed
        ledger.record("a1", 100, "v@x.example")
