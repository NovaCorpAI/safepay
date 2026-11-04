"""MCP plain callables for the operator controls (offline)."""

from __future__ import annotations

import os

for _k in ("NEBIUS_API_KEY", "TAVILY_API_KEY", "PAYPAL_CLIENT_ID", "PAYPAL_SECRET"):
    os.environ[_k] = ""

from safepay import mcp_server as m  # noqa: E402


def _reset():
    from safepay.agent import SafePayAgent
    m._agent = SafePayAgent(config=m._config, policy=m._policy)


def test_draft_policy_tool():
    _reset()
    out = m.draft_policy_text("$120 per payment, ask me over 60, no crypto")
    assert out["policy"]["per_tx_limit"] == 120 and out["policy"]["hold_above"] == 60
    assert "crypto" in out["policy"]["blocked_categories"] and out["rules"]
    assert "error" in m.draft_policy_text("")


def test_pause_resume_tools_gate_payments():
    _reset()
    assert m.pay(recipient="v@x.example", amount=5, agent_id="bot-1")["verdict"] == "allow"
    assert m.set_agent_paused("bot-1", True, "incident")["status"] == "paused"
    out = m.pay(recipient="v@x.example", amount=5, agent_id="bot-1")
    assert out["verdict"] == "block" and out["executed"] is False
    assert m.list_agents()["agents"][0]["status"] == "paused"
    assert m.set_agent_paused("bot-1", False)["status"] == "active"
    assert m.pay(recipient="v@x.example", amount=5, agent_id="bot-1")["verdict"] == "allow"
