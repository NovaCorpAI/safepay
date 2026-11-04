"""Web console approval endpoints (offline: fake reasoner forces a HOLD)."""

from __future__ import annotations

import json
import os

for _k in ("NEBIUS_API_KEY", "TAVILY_API_KEY", "PAYPAL_CLIENT_ID", "PAYPAL_SECRET"):
    os.environ[_k] = ""

import pytest  # noqa: E402

import webapp.app as w  # noqa: E402


class HoldingChat:
    model = "fake"

    def complete(self, system, user, temperature=0.1):
        return json.dumps({"aligned": False, "risk": 0.9, "reason": "off-goal"})


@pytest.fixture()
def client():
    w._reset_agent()
    w._agent.guardrail.chat = HoldingChat()
    return w.app.test_client()


def _hold(client):
    r = client.post("/api/evaluate", json={
        "recipient": "payouts@casino.example", "amount": 120, "category": "entertainment",
        "merchant_name": "Casino", "task_goal": "pay invoices", "agent_id": "a1",
    }).get_json()
    assert r["decision"]["verdict"] == "hold"
    return r["approval_id"]


def test_page_has_approvals_panel(client):
    assert b"Pending approvals" in client.get("/console").data


def test_hold_lists_then_approve_executes(client):
    aid = _hold(client)
    pend = client.get("/api/approvals").get_json()
    assert pend["count"] == 1 and pend["pending"][0]["id"] == aid
    out = client.post(f"/api/approvals/{aid}/approve", json={"note": "ok"}).get_json()
    assert out["decision"]["verdict"] == "allow" and out["decision"]["executed"] is True
    assert client.get("/api/approvals").get_json()["count"] == 0
    audit = client.get("/api/audit").get_json()
    assert audit["ok"] is True and audit["count"] == 2        # hold + approval


def test_reject_and_unknown(client):
    aid = _hold(client)
    out = client.post(f"/api/approvals/{aid}/reject", json={}).get_json()
    assert out["decision"]["verdict"] == "block"
    assert client.post(f"/api/approvals/{aid}/reject").status_code == 404   # already resolved
    assert client.post("/api/approvals/apr-nope/approve").status_code == 404
