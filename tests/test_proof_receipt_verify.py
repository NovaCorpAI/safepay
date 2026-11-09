"""Proof lookups against PayPal, the receipt signing seed and offline CLI, the
merchant check's fallbacks, and the connector's verify paths called directly."""

from __future__ import annotations

import base64
import copy
import json

import pytest

from safepay import connector, proof, receipt, settlement, verify
from safepay.agent import SafePayAgent
from safepay.connector import ToolError
from safepay.models import PaymentIntent, PolicyConfig, Severity
from safepay.search import SearchHit


# --- proof -----------------------------------------------------------------------------

def test_order_id_from_strings_and_junk():
    assert proof._order_id({"id": "9UG33738VS856624H"}) == "9UG33738VS856624H"
    assert proof._order_id('{"id": "9UG33738VS856624H"}') == "9UG33738VS856624H"
    assert proof._order_id('Order created: {"id": "5O190127TN364715T", "status": "CREATED"') == "5O190127TN364715T"
    assert proof._order_id("nothing here") == "" and proof._order_id(None) == "" and proof._order_id(7) == ""


class LivePayPal:
    live = True

    def __init__(self):
        self.paths = []

    def get_payout(self, pid):
        return {"item_status": "UNCLAIMED", "batch_status": "SUCCESS"}

    def get_json(self, path):
        self.paths.append(path)
        if "orders" in path:
            return {"status": "COMPLETED", "purchase_units": [{"amount": {"currency_code": "USD", "value": "48.00"}}]}
        return {"status": "CAPTURED", "amount": {"currency_code": "EUR", "value": "9.50"}}


def test_check_asks_paypal_for_each_kind():
    pp = LivePayPal()
    assert proof.check(pp, "payout", "4B4NJKRHZCU5W") == {"status": "UNCLAIMED", "detail": "SUCCESS"}
    assert proof.check(pp, "order", "9UG33738VS856624H") == {"status": "COMPLETED", "detail": "USD 48.00"}
    assert proof.check(pp, "capture", "02M56044XK893812S") == {"status": "CAPTURED", "detail": "EUR 9.50"}
    assert proof.check(pp, "authorization", "6B169298WM7409252")["status"] == "CAPTURED"
    assert pp.paths == ["/v2/checkout/orders/9UG33738VS856624H", "/v2/payments/captures/02M56044XK893812S",
                        "/v2/payments/authorizations/6B169298WM7409252"]

    class Off:
        live = False
    with pytest.raises(ValueError, match="no PayPal credentials"):
        proof.check(Off(), "payout", "4B4NJKRHZCU5W")


def test_items_respects_the_limit_and_skips_links_without_ids():
    recs = [{"payload": {"seq": n, "event": "decision", "execution": {"mode": "sandbox", "payout_batch_id": f"4B4NJKRHZCU{n}W"}}}
            for n in range(5)]
    recs.append({"payload": {"seq": 9, "execution": {"mode": "sandbox", "method": "create_order", "result": "x"}}})
    recs.append({"payload": {"seq": 10, "execution": "not a dict"}})
    assert [i["seq"] for i in proof.items(recs, limit=2)] == [4, 3]
    assert [i["seq"] for i in proof.items(recs)] == [4, 3, 2, 1, 0]


# --- receipt seed and CLI -----------------------------------------------------------------

def test_seed_prefers_a_valid_ed25519_seed(monkeypatch):
    monkeypatch.setenv("SAFEPAY_SIGNING_KEY", "base-key")
    monkeypatch.setenv("SAFEPAY_SEAL_ED25519_SEED", " ")
    derived = receipt.public_jwk()["x"]
    monkeypatch.setenv("SAFEPAY_SEAL_ED25519_SEED", "!!!not-base64!!!")
    assert receipt.public_jwk()["x"] == derived                  # unreadable seed: fall back
    monkeypatch.setenv("SAFEPAY_SEAL_ED25519_SEED", base64.urlsafe_b64encode(b"short").decode())
    assert receipt.public_jwk()["x"] == derived                  # wrong length: fall back
    monkeypatch.setenv("SAFEPAY_SEAL_ED25519_SEED", base64.urlsafe_b64encode(b"s" * 32).decode().rstrip("="))
    own = receipt.public_jwk()
    assert own["x"] != derived and own["kty"] == "OKP" and len(own["kid"]) == 16
    monkeypatch.setenv("SAFEPAY_SIGNING_KEY", " ")
    assert receipt.public_jwk()["x"] == own["x"]                 # the seed alone is enough
    monkeypatch.setenv("SAFEPAY_SEAL_ED25519_SEED", " ")
    assert receipt.public_jwk() is None and receipt.sign_envelope({"v": receipt.VERSION}) is None


