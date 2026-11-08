"""SafePay Check: remote MCP connector (streamable HTTP, JSON-RPC), no money moves."""

import json

import pytest

from safepay import receipt
from safepay.agent import SafePayAgent
from safepay.models import PolicyConfig


@pytest.fixture
def agent(monkeypatch):
    for k in ("PAYPAL_CLIENT_ID", "PAYPAL_CLIENT_SECRET", "TAVILY_API_KEY", "NEBIUS_API_KEY",
              "SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY", "TELEGRAM_BOT_TOKEN", "SAFEPAY_SEAL_ED25519_SEED"):
        monkeypatch.setenv(k, " ")
    monkeypatch.setenv("SAFEPAY_SIGNING_KEY", "k")
    a = SafePayAgent()
    a.policy = PolicyConfig(per_tx_limit=500, daily_limit=1000)
    a.guardrail.policy = a.policy
    return a


@pytest.fixture
def client(agent, monkeypatch):
    import webapp.app as appmod
    monkeypatch.setattr(appmod, "_agent", agent)
    return appmod.app.test_client()


def rpc(c, method, params=None, key=None, mid=1):
    h = {"Authorization": f"Bearer {key}"} if key else {}
    body = {"jsonrpc": "2.0", "id": mid, "method": method}
    if params is not None:
        body["params"] = params
    return c.post("/mcp", json=body, headers=h)


def call(c, key, name, args):
    r = rpc(c, "tools/call", {"name": name, "arguments": args}, key)
    assert r.status_code == 200
    return r.get_json()["result"]


def test_handshake_and_tools_are_public(client):
    r = rpc(client, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                   "clientInfo": {"name": "t", "version": "1"}}).get_json()["result"]
    assert r["protocolVersion"] == "2025-06-18" and r["serverInfo"]["name"] == "safepay"
    assert client.post("/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"}).status_code == 202
    tools = rpc(client, "tools/list").get_json()["result"]["tools"]
    assert [t["name"] for t in tools] == ["safepay_check_payment", "safepay_verify", "safepay_verify_settlement", "safepay_policy",
                                          "safepay_recent_checks"]
    assert all(not t["annotations"]["destructiveHint"] for t in tools)
    assert client.get("/mcp").status_code == 405
    assert client.options("/mcp").headers["Access-Control-Allow-Origin"] == "*"


def test_calls_need_a_valid_key(client):
    r = call(client, None, "safepay_policy", {})
    assert r["isError"] and "agent key is required" in r["structuredContent"]["error"]
    assert call(client, "spk_nope", "safepay_policy", {})["isError"]


def test_check_allows_blocks_and_signs_without_moving_money(client, agent):
    key = agent.issue_key("muse-agent:RUNX", "test")["key"]
    ok = call(client, key, "safepay_check_payment", {
        "merchant_name": "Notion", "amount": 48, "merchant_url": "https://www.notion.so/pricing", "payee": "billing@notion.so",
        "task": "Pay our monthly software invoices", "category": "software"})["structuredContent"]
    assert ok["verdict"] == "allow" and ok["moved_money"] is False and ok["check_id"].startswith("SC-")
    assert receipt.verify(ok["attestation"], signer=receipt.public_jwk()["x"]) == (True, "OK")
    assert ok["attestation"]["body"]["terms"]["amountMinor"] == "4800"
    link = agent.audit_log.records[-1]["payload"]
    assert link["event"] == "precheck" and not link["executed"] and link["intent"]["agent_id"] == "muse-agent:RUNX"
    big = call(client, key, "safepay_check_payment", {"merchant_name": "Notion", "amount": 900,
                                                      "task": "Pay our monthly software invoices"})["structuredContent"]
    assert big["verdict"] == "block" and "do not allow" in big["recommendation"]
    chained = [r["payload"] for r in agent.audit_log.records if r["payload"].get("event") == "precheck"]
    blob = json.dumps(chained)
    for secret in ("billing@notion.so", "Pay our monthly software invoices", "/pricing"):
        assert secret not in blob                                    # no personal data in the chain
    assert chained[0]["intent"]["recipient"].startswith("sha256:") and chained[0]["intent"]["merchant_name"] == "Notion"
    assert agent.guardrail.ledger.spent_today("muse-agent:RUNX") == 0          # nothing was spent
    v = call(client, key, "safepay_verify", {"attestation": ok["attestation"]})["structuredContent"]
    assert v["genuine"] and v["kind"] == "precheck" and v["states"]["check"] == ok["check_id"]
    tampered = dict(ok["attestation"], body=dict(ok["attestation"]["body"], decision={"verdict": "allow"}))
    assert call(client, key, "safepay_verify", {"attestation": tampered})["structuredContent"]["code"] == "RECEIPT_TAMPERED"
    recent = call(client, key, "safepay_recent_checks", {"limit": 5})["structuredContent"]["checks"]
    assert [r["verdict"] for r in recent] == ["block", "allow"]
    other = agent.issue_key("someone-else", "x")["key"]
    assert call(client, other, "safepay_recent_checks", {})["structuredContent"]["checks"] == []
    rules = call(client, key, "safepay_policy", {})["structuredContent"]
    assert any("USD 500.00" in r for r in rules["rules"]) and len(rules["fingerprint"]) == 16


