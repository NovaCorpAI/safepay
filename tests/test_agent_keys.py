"""Agent keys (an identity the agent cannot spoof) and the operator token."""

from __future__ import annotations

import os

for _k in ("NEBIUS_API_KEY", "TAVILY_API_KEY", "PAYPAL_CLIENT_ID", "PAYPAL_SECRET"):
    os.environ[_k] = ""

import pytest  # noqa: E402

from safepay.agent import SafePayAgent  # noqa: E402
from safepay.control import KEY_PREFIX, MemoryControl  # noqa: E402
from safepay.models import PaymentIntent, PolicyConfig  # noqa: E402
import webapp.app as w  # noqa: E402


def test_issue_lookup_replace_revoke():
    c = MemoryControl()
    k1 = c.issue_key("ops:r1", "first")
    assert k1["key"].startswith(KEY_PREFIX) and c.has_key("ops:r1")
    assert c.agent_for_key(k1["key"]) == "ops:r1"
    assert c.agent("ops:r1")["has_key"] is True
    k2 = c.issue_key("ops:r1")                       # replaces: the old key dies
    assert c.agent_for_key(k1["key"]) is None and c.agent_for_key(k2["key"]) == "ops:r1"
    assert c.agent_for_key("nope") is None and c.agent_for_key("") is None
    assert c.revoke_key("ops:r1") is True and not c.has_key("ops:r1")
    assert c.agent_for_key(k2["key"]) is None and c.agent("ops:r1")["has_key"] is False
    assert c.revoke_key("ops:r1") is False


def test_resolve_agent_binds_identity():
    a = SafePayAgent(policy=PolicyConfig())
    assert a.resolve_agent("free:r1", None) == "free:r1"       # unregistered: as claimed
    k = a.issue_key("ops:r1")["key"]
    assert a.resolve_agent("someone-else", k) == "ops:r1"       # the key wins over the body
    with pytest.raises(ValueError):
        a.resolve_agent("ops:r1", None)                         # keyed agent without its key
    with pytest.raises(ValueError):
        a.resolve_agent("ops:r1", "spk_bogus")


@pytest.fixture()
def client():
    w._reset_agent()
    return w.app.test_client()


def _pay(client, agent_id, headers=None):
    return client.post("/api/evaluate", json={"recipient": "billing@notion.so", "amount": 10,
                                              "category": "software", "agent_id": agent_id},
                       headers=headers or {})


def test_evaluate_enforces_keys(client):
    assert _pay(client, "ops:r1").status_code == 200           # no key yet: open
    k = client.post("/api/agents/ops:r1/key", json={"label": "t"}).get_json()
    assert k["ok"] and k["key"].startswith(KEY_PREFIX)
    r = _pay(client, "ops:r1")
    assert r.status_code == 401 and "must present it" in r.get_json()["error"]
    r = _pay(client, "impostor", {"Authorization": f"Bearer {k['key']}"})
    assert r.status_code == 200
    assert r.get_json()["decision"]["intent"]["agent_id"] == "ops:r1"   # identity from the key
    assert _pay(client, "ops:r1", {"Authorization": "Bearer spk_wrong"}).status_code == 401
    rows = client.get("/api/agents?run=r1").get_json()["agents"]
    assert rows[0]["has_key"] is True
    assert client.delete("/api/agents/ops:r1/key").get_json()["revoked"] is True
    assert _pay(client, "ops:r1").status_code == 200


def test_operator_token_locks_operator_actions(client, monkeypatch):
    monkeypatch.setenv("SAFEPAY_OPERATOR_TOKEN", "op-secret")
    assert b"token required" in client.get("/").data
    assert client.post("/api/policy/apply", json={"run": "r1", "policy": {}}).status_code == 401
    assert client.post("/api/agents/ops:r1/pause").status_code == 401
    assert client.post("/api/agents/ops:r1/key").status_code == 401
    assert client.post("/api/reconcile", json={}).status_code == 401
    h = {"X-SafePay-Operator": "op-secret"}
    assert client.post("/api/policy/apply", json={"run": "r1", "policy": {}}, headers=h).status_code == 200
    assert client.post("/api/agents/ops:r1/pause", headers=h).get_json()["status"] == "paused"
    # Paying is not an operator action: agents keep working.
    assert _pay(client, "free:r1").status_code == 200
