"""Settlement: what PayPal says happened to a released payout, appended to the
chain, with the budget released when the money never left."""

from __future__ import annotations

import os

for _k in ("NEBIUS_API_KEY", "TAVILY_API_KEY", "PAYPAL_CLIENT_ID", "PAYPAL_SECRET"):
    os.environ[_k] = ""

import pytest  # noqa: E402

import webapp.app as w  # noqa: E402
from safepay.agent import SafePayAgent  # noqa: E402
from safepay.models import PaymentIntent, PolicyConfig  # noqa: E402


def _agent():
    return SafePayAgent(policy=PolicyConfig(per_tx_limit=500, daily_limit=1000))


def _pay(agent, amount=100):
    out = agent.pay(PaymentIntent(recipient="v@x.example", amount=amount, agent_id="a1"))
    assert out["decision"]["executed"] is True
    return out["decision"]["execution"]["payout_batch_id"]


def test_success_settlement_is_an_allow_link_and_idempotent():
    agent = _agent()
    batch = _pay(agent)
    out = agent.settle(batch, source="test")
    assert out["status"] == "SUCCESS" and out["duplicate"] is False and out["seq"] == 1
    p = out["audit"]["payload"]
    assert p["event"] == "settlement" and p["verdict"] == "allow" and p["executed"] is True
    assert p["execution"]["source"] == "test" and p["execution"]["payout_batch_id"] == batch
    assert "money moved" in p["reasons"][0]
    again = agent.settle(batch)
    assert again["duplicate"] is True and again["seq"] == 1
    assert len(agent.audit_log.records) == 2 and agent.audit_log.verify() == (True, None)


def test_failed_settlement_blocks_and_releases_budget(monkeypatch):
    agent = _agent()
    batch = _pay(agent, 400)
    assert agent.guardrail.ledger.spent_today("a1") == 400
    monkeypatch.setattr(agent.paypal, "get_payout", lambda b: {
        "payout_batch_id": b, "batch_status": "SUCCESS", "item_status": "RETURNED",
        "transaction_id": "T1", "time_processed": None, "errors": None, "final": True})
    out = agent.settle(batch, source="webhook")
    p = out["audit"]["payload"]
    assert p["verdict"] == "block" and p["executed"] is False
    assert agent.guardrail.ledger.spent_today("a1") == 0          # budget released
    agent.settle(batch, source="webhook")                          # duplicate: no double release
    assert agent.guardrail.ledger.spent_today("a1") == 0
    # The next payment can use the released budget.
    out2 = agent.pay(PaymentIntent(recipient="v@x.example", amount=900, agent_id="a1"))
    assert out2["decision"]["verdict"] == "block"                   # per-tx limit still applies
    out3 = agent.pay(PaymentIntent(recipient="v@x.example", amount=450, agent_id="a1"))
    assert out3["decision"]["verdict"] == "allow"


def test_in_flight_settlement_is_a_hold_link(monkeypatch):
    agent = _agent()
    batch = _pay(agent)
    monkeypatch.setattr(agent.paypal, "get_payout", lambda b: {
        "payout_batch_id": b, "batch_status": "PROCESSING", "item_status": "PENDING",
        "transaction_id": None, "time_processed": None, "errors": None, "final": False})
    assert agent.settle(batch)["audit"]["payload"]["verdict"] == "hold"


def test_unknown_batch_raises():
    with pytest.raises(ValueError):
        _agent().settle("SAFEPAY-nope")


@pytest.fixture()
def client(monkeypatch):
    w._reset_agent()
    monkeypatch.setattr(w, "_RL_MAX", 0)
    return w.app.test_client()


def test_settle_endpoint(client):
    r = client.post("/api/evaluate", json={"recipient": "v@x.example", "amount": 50,
                                           "agent_id": "a1"}).get_json()
    batch = r["decision"]["execution"]["payout_batch_id"]
    out = client.post(f"/api/payouts/{batch}/settle").get_json()
    assert out["status"] == "SUCCESS" and out["seq"] == 1
    assert client.post("/api/payouts/SAFEPAY-nope/settle").status_code == 404
    audit = client.get("/api/audit").get_json()
    assert audit["ok"] and audit["records"][1]["payload"]["event"] == "settlement"


def test_webhook_requires_configuration_and_verification(client, monkeypatch):
    monkeypatch.delenv("PAYPAL_WEBHOOK_ID", raising=False)
    assert client.post("/api/paypal/webhook", json={}).status_code == 503
    monkeypatch.setenv("PAYPAL_WEBHOOK_ID", "WH-1")
    assert client.post("/api/paypal/webhook", json={"resource": {}}).status_code == 403


def test_verified_webhook_settles_the_named_batch(client, monkeypatch):
    monkeypatch.setenv("PAYPAL_WEBHOOK_ID", "WH-1")
    r = client.post("/api/evaluate", json={"recipient": "v@x.example", "amount": 50,
                                           "agent_id": "a1"}).get_json()
    batch = r["decision"]["execution"]["payout_batch_id"]
    monkeypatch.setattr(w._agent.paypal, "verify_webhook", lambda h, e, wid: wid == "WH-1")
    event = {"event_type": "PAYMENT.PAYOUTS-ITEM.SUCCEEDED",
             "resource": {"payout_batch_id": batch, "transaction_status": "FAILED"}}
    out = client.post("/api/paypal/webhook", json=event).get_json()
    # The state is re-read from PayPal (simulated: SUCCESS), not trusted from the body.
    assert out == {"ok": True, "status": "SUCCESS", "duplicate": False}
    assert client.post("/api/paypal/webhook", json=event).get_json()["duplicate"] is True
    unknown = {"event_type": "PAYMENT.PAYOUTSBATCH.SUCCESS",
               "resource": {"batch_header": {"payout_batch_id": "SAFEPAY-nope"}}}
    assert client.post("/api/paypal/webhook", json=unknown).get_json()["ignored"] == "unknown batch"
