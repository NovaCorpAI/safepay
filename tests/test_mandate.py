"""Mandates: authorize a budget once, capture only what the task costs."""

import pytest

from safepay.agent import SafePayAgent
from safepay.models import PaymentIntent, PolicyConfig


@pytest.fixture
def agent(monkeypatch):
    for k in ("PAYPAL_CLIENT_ID", "PAYPAL_CLIENT_SECRET", "TAVILY_API_KEY", "NEBIUS_API_KEY",
              "SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY", "TELEGRAM_BOT_TOKEN"):
        monkeypatch.setenv(k, " ")
    a = SafePayAgent()
    a.policy = PolicyConfig(per_tx_limit=500, daily_limit=1000)
    a.guardrail.policy = a.policy
    return a


def _hotel(amount=300, agent_id="travel-agent:RUN1"):
    return PaymentIntent(recipient="reservations@harbor-hotel.example", amount=amount, currency="USD",
                         category="travel", merchant_name="Harbor Hotel", agent_id=agent_id,
                         purpose="Hotel for the Lisbon offsite, up to 2 nights",
                         task_goal="Book lodging for the Lisbon offsite")


def _open(agent, **kw):
    out = agent.open_mandate(_hotel(**kw))
    assert out["decision"]["verdict"] == "allow", out["decision"]["reasons"]
    return out["mandate"]


def test_open_judges_the_ceiling_and_reserves_it(agent):
    m = _open(agent)
    assert m["authorized"] == 300 and m["remaining"] == 300 and not m["closed"]
    assert m["authorization_id"].startswith("SIMAUTH")
    assert agent.guardrail.ledger.spent_today("travel-agent:RUN1") == 300
    assert agent.audit_log.records[-1]["payload"]["event"] == "authorize"


def test_ceiling_over_policy_is_blocked_and_nothing_is_authorized(agent):
    out = agent.open_mandate(_hotel(amount=900))
    assert out["decision"]["verdict"] == "block" and out["mandate"] is None
    assert not out["decision"]["executed"]


def test_captures_draw_down_and_overdraw_is_blocked(agent):
    m = _open(agent)
    a = m["authorization_id"]
    c1 = agent.capture(a, 180, "travel-agent:RUN1", "Night 1 and 2, room 412")
    assert c1["decision"]["verdict"] == "allow" and c1["decision"]["execution"]["seal"].startswith("SP-")
    assert c1["mandate"]["remaining"] == 120
    over = agent.capture(a, 200, "travel-agent:RUN1", "Late checkout and minibar")
    assert over["decision"]["verdict"] == "block"
    assert "more than the USD 120.00 left" in " ".join(over["decision"]["reasons"])
    assert not over["decision"]["executed"] and over["mandate"]["remaining"] == 120


def test_only_the_owner_can_capture(agent):
    a = _open(agent)["authorization_id"]
    out = agent.capture(a, 50, "shopping-agent:RUN1", "gift shop")
    assert out["decision"]["verdict"] == "block"
    assert "only 'travel-agent:RUN1'" in out["decision"]["reasons"][0]


def test_frozen_agent_cannot_capture(agent):
    a = _open(agent)["authorization_id"]
    agent.pause_agent("travel-agent:RUN1", "looping on bookings")
    out = agent.capture(a, 10, "travel-agent:RUN1", "breakfast")
    assert out["decision"]["verdict"] == "block" and "frozen" in out["decision"]["reasons"][0]


def test_close_voids_the_rest_and_credits_the_cap(agent):
    a = _open(agent)["authorization_id"]
    agent.capture(a, 180, "travel-agent:RUN1", "two nights")
    out = agent.close_mandate(a, "travel-agent:RUN1", "checked out")
    m = out["mandate"]
    assert m["closed"] and m["released"] == 120 and m["captured"] == 180 and m["remaining"] == 0
    assert agent.guardrail.ledger.spent_today("travel-agent:RUN1") == 180
    after = agent.capture(a, 10, "travel-agent:RUN1", "late fee")
    assert after["decision"]["verdict"] == "block" and "closed" in after["decision"]["reasons"][0]
    with pytest.raises(ValueError):
        agent.close_mandate(a, "travel-agent:RUN1")


