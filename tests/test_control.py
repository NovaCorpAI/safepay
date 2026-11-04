"""Agents on a leash: the registry, pause/resume, and the hard 'agent paused'
rule in the guardrail (memory store)."""

from __future__ import annotations

import pytest

from safepay.agent import SafePayAgent
from safepay.control import ACTIVE, PAUSED, MemoryControl, run_of
from safepay.models import PaymentIntent, PolicyConfig


def test_run_of():
    assert run_of("ops-agent:ab12") == "ab12"
    assert run_of("ops-agent") == ""
    assert run_of(":x") == "" and run_of("") == ""


def test_registry_touch_and_status():
    c = MemoryControl()
    assert c.agent("a") is None
    c.touch_agent("a:r1"); c.touch_agent("a:r1"); c.touch_agent("b:r2")
    rows = c.agents()
    assert [r["agent_id"] for r in rows] == ["a:r1", "b:r2"]
    assert rows[0]["decisions"] == 2 and rows[0]["status"] == ACTIVE and rows[0]["run"] == "r1"
    assert [r["agent_id"] for r in c.agents("r2")] == ["b:r2"]
    c.set_agent_status("a:r1", PAUSED, "suspicious burst")
    assert c.agent("a:r1")["status"] == PAUSED and c.agent("a:r1")["reason"] == "suspicious burst"
    with pytest.raises(ValueError):
        c.set_agent_status("a:r1", "frozen")
    # Pausing an agent never seen registers it, so the pause applies on its first call.
    c.set_agent_status("new:r1", PAUSED)
    assert c.agent("new:r1")["status"] == PAUSED


def test_paused_agent_is_blocked_before_any_check_and_resumes():
    agent = SafePayAgent(policy=PolicyConfig(per_tx_limit=500, daily_limit=2000))
    ok = agent.pay(PaymentIntent(recipient="v@x.example", amount=10, agent_id="ops:r1"))
    assert ok["decision"]["verdict"] == "allow"
    agent.pause_agent("ops:r1", "incident 42")
    out = agent.pay(PaymentIntent(recipient="v@x.example", amount=10, agent_id="ops:r1"))
    d = out["decision"]
    assert d["verdict"] == "block" and d["executed"] is False
    assert [c["name"] for c in d["checks"]] == ["agent"]            # nothing else ran
    assert "incident 42" in d["reasons"][0]
    assert agent.audit_log.verify() == (True, None)                   # the refusal is chained
    agent.resume_agent("ops:r1")
    again = agent.pay(PaymentIntent(recipient="v@x.example", amount=10, agent_id="ops:r1"))
    assert again["decision"]["verdict"] == "allow"
    assert agent.agents("r1")[0]["decisions"] == 3
