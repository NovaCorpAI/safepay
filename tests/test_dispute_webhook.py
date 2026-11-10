"""A PayPal dispute freezes the agent whose payment it is: the dispute is
re-read from PayPal, chained as a dispute link, the agent's next payment is
refused until an operator resumes it, the operator is alerted, and a repeat
delivery changes nothing. A dispute on money SafePay never saw is chained
and freezes nobody; a resolved dispute is chained and freezes nobody."""

from __future__ import annotations

import os

import pytest

for _k in ("NEBIUS_API_KEY", "TAVILY_API_KEY", "PAYPAL_CLIENT_ID", "PAYPAL_SECRET", "TELEGRAM_BOT_TOKEN",
           "TELEGRAM_CHAT_ID", "SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY", "SAFEPAY_OPERATOR_TOKEN"):
    os.environ[_k] = ""

import webapp.app as appmod  # noqa: E402
from safepay.agent import SafePayAgent  # noqa: E402
from safepay.config import Config  # noqa: E402
from safepay.models import PaymentIntent, PolicyConfig  # noqa: E402
from webapp.app import app, _reset_agent  # noqa: E402


def _cfg():
    return Config(nebius_api_key=None, nebius_base_url="", nebius_model="", tavily_api_key=None,
                  paypal_client_id=None, paypal_secret=None, paypal_base_url="https://api-m.sandbox.paypal.com")


def _intent(amount=40, agent_id="ops:r1"):
    return PaymentIntent(recipient="v@x.example", amount=amount, category="software", merchant_name="V",
                         task_goal="pay invoices", agent_id=agent_id)


class Alerts:
    chat_id = "5"

    def __init__(self):
        self.sent: list[str] = []

    def send_alert(self, text):
        self.sent.append(text)


def _dispute(tx, status="OPEN", reason="MERCHANDISE_OR_SERVICE_NOT_RECEIVED", amount="40.00"):
    return {"dispute_id": "PP-D-1", "status": status, "reason": reason,
            "dispute_amount": {"currency_code": "USD", "value": amount},
            "disputed_transactions": [{"seller_transaction_id": tx, "buyer_transaction_id": "B-" + tx}]}


@pytest.fixture
def agent():
    a = SafePayAgent(config=_cfg(), policy=PolicyConfig(per_tx_limit=500, daily_limit=2000))
    a.notifier = Alerts()
    return a


def test_dispute_on_an_agent_payment_freezes_that_agent(agent, monkeypatch):
    out = agent.pay(_intent())
    batch = out["decision"]["execution"]["payout_batch_id"]
    seq = out["audit"]["payload"]["seq"]
    monkeypatch.setattr(agent.paypal, "get_json", lambda path: _dispute(batch) if path.endswith("/PP-D-1") else {})

    res = agent.dispute_event("CUSTOMER.DISPUTE.CREATED", {"dispute_id": "PP-D-1"})
    assert res["frozen"] == "ops:r1" and res["agent_id"] == "ops:r1" and res["link_seq"] == seq
    assert res["duplicate"] is False and res["status"] == "OPEN"
    link = agent.audit_log.records[-1]["payload"]
    assert link["event"] == "dispute" and link["verdict"] == "hold" and link["execution"]["frozen_agent"] == "ops:r1"
    assert link["execution"]["dispute_id"] == "PP-D-1" and batch in link["execution"]["transactions"]
    assert "frozen until an operator resumes it" in link["reasons"][0]

    nxt = agent.pay(_intent(amount=5))["decision"]
    assert nxt["verdict"] == "block" and any("PP-D-1" in r for r in nxt["reasons"])
    assert len(agent.notifier.sent) == 1 and "FROZEN" in agent.notifier.sent[0] and "PP-D-1" in agent.notifier.sent[0]

    again = agent.dispute_event("CUSTOMER.DISPUTE.CREATED", {"dispute_id": "PP-D-1"})
    assert again["duplicate"] is True and again["frozen"] == "ops:r1"
    assert len(agent.notifier.sent) == 1                      # a redelivery alerts nobody twice

    agent.resume_agent("ops:r1")
    assert agent.pay(_intent(amount=5))["decision"]["verdict"] == "allow"