def test_sign_envelope_refuses_unknown_versions(monkeypatch):
    monkeypatch.setenv("SAFEPAY_SIGNING_KEY", "base-key")
    monkeypatch.setenv("SAFEPAY_SEAL_ED25519_SEED", " ")
    assert receipt.sign_envelope({"v": "something-else"}) is None
    j = receipt.public_jwk()
    env = receipt.sign_envelope({"v": receipt.PRECHECK, "check": "SC-1", "gone": None,
                                 "issuer": {"name": "SafePay", "signer": j["x"], "kid": j["kid"]}})
    assert "gone" not in env["body"] and receipt.verify(env, signer=j["x"]) == (True, "OK")
    # An envelope that names no signer proves nothing: the verifier refuses it.
    anon = receipt.sign_envelope({"v": receipt.PRECHECK, "check": "SC-1"})
    assert receipt.verify(anon) == (False, "RECEIPT_SIGNATURE_INVALID")


@pytest.fixture
def sealed(monkeypatch):
    for k in ("PAYPAL_CLIENT_ID", "PAYPAL_SECRET", "TAVILY_API_KEY", "NEBIUS_API_KEY",
              "SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY", "SAFEPAY_SEAL_ED25519_SEED"):
        monkeypatch.setenv(k, " ")
    monkeypatch.setenv("SAFEPAY_SIGNING_KEY", "test-signing-key")
    agent = SafePayAgent(policy=PolicyConfig(per_tx_limit=500, daily_limit=2000))
    out = agent.pay(PaymentIntent(recipient="billing@notion.so", amount=48, merchant_name="Notion",
                                  task_goal="pay software invoices"))
    sid = out["decision"]["execution"]["seal"]
    from safepay import seal
    return agent, sid, receipt.issue(seal.find(agent.audit_log, sid), sid)


def test_receipt_cli_checks_a_receipt_offline(sealed, tmp_path, capsys):
    agent, sid, r = sealed
    rf, kf = tmp_path / "receipt.json", tmp_path / "keys.json"
    rf.write_text(json.dumps(r), "utf-8")
    kf.write_text(json.dumps(receipt.keyset()), "utf-8")
    assert receipt._main([str(rf), str(kf)]) == 0
    out = capsys.readouterr().out
    assert out.startswith("OK\n") and f"{sid}: USD 4800 (minor units), payment.released" in out
    assert "integrity only" not in out
    assert receipt._main([str(rf)]) == 0
    assert "integrity only" in capsys.readouterr().out
    kf.write_text(json.dumps({"keys": [{"x": "A" * 43}]}), "utf-8")
    assert receipt._main([str(rf), str(kf)]) == 1
    assert "not signed by a published SafePay key" in capsys.readouterr().out
    t = copy.deepcopy(r)
    t["body"]["terms"]["amountMinor"] = "1"
    rf.write_text(json.dumps(t), "utf-8")
    assert receipt._main([str(rf)]) == 1
    assert capsys.readouterr().out.strip() == "RECEIPT_TAMPERED"


# --- the merchant check without a model, or with a broken one ---------------------------

class Web:
    enabled = True

    def __init__(self, hits):
        self.hits = hits

    def search(self, q, max_results=5):
        return self.hits


class Chat:
    def __init__(self, reply=None, raise_=False):
        self.reply, self.raise_ = reply, raise_

    def complete(self, system, user, temperature=0.1):
        if self.raise_:
            raise RuntimeError("model down")
        return json.dumps(self.reply)


def _intent(**kw):
    base = dict(recipient="pay@shop.example", amount=10, merchant_name="Shop")
    base.update(kw)
    return PaymentIntent(**base)


def test_nothing_to_verify_passes_as_info():
    c = verify.check_merchant(_intent(recipient="", merchant_name=None), Web([]))
    assert c.passed and c.severity == Severity.INFO and "no merchant name" in c.detail


def test_keyword_fallback_needs_two_distinct_terms():
    one = [SearchHit("Shop reviews", "https://r.example", "some say scam but mostly fine")]
    c = verify.check_merchant(_intent(), Web(one), Chat(raise_=True))      # model down: heuristic
    assert c.passed and c.severity == Severity.OK and c.risk == 0.05
    two = [SearchHit("Shop", "https://r.example", "scam"), SearchHit("x", "https://y.example", "ripoff, do not buy")]
    c = verify.check_merchant(_intent(), Web(two))                          # no model at all
    assert not c.passed and c.severity == Severity.WARN
    assert "do not buy, ripoff, scam" in c.detail and c.risk == 0.85
    assert [e["url"] for e in c.evidence] == ["https://r.example", "https://y.example"]


