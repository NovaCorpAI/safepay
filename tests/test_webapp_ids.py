"""Path ids arrive percent-encoded on some hosts (Vercel hands Flask the raw
path): `ops:r1` comes in as `ops%3Ar1`. The console must freeze, resume and
key the real agent, never a new one named with the literal encoding."""

from __future__ import annotations

import os

import pytest

for _k in ("NEBIUS_API_KEY", "TAVILY_API_KEY", "PAYPAL_CLIENT_ID", "PAYPAL_SECRET", "SUPABASE_URL",
           "SUPABASE_SERVICE_ROLE_KEY", "SAFEPAY_OPERATOR_TOKEN"):
    os.environ[_k] = ""

import webapp.app as appmod  # noqa: E402
from webapp.app import app, _decode_path_ids, _reset_agent  # noqa: E402


@pytest.fixture
def client(monkeypatch):
    _reset_agent()
    monkeypatch.setattr(appmod, "_RL_MAX", 0)
    return app.test_client()


def test_preprocessor_decodes_once_and_leaves_plain_ids():
    values = {"agent_id": "custom-agent%3Ar1", "other": "plain:id", "n": 3}
    _decode_path_ids("x", values)
    assert values == {"agent_id": "custom-agent:r1", "other": "plain:id", "n": 3}
    _decode_path_ids("x", None)      # no values: nothing to do


def test_encoded_agent_id_freezes_the_real_agent(client):
    client.post("/api/evaluate", json={"recipient": "billing@notion.so", "amount": 48, "category": "software",
                                       "merchant_name": "Notion", "task_goal": "pay invoices",
                                       "agent_id": "custom-agent:r1"})
    # The test client decodes the path itself; drive the view with the raw
    # value the way the host would hand it over.
    with app.test_request_context("/api/agents/custom-agent%3Ar1/pause", method="POST", json={"reason": "demo"}):
        values = {"agent_id": "custom-agent%3Ar1"}
        _decode_path_ids("pause_agent", values)
        resp = appmod._agent_status(values["agent_id"], True)
    body = resp.get_json() if hasattr(resp, "get_json") else resp[0].get_json()
    assert body["agent_id"] == "custom-agent:r1" and body["status"] == "paused"
    agents = {a["agent_id"]: a["status"] for a in client.get("/api/agents?run=r1").get_json()["agents"]}
    assert agents == {"custom-agent:r1": "paused"}          # no phantom "custom-agent%3Ar1"
    nxt = client.post("/api/evaluate", json={"recipient": "billing@notion.so", "amount": 5, "category": "software",
                                             "merchant_name": "Notion", "task_goal": "pay invoices",
                                             "agent_id": "custom-agent:r1"}).get_json()
    assert nxt["decision"]["verdict"] == "block"
    assert client.post("/api/agents/custom-agent%3Ar1/resume", json={}).status_code == 200
