"""A USDC settlement on Stellar checked against its SafePay precheck (Horizon mocked)."""

from datetime import datetime, timedelta, timezone

import pytest

from safepay import connector, settlement
from safepay.agent import SafePayAgent
from safepay.models import PolicyConfig

STORE = "GAAACAQDAQCQMBYIBEFAWDANBYHRAEISCMKBKFQXDAMRUGY4DUPB7JZX"
OTHER = "GBANBETBAAAACAQDAQCQMBYIBEFAWDANBYHRAEISCMKBKFQXDAMRUGY4"
CIRCLE = settlement.USDC_ISSUER["stellar:pubnet"]
TX = "a" * 64


@pytest.fixture
def agent(monkeypatch):
    for k in ("PAYPAL_CLIENT_ID", "PAYPAL_CLIENT_SECRET", "TAVILY_API_KEY", "NEBIUS_API_KEY",
              "SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY", "TELEGRAM_BOT_TOKEN", "SAFEPAY_SEAL_ED25519_SEED"):
        monkeypatch.setenv(k, " ")
    monkeypatch.setenv("SAFEPAY_SIGNING_KEY", "k")
    a = SafePayAgent()
    a.policy = PolicyConfig(allowed_currencies=["USD", "USDC"], allowed_networks=["stellar"])
    a.guardrail.policy = a.policy
    return a


def precheck(agent, amount=1):
    return connector.check_payment(agent, "connector:t1", {
        "merchant_name": "AgentPOS demo store", "merchant_url": "https://demo.agentposhq.com", "amount": amount,
        "currency": "USDC", "network": "stellar", "payee": STORE, "task": "Buy the mug from the demo store"})


def ledger(monkeypatch, to=STORE, amount="1.0000000", issuer=CIRCLE, ok=True, when=None):
    when = when or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def fake(url):
        if url.endswith("/operations?limit=50"):
            return {"_embedded": {"records": [{"type": "invoke_host_function", "asset_balance_changes": [
                {"type": "transfer", "asset_code": "USDC", "asset_issuer": issuer, "amount": amount,
                 "from": OTHER, "to": to}]}]}}
        return {"successful": ok, "created_at": when, "ledger": 64263818}
    monkeypatch.setattr(settlement, "_get", fake)


def test_matching_settlement(agent, monkeypatch):
    c = precheck(agent)
    assert c["verdict"] == "allow" and c["attestation"]["body"]["terms"]["network"] == "stellar"
    ledger(monkeypatch)
    r = settlement.verify_settlement(c["attestation"], TX)
    assert r["match"] is True and r["paid"] == "1" and r["moved_money"] is False
    assert r["explorer"].endswith(TX) and r["attestation"]["body"]["v"] == "safepay-settlement-1"
    from safepay import receipt
    assert receipt.verify(r["attestation"])[0]


@pytest.mark.parametrize("kw,bad", [
    ({"to": OTHER}, "payee"),
    ({"amount": "1.5000000"}, "amount"),
    ({"issuer": "GFAKEISSUER"}, "token"),
    ({"ok": False}, "succeeded"),
    ({"when": (datetime.now(timezone.utc) + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")}, "timing"),
])
def test_mismatches(agent, monkeypatch, kw, bad):
    c = precheck(agent)
    ledger(monkeypatch, **kw)
    r = settlement.verify_settlement(c["attestation"], TX)
    assert r["match"] is False and bad in [f["name"] for f in r["findings"] if not f["passed"]]


def test_tampered_attestation_and_bad_input(agent, monkeypatch):
    c = precheck(agent)
    att = c["attestation"]
    att["body"]["terms"]["amountMinor"] = "100000"
    ledger(monkeypatch)
    r = settlement.verify_settlement(att, TX)
    assert r["match"] is False and not r["findings"][0]["passed"]
    with pytest.raises(settlement.SettlementError):
        settlement.fetch("nothex", "stellar:pubnet")
    with pytest.raises(settlement.SettlementError):
        settlement.fetch(TX, "ethereum")
