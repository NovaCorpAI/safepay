"""The rest of the MCP server's plain callables: mandates, approvals,
settlement, policy drafts, agent controls, reconciliation and the entry point."""

from __future__ import annotations

import os

import pytest

for _k in ("NEBIUS_API_KEY", "TAVILY_API_KEY", "PAYPAL_CLIENT_ID", "PAYPAL_SECRET"):
    os.environ[_k] = ""

from safepay import mcp_server as m  # noqa: E402
from safepay.agent import SafePayAgent  # noqa: E402
from safepay.agent_toolkit import GuardedToolkit  # noqa: E402
from safepay.models import PolicyConfig  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_agent(monkeypatch):
    policy = PolicyConfig(per_tx_limit=500, daily_limit=2000, hold_above=100)
    agent = SafePayAgent(config=m._config, policy=policy)
    monkeypatch.setattr(m, "_agent", agent)
    monkeypatch.setattr(m, "_gate", GuardedToolkit(config=m._config, policy=policy,
                                                   ledger=agent.guardrail.ledger))
    return agent


def test_mandate_open_capture_and_close():
    held = m.open_mandate(recipient="cloud@x.example", amount=200, category="software",
                          task_goal="book compute for the batch job", agent_id="batch")
    assert held["verdict"] == "hold" and held["executed"] is False     # over the ask-me line
    opened = m.open_mandate(recipient="cloud@x.example", amount=80, category="software",
                            task_goal="book compute for the batch job", agent_id="batch")
    assert opened["verdict"] == "allow" and opened["executed"] is True
    auth = opened["mandate"]["authorization_id"]
    assert opened["mandate"]["remaining"] == 80 and opened["paypal_mode"] == "simulated"
    cap = m.capture_mandate(auth, 30, agent_id="batch", purpose="first hour")
    assert cap["executed"] is True and cap["mandate"]["captured"] == 30 and cap["seal"]
    other = m.capture_mandate(auth, 10, agent_id="someone-else")
    assert other["verdict"] == "block" and other["executed"] is False
    assert "error" in m.capture_mandate("nope", 1)
    closed = m.close_mandate(auth, agent_id="batch", reason="done")
    assert closed["mandate"]["closed"] and closed["mandate"]["released"] == 50
    assert "error" in m.close_mandate("nope")
    assert m.audit_status()["ok"] is True and m.audit_status()["count"] >= 3


def test_held_payment_is_listed_then_approved_or_rejected():
    first = m.pay(recipient="v@x.example", amount=150, agent_id="ops")
    assert first["verdict"] == "hold" and first["executed"] is False
    listed = m.pending_approvals()
    assert listed["count"] == 1
    apr = listed["pending"][0]["id"]
    rejected = m.resolve_approval(apr, False, note="not now")
    assert rejected["approval_id"] == apr and rejected["verdict"] == "block"
    assert rejected["executed"] is False and rejected["audit_seq"] == 1
    assert any("rejected by human" in r for r in rejected["reasons"])
    assert m.pending_approvals()["count"] == 0
    assert "error" in m.resolve_approval(apr, True)          # resolved once, never twice
    m.pay(recipient="v@x.example", amount=150, agent_id="ops")
    out = m.resolve_approval(m.pending_approvals()["pending"][0]["id"], True, note="ok")
    assert out["verdict"] == "allow" and out["executed"] is True
    assert out["execution"]["mode"] == "simulated" and out["audit_seq"] == 3


def test_payout_status_settles_a_released_payment():
    paid = m.pay(recipient="v@x.example", amount=40, agent_id="ops")
    batch = paid["execution"]["payout_batch_id"]
    out = m.payout_status(batch)
    assert out["status"] == "SUCCESS" and out["already_recorded"] is False
    assert out["audit_seq"] == 1 and out["settlement"]["final"] is True
    assert m.payout_status(batch)["already_recorded"] is True
    assert m.payout_status("nope") == {"error": "no released payout 'nope' in this audit log"}


def test_payout_status_reports_paypal_outages(monkeypatch):
    paid = m.pay(recipient="v@x.example", amount=40, agent_id="ops")

    def boom(*a, **k):
        raise RuntimeError("sandbox down")
    monkeypatch.setattr(m._agent, "settle", boom)
    assert m.payout_status(paid["execution"]["payout_batch_id"]) == {"error": "PayPal lookup failed: sandbox down"}


def test_draft_policy_and_agents_and_reconcile():
    assert m.draft_policy_text("")["error"]
    draft = m.draft_policy_text("$300 per payment, ask me over 100, no gambling")
    assert draft["policy"]["per_tx_limit"] == 300.0 and draft["policy"]["hold_above"] == 100.0
    m.pay(recipient="v@x.example", amount=10, agent_id="shop-bot")
    ids = [a["agent_id"] for a in m.list_agents()["agents"]]
    assert "shop-bot" in ids
    paused = m.set_agent_paused("shop-bot", True, "incident")
    assert paused == {"agent_id": "shop-bot", "status": "paused", "reason": "incident"}
    frozen = m.pay(recipient="v@x.example", amount=10, agent_id="shop-bot")
    assert frozen["verdict"] == "block" and frozen["executed"] is False
    assert m.set_agent_paused("shop-bot", False)["status"] == "active"
    rec = m.reconcile_account(hours=2)
    assert rec["window_hours"] == 2 and rec["outside"] == []


def test_reconcile_reports_errors(monkeypatch):
    def boom(**k):
        raise RuntimeError("no transaction search")
    monkeypatch.setattr(m._agent, "reconcile", boom)
    assert m.reconcile_account() == {"error": "no transaction search"}


def test_pay_invoice_reads_a_billing_email():
    out = m.pay_invoice("Invoice 4471 from Acme Hosting\nTotal due: USD 42.00\n"
                        "Pay to billing@acme.example", task_goal="pay the hosting invoices",
                        agent_id="payables")
    assert "error" not in out, out
    assert out["invoice"]["amount"] == 42.0 and out["invoice"]["recipient"] == "billing@acme.example"
    assert out["verdict"] in ("allow", "hold") and out["stamp"]
    assert m.pay_invoice("hello") == {"error": "could not find recipient and amount in the invoice"}


def test_main_refuses_without_the_mcp_package(monkeypatch):
    monkeypatch.setattr(m, "_MCP", None)
    with pytest.raises(SystemExit, match="'mcp' package is not installed"):
        m.main()
    ran = []
    monkeypatch.setattr(m, "_MCP", type("S", (), {"run": lambda self: ran.append(1)})())
    m.main()
    assert ran == [1]
