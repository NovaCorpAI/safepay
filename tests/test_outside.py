"""Money that moves outside the firewall: a PayPal webhook for a payout
SafePay never released, and reconciliation against Transaction Search."""

from __future__ import annotations

import os

for _k in ("NEBIUS_API_KEY", "TAVILY_API_KEY", "PAYPAL_CLIENT_ID", "PAYPAL_SECRET"):
    os.environ[_k] = ""

from safepay.agent import SafePayAgent  # noqa: E402
from safepay.models import PaymentIntent, PolicyConfig  # noqa: E402
from safepay.telegram import TelegramNotifier  # noqa: E402
import webapp.app as w  # noqa: E402


class FakePayPal:
    mode = "sandbox"
    FINAL_OK = ("SUCCESS",)
    FINAL_FAILED = ("FAILED", "RETURNED", "BLOCKED", "DENIED", "CANCELED", "REFUNDED", "REVERSED")

    def __init__(self):
        self.payouts = {}
        self.txs = []
        self.verified = True

    def send_payout(self, recipient, amount, currency="USD", note="", idempotency_key=None):
        bid = f"B{len(self.payouts) + 1}"
        self.payouts[bid] = {"receiver": recipient, "amount": amount, "currency": currency,
                             "transaction_id": f"T{bid}", "item_status": "SUCCESS"}
        return {"mode": "sandbox", "payout_batch_id": bid, "batch_status": "PENDING"}

    def get_payout(self, batch_id):
        p = self.payouts.get(batch_id)
        if p is None:
            raise RuntimeError(f"no batch {batch_id}")
        return {"mode": "sandbox", "payout_batch_id": batch_id, "batch_status": "SUCCESS",
                "item_status": p["item_status"], "transaction_id": p["transaction_id"],
                "time_processed": "t", "errors": None, "final": True, "receiver": p["receiver"],
                "amount": p["amount"], "currency": p["currency"], "note": "direct"}

    def list_transactions(self, start, end):
        return list(self.txs)

    def verify_webhook(self, headers, event, webhook_id):
        return self.verified


def _agent():
    a = SafePayAgent(policy=PolicyConfig(per_tx_limit=500, daily_limit=2000))
    a.paypal = FakePayPal()
    return a


def test_unknown_payout_is_chained_as_outside_and_alerts():
    a = _agent()
    sent = []
    a.notifier = TelegramNotifier("t", "1", http=lambda u, p: sent.append(p) or {"ok": True})
    a.paypal.payouts["B9"] = {"receiver": "attacker@x.example", "amount": 77.0,
                              "currency": "USD", "transaction_id": "TB9", "item_status": "SUCCESS"}
    res = a.flag_outside("B9", "webhook")
    assert res["outside"] is True and res["duplicate"] is False and res["seq"] == 0
    p = res["audit"]["payload"]
    assert p["event"] == "outside" and p["verdict"] == "block" and p["executed"] is True
    assert p["intent"]["recipient"] == "attacker@x.example" and p["intent"]["amount"] == 77.0
    assert p["checks"][0]["name"] == "firewall"
    assert "outside the firewall" in sent[-1]["text"] and "reply_markup" not in sent[-1]
    again = a.flag_outside("B9", "webhook")
    assert again["duplicate"] is True and len(a.audit_log.records) == 1
    # A payout SafePay released is never "outside".
    out = a.pay(PaymentIntent(recipient="v@x.example", amount=10, agent_id="a"))
    bid = out["decision"]["execution"]["payout_batch_id"]
    assert a.flag_outside(bid)["outside"] is False


def test_reconcile_flags_only_unknown_debits():
    a = _agent()
    out = a.pay(PaymentIntent(recipient="v@x.example", amount=10, agent_id="a"))
    bid = out["decision"]["execution"]["payout_batch_id"]
    a.paypal.txs = [
        {"transaction_id": f"T{bid}", "event_code": "T0001", "status": "S", "amount": -10.0,
         "currency": "USD", "time": "t", "counterparty": "v@x.example", "subject": ""},
        {"transaction_id": "TX-ROGUE", "event_code": "T0001", "status": "S", "amount": -250.0,
         "currency": "USD", "time": "t", "counterparty": "rogue@x.example", "subject": "oops"},
        {"transaction_id": "TX-CREDIT", "event_code": "T0001", "status": "S", "amount": 500.0,
         "currency": "USD", "time": "t", "counterparty": "", "subject": ""},
        {"transaction_id": "TX-FEE", "event_code": "T0106", "status": "S", "amount": -1.0,
         "currency": "USD", "time": "t", "counterparty": "", "subject": ""},
    ]
    r = a.reconcile(hours=24)
    assert r["debits"] == 2 and r["known"] == 1
    assert [o["transaction_id"] for o in r["outside"]] == ["TX-ROGUE"]
    last = a.audit_log.records[-1]["payload"]
    assert last["event"] == "outside" and last["intent"]["amount"] == 250.0
    assert last["intent"]["recipient"] == "rogue@x.example"
    n = len(a.audit_log.records)
    assert a.reconcile(hours=24)["outside"] == []        # already chained: not flagged twice
    assert len(a.audit_log.records) == n
    assert a.audit_log.verify() == (True, None)


def test_webhook_unknown_batch_becomes_outside(monkeypatch):
    w._reset_agent()
    w._agent.paypal = FakePayPal()
    w._agent.paypal.payouts["B5"] = {"receiver": "x@y.example", "amount": 5.0, "currency": "USD",
                                     "transaction_id": "T5", "item_status": "SUCCESS"}
    monkeypatch.setenv("PAYPAL_WEBHOOK_ID", "WH1")
    c = w.app.test_client()
    r = c.post("/api/paypal/webhook", json={"event_type": "PAYMENT.PAYOUTSBATCH.SUCCESS",
                                            "resource": {"batch_header": {"payout_batch_id": "B5"}}})
    assert r.status_code == 200 and r.get_json()["outside"] is True
    audit = c.get("/api/audit").get_json()
    assert audit["records"][-1]["payload"]["event"] == "outside"


def test_reconcile_endpoint_reports_search_errors():
    w._reset_agent()
    c = w.app.test_client()
    r = c.post("/api/reconcile", json={"hours": 24}).get_json()   # simulated: nothing to list
    assert r["ok"] and r["debits"] == 0 and r["outside"] == []

    class NoSearch(FakePayPal):
        def list_transactions(self, start, end):
            raise RuntimeError("Transaction Search is not enabled for this app (PayPal 403)")
    w._agent.paypal = NoSearch()
    r = c.post("/api/reconcile", json={})
    assert r.status_code == 502 and "not enabled" in r.get_json()["error"]
