"""Purchase check for apps that record supplier documents (Impulsa)."""

import json

import pytest

from safepay import purchase_check
from safepay.agent import SafePayAgent

HIST = [
    {"vendor": "Distribuidora El Sol SpA", "invoice_ref": "F-1201", "date": "2026-10-02", "product": "p1", "unit_cost": 1000},
    {"vendor": "Distribuidora El Sol", "invoice_ref": "F-1240", "date": "2026-10-16", "product": "p1", "unit_cost": 1100},
    {"vendor": "Harinas Sur", "invoice_ref": "77", "date": "2026-10-20", "product": "p2", "unit_cost": 500},
]


def body(**kw):
    b = {"business": "emp-1", "vendor": "Distribuidora El Sol", "invoice_ref": "F-1300", "currency": "CLP",
         "items": [{"product": "p1", "unit_cost": 1050, "qty": 10}], "history": HIST}
    b.update(kw)
    return b


def test_clean_purchase_allows():
    d, f = purchase_check.evaluate(body(), "impulsa")
    assert d.verdict.value == "allow" and f == []


def test_duplicate_document_blocks_even_with_name_variants():
    d, f = purchase_check.evaluate(body(invoice_ref="f 1201", vendor="DISTRIBUIDORA EL SOL S.P.A."), "impulsa")
    assert d.verdict.value == "block" and f[0]["rule"] == "duplicate_document"
    assert "2026-10-02" in f[0]["detail"]


def test_same_number_other_supplier_is_not_a_duplicate():
    d, _ = purchase_check.evaluate(body(invoice_ref="77"), "impulsa")
    assert d.verdict.value == "allow"


def test_price_jump_holds_and_needs_two_priors():
    d, f = purchase_check.evaluate(body(items=[{"product": "p1", "unit_cost": 2600}]), "impulsa")
    assert d.verdict.value == "hold" and f[0]["rule"] == "price_above_history"
    d2, _ = purchase_check.evaluate(body(items=[{"product": "p2", "unit_cost": 5000}]), "impulsa")
    assert d2.verdict.value == "allow"          # one prior purchase is not a history


def test_chained_link_carries_no_names_numbers_or_amounts():
    d, _ = purchase_check.evaluate(body(invoice_ref="F-1201"), "impulsa")
    blob = json.dumps(d.to_dict())
    for secret in ("Sol", "F-1201", "1201", "emp-1", "1050"):
        assert secret not in blob
    assert d.event == "check" and d.intent.amount == 0


def test_needs_items():
    with pytest.raises(ValueError):
        purchase_check.evaluate(body(items=[]), "impulsa")


@pytest.fixture
def agent(monkeypatch):
    for k in ("PAYPAL_CLIENT_ID", "PAYPAL_CLIENT_SECRET", "TAVILY_API_KEY", "NEBIUS_API_KEY",
              "SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY", "TELEGRAM_BOT_TOKEN"):
        monkeypatch.setenv(k, " ")
    return SafePayAgent()


def test_endpoint_requires_a_key_and_chains(agent, monkeypatch):
    import webapp.app as appmod
    monkeypatch.setattr(appmod, "_agent", agent)
    c = appmod.app.test_client()
    assert c.post("/api/check/purchase", json=body()).status_code == 401
    assert c.post("/api/check/purchase", json=body(), headers={"Authorization": "Bearer spk_nope"}).status_code == 401
    key = agent.issue_key("impulsa", "Impulsa purchases")["key"]
    r = c.post("/api/check/purchase", json=body(invoice_ref="F-1240"), headers={"Authorization": f"Bearer {key}"})
    j = r.get_json()
    assert r.status_code == 200 and j["verdict"] == "block" and j["chain_seq"] is not None
    assert agent.audit_log.records[-1]["payload"]["event"] == "check"


def test_outcomes_tally_and_public_totals(agent, monkeypatch):
    import webapp.app as appmod
    monkeypatch.setattr(appmod, "_agent", agent)
    monkeypatch.setenv("SAFEPAY_IMPACT_EXCLUDE", "")
    c = appmod.app.test_client()
    key = agent.issue_key("impulsa", "Impulsa")["key"]
    H = {"Authorization": f"Bearer {key}"}
    a = c.post("/api/check/purchase", json=body(invoice_ref="F-1240"), headers=H).get_json()   # duplicate -> block
    b = c.post("/api/check/purchase", json=body(items=[{"product": "p1", "unit_cost": 2600}]), headers=H).get_json()  # hold
    c.post("/api/check/purchase", json=body(), headers=H)                                       # clean
    r = c.post("/api/check/purchase/outcome", json={"seq": a["chain_seq"], "outcome": "corrected", "amount": 26000}, headers=H).get_json()
    assert r["ok"] and not r["duplicate"]
    assert c.post("/api/check/purchase/outcome", json={"seq": a["chain_seq"], "outcome": "corrected"}, headers=H).get_json()["duplicate"]
    c.post("/api/check/purchase/outcome", json={"seq": b["chain_seq"], "outcome": "recorded_anyway"}, headers=H)
    assert agent.audit_log.records[-1]["payload"]["event"] == "outcome"
    s = c.get("/api/check/purchase/summary?business=emp-1", headers=H).get_json()
    assert s["checks"] == 3 and s["alerts"] == 2 and s["corrected"] == 1 and s["recorded_anyway"] == 1
    assert s["precision"] == 0.5 and s["avoided"] == 26000 and s["rules"]["duplicate_document"] == 1
    t = c.get("/api/impact").get_json()["live"]
    assert t["businesses"] == 1 and t["avoided"] == {"CLP": 26000} and t["precision"] == 0.5
    # Nobody named as ours: the one business counts as external.
    assert t["external"] == {"businesses": 1, "checks": 3, "alerts": 2, "corrected": 1, "labels": []}
    assert t["team"]["businesses"] == 0
    assert c.post("/api/check/purchase/outcome", json={"seq": 0, "outcome": "corrected"}).status_code == 401
    assert c.post("/api/check/purchase/outcome", json={"seq": 99999, "outcome": "corrected"}, headers=H).status_code == 404
    business = agent.audit_log.records[-1]["payload"]["execution"]["business"]
    # Named as our own: still counted, but apart and labelled, never as external.
    monkeypatch.setenv("SAFEPAY_IMPACT_TEAM", f"{business}:AgentPOS, nope:Other")
    t = c.get("/api/impact").get_json()["live"]
    assert t["businesses"] == 1 and t["checks"] == 3
    assert t["team"] == {"businesses": 1, "checks": 3, "alerts": 2, "corrected": 1, "labels": ["AgentPOS"]}
    assert t["external"]["businesses"] == 0
    # A test account is excluded from everything, team or not.
    monkeypatch.setenv("SAFEPAY_IMPACT_EXCLUDE", business)
    t = c.get("/api/impact").get_json()["live"]
    assert t["businesses"] == 0 and t["team"]["businesses"] == 0 and t["external"]["businesses"] == 0


def test_business_hash_matches_what_impulsa_sends():
    from hashlib import sha256
    from safepay import impact
    sent = sha256(b"emp-uuid-1").hexdigest()          # what Impulsa puts in `business`
    d, _ = purchase_check.evaluate(body(business=sent), "impulsa")
    assert d.execution["business"] == impact.business_hash("emp-uuid-1")
    assert len(impact.business_hash("x")) == 12
