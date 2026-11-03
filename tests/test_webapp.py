"""Web console endpoint tests (offline: no keys, simulated execution).

The env is forced offline BEFORE importing the app, so importing it does not
pick up a developer's .env and start calling Nebius/Tavily or the real sandbox.
"""

from __future__ import annotations

import os

# No keys -> the client runs offline: simulated payouts, no network, no cost.
# (We deliberately do NOT set SAFEPAY_EXECUTE here, to avoid leaking a global
# env change into other tests.)
for _k in ("NEBIUS_API_KEY", "TAVILY_API_KEY", "PAYPAL_CLIENT_ID", "PAYPAL_SECRET"):
    os.environ[_k] = ""

import pytest  # noqa: E402

from webapp.app import app, _reset_agent  # noqa: E402


@pytest.fixture()
def client():
    _reset_agent()           # a clean, empty chain per test
    return app.test_client()


def _evaluate(client, amount=48):
    return client.post("/api/evaluate", json={
        "recipient": "billing@notion.so", "amount": amount, "category": "software",
        "merchant_name": "Notion", "task_goal": "pay invoices", "agent_id": "a1",
    })


def test_index_renders_ledger(client):
    r = client.get("/")
    assert r.status_code == 200
    assert b"audit-grid" in r.data            # the AG Grid mount point is present


def test_evaluate_appends_to_chain(client):
    r = _evaluate(client)
    j = r.get_json()
    assert j["decision"]["verdict"] in ("allow", "hold", "block")
    assert j["audit"]["payload"]["seq"] == 0
    a = client.get("/api/audit").get_json()
    assert a["ok"] is True and a["count"] == 1


def test_tamper_is_simulated_on_a_copy(client):
    _evaluate(client)
    _evaluate(client)
    t = client.post("/api/audit/tamper").get_json()
    assert t["ok"] is False and t["simulated"] is True     # the copy fails verification
    assert client.get("/api/audit").get_json()["ok"] is True  # the stored chain is intact
    client.post("/api/reset")
    assert client.get("/api/audit").get_json()["count"] == 0  # in-memory store only
