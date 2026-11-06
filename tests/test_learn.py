"""Policy learning: drafts proposed from the operator's approvals and rejections."""

import json

import pytest

from safepay import learn
from safepay.agent import SafePayAgent
from safepay.models import PaymentIntent, PolicyConfig

RUN = "LRN1"


@pytest.fixture
def agent(monkeypatch):
    for k in ("PAYPAL_CLIENT_ID", "PAYPAL_CLIENT_SECRET", "TAVILY_API_KEY", "NEBIUS_API_KEY",
              "SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY", "TELEGRAM_BOT_TOKEN"):
        monkeypatch.setenv(k, " ")
    a = SafePayAgent()
    a.policy = PolicyConfig(per_tx_limit=500, daily_limit=5000, hold_above=40)
    a.guardrail.policy = a.policy
    return a


def _held(agent, recipient, amount, merchant, category="software"):
    out = agent.pay(PaymentIntent(recipient=recipient, amount=amount, currency="USD", category=category,
                                  merchant_name=merchant, agent_id=f"ops:{RUN}", purpose="monthly plan",
                                  task_goal="pay software invoices"))
    assert out["decision"]["verdict"] == "hold", out["decision"]["reasons"]
    return out["approval_id"]


def _history(agent):
    for amt in (48, 52, 50):
        agent.approve(_held(agent, "billing@notion.so", amt, "Notion"))
    for _ in range(2):
        agent.reject(_held(agent, "payouts@lucky-spins.example", 90, "Lucky Spins", "gambling"))
    agent.approve(_held(agent, "billing@figma.com", 45, "Figma"))       # once: not a pattern yet


def test_learns_vendor_blocklist_and_category_with_evidence(agent):
    _history(agent)
    d = agent.learn_policy(RUN)
    assert d["decisions"] == 6 and d["approved"] == 4 and d["rejected"] == 2
    kinds = {p["kind"]: p for p in d["proposals"]}
    assert kinds["vendor"]["change"] == {"name": "Notion", "domain": "notion.so", "expected_amount": 50}
    assert len(kinds["vendor"]["evidence"]) == 3
    assert kinds["blocklist"]["change"] == {"recipient": "payouts@lucky-spins.example"}
    assert kinds["category"]["change"] == {"category": "gambling"}
    assert "figma" not in json.dumps(d["policy"]["vendors"]).lower()
    assert d["policy"]["vendors"]["Notion"]["expected_amount"] == 50
    assert set(d["changed"]) == {"vendors", "recipient_blocklist", "blocked_categories"}


def test_nothing_applies_until_the_operator_does(agent):
    _history(agent)
    before = agent.policy_for(f"console:{RUN}").to_dict()
    agent.learn_policy(RUN)
    assert agent.policy_for(f"console:{RUN}").to_dict() == before


def test_a_mixed_record_is_not_a_pattern(agent):
    agent.approve(_held(agent, "billing@notion.so", 48, "Notion"))
    agent.reject(_held(agent, "billing@notion.so", 49, "Notion"))
    agent.approve(_held(agent, "billing@notion.so", 50, "Notion"))
    assert agent.learn_policy(RUN)["proposals"] == []


def test_loosening_is_only_ever_a_question(agent):
    for amt in (45, 60, 70):
        agent.approve(_held(agent, f"a{amt}@vendor{amt}.example", amt, f"V{amt}"))
    d = agent.learn_policy(RUN)
    q = [p for p in d["proposals"] if p["kind"] == "question"]
    assert q and "Raise the threshold" in q[0]["text"]
    assert d["policy"]["hold_above"] == 40


def test_other_sessions_are_not_learned_from_unless_asked(agent):
    _history(agent)
    assert agent.learn_policy("OTHER")["decisions"] == 0
    assert agent.learn_policy("OTHER", scope="all")["decisions"] == 6


class FakeChat:
    model = "nemotron-test"

    def __init__(self, reply):
        self.reply, self.seen = reply, []

    def complete(self, system, user, temperature=0.1):
        self.seen.append(user)
        return self.reply


def test_model_reviews_but_cannot_add_or_change(agent):
    _history(agent)
    props = learn.propose(learn.human_decisions(agent.audit_log.records), agent.policy)
    n_cat = next(i for i, p in enumerate(props) if p["kind"] == "category")
    reply = json.dumps({"reviews": [{"id": n_cat, "keep": False, "reason": "too broad"},
                                    {"id": 99, "keep": True, "reason": "add crypto too"}]})
    chat = FakeChat(reply)
    d = learn.learn(agent.audit_log.records, agent.policy, chat)
    assert "gambling" not in d["policy"]["blocked_categories"]
    assert any("advises against" in q for q in d["questions"])
    assert "crypto" not in json.dumps(d["policy"])
    assert "monthly plan" not in chat.seen[0]              # no payment free text reaches the model
    assert d["source"].endswith("reviewed by nemotron-test")


def test_endpoint(agent, monkeypatch):
    import webapp.app as appmod
    monkeypatch.setattr(appmod, "_agent", agent)
    _history(agent)
    r = appmod.app.test_client().post("/api/policy/learn", json={"run": RUN}).get_json()
    assert r["decisions"] == 6 and len(r["proposals"]) == 3
