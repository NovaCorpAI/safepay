"""Which rules governed which payment: every decision carries the hash of the
policy it was judged under, and policy changes are chained as 'policy' links
with the same hash."""

from __future__ import annotations

from safepay.agent import SafePayAgent
from safepay.attest import audit_record, policy_hash, verify_record
from safepay.auditlog import verify_chain
from safepay.models import Decision, PaymentIntent, PolicyConfig, Verdict


def test_policy_hash_is_canonical_and_sensitive():
    a = PolicyConfig(per_tx_limit=100, daily_limit=500)
    b = PolicyConfig(daily_limit=500, per_tx_limit=100)
    assert policy_hash(a) == policy_hash(b) == policy_hash(a.to_dict())
    assert policy_hash(PolicyConfig(per_tx_limit=101, daily_limit=500)) != policy_hash(a)


def test_decisions_carry_the_hash_and_old_records_are_unchanged():
    agent = SafePayAgent(policy=PolicyConfig(per_tx_limit=500, daily_limit=2000))
    out = agent.pay(PaymentIntent(recipient="v@x.example", amount=10, agent_id="a"))
    assert out["audit"]["payload"]["policy_hash"] == policy_hash(agent.policy)
    assert out["decision"]["policy_hash"] == policy_hash(agent.policy)
    # A record built without a hash (older code paths) keeps the old shape.
    bare = Decision(verdict=Verdict.ALLOW, intent=PaymentIntent(recipient="v", amount=1))
    rec = audit_record(bare)
    assert "policy_hash" not in rec["payload"] and verify_record(rec)


def test_apply_and_reset_are_chained_and_decisions_reference_them():
    agent = SafePayAgent(policy=PolicyConfig(per_tx_limit=500, daily_limit=2000))
    before = agent.pay(PaymentIntent(recipient="v@x.example", amount=10, agent_id="ops:r1"))
    applied = agent.apply_policy("r1", {"per_tx_limit": 50, "hold_above": 20})
    link = applied["audit"]["payload"]
    assert link["event"] == "policy" and link["seq"] == applied["seq"] == 1
    assert link["policy_hash"] == applied["policy_hash"] == link["execution"]["policy_hash"]
    assert link["execution"]["policy"]["per_tx_limit"] == 50
    assert link["intent"]["amount"] == 0 and link["intent"]["agent_id"] == "operator:r1"
    after = agent.pay(PaymentIntent(recipient="v@x.example", amount=10, agent_id="ops:r1"))
    assert after["audit"]["payload"]["policy_hash"] == applied["policy_hash"]
    assert after["audit"]["payload"]["policy_hash"] != before["audit"]["payload"]["policy_hash"]
    # Another run is still judged under the default policy.
    other = agent.pay(PaymentIntent(recipient="v@x.example", amount=10, agent_id="ops:r2"))
    assert other["audit"]["payload"]["policy_hash"] == before["audit"]["payload"]["policy_hash"]
    reset = agent.clear_policy("r1")
    assert reset["audit"]["payload"]["event"] == "policy"
    assert reset["policy_hash"] == before["audit"]["payload"]["policy_hash"]
    assert agent.clear_policy("r1").get("audit") is None      # nothing to reset: no link
    assert verify_chain(agent.audit_log.records) == (True, None)
    assert [r["payload"].get("event", "decision") for r in agent.audit_log.records] == \
        ["decision", "policy", "decision", "decision", "policy"]


def test_approval_links_keep_the_decision_hash():
    agent = SafePayAgent(policy=PolicyConfig(per_tx_limit=500, daily_limit=2000, hold_above=5))
    held = agent.pay(PaymentIntent(recipient="v@x.example", amount=10, agent_id="a"))
    done = agent.approve(held["approval_id"], "ok")
    assert done["audit"]["payload"]["policy_hash"] == held["audit"]["payload"]["policy_hash"]