def test_model_verdict_without_a_reason_gets_a_default_one():
    hits = [SearchHit("Shop", "https://r.example", "fine")]
    ok = verify.check_merchant(_intent(), Web(hits), Chat({"legitimate": True, "risk": 0.1}))
    assert ok.passed and ok.detail == "no credible fraud reports found for 'Shop'"
    bad = verify.check_merchant(_intent(), Web(hits), Chat({"legitimate": False, "risk": 2.5, "reason": " "}))
    assert not bad.passed and bad.risk == 1.0 and bad.detail == "'Shop' flagged as potentially unsafe"
    partial = verify.check_merchant(_intent(), Web(hits), Chat({"risk": 0.1}))
    assert not partial.passed                       # a reply that never clears the payee is not a pass


# --- the connector's verify paths, called directly -----------------------------------------

@pytest.fixture
def agent(monkeypatch):
    for k in ("PAYPAL_CLIENT_ID", "PAYPAL_SECRET", "TAVILY_API_KEY", "NEBIUS_API_KEY",
              "SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY", "SAFEPAY_SEAL_ED25519_SEED"):
        monkeypatch.setenv(k, " ")
    monkeypatch.setenv("SAFEPAY_SIGNING_KEY", "k")
    return SafePayAgent(policy=PolicyConfig(per_tx_limit=500, daily_limit=2000))


def test_verify_accepts_json_text_and_seal_ids(agent):
    key = agent.issue_key("muse:R1")["key"]
    check = connector.check_payment(agent, "muse:R1", {"merchant_name": "Notion", "amount": 48,
                                                        "task": "pay software invoices"})
    as_text = connector.verify(agent, {"attestation": json.dumps(check["attestation"])})
    assert as_text["genuine"] and as_text["kind"] == "precheck" and as_text["expired"] is False
    with pytest.raises(ToolError, match="must be the JSON"):
        connector.verify(agent, {"attestation": "{not json"})
    with pytest.raises(ToolError, match="pass a seal id"):
        connector.verify(agent, {})
    assert connector.verify(agent, {"seal": "SP-0000000000"}) == {"genuine": False, "code": "RECEIPT_NOT_FOUND",
                                                                 "seal": "SP-0000000000"}
    paid = agent.pay(PaymentIntent(recipient="billing@notion.so", amount=48, merchant_name="Notion",
                                   task_goal="pay software invoices", agent_id="muse:R1"))
    sid = paid["decision"]["execution"]["seal"]
    v = connector.verify(agent, {"seal": sid.lower()})
    assert v["genuine"] is True and v["kind"] == "seal" and v["states"]["amount"] == 48
    assert v["page"].endswith(f"/v/{sid}")
    res = connector.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                            "params": {"name": "safepay_verify", "arguments": {"seal": sid}}}, agent, key)
    assert res["result"]["structuredContent"]["genuine"] is True


def test_verify_settlement_wraps_every_failure(agent, monkeypatch):
    with pytest.raises(ToolError, match="must be the JSON"):
        connector.verify_settlement({"attestation": "{"})
    with pytest.raises(ToolError, match="attestation is required"):
        connector.verify_settlement({"attestation": 5})
    monkeypatch.setattr(settlement, "verify_settlement",
                        lambda att, tx, net: (_ for _ in ()).throw(settlement.SettlementError("amount differs")))
    with pytest.raises(ToolError, match="amount differs"):
        connector.verify_settlement({"attestation": {"body": {}}, "tx_hash": "ABC"})
    monkeypatch.setattr(settlement, "verify_settlement",
                        lambda att, tx, net: (_ for _ in ()).throw(OSError("horizon timeout")))
    with pytest.raises(ToolError, match="could not read the ledger: horizon timeout"):
        connector.verify_settlement({"attestation": json.dumps({"body": {}})})
    seen = {}
    monkeypatch.setattr(settlement, "verify_settlement",
                        lambda att, tx, net: seen.update(tx=tx, net=net) or {"settled": True})
    assert connector.verify_settlement({"attestation": {"body": {}}, "tx_hash": " ABCD "}) == {"settled": True}
    assert seen == {"tx": "abcd", "net": "stellar:pubnet"}
    key = agent.issue_key("muse:R2")["key"]
    res = connector.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                            "params": {"name": "safepay_verify_settlement", "arguments": {"attestation": 5}}}, agent, key)
    assert res["result"]["isError"] and "attestation is required" in res["result"]["structuredContent"]["error"]


def test_recent_limits_and_invalid_requests(agent):
    assert connector.recent(agent, "nobody", "abc") == {"checks": []}
    assert connector.recent(agent, "nobody", 99) == {"checks": []}
    bad = connector.handle("not a message", agent, None)
    assert bad["error"]["code"] == -32600 and bad["id"] is None
    assert connector.handle({"jsonrpc": "1.0", "id": 3, "method": "ping"}, agent, None)["error"]["code"] == -32600
    assert connector.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}, agent, None) is None
    init = connector.handle({"jsonrpc": "2.0", "id": 4, "method": "initialize",
                             "params": {"protocolVersion": "1999-01-01"}}, agent, None)
    assert init["result"]["protocolVersion"] == connector.PROTOCOL
