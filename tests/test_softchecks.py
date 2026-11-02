"""Soft-check HOLD paths and the execute flag, with fake model/web (no network)."""

from __future__ import annotations

import json

from safepay.guardrail import Guardrail
from safepay.models import PaymentIntent, PolicyConfig, Verdict
from safepay.search import SearchHit


class FakeChat:
    """Duck-types NebiusChat: routes by system prompt to a merchant or reasoning
    verdict so each soft check can be driven independently."""

    model = "fake-model"

    def __init__(self, merchant=None, reasoning=None):
        self.merchant = merchant
        self.reasoning = reasoning

    def complete(self, system, user, temperature=0.1):
        if "reputation" in system.lower():
            return json.dumps(self.merchant or {"legitimate": True, "risk": 0.0, "reason": "ok"})
        return json.dumps(self.reasoning or {"aligned": True, "risk": 0.0, "reason": "ok"})


class FakeWeb:
    def __init__(self, hits=None, enabled=True):
        self._hits = hits or []
        self.enabled = enabled

    def search(self, query, max_results=5):
        return self._hits


_OK_POLICY = PolicyConfig(per_tx_limit=1000, daily_limit=10000)


def _intent(amount=100):
    return PaymentIntent(recipient="v@x.example", amount=amount, merchant_name="V",
                         category="software", task_goal="pay approved invoices")


def test_reasoning_mismatch_holds():
    chat = FakeChat(reasoning={"aligned": False, "risk": 0.9, "reason": "casino, not an invoice"})
    g = Guardrail(_OK_POLICY, FakeWeb(enabled=False), chat)
    d = g.evaluate(_intent())
    assert d.verdict == Verdict.HOLD
    assert any(c.name == "reasoning" and not c.passed for c in d.checks)


def test_merchant_unsafe_holds():
    hit = SearchHit(title="V complaints", url="http://x", content="some text")
    chat = FakeChat(merchant={"legitimate": False, "risk": 0.9, "reason": "scam reports"})
    g = Guardrail(_OK_POLICY, FakeWeb(hits=[hit], enabled=True), chat)
    d = g.evaluate(_intent())
    assert d.verdict == Verdict.HOLD
    assert any(c.name == "merchant" and not c.passed for c in d.checks)


def test_all_pass_allows():
    g = Guardrail(_OK_POLICY, FakeWeb(enabled=False), FakeChat())
    d = g.evaluate(_intent())
    assert d.verdict == Verdict.ALLOW


def test_no_web_footprint_holds():
    # Web enabled but zero hits: the unknown-payee case must HOLD, not ALLOW.
    g = Guardrail(_OK_POLICY, FakeWeb(hits=[], enabled=True), FakeChat())
    d = g.evaluate(_intent())
    assert d.verdict == Verdict.HOLD
    assert any(c.name == "merchant" and not c.passed for c in d.checks)


def test_execute_flag_off_does_not_move_money(monkeypatch):
    monkeypatch.setenv("SAFEPAY_EXECUTE", "simulate")
    from safepay.agent import SafePayAgent
    agent = SafePayAgent(policy=_OK_POLICY)
    out = agent.pay(_intent(50))
    dec = out["decision"]
    assert dec["verdict"] == "allow"
    assert dec["executed"] is False
    assert dec["execution"]["mode"] == "disabled"
    # Budget is still charged, so the caps behave as in production.
    assert agent.guardrail.ledger.spent_today("agent-demo") == 50
