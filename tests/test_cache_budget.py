"""Paid checks are cached on their inputs and capped by a daily budget that
fails closed."""

from __future__ import annotations

import json

from safepay.cache import MemoryCache
from safepay.guardrail import Guardrail
from safepay.models import PaymentIntent, PolicyConfig


class Hit:
    def __init__(self, t, u, c):
        self.title, self.url, self.content = t, u, c


class CountingWeb:
    enabled = True

    def __init__(self):
        self.calls = 0

    def search(self, q, max_results=5):
        self.calls += 1
        return [Hit("Acme reviews", "https://acme.example/reviews", "Established vendor, good reviews.")]


class CountingChat:
    model = "fake"

    def __init__(self):
        self.calls = 0

    def complete(self, system, user, temperature=0.1):
        self.calls += 1
        if "legitimate" in system:
            return json.dumps({"legitimate": True, "risk": 0.05, "reason": "established"})
        return json.dumps({"aligned": True, "risk": 0.05, "reason": "fits the task"})


def _g(cache):
    web, chat = CountingWeb(), CountingChat()
    return Guardrail(PolicyConfig(), web, chat, cache=cache), web, chat


def _intent(amount=40, purpose="monthly plan"):
    return PaymentIntent(recipient="billing@acme.example", amount=amount, merchant_name="Acme",
                         purpose=purpose, task_goal="pay approved SaaS invoices", agent_id="a")


def test_same_inputs_are_served_from_cache():
    g, web, chat = _g(MemoryCache())
    d1 = g.evaluate(_intent())
    d2 = g.evaluate(_intent())
    assert d1.verdict.value == d2.verdict.value == "allow"
    assert web.calls == 1 and chat.calls == 2            # merchant + reasoning, once each
    assert any(e.get("cache") == "hit" for c in d2.checks for e in c.evidence)
    g.evaluate(_intent(purpose="annual plan"))             # reasoning inputs changed
    assert web.calls == 1 and chat.calls == 3


def test_budget_fails_closed(monkeypatch):
    monkeypatch.setenv("SAFEPAY_EXTERNAL_DAILY_BUDGET", "2")
    g, web, chat = _g(MemoryCache())
    assert g.evaluate(_intent()).verdict.value == "allow"  # spends 2
    d = g.evaluate(_intent(amount=41, purpose="another bill"))
    # merchant is cached (same payee); reasoning would be a new call over budget
    assert d.verdict.value == "hold"
    assert any("budget" in c.detail for c in d.checks)
    assert chat.calls == 2


def test_no_cache_means_every_call_runs():
    g, web, chat = _g(None)
    g.evaluate(_intent()); g.evaluate(_intent())
    assert web.calls == 2
