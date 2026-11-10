"""Approvals signed by the operator's device. The browser signs the exact
text the server gives for one button (action, approval id, currency, amount,
recipient) with the same WebCrypto key that signs the policy. An unpinned
session takes unsigned decisions as before and records a signer when one
comes; a session pinned to an operator key refuses an unsigned decision, one
signed by another key, or one signed for another payment or the other
action. The chain link carries the signer and the signed message."""

from __future__ import annotations

import os

import pytest

for _k in ("NEBIUS_API_KEY", "TAVILY_API_KEY", "PAYPAL_CLIENT_ID", "PAYPAL_SECRET", "TELEGRAM_BOT_TOKEN",
           "TELEGRAM_CHAT_ID", "SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY", "SAFEPAY_OPERATOR_TOKEN"):
    os.environ[_k] = ""

import webapp.app as appmod  # noqa: E402
from safepay import opsig  # noqa: E402
from safepay.agent import SafePayAgent  # noqa: E402
from safepay.config import Config  # noqa: E402
from safepay.models import PaymentIntent, PolicyConfig  # noqa: E402
from tests.test_opsig import Operator  # noqa: E402
from webapp.app import app, _reset_agent  # noqa: E402

RUN = "r9"
POLICY = {"per_tx_limit": 500, "daily_limit": 2000, "hold_above": 100}


def _cfg():
    return Config(nebius_api_key=None, nebius_base_url="", nebius_model="", tavily_api_key=None,
                  paypal_client_id=None, paypal_secret=None, paypal_base_url="https://api-m.sandbox.paypal.com")


@pytest.fixture
def agent():
    return SafePayAgent(config=_cfg(), policy=PolicyConfig(**POLICY))


def _hold(agent, amount=150, run=RUN):
    out = agent.pay(PaymentIntent(recipient="billing@notion.so", amount=amount, category="software",
                                  merchant_name="Notion", task_goal="pay invoices", agent_id=f"ops:{run}"))
    assert out["decision"]["verdict"] == "hold"
    return out["approval_id"]


def _pin(agent, op, run=RUN):
    msg = agent.policy_message(run, POLICY)["message"]
    return agent.apply_policy(run, POLICY, signature=op.sign(msg))


def test_message_binds_action_id_and_payment():
    intent = {"currency": "USD", "amount": 150, "recipient": "billing@notion.so"}
    m = opsig.approval_message("approve", "apr-1", intent)
    assert m == "safepay-approval:v1:approve:apr-1:USD:150.00:billing@notion.so"
    assert opsig.approval_message("reject", "apr-1", intent) != m
    assert opsig.approval_message("approve", "apr-2", intent) != m
    assert opsig.approval_message("approve", "apr-1", dict(intent, amount=151)) != m


def test_unpinned_session_takes_unsigned_and_records_a_signer_when_given(agent):
    a1 = _hold(agent)
    out = agent.approve(a1, "ok")
    assert out["decision"]["executed"] and "signer" not in (out["decision"]["execution"] or {})

    op = Operator()
    a2 = _hold(agent)
    sig = op.sign(agent.approval_message(a2, "approve"))
    out = agent.approve(a2, "ok", signature=sig)
    signer = out["decision"]["execution"]["signer"]
    assert signer["kid"] == opsig.key_id(op.jwk) and signer["msg"].endswith(":billing@notion.so")
    assert opsig.verify_bytes(signer["jwk"], signer["msg"].encode(), signer["sig"])   # verifiable offline
    assert any(f"signed by operator key {signer['kid']}" in r for r in out["decision"]["reasons"])
    assert agent.audit_log.records[-1]["payload"]["execution"]["signer"]["kid"] == signer["kid"]

    a3 = _hold(agent)
    out = agent.reject(a3, "no", signature=op.sign(agent.approval_message(a3, "reject")))
    assert out["decision"]["verdict"] == "block" and out["decision"]["execution"]["signer"]["kid"] == signer["kid"]


