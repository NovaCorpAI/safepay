"""Tests for the PayPal Agent Toolkit gate.

These do not require the toolkit package or any network: a fake PayPalAPI is
injected, so the tests assert exactly what SafePay does *around* the toolkit,
which tool calls it forwards and which it stops.
"""

from __future__ import annotations

import pytest

from safepay.agent_toolkit import GuardedToolkit, order_params_from_intent
from safepay.config import Config
from safepay.models import PaymentIntent, PolicyConfig


class FakePayPalAPI:
    """Stand-in for the toolkit's PayPalAPI that records what it was asked to do."""

    def __init__(self):
        self.calls = []

    def run(self, method, params):
        self.calls.append((method, params))
        return '{"id": "ORDER-FAKE-1", "status": "CREATED", "links": [' \
               '{"rel": "approve", "href": "https://sandbox.paypal.com/approve/ORDER-FAKE-1"}]}'


def _gate(policy=None, reasoning=False, web=False):
    # A config with no keys: reasoning/web stay off, so the gate is deterministic.
    cfg = Config.from_env()
    gate = GuardedToolkit(cfg, policy or PolicyConfig(), reasoning=reasoning, web=web)
    fake = FakePayPalAPI()
    gate._api = fake  # inject, bypassing the lazy builder (no package/creds needed)
    return gate, fake


def _intent(amount, **kw):
    base = dict(recipient="billing@vendor.example", amount=amount, category="software",
                merchant_name="Vendor", purpose="monthly", agent_id="a1",
                task_goal="pay approved invoices")
    base.update(kw)
    return PaymentIntent(**base)


def test_allow_forwards_to_toolkit():
    gate, fake = _gate(PolicyConfig(per_tx_limit=500))
    out = gate.create_order(_intent(48))
    assert out["decision"]["verdict"] == "allow"
    assert out["decision"]["executed"] is True
    assert len(fake.calls) == 1 and fake.calls[0][0] == "create_order"
    assert out["result"] and "ORDER-FAKE-1" in out["result"]


def test_block_over_per_tx_never_calls_toolkit():
    gate, fake = _gate(PolicyConfig(per_tx_limit=500))
    out = gate.create_order(_intent(900))
    assert out["decision"]["verdict"] == "block"
    assert out["decision"]["executed"] is False
    assert fake.calls == []            # the toolkit was never invoked
    assert out["result"] is None


def test_daily_cap_holds_across_a_burst():
    gate, fake = _gate(PolicyConfig(per_tx_limit=500, daily_limit=1000))
    gate.create_order(_intent(420))    # allowed, spent 420
    gate.create_order(_intent(420))    # allowed, spent 840
    out = gate.create_order(_intent(420))  # 1260 > 1000: blocked
    assert out["decision"]["verdict"] == "block"
    assert out["result"] is None
    # Only the two allowed orders reached the toolkit.
    assert [c[0] for c in fake.calls] == ["create_order", "create_order"]


def test_read_only_method_passes_through():
    gate, fake = _gate()
    out = gate.run("get_order_details", {"order_id": "X"})
    assert out["gated"] is False
    assert fake.calls == [("get_order_details", {"order_id": "X"})]


def test_money_method_without_intent_raises():
    gate, _ = _gate()
    with pytest.raises(ValueError):
        gate.run("create_order", {"currency_code": "USD", "items": []})


class FailingPayPalAPI:
    def run(self, method, params):
        raise RuntimeError("sandbox 500")


def test_toolkit_error_is_captured_not_raised():
    gate, _ = _gate(PolicyConfig(per_tx_limit=500))
    gate._api = FailingPayPalAPI()
    out = gate.create_order(_intent(48))      # ALLOW, but the toolkit call fails
    assert out["decision"]["verdict"] == "allow"
    assert out["decision"]["executed"] is False
    assert "toolkit error" in " ".join(out["decision"]["reasons"])
    assert out["result"] is None


def test_order_params_from_intent_single_line_item():
    params = order_params_from_intent(_intent(48, currency="USD", merchant_name="Notion"))
    assert params["currency_code"] == "USD"
    assert len(params["items"]) == 1
    item = params["items"][0]
    assert item["name"] == "Notion"
    assert item["item_total"] == 48.0 and item["quantity"] == 1
