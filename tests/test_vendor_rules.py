"""Vendor directory rules: lookalike recipient and amount anomaly. These close
the two benchmark misses that stay inside every cap."""

from __future__ import annotations

from safepay.models import PaymentIntent, PolicyConfig
from safepay.policy import SpendLedger, check_policy, domain_of, registrable

VENDORS = {
    "Notion": {"domains": ["notion.so"], "expected_amount": 48, "aliases": ["Notion Labs"]},
    "AWS": {"domains": ["amazon.com", "aws.amazon.com"]},
}


def _check(**kw):
    policy = PolicyConfig(per_tx_limit=500, daily_limit=5000, vendors=VENDORS)
    intent = PaymentIntent(agent_id="a1", **kw)
    return check_policy(intent, policy, SpendLedger())


def test_domain_helpers():
    assert domain_of("billing@notion.so") == "notion.so"
    assert domain_of("https://www.notion.so/pricing") == "notion.so"
    assert domain_of("cloud.google.com") == "cloud.google.com"
    assert registrable("cloud.google.com") == "google.com"
    assert registrable("billing.example.co.uk") == "example.co.uk"
    assert registrable("notion-payments.example") == "notion-payments.example"


def test_lookalike_recipient_is_blocked():
    r = _check(recipient="billing@notion-payments.example", amount=48,
               merchant_name="Notion", category="software")
    assert not r.passed and "lookalike" in r.detail and "notion.so" in r.detail


def test_alias_and_subdomain_are_fine():
    assert _check(recipient="billing@notion.so", amount=48, merchant_name="Notion Labs").passed
    assert _check(recipient="aws-billing@amazon.com", amount=420, merchant_name="aws").passed
    assert _check(recipient="billing@mail.notion.so", amount=48, merchant_name="Notion").passed


def test_amount_inflation_is_blocked():
    r = _check(recipient="billing@notion.so", amount=480, merchant_name="Notion")
    assert not r.passed and "10.0x" in r.detail and "expected" in r.detail
    # Inside the tolerance (1.5x of 48 = 72) is fine: plans change a little.
    assert _check(recipient="billing@notion.so", amount=60, merchant_name="Notion").passed


def test_unknown_vendor_is_not_judged_by_directory():
    r = _check(recipient="retainer@newpayee.example", amount=250, merchant_name="NewPayee")
    assert r.passed


def test_merchant_url_must_match_recipient_domain():
    bad = _check(recipient="pay@casino-cashier.win", amount=100,
                 merchant_name="LuckyDice Casino", merchant_url="https://luckydice-casino.win")
    assert not bad.passed and "merchant site" in bad.detail
    ok = _check(recipient="cloud-billing@google.com", amount=100,
                merchant_name="Google Cloud", merchant_url="https://cloud.google.com")
    assert ok.passed


def test_hold_unknown_vendors_holds_new_payees_only():
    from safepay.guardrail import Guardrail
    from safepay.models import Verdict
    from safepay.policy import is_known_vendor
    from safepay.search import WebSearch
    policy = PolicyConfig(per_tx_limit=500, daily_limit=5000, vendors=VENDORS,
                          hold_unknown_vendors=True)
    guard = Guardrail(policy, WebSearch(None), None)
    known = PaymentIntent(recipient="billing@notion.so", amount=48, merchant_name="Notion")
    by_domain = PaymentIntent(recipient="aws-billing@amazon.com", amount=48)
    new = PaymentIntent(recipient="invoices@zq-consult.example", amount=200,
                        merchant_name="ZQ Consult 7781")
    assert is_known_vendor(known, policy) and is_known_vendor(by_domain, policy)
    assert not is_known_vendor(new, policy)
    assert guard.evaluate(known).verdict == Verdict.ALLOW
    d = guard.evaluate(new)
    assert d.verdict == Verdict.HOLD and d.checks[-1].name == "vendor"
    # Off by default: the same new payee is judged by the soft checks only.
    off = Guardrail(PolicyConfig(per_tx_limit=500, daily_limit=5000, vendors=VENDORS),
                    WebSearch(None), None)
    assert off.evaluate(new).verdict == Verdict.ALLOW


def test_vendor_evidence_is_recorded():
    r = _check(recipient="billing@notion.so", amount=48, merchant_name="Notion")
    rules = [e["rule"] for e in r.evidence]
    assert "vendor_directory" in rules