def test_bad_input_is_a_tool_error_not_a_crash(client, agent):
    key = agent.issue_key("muse-agent:RUNY")["key"]
    r = call(client, key, "safepay_check_payment", {"merchant_name": "X", "amount": -3, "task": "t"})
    assert r["isError"] and "positive" in r["structuredContent"]["error"]
    assert rpc(client, "tools/call", {"name": "nope", "arguments": {}}, key).get_json()["error"]["code"] == -32602
    assert rpc(client, "resources/list").get_json()["error"]["code"] == -32601
    assert client.post("/mcp", data="{", content_type="application/json").status_code == 400
    batch = client.post("/mcp", json=[{"jsonrpc": "2.0", "id": 1, "method": "ping"},
                                      {"jsonrpc": "2.0", "method": "notifications/initialized"}]).get_json()
    assert batch == [{"jsonrpc": "2.0", "id": 1, "result": {}}]


def test_pages(client):
    for path, word in (("/connect", "safepay_check_payment"), ("/security", "17"), ("/privacy", "Tavily"),
                       ("/terms", "Verdicts are advice")):
        r = client.get(path)
        assert r.status_code == 200 and word in r.get_data(as_text=True), path


def test_connector_account_cannot_be_taken_over(client, monkeypatch):
    import webapp.app as appmod
    monkeypatch.setattr(appmod, "_RL_MAX", 0)
    acct = "connector:own123"
    k1 = client.post(f"/api/agents/{acct}/key", json={}).get_json()["key"]
    # a stranger cannot replace the key (the agent id is visible in the public log)
    r = client.post(f"/api/agents/{acct}/key", json={})
    assert r.status_code == 409 and "key" not in r.get_json()
    # nor loosen, reset or tamper with its unpinned policy
    loose = {"per_tx_limit": 100000, "daily_limit": 1000000}
    assert client.post("/api/policy/apply", json={"run": "own123", "policy": loose}).status_code == 403
    assert client.post("/api/policy/reset", json={"run": "own123"}).status_code == 403
    assert client.post("/api/policy/tamper", json={"run": "own123"}).status_code in (403, 404)
    assert call(client, k1, "safepay_check_payment", {"merchant_name": "Notion", "amount": 900,
                                                      "task": "Pay our software"})["structuredContent"]["verdict"] == "block"
    # the owner, holding the key, can change the rules and rotate the key
    h = {"Authorization": f"Bearer {k1}"}
    assert client.post("/api/policy/apply", json={"run": "own123", "policy": {"per_tx_limit": 1000, "daily_limit": 2000}},
                       headers=h).status_code == 200
    r = client.post(f"/api/agents/{acct}/key", json={}, headers=h)
    assert r.status_code == 200 and r.get_json()["key"] != k1
    # sessions without a connector key keep the open demo console
    assert client.post("/api/policy/apply", json={"run": "demo42", "policy": loose}).status_code == 200
    assert client.post("/api/agents/agent:demo42/key", json={}).status_code == 200
    assert client.post("/api/agents/agent:demo42/key", json={}).status_code == 200
