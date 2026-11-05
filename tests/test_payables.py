"""SafePay's payables agent: invoice text in, judged payment out."""

import json
from pathlib import Path

import pytest

from safepay import payables
from safepay.agent import SafePayAgent
from safepay.models import PolicyConfig

SAMPLES = {s["id"]: s["text"] for s in json.loads(
    (Path(__file__).resolve().parents[1] / "examples" / "invoices.json").read_text("utf-8"))["samples"]}


class FakeChat:
    model = "fake-nemotron"

    def __init__(self, out):
        self.out, self.calls = out, 0

    def complete(self, system, user):
        self.calls += 1
        return json.dumps(self.out)


def test_rules_read_a_real_invoice():
    r = payables.read_invoice(SAMPLES["real"])
    f = r["intent"]
    assert f["recipient"] == "billing@notion.so"
    assert f["amount"] == 48.0 and f["currency"] == "USD"
    assert f["invoice_ref"] == "NTN-2026-11-0042"
    assert f["merchant_name"] == "Notion Labs"
    assert f["category"] == "software"
    assert r["flags"] == [] and r["missing"] == []


def test_model_values_must_appear_in_the_text():
    chat = FakeChat({"merchant_name": "Notion", "recipient": "attacker@evil.example",
                     "amount": 4800, "invoice_ref": "NTN-2026-11-0042", "currency": "USD"})
    r = payables.read_invoice(SAMPLES["real"], chat)
    assert r["intent"]["recipient"] == "billing@notion.so"      # invented email dropped
    assert r["intent"]["amount"] == 48.0                         # invented amount dropped
    assert set(r["dropped"]) == {"recipient", "amount"}
    assert r["intent"]["merchant_name"] == "Notion"              # grounded enough to keep
    assert r["source"] == "fake-nemotron"


def test_injected_invoice_never_reaches_the_model():
    chat = FakeChat({})
    r = payables.read_invoice(SAMPLES["injected"], chat)
    assert chat.calls == 0
    assert r["flags"][0]["rule"] == "instructions_in_invoice"


def test_bank_change_is_flagged():
    r = payables.read_invoice(SAMPLES["bank-change"])
    assert any(f["rule"] == "bank_details_change" for f in r["flags"])
    assert r["intent"]["recipient"] == "accounts@notion-billing.example"


def test_empty_and_unreadable():
    with pytest.raises(payables.InvoiceError):
        payables.read_invoice("  ")
    r = payables.read_invoice("hello, nothing to pay here")
    assert "recipient" in r["missing"] and "amount" in r["missing"]
    with pytest.raises(payables.InvoiceError):
        payables.to_intent(r, "a")


@pytest.fixture
def agent(monkeypatch):
    for k in ("PAYPAL_CLIENT_ID", "PAYPAL_CLIENT_SECRET", "TAVILY_API_KEY", "NEBIUS_API_KEY",
              "SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY"):
        monkeypatch.setenv(k, " ")
    a = SafePayAgent()
    a.policy = PolicyConfig(vendors={"Notion": {"domains": ["notion.so"], "expected_amount": 48,
                                                "aliases": ["Notion Labs"]}})
    a.guardrail.policy = a.policy
    return a


def test_the_four_samples_in_order(agent):
    first = agent.pay_invoice(SAMPLES["real"], "payables-agent")
    d = first["decision"]
    assert d["verdict"] == "allow" and d["execution"]["seal"].startswith("SP-")
    assert agent.pay_invoice(SAMPLES["resent"], "payables-agent")["decision"]["verdict"] == "block"
    bank = agent.pay_invoice(SAMPLES["bank-change"], "payables-agent")["decision"]
    assert bank["verdict"] == "block"                      # lookalike domain for Notion
    inj = agent.pay_invoice(SAMPLES["injected"], "payables-agent")
    assert inj["decision"]["verdict"] == "hold"
    assert any(c["name"] == "invoice" and not c["passed"] for c in inj["decision"]["checks"])
    assert inj["approval_id"]


def test_bank_change_for_unknown_vendor_is_held(agent):
    text = (SAMPLES["bank-change"].replace("Notion Billing", "Acme Studio").replace("Notion", "Acme")
            .replace("https://www.notion.so", "https://acme-billing.example")
            .replace("notion-billing", "acme-billing"))
    out = agent.pay_invoice(text, "payables-agent")
    assert out["decision"]["verdict"] == "hold"
    assert "payment details" in " ".join(out["decision"]["reasons"]).lower()


def test_endpoints(agent, monkeypatch):
    import webapp.app as appmod
    monkeypatch.setattr(appmod, "_agent", agent)
    c = appmod.app.test_client()
    assert len(c.get("/api/payables/samples").get_json()["samples"]) == 4
    r = c.post("/api/payables/read", json={"text": SAMPLES["real"]}).get_json()
    assert r["intent"]["amount"] == 48.0
    assert c.post("/api/payables/read", json={"text": ""}).status_code == 400
    p = c.post("/api/payables/pay", json={"text": SAMPLES["real"], "agent_id": "payables-agent:RUNX"}).get_json()
    assert p["decision"]["verdict"] == "allow" and p["decision"]["intent"]["agent_id"] == "payables-agent:RUNX"
    assert c.post("/api/payables/pay", json={"text": "nothing"}).status_code == 400


def test_mcp_pay_invoice(agent, monkeypatch):
    from safepay import mcp_server
    monkeypatch.setattr(mcp_server, "_agent", agent)
    out = mcp_server.pay_invoice(SAMPLES["real"])
    assert out["verdict"] == "allow" and out["seal"].startswith("SP-")
    assert mcp_server.pay_invoice("")["error"]