def test_pinned_session_refuses_unsigned_foreign_and_replayed(agent):
    op = Operator()
    _pin(agent, op)
    a1 = _hold(agent)
    with pytest.raises(PermissionError, match="pinned to operator key"):
        agent.approve(a1, "ok")
    with pytest.raises(PermissionError, match="not the pinned operator key"):
        agent.approve(a1, "ok", signature=Operator().sign(agent.approval_message(a1, "approve")))
    with pytest.raises(opsig.SignatureError):                       # signed the other action
        agent.approve(a1, "ok", signature=op.sign(agent.approval_message(a1, "reject")))
    a2 = _hold(agent)
    with pytest.raises(opsig.SignatureError):                       # signed for another payment
        agent.approve(a1, "ok", signature=op.sign(agent.approval_message(a2, "approve")))
    with pytest.raises(opsig.SignatureError):
        agent.approve(a1, "ok", signature={"jwk": op.jwk, "sig": "AAAA"})
    assert agent.approvals.get(a1).status == "pending"                # nothing moved
    assert agent.guardrail.ledger.spent_today(f"ops:{RUN}") == 0

    out = agent.approve(a1, "ok", signature=op.sign(agent.approval_message(a1, "approve")))
    assert out["decision"]["executed"] and out["decision"]["execution"]["signer"]["kid"] == opsig.key_id(op.jwk)
    with pytest.raises(PermissionError):
        agent.reject(a2, "no")
    out = agent.reject(a2, "no", signature=op.sign(agent.approval_message(a2, "reject")))
    assert out["decision"]["execution"]["signer"]["kid"] == opsig.key_id(op.jwk)


def test_other_sessions_are_not_pinned_by_this_one(agent):
    _pin(agent, Operator())
    a = _hold(agent, run="other")
    assert agent.approve(a, "ok")["decision"]["executed"]


def test_approval_message_needs_a_pending_approval(agent):
    with pytest.raises(ValueError, match="no pending approval"):
        agent.approval_message("nope", "approve")


@pytest.fixture
def client(monkeypatch):
    _reset_agent()
    monkeypatch.setattr(appmod, "_RL_MAX", 0)
    return app.test_client()


def _web_hold(client, run=RUN):
    # A vendor outside the directory: the console's default directory expects
    # USD 48 for Notion and would block 150 as inflated before any hold.
    out = client.post("/api/evaluate", json={
        "recipient": "billing@vendor.example", "amount": 150, "category": "software", "merchant_name": "Vendor",
        "task_goal": "pay invoices", "agent_id": f"ops:{run}"}).get_json()
    assert out["decision"]["verdict"] == "hold", out["decision"]["reasons"]
    return out["approval_id"]


def test_web_listing_gives_the_text_to_sign_and_the_pin(client):
    appmod._agent.apply_policy(RUN, POLICY)
    a = _web_hold(client)
    item = client.get(f"/api/approvals?run={RUN}").get_json()["pending"][0]
    assert item["id"] == a and item["pinned"] is None
    assert item["sign"]["approve"] == f"safepay-approval:v1:approve:{a}:USD:150.00:billing@vendor.example"
    assert item["sign"]["reject"].startswith(f"safepay-approval:v1:reject:{a}:")


def test_web_pinned_session_refuses_unsigned_and_takes_signed(client):
    op = Operator()
    appmod._agent.apply_policy(RUN, POLICY)
    msg = appmod._agent.policy_message(RUN, POLICY)["message"]
    assert client.post("/api/policy/apply", json={"run": RUN, "policy": POLICY, "signature": op.sign(msg)}).status_code == 200
    a = _web_hold(client)
    item = client.get(f"/api/approvals?run={RUN}").get_json()["pending"][0]
    assert item["pinned"] == opsig.key_id(op.jwk)

    r = client.post(f"/api/approvals/{a}/approve", json={"note": "x"})
    assert r.status_code == 403 and "pinned" in r.get_json()["error"]
    r = client.post(f"/api/approvals/{a}/approve", json={"note": "x", "signature": {"jwk": op.jwk, "sig": "AAAA"}})
    assert r.status_code == 403 and "does not verify" in r.get_json()["error"]
    r = client.post(f"/api/approvals/{a}/approve", json={"note": "x", "signature": op.sign(item["sign"]["approve"])})
    assert r.status_code == 200 and r.get_json()["decision"]["execution"]["signer"]["kid"] == item["pinned"]
    assert client.get(f"/api/approvals?run={RUN}").get_json()["count"] == 0
    assert client.post(f"/api/approvals/{a}/approve", json={}).status_code == 404   # already resolved
