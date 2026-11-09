"""The agent when something underneath it fails: PayPal calls that raise,
a control store that is down, the stablecoin rail, disabled execution,
failed settlements that release budget, and reconciliation with a payout
PayPal cannot describe."""

from __future__ import annotations

import os

import pytest

for _k in ("NEBIUS_API_KEY", "TAVILY_API_KEY", "PAYPAL_CLIENT_ID", "PAYPAL_SECRET", "TELEGRAM_BOT_TOKEN",
           "TELEGRAM_CHAT_ID", "SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY"):
    os.environ[_k] = ""

from safepay.agent import SafePayAgent  # noqa: E402
from safepay.config import Config  # noqa: E402
from safepay.models import PaymentIntent, PolicyConfig  # noqa: E402
from safepay.telegram import TelegramNotifier  # noqa: E402

G = "GAAACAQDAQCQMBYIBEFAWDANBYHRAEISCMKBKFQXDAMRUGY4DUPB7JZX"


def _cfg(**kw):
    base = dict(nebius_api_key=None, nebius_base_url="", nebius_model="", tavily_api_key=None,
                paypal_client_id=None, paypal_secret=None, paypal_base_url="https://api-m.sandbox.paypal.com")
    base.update(kw)
    return Config(**base)


@pytest.fixture
def agent():
    return SafePayAgent(config=_cfg(), policy=PolicyConfig(per_tx_limit=500, daily_limit=2000))


def _intent(amount=40, **kw):
    base = dict(recipient="v@x.example", amount=amount, category="software", merchant_name="V",
                task_goal="pay invoices", agent_id="ops:r1")
    base.update(kw)
    return PaymentIntent(**base)


def boom(*a, **k):
    raise OSError("paypal down")


def test_telegram_notifier_is_built_from_config():
    a = SafePayAgent(config=_cfg(telegram_token="tok", telegram_chat_id="5"))
    assert isinstance(a.notifier, TelegramNotifier) and a.notifier.chat_id == "5"


def test_payout_error_is_chained_not_raised(agent, monkeypatch):
    monkeypatch.setattr(agent.paypal, "send_payout", boom)
    out = agent.pay(_intent())
    d = out["decision"]
    assert d["verdict"] == "allow" and d["executed"] is False
    assert d["execution"] == {"error": "paypal down"} and any("payout error" in r for r in d["reasons"])
    assert agent.guardrail.ledger.spent_today("ops:r1") == 0            # nothing charged


def test_stablecoin_rail_error(monkeypatch):
    a = SafePayAgent(config=_cfg(), policy=PolicyConfig(per_tx_limit=500, daily_limit=2000,
                                                       allowed_currencies=["USD", "USDC"], allowed_networks=["stellar"]))
    judged = a.pay(_intent(recipient=G, currency="USDC", network="stellar"))["decision"]
    assert judged["execution"]["mode"] == "judged" and judged["executed"] is False

    class Rail:
        def send(self, intent, memo):
            raise OSError("wallet offline")
    a.stablecoin_rail = Rail()
    d = a.pay(_intent(recipient=G, currency="USDC", network="stellar", amount=5))["decision"]
    assert d["executed"] is False and d["execution"]["error"] == "wallet offline"
    assert d["execution"]["rail"] == "USDC on stellar"


def test_control_store_outage_falls_back_to_the_default_policy(agent, monkeypatch):
    monkeypatch.setattr(agent.control, "policy_for", boom)
    monkeypatch.setattr(agent.control, "setting", boom)
    pol = agent.policy_for("ops:r1")
    assert pol.per_tx_limit == 500 and agent._pin("r1") is None
    monkeypatch.setattr(agent.control, "touch_agent", boom)
    assert agent._frozen("ops:r1") is None


def test_mandate_without_execution_and_with_paypal_errors(agent, monkeypatch):
    monkeypatch.setenv("SAFEPAY_EXECUTE", "0")
    out = agent.open_mandate(_intent(amount=80))
    assert out["decision"]["execution"]["mode"] == "disabled" and out["mandate"] is None
    monkeypatch.delenv("SAFEPAY_EXECUTE")
    monkeypatch.setattr(agent.paypal, "vault_card", boom)
    assert agent._vaulted_card() is None
    monkeypatch.setattr(agent.paypal, "authorize", boom)
    out = agent.open_mandate(_intent(amount=80))
    assert out["decision"]["executed"] is False and any("authorize error" in r for r in out["decision"]["reasons"])
    monkeypatch.undo()
    opened = agent.open_mandate(_intent(amount=80))
    auth = opened["mandate"]["authorization_id"]
    monkeypatch.setattr(agent.paypal, "capture_authorization", boom)
    cap = agent.capture(auth, 10, "ops:r1")
    assert cap["decision"]["executed"] is False and cap["decision"]["execution"]["error"] == "paypal down"
    assert cap["mandate"]["captured"] == 0
    monkeypatch.setattr(agent.paypal, "void_authorization", boom)
    closed = agent.close_mandate(auth, "ops:r1")
    assert closed["decision"]["executed"] is False and "void error" in closed["decision"]["reasons"][-1]
    assert agent.mandate_ap2("NOPE") is None
    with pytest.raises(ValueError, match="without a resource id"):
        agent.settle_payment_event("PAYMENT.CAPTURE.COMPLETED", {})


def test_failed_settlement_releases_the_budget_once(agent, monkeypatch):
    out = agent.pay(_intent(amount=40))
    batch = out["decision"]["execution"]["payout_batch_id"]
    assert agent.guardrail.ledger.spent_today("ops:r1") == 40
    state = {"payout_batch_id": batch, "batch_status": "DENIED", "item_status": "FAILED", "final": True,
             "transaction_id": None, "time_processed": None, "errors": {"name": "RECEIVER_UNREGISTERED"}}
    monkeypatch.setattr(agent.paypal, "get_payout", lambda b: dict(state))
    s = agent.settle(batch)
    assert s["status"] == "FAILED" and s["audit"]["payload"]["verdict"] == "block"
    assert agent.guardrail.ledger.spent_today("ops:r1") == 0
    assert agent.settle(batch)["duplicate"] is True
    monkeypatch.setattr(agent.paypal, "get_payout", lambda b: dict(state, item_status="RETURNED"))
    agent.settle(batch)
    assert agent.guardrail.ledger.spent_today("ops:r1") == 0              # not released twice
    monkeypatch.setattr(agent.paypal, "get_payout", lambda b: dict(state, item_status="UNCLAIMED"))
    assert agent.settle(batch)["audit"]["payload"]["verdict"] == "hold"


def test_reconcile_survives_a_payout_lookup_failure(agent, monkeypatch):
    agent.pay(_intent(amount=40))
    monkeypatch.setattr(agent.paypal, "get_payout", boom)
    monkeypatch.setattr(agent.paypal, "list_transactions", lambda s, e: [
        {"transaction_id": "T9", "event_code": "T0001", "amount": -3.0, "currency": "USD",
         "time": "2026-11-09T00:00:00Z", "counterparty": "x@y.example", "subject": "?"}])
    out = agent.reconcile(hours=1)
    assert out["debits"] == 1 and len(out["outside"]) == 1
    assert agent.audit_log.records[-1]["payload"]["event"] == "outside"
