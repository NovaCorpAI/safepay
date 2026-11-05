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
