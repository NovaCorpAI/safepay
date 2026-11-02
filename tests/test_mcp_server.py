"""Tests for the SafePay MCP server's plain callables (offline, no mcp package)."""

from __future__ import annotations

import os

for _k in ("NEBIUS_API_KEY", "TAVILY_API_KEY", "PAYPAL_CLIENT_ID", "PAYPAL_SECRET"):
    os.environ[_k] = ""

from safepay import mcp_server as m  # noqa: E402


def _reset():
    # Rebuild the shared agent so each test starts from an empty chain/ledger.
    from safepay.agent import SafePayAgent
    from safepay.agent_toolkit import GuardedToolkit
    m._agent = SafePayAgent(config=m._config, policy=m._policy)
    m._gate = GuardedToolkit(config=m._config, policy=m._policy,
                             ledger=m._agent.guardrail.ledger)


def test_evaluate_allows_within_policy():
    _reset()
    out = m.evaluate_payment(recipient="v@x.example", amount=48, category="software",
                             task_goal="pay invoices")
    assert out["verdict"] == "allow"
    assert any(c["name"] == "policy" for c in out["checks"])


def test_evaluate_blocks_over_limit():
    _reset()
    out = m.evaluate_payment(recipient="v@x.example", amount=9000)
    assert out["verdict"] == "block"


def test_pay_executes_on_allow_and_chains_audit():
    _reset()
    out = m.pay(recipient="v@x.example", amount=48, category="software")
    assert out["verdict"] == "allow"
    assert out["executed"] is True           # simulated payout offline
    assert out["audit_seq"] == 0
    status = m.audit_status()
    assert status["ok"] is True and status["count"] == 1


def test_pay_block_does_not_execute():
    _reset()
    out = m.pay(recipient="v@x.example", amount=9000)
    assert out["verdict"] == "block"
    assert out["executed"] is False
