"""Seal receipts: Ed25519, AgentPOS receipt envelope, verifiable offline."""

import copy
import hashlib
import json

import pytest

from safepay import receipt, seal
from safepay.agent import SafePayAgent
from safepay.models import PaymentIntent


@pytest.fixture
def agent(monkeypatch):
    for k in ("PAYPAL_CLIENT_ID", "PAYPAL_CLIENT_SECRET", "TAVILY_API_KEY", "NEBIUS_API_KEY",
              "SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY", "SAFEPAY_SEAL_ED25519_SEED"):
        monkeypatch.setenv(k, " ")
    monkeypatch.setenv("SAFEPAY_SIGNING_KEY", "test-signing-key")
    return SafePayAgent()


def _sealed(agent):
    out = agent.pay(PaymentIntent(recipient="billing@notion.so", amount=48, merchant_name="Notion",
                                  purpose="Team plan", task_goal="pay software invoices",
                                  merchant_url="https://www.notion.so/pricing"))
    sid = out["decision"]["execution"]["seal"]
    return sid, seal.find(agent.audit_log, sid)


def test_receipt_verifies_and_matches_the_chain(agent):
    sid, found = _sealed(agent)
    r = receipt.issue(found, sid)
    assert receipt.verify(r) == (True, "OK")
    assert receipt.verify(r, signer=receipt.public_jwk()["x"]) == (True, "OK")
    b = r["body"]
    assert b["v"] == "safepay-receipt-1" and b["seal"] == sid
    assert b["chain"]["hash"] == found["record"]["sha256"]
    assert b["terms"] == {"amountMinor": "4800", "currency": "USD", "payeeDomain": "notion.so",
                          "payeeSha256": hashlib.sha256(b"billing@notion.so").hexdigest(),
                          "merchantDomain": "www.notion.so"}
    assert "billing@" not in json.dumps(r)          # the address itself never appears
    assert r == receipt.issue(found, sid)           # deterministic: same link, same receipt


def test_tampering_and_foreign_keys_are_caught(agent):
    sid, found = _sealed(agent)
    r = receipt.issue(found, sid)
    t = copy.deepcopy(r)
    t["body"]["terms"]["amountMinor"] = "480000"
    assert receipt.verify(t) == (False, "RECEIPT_TAMPERED")
    t["hash"] = hashlib.sha256(receipt.canonical(t["body"])).hexdigest()
    assert receipt.verify(t) == (False, "RECEIPT_SIGNATURE_INVALID")
    assert receipt.verify(r, signer="A" * 43) == (False, "RECEIPT_SIGNATURE_INVALID")
    assert receipt.verify({"nope": 1}) == (False, "RECEIPT_MALFORMED")


def test_minor_units():
    assert receipt.minor(48, "USD") == "4800" and receipt.minor(12990, "CLP") == "12990"
    assert receipt.minor(0.1 + 0.2, "EUR") == "30"


def test_no_key_no_receipt(monkeypatch, agent):
    sid, found = _sealed(agent)
    monkeypatch.setenv("SAFEPAY_SIGNING_KEY", " ")
    assert receipt.issue(found, sid) is None and receipt.keyset()["keys"] == []


def test_endpoints(agent, monkeypatch):
    import webapp.app as appmod
    monkeypatch.setattr(appmod, "_agent", agent)
    c = appmod.app.test_client()
    sid, _ = _sealed(agent)
    keys = c.get("/.well-known/safepay-seal-keys.json").get_json()["keys"]
    r = c.get(f"/api/v/{sid}/receipt").get_json()
    assert receipt.verify(r, signer=keys[0]["x"]) == (True, "OK")
    assert c.get("/api/v/SP-0000000000/receipt").status_code == 404
    m = c.post("/api/merchant/verify", json={"seal": sid, "amount": 48}).get_json()
    assert m["receipt"]["hash"] == r["hash"]
    page = c.get(f"/v/{sid}").get_data(as_text=True)
    assert "receipt.js" in page and "safepay-seal-keys.json" in page


def test_dump_for_browser_check(agent, tmp_path_factory, monkeypatch):
    import os
    out = os.environ.get("SAFEPAY_RC_DUMP")
    if not out:
        pytest.skip("dump only on request")
    sid, found = _sealed(agent)
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"r": receipt.issue(found, sid), "keys": receipt.keyset()["keys"]}, f, ensure_ascii=False)