def test_resolved_dispute_is_chained_but_freezes_nobody(agent, monkeypatch):
    batch = agent.pay(_intent())["decision"]["execution"]["payout_batch_id"]
    monkeypatch.setattr(agent.paypal, "get_json", lambda path: _dispute(batch, status="RESOLVED"))
    res = agent.dispute_event("CUSTOMER.DISPUTE.RESOLVED", {"dispute_id": "PP-D-1"})
    assert res["frozen"] is None and res["agent_id"] == "ops:r1"
    assert agent.pay(_intent(amount=5))["decision"]["verdict"] == "allow"


def test_dispute_on_money_safepay_never_saw(agent, monkeypatch):
    monkeypatch.setattr(agent.paypal, "get_json", lambda path: _dispute("TX-STRANGER"))
    res = agent.dispute_event("CUSTOMER.DISPUTE.CREATED", {"dispute_id": "PP-D-1"})
    assert res["frozen"] is None and res["agent_id"] is None
    link = agent.audit_log.records[-1]["payload"]
    assert link["intent"]["agent_id"] == "outside-safepay" and "no SafePay link found" in link["reasons"][0]
    assert "nothing frozen" in agent.notifier.sent[0]


def test_dispute_body_is_reread_never_trusted(agent, monkeypatch):
    batch = agent.pay(_intent())["decision"]["execution"]["payout_batch_id"]
    monkeypatch.setattr(agent.paypal, "get_json", lambda path: _dispute("TX-OTHER"))
    # The event body names the agent's payout, PayPal's record names another: PayPal wins.
    res = agent.dispute_event("CUSTOMER.DISPUTE.CREATED", {
        "dispute_id": "PP-D-1", "disputed_transactions": [{"seller_transaction_id": batch}]})
    assert res["frozen"] is None


def test_dispute_errors(agent, monkeypatch):
    with pytest.raises(ValueError, match="without a dispute id"):
        agent.dispute_event("CUSTOMER.DISPUTE.CREATED", {})

    def boom(path):
        raise OSError("paypal down")
    monkeypatch.setattr(agent.paypal, "get_json", boom)
    with pytest.raises(OSError):
        agent.dispute_event("CUSTOMER.DISPUTE.CREATED", {"dispute_id": "PP-D-9"})


@pytest.fixture
def client(monkeypatch):
    _reset_agent()
    monkeypatch.setattr(appmod, "_RL_MAX", 0)
    monkeypatch.setenv("PAYPAL_WEBHOOK_ID", "WH-1")
    monkeypatch.setattr(appmod._agent.paypal, "verify_webhook", lambda h, e, w: True)
    return app.test_client()


def test_webhook_route_freezes_and_reports(client, monkeypatch):
    paid = client.post("/api/evaluate", json={
        "recipient": "billing@notion.so", "amount": 48, "category": "software", "merchant_name": "Notion",
        "task_goal": "pay invoices", "agent_id": "a1"}).get_json()
    batch = paid["decision"]["execution"]["payout_batch_id"]
    monkeypatch.setattr(appmod._agent.paypal, "get_json", lambda path: _dispute(batch))

    r = client.post("/api/paypal/webhook", json={"event_type": "CUSTOMER.DISPUTE.CREATED",
                                                  "resource": {"dispute_id": "PP-D-1"}})
    assert r.status_code == 200 and r.get_json()["ok"] is True and r.get_json()["frozen"] == "a1"
    agents = {a["agent_id"]: a for a in client.get("/api/agents").get_json()["agents"]}
    assert agents["a1"]["status"] == "paused" and "PP-D-1" in agents["a1"]["reason"]

    r = client.post("/api/paypal/webhook", json={"event_type": "CUSTOMER.DISPUTE.UPDATED", "resource": {}})
    assert r.status_code == 200 and "without a dispute id" in r.get_json()["ignored"]

    def boom(path):
        raise OSError("paypal down")
    monkeypatch.setattr(appmod._agent.paypal, "get_json", boom)
    r = client.post("/api/paypal/webhook", json={"event_type": "CUSTOMER.DISPUTE.CREATED",
                                                  "resource": {"dispute_id": "PP-D-2"}})
    assert r.status_code == 502 and "PayPal lookup failed" in r.get_json()["error"]
