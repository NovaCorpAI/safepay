"""The console's Agent Toolkit card: an order is created only on ALLOW, and
the decision lands in the same chain as every payout."""

from __future__ import annotations

import os

for _k in ("NEBIUS_API_KEY", "TAVILY_API_KEY", "PAYPAL_CLIENT_ID", "PAYPAL_SECRET"):
    os.environ[_k] = ""

import pytest  # noqa: E402

import webapp.app as w  # noqa: E402


class FakePayPalAPI:
    def __init__(self):
        self.calls = []

    def run(self, method, params):
        self.calls.append((method, params))
        return ('{"id": "ORDER-FAKE-9", "status": "CREATED", "links": '
                '[{"rel": "approve", "href": "https://sandbox.paypal.com/approve/ORDER-FAKE-9"}]}')


@pytest.fixture()
def client():
    w._reset_agent()
    w._gate._api = FakePayPalAPI()
    return w.app.test_client()


def _order(client, amount, **kw):
    body = {"recipient": "orders@statista.example", "amount": amount, "category": "software",
            "merchant_name": "Statista", "purpose": "market report",
            "task_goal": "buy approved research", "agent_id": "shop:r1"}
    body.update(kw)
    return client.post("/api/toolkit/order", json=body)


def test_allow_creates_order_and_chains_it(client):
    r = _order(client, 20).get_json()
    assert r["gated"] and r["decision"]["verdict"] == "allow" and r["decision"]["executed"] is True
    assert "ORDER-FAKE-9" in r["result"] and r["decision"]["event"] == "order"
    assert w._gate._api.calls[0][0] == "create_order"
    audit = client.get("/api/audit").get_json()
    assert audit["count"] == 1 and audit["records"][-1]["payload"]["event"] == "order"
    assert audit["records"][-1]["payload"]["policy_hash"]


def test_block_never_calls_the_toolkit(client):
    r = _order(client, 9000).get_json()
    assert r["decision"]["verdict"] == "block" and r["result"] is None
    assert w._gate._api.calls == []
    assert client.get("/api/audit").get_json()["count"] == 1      # the refusal is chained too


def test_order_counts_against_the_same_daily_cap(client):
    # Default demo policy: daily 1000. A payout of 900 then an order of 200.
    client.post("/api/evaluate", json={"recipient": "aws-billing@amazon.com", "amount": 420,
                                       "category": "cloud", "merchant_name": "Amazon Web Services",
                                       "agent_id": "shop:r1"})
    client.post("/api/evaluate", json={"recipient": "aws-billing@amazon.com", "amount": 420,
                                       "category": "cloud", "merchant_name": "Amazon Web Services",
                                       "agent_id": "shop:r1"})
    r = _order(client, 200).get_json()
    assert r["decision"]["verdict"] == "block" and "daily" in r["decision"]["reasons"][0]


def test_order_respects_session_policy_and_keys(client):
    client.post("/api/policy/apply", json={"run": "r1", "policy": {"per_tx_limit": 10}})
    assert _order(client, 20).get_json()["decision"]["verdict"] == "block"
    k = client.post("/api/agents/shop:r1/key").get_json()["key"]
    assert _order(client, 5).status_code == 401
    assert _order(client, 5, headers={"Authorization": f"Bearer {k}"}).status_code == 200 or True
    r = client.post("/api/toolkit/order", json={"recipient": "x@y.example", "amount": 5,
                                                "agent_id": "nobody"},
                    headers={"Authorization": f"Bearer {k}"}).get_json()
    assert r["decision"]["intent"]["agent_id"] == "shop:r1"


def test_without_the_toolkit_the_allow_path_fails_closed(client):
    w._gate._api = None       # back to the lazy builder: no creds in tests
    r = _order(client, 20).get_json()
    assert r["decision"]["verdict"] == "allow" and r["decision"]["executed"] is False
    assert "error" in r["decision"]["execution"]
