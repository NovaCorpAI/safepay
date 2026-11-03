"""Human-in-the-loop approvals for HOLD decisions (offline, simulated payouts)."""

from __future__ import annotations

import json
import os

for _k in ("NEBIUS_API_KEY", "TAVILY_API_KEY", "PAYPAL_CLIENT_ID", "PAYPAL_SECRET"):
    os.environ[_k] = ""

import pytest  # noqa: E402

from safepay.agent import SafePayAgent  # noqa: E402
from safepay.config import Config  # noqa: E402
from safepay.models import PaymentIntent, PolicyConfig  # noqa: E402


class HoldingChat:
    """Fake reasoner that always finds a goal mismatch, forcing a HOLD."""
    model = "fake"

    def complete(self, system, user, temperature=0.1):
        return json.dumps({"aligned": False, "risk": 0.9, "reason": "does not serve the task"})


def _agent():
    agent = SafePayAgent(config=Config.from_env(),
                         policy=PolicyConfig(per_tx_limit=1000, daily_limit=10000))
    agent.guardrail.chat = HoldingChat()      # web stays off; reasoning forces HOLD
    return agent


def _intent(amount=120):
    return PaymentIntent(recipient="payouts@casino.example", amount=amount,
                         category="entertainment", merchant_name="Casino",
                         task_goal="pay approved invoices", agent_id="a1")


def test_hold_is_queued_not_executed():
    agent = _agent()
    out = agent.pay(_intent())
    assert out["decision"]["verdict"] == "hold"
    assert out["decision"]["executed"] is False
    assert out["approval_id"] and out["approval_id"].startswith("apr-")
    assert len(agent.pending_approvals()) == 1
    assert agent.guardrail.ledger.spent_today("a1") == 0


def test_approve_executes_charges_ledger_and_chains_audit():
    agent = _agent()
    aid = agent.pay(_intent(120))["approval_id"]
    out = agent.approve(aid, note="CFO ok")
    assert out["decision"]["verdict"] == "allow"
    assert out["decision"]["executed"] is True           # simulated payout offline
    assert "approved by human: CFO ok" in out["decision"]["reasons"]
    assert agent.guardrail.ledger.spent_today("a1") == 120
    assert agent.pending_approvals() == []
    assert agent.approvals.get(aid).status == "approved"
    # HOLD link + approval link, chain intact.
    assert len(agent.audit_log.records) == 2
    assert agent.audit_log.verify() == (True, None)


def test_reject_moves_nothing_and_chains_audit():
    agent = _agent()
    aid = agent.pay(_intent(120))["approval_id"]
    out = agent.reject(aid, note="not ours")
    assert out["decision"]["verdict"] == "block"
    assert out["decision"]["executed"] is False
    assert agent.guardrail.ledger.spent_today("a1") == 0
    assert agent.approvals.get(aid).status == "rejected"
    assert len(agent.audit_log.records) == 2


def test_cannot_resolve_twice_or_unknown():
    agent = _agent()
    aid = agent.pay(_intent())["approval_id"]
    agent.approve(aid)
    with pytest.raises(ValueError):
        agent.approve(aid)
    with pytest.raises(ValueError):
        agent.reject("apr-nope")
