"""SafePay in front of PayPal's MCP server: gate the five money tools."""

import asyncio
import json
from types import SimpleNamespace

import pytest

from safepay.agent import SafePayAgent
from safepay.mcp_proxy import CONTEXT_FIELDS, GATED, PaymentProxy


def _res(obj, error=False):
    return SimpleNamespace(content=[SimpleNamespace(type="text", text=json.dumps(obj))], isError=error)


class FakePayPal:
    def __init__(self):
        self.calls = []

    async def __call__(self, name, args):
        self.calls.append((name, args))
        if name == "get_order":
            return _res({"id": args["id"], "purchase_units": [{
                "amount": {"currency_code": "USD", "value": "900.00"},
                "payee": {"email_address": "seller@shop.example"},
                "items": [{"name": "Laptop"}]}]})
        if name == "get_dispute":
            return _res({"dispute_amount": {"currency_code": "USD", "value": "35.00"}})
        if name == "show_subscription_plan_details":
            return _res({"name": "Pro plan", "billing_cycles": [
                {"pricing_scheme": {"fixed_price": {"currency_code": "USD", "value": "20"}}}]})
        return _res({"ok": True, "tool": name})


@pytest.fixture
def agent(monkeypatch):
    for k in ("PAYPAL_CLIENT_ID", "PAYPAL_CLIENT_SECRET", "TAVILY_API_KEY", "NEBIUS_API_KEY",
              "SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY"):
        monkeypatch.setenv(k, " ")
    return SafePayAgent()


def run(coro):
    return asyncio.run(coro)


def _order(total):
    return {"currencyCode": "USD", "items": [{"name": "Research report", "itemCost": total,
                                               "itemTotal": total, "quantity": 1}]}


def test_read_tools_pass_through(agent):
    up = FakePayPal()
    out = run(PaymentProxy(agent, up).call("list_products", {"page": 1}))
    assert out["forward"] and out["safepay"] is None
    assert up.calls == [("list_products", {"page": 1})]


def test_small_order_is_forwarded_and_chained(agent):
    up = FakePayPal()
    before = len(agent.audit_log.records)
    out = run(PaymentProxy(agent, up).call("create_order", dict(_order(20),
                                                               safepay_purpose="report for the team")))
    assert out["forward"] and out["safepay"]["verdict"] == "allow"
    name, args = up.calls[-1]
    assert name == "create_order" and "safepay_purpose" not in args   # context stripped
    rec = agent.audit_log.records[-1]["payload"]
    assert len(agent.audit_log.records) == before + 1
    assert rec["event"] == "order" and rec["intent"]["amount"] == 20


def test_order_over_the_cap_never_reaches_paypal(agent):
    up = FakePayPal()
    out = run(PaymentProxy(agent, up).call("create_order", _order(900)))
    assert not out["forward"] and out["safepay"]["verdict"] == "block"
    assert all(c[0] != "create_order" for c in up.calls)


def test_pay_order_reads_the_amount_from_paypal(agent):
    up = FakePayPal()
    out = run(PaymentProxy(agent, up).call("pay_order", {"id": "5O190127TN364715T"}))
    assert ("get_order", {"id": "5O190127TN364715T"}) in up.calls
    assert out["safepay"]["amount"] == 900.0 and out["safepay"]["verdict"] == "block"
    assert all(c[0] != "pay_order" for c in up.calls)


def test_refund_without_amount_is_held(agent):
    up = FakePayPal()
    out = run(PaymentProxy(agent, up).call("create_refund", {"capture_id": "2GG279541U471931P"}))
    assert out["safepay"]["verdict"] == "hold" and not out["forward"]


def test_dispute_and_subscription_amounts_come_from_paypal(agent):
    up = FakePayPal()
    p = PaymentProxy(agent, up)
    d = run(p.call("accept_dispute_claim", {"dispute_id": "PP-D-1", "note": "ok"}))
    assert d["safepay"]["amount"] == 35.0
    s = run(p.call("create_subscription", {"plan_id": "P-1", "quantity": 2}))
    assert s["safepay"]["amount"] == 40.0


def test_upstream_error_is_not_counted_as_paid(agent):
    async def broken(name, args):
        return _res({"error": "boom"}, error=True)
    out = run(PaymentProxy(agent, broken).call("create_order", _order(20)))
    assert out["safepay"]["executed"] is False


def test_augment_marks_only_gated_tools():
    t = {"name": "create_order", "description": "Create an order.", "inputSchema": {"type": "object", "properties": {"x": {}}}}
    a = PaymentProxy.augment(t)
    assert a["description"].startswith("[Gated by SafePay")
    assert set(CONTEXT_FIELDS) <= set(a["inputSchema"]["properties"]) and "x" in a["inputSchema"]["properties"]
    assert "safepay_purpose" not in t["inputSchema"]["properties"]      # original untouched
    r = {"name": "list_products", "description": "List.", "inputSchema": {}}
    assert PaymentProxy.augment(r) is r
    assert len(GATED) == 5