def test_capturing_exactly_what_is_left_closes_it(agent):
    a = _open(agent, amount=100)["authorization_id"]
    out = agent.capture(a, 100, "travel-agent:RUN1", "one night")
    assert out["decision"]["execution"]["final"] and out["mandate"]["closed"]


def test_final_capture_releases_the_rest(agent):
    a = _open(agent)["authorization_id"]
    out = agent.capture(a, 210, "travel-agent:RUN1", "final bill", final=True)
    assert out["mandate"]["closed"] and out["mandate"]["released"] == 90
    assert agent.guardrail.ledger.spent_today("travel-agent:RUN1") == 210


def test_unknown_mandate_and_stranger_close(agent):
    with pytest.raises(LookupError):
        agent.capture("NOPE", 1, "x")
    a = _open(agent)["authorization_id"]
    with pytest.raises(PermissionError):
        agent.close_mandate(a, "someone-else:RUN1")
    assert agent.close_mandate(a, "operator:RUN1")["mandate"]["closed"]


def test_chain_stays_verifiable(agent):
    a = _open(agent)["authorization_id"]
    agent.capture(a, 50, "travel-agent:RUN1", "night 1")
    agent.capture(a, 500, "travel-agent:RUN1", "too much")
    agent.close_mandate(a, "travel-agent:RUN1")
    ok, bad = agent.audit_log.verify()
    assert ok and bad is None
    events = [r["payload"]["event"] for r in agent.audit_log.records[-4:]]
    assert events == ["authorize", "capture", "capture", "void"]


def test_capture_seal_verifies_as_a_capture(agent):
    from safepay import seal
    a = _open(agent)["authorization_id"]
    sid = agent.capture(a, 40, "travel-agent:RUN1", "night 1")["decision"]["execution"]["seal"]
    v = seal.view(seal.find(agent.audit_log, sid), sid)
    assert v["kind"] == "capture" and v["payout_status"] == "SUCCESS" and v["intact"]


def test_endpoints(agent, monkeypatch):
    import webapp.app as appmod
    monkeypatch.setattr(appmod, "_agent", agent)
    c = appmod.app.test_client()
    body = dict(_hotel().to_dict())
    o = c.post("/api/mandates", json=body).get_json()
    a = o["mandate"]["authorization_id"]
    agent_id = o["decision"]["intent"]["agent_id"]
    assert c.get(f"/api/mandates/{a}").get_json()["remaining"] == 300
    assert c.get("/api/mandates/bad id").status_code in (400, 404)
    assert c.get("/api/mandates/NOPE123").status_code == 404
    cap = c.post(f"/api/mandates/{a}/capture", json={"agent_id": agent_id, "amount": 100}).get_json()
    assert cap["decision"]["verdict"] == "allow"
    assert c.post(f"/api/mandates/{a}/capture", json={"agent_id": agent_id, "amount": "x"}).status_code == 400
    assert c.post(f"/api/mandates/{a}/close", json={"agent_id": "stranger"}).status_code == 403
    assert c.post(f"/api/mandates/{a}/close", json={"agent_id": agent_id}).get_json()["mandate"]["closed"]
    assert c.post(f"/api/mandates/{a}/close", json={"agent_id": agent_id}).status_code == 409


def test_mcp_tools(agent, monkeypatch):
    from safepay import mcp_server
    monkeypatch.setattr(mcp_server, "_agent", agent)
    o = mcp_server.open_mandate(**{k: v for k, v in _hotel().to_dict().items()
                                   if k in ("recipient", "amount", "currency", "category", "purpose",
                                            "task_goal", "merchant_name", "agent_id")})
    a = o["mandate"]["authorization_id"]
    assert mcp_server.capture_mandate(a, 50, "travel-agent:RUN1")["seal"].startswith("SP-")
    assert mcp_server.close_mandate(a, "travel-agent:RUN1")["mandate"]["closed"]
    assert mcp_server.capture_mandate("NOPE", 1)["error"]
