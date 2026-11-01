"""Deterministic tests for the SafePay guardrail. No network, no keys.

These exercise the policy engine, the allow/hold/block aggregation, the daily
cap across a burst, simulated payout execution, and audit-record integrity, so
the core logic is verified even where Nebius and Tavily are not configured.
"""

from __future__ import annotations

from safepay.config import Config
from safepay.guardrail import Guardrail
from safepay.models import PaymentIntent, PolicyConfig, Verdict
from safepay.paypal_client import PayPalClient
from safepay.search import WebSearch
from safepay import attest
from safepay.agent import SafePayAgent


def _offline_config() -> Config:
    # No keys: Nebius/Tavily skipped, PayPal simulated. Pure deterministic path.
    return Config(
        nebius_api_key=None,
        nebius_base_url="https://example.invalid/v1/",
        nebius_model="nvidia/nemotron-3-super-120b-a12b",
        tavily_api_key=None,
        paypal_client_id=None,
        paypal_secret=None,
        paypal_base_url="https://api-m.sandbox.paypal.com",
    )


def _guardrail(policy: PolicyConfig) -> Guardrail:
    return Guardrail(policy, WebSearch(None), None)


def test_within_limits_allows():
    g = _guardrail(PolicyConfig(per_tx_limit=500, daily_limit=1000))
    d = g.evaluate(PaymentIntent(recipient="a@b.com", amount=50, category="software"))
    assert d.verdict == Verdict.ALLOW


def test_over_per_tx_blocks():
    g = _guardrail(PolicyConfig(per_tx_limit=500, daily_limit=10000))
    d = g.evaluate(PaymentIntent(recipient="a@b.com", amount=900))
    assert d.verdict == Verdict.BLOCK
    assert "per-transaction" in d.checks[0].detail


def test_daily_cap_blocks_across_burst():
    g = _guardrail(PolicyConfig(per_tx_limit=500, daily_limit=1000))
    for amt in (420, 420):
        d = g.evaluate(PaymentIntent(recipient="a@b.com", amount=amt, agent_id="x"))
        assert d.verdict == Verdict.ALLOW
        g.commit_spend(d)
    d3 = g.evaluate(PaymentIntent(recipient="a@b.com", amount=420, agent_id="x"))
    assert d3.verdict == Verdict.BLOCK
    assert "daily" in d3.checks[0].detail


def test_blocked_category():
    g = _guardrail(PolicyConfig(blocked_categories=["gambling"]))
    d = g.evaluate(PaymentIntent(recipient="a@b.com", amount=10, category="gambling"))
    assert d.verdict == Verdict.BLOCK


def test_recipient_blocklist():
    g = _guardrail(PolicyConfig(recipient_blocklist=["bad@x.com"]))
    d = g.evaluate(PaymentIntent(recipient="bad@x.com", amount=10))
    assert d.verdict == Verdict.BLOCK


def test_simulated_payout_executes():
    pc = PayPalClient(_offline_config())
    assert pc.mode == "simulated"
    res = pc.send_payout("a@b.com", 10.0)
    assert res["batch_status"] == "SUCCESS"
    assert res["payout_batch_id"].startswith("SAFEPAY-")


def test_audit_record_roundtrip():
    g = _guardrail(PolicyConfig())
    d = g.evaluate(PaymentIntent(recipient="a@b.com", amount=10))
    rec = attest.audit_record(d)
    assert attest.verify_record(rec)
    # Tampering with the payload breaks integrity.
    rec["payload"]["risk"] = 0.999
    assert not attest.verify_record(rec)


def test_agent_offline_end_to_end():
    agent = SafePayAgent(config=_offline_config(), policy=PolicyConfig())
    out = agent.pay(PaymentIntent(recipient="a@b.com", amount=25, category="software"))
    assert out["decision"]["verdict"] == "allow"
    assert out["decision"]["executed"] is True
    assert out["paypal_mode"] == "simulated"
