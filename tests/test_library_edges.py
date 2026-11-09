"""Remaining branches of the library modules: the MCP proxy's parsing and
upstream errors, AP2 object validation, policy coercion and description,
operator signatures, stablecoin addresses, invoice reading, settlement
fetches, seal views, the guardrail's cache tolerance, the toolkit gate's
idempotency wrapper and replay window, and every FastMCP tool wrapper."""

from __future__ import annotations

import asyncio
import base64
import json
import os
import time
from types import SimpleNamespace

import pytest

for _k in ("NEBIUS_API_KEY", "TAVILY_API_KEY", "PAYPAL_CLIENT_ID", "PAYPAL_SECRET"):
    os.environ[_k] = ""

from safepay import agent_toolkit, ap2, mcp_proxy, opsig, payables, policy_draft, seal, settlement, stablecoin  # noqa: E402
from safepay.agent import SafePayAgent  # noqa: E402
from safepay.guardrail import Guardrail  # noqa: E402
from safepay.models import PaymentIntent, PolicyConfig  # noqa: E402
from safepay.policy_draft import PolicyDraftError, coerce_policy, describe_policy, draft_from_rules, draft_policy  # noqa: E402
from safepay.search import SearchHit  # noqa: E402
from tests.test_ap2 import Operator, _intent as signed_intent  # noqa: E402


# --- MCP proxy ------------------------------------------------------------------------------

def _res(text, error=False):
    return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)], isError=error)


def test_proxy_reads_json_out_of_prose():
    assert mcp_proxy._json(_res('Created: {"id": "O1"} ok')) == {"id": "O1"}
    assert mcp_proxy._json(_res("{bad")) is None
    assert mcp_proxy._json(_res('x {"a": } y')) is None
    assert mcp_proxy._json(_res("")) is None and mcp_proxy._json(SimpleNamespace(content=None)) is None


def test_proxy_records_an_upstream_crash_without_paying():
    agent = SafePayAgent(policy=PolicyConfig(per_tx_limit=500, daily_limit=2000))

    async def upstream(name, args):
        raise OSError("paypal mcp exited")
    out = asyncio.run(mcp_proxy.PaymentProxy(agent, upstream).call(
        "create_order", {"currencyCode": "USD", "items": [{"name": "Report", "itemCost": 20, "quantity": 1}]}))
    assert out["forward"] is True and out["result"] is None
    assert out["safepay"]["executed"] is False and any("upstream error" in r for r in out["safepay"]["reasons"])
    assert agent.audit_log.records[-1]["payload"]["execution"]["error"] == "paypal mcp exited"
    assert agent.guardrail.ledger.spent_today("mcp-proxy") == 0


def test_upstream_command_from_env(monkeypatch):
    monkeypatch.setenv("SAFEPAY_UPSTREAM_CMD", "node server.js --tools=all")
    assert mcp_proxy._upstream_command() == ("node", ["server.js", "--tools=all"])
    monkeypatch.delenv("SAFEPAY_UPSTREAM_CMD")
    cmd, args = mcp_proxy._upstream_command()
    assert cmd == ("npx.cmd" if os.name == "nt" else "npx") and args == ["-y", "@paypal/mcp", "--tools=all"]


# --- AP2 ----------------------------------------------------------------------------------------

def test_ap2_shape_errors():
    with pytest.raises(ap2.MandateError, match="ISO 8601"):
        ap2._parse_iso("yesterday")
    with pytest.raises(ap2.MandateError, match="must be an object"):
        ap2._check_intent_shape("x")
    with pytest.raises(ap2.MandateError, match="lacks intent_expiry"):
        ap2._check_intent_shape({"user_cart_confirmation_required": False, "natural_language_description": "d"})
    with pytest.raises(ap2.MandateError, match="list of strings"):
        ap2._check_intent_shape(dict(signed_intent(), merchants=[1]))
    assert ap2.host("https://www.Shop.example/x") == "shop.example" and ap2.host("a@b.example") == "b.example"
    assert ap2._iso(0).startswith("1970-01-01T00:00:00")


def test_ap2_authorization_errors():
    op = Operator()
    m = signed_intent()
    with pytest.raises(ap2.MandateError, match="compact JWS"):
        ap2.verify_intent({"intent_mandate": m, "user_authorization": "nope"}, "x@harbor-hotel.example", None)
    good = op.sign(m)
    h, p, s = good["user_authorization"].split(".")
    rs = base64.urlsafe_b64encode(json.dumps({"alg": "RS256", "jwk": op.jwk}).encode()).rstrip(b"=").decode()
    with pytest.raises(ap2.MandateError, match="ES256"):
        ap2.verify_intent({"intent_mandate": m, "user_authorization": f"{rs}.{p}.{s}"}, "x@harbor-hotel.example", None)
    badjwk = base64.urlsafe_b64encode(json.dumps({"alg": "ES256", "jwk": {"kty": "RSA"}}).encode()).rstrip(b"=").decode()
    with pytest.raises(ap2.MandateError, match="P-256"):
        ap2.verify_intent({"intent_mandate": m, "user_authorization": f"{badjwk}.{p}.{s}"}, "x@harbor-hotel.example", None)
    with pytest.raises(ap2.MandateError, match="does not verify"):
        ap2.verify_intent({"intent_mandate": m, "user_authorization": f"{h}.{p}.{s[:-4]}AAAA"}, "x@harbor-hotel.example", None)
    ok = ap2.verify_intent(good, "x@harbor-hotel.example", None)
    assert ok["merchants"] == ["harbor-hotel.example"] and ok["pinned"] is False
    assert ap2.intent_checks(None) == []
    assert ap2.intent_checks(ok)[0][0] is True
    expired = dict(ok, expires_ts=time.time() - 1)
    assert ap2.intent_checks(expired)[0][0] is False


def test_cart_mandate_errors(monkeypatch):
    q = {"cart_id": "c1", "total": 2.0, "lines": [{"title": "S", "quantity": 2, "lineTotal": "2"}],
         "intent": {"merchant_url": "https://demo.agentposhq.com"}}
    with pytest.raises(ap2.MandateError, match="cart_id and lines"):
        ap2.cart_mandate({}, "orders@demo.agentposhq.com", 2.0)
    with pytest.raises(ap2.MandateError, match="the payee is other.example"):
        ap2.cart_mandate(q, "orders@other.example", 2.0)
    with pytest.raises(ap2.MandateError, match="do not add up"):
        ap2.cart_mandate(dict(q, lines=[{"title": "S", "quantity": 1, "lineTotal": "1"}]), "orders@demo.agentposhq.com", 2.0)
    c = ap2.cart_mandate(q, "orders@demo.agentposhq.com", 2.0)
    assert c["contents"]["cart_expiry"] and c["contents"]["merchant_name"] == "demo.agentposhq.com"
    monkeypatch.setenv("SAFEPAY_SIGNING_KEY", " ")
    monkeypatch.setenv("SAFEPAY_SEAL_ED25519_SEED", " ")
    assert ap2._ed25519_jwt({"a": 1}) is None


# --- policy drafts ------------------------------------------------------------------------------

def test_coerce_policy_cleans_and_refuses():
    p = coerce_policy({"recipient_allowlist": "a@x.example,, B@x.example", "allowed_currencies": [],
                       "vendors": {"": {}, "Acme": {"expected_amount": "lots", "domains": "acme.example"},
                                   "Beta": "not a spec"},
                       "vendor_amount_tolerance": 0.5, "hold_on_soft_risk": "yes", "unknown": 1,
                       "max_payments_per_window": "3.7"})
    assert p.recipient_allowlist == ["a@x.example", "b@x.example"] and p.allowed_currencies == ["USD"]
    assert p.vendors == {"Acme": {"domains": ["acme.example"], "expected_amount": None, "aliases": []},
                         "Beta": {"domains": [], "expected_amount": None, "aliases": []}}
    assert p.vendor_amount_tolerance == 1.0 and p.hold_on_soft_risk is True and p.max_payments_per_window == 3
    assert policy_draft._vendors("nope") == {}
    with pytest.raises(PolicyDraftError, match="unusable value"):
        coerce_policy({"per_tx_limit": "a lot"})
    with pytest.raises(PolicyDraftError, match="daily_limit"):
        coerce_policy({"daily_limit": 0})


def test_describe_every_rule():
    p = PolicyConfig(per_tx_limit=100, daily_limit=500, per_recipient_limit=50, max_payments_per_window=3,
                     velocity_window_seconds=600, allowed_categories=["software"], blocked_categories=["gambling"],
                     recipient_allowlist=["a@x.example"], recipient_blocklist=["b@x.example"],
                     allowed_currencies=["USD", "USDC"], hold_unknown_vendors=True, hold_on_soft_risk=False)
    text = "\n".join(describe_policy(p))
    for piece in ("USD 50.00 per payee", "3 payments per 10 minutes", "Only these categories: software",
                  "Never these categories: gambling", "Only pay: a@x.example", "Never pay: b@x.example",
                  "Stablecoins (USDC) only on any network", "outside the vendor directory", "is only logged"):
        assert piece in text, piece
    p2 = PolicyConfig(allowed_currencies=["USDC"], allowed_networks=["stellar"])
    assert "only on stellar" in "\n".join(describe_policy(p2))


def test_rules_parser_reads_weeks_months_and_only():
    d = draft_from_rules("Spend up to 700 a week, only software purchases.")
    assert d["policy"]["daily_limit"] == 100.0 and d["policy"]["allowed_categories"] == ["software"]
    assert any("Weekly" in s for s in d["summary"])
    d = draft_from_rules("Budget 3000 per month.")
    assert d["policy"]["daily_limit"] == 100.0 and any("Monthly" in s for s in d["summary"])
    d = draft_from_rules("Never gambling. Pay in EUR.")
    assert d["policy"]["blocked_categories"] == ["gambling"] and d["policy"]["allowed_currencies"] == ["EUR"]
    assert any("No amount" in q for q in d["questions"])


class Chat:
    model = "fake-model"

    def __init__(self, reply):
        self.reply = reply

    def complete(self, system, user, temperature=0.0):
        return self.reply


def test_model_drafts_are_validated_and_fall_back():
    with pytest.raises(PolicyDraftError, match="too long"):
        draft_policy("x" * 4001)
    out = draft_policy("ask me over 100", Chat("[1, 2]"))
    assert out["source"] == "rules" and any("model was unavailable" in q for q in out["questions"])
    out = draft_policy("ask me over 100", Chat(json.dumps({"policy": "nope"})))
    assert out["source"] == "rules"
    out = draft_policy("ask me over 100", Chat(json.dumps({"policy": {"hold_above": 100}, "summary": [" "]})))
    assert out["source"] == "fake-model" and out["policy"]["hold_above"] == 100.0
    assert out["summary"] == describe_policy(PolicyConfig(**out["policy"]))      # empty summary: described


# --- operator signatures ----------------------------------------------------------------------

def test_opsig_rejects_bad_keys_and_signatures():
    from tests.test_opsig import Operator as Op
    op = Op()
    with pytest.raises(opsig.SignatureError, match="P-256"):
        opsig.public_jwk({"kty": "RSA"})
    with pytest.raises(opsig.SignatureError, match="valid P-256 point"):
        opsig.public_jwk(dict(op.jwk, x="AAAA"))
    assert opsig.verify(op.jwk, "r", "h", "!!!") is False
    assert opsig.verify(op.jwk, "r", "h", "AAAA") is False
    with pytest.raises(opsig.SignatureError, match="must be {jwk, sig}"):
        opsig.seal_policy("r", "h", "nope")
    sealed = opsig.seal_policy("r", "h", op.sign(opsig.message("r", "h")))
    assert opsig.check_stored(sealed, "r", "h", sealed["kid"]) is None
    assert "not the pinned operator key" in opsig.check_stored(sealed, "r", "h", "OTHERKID")
    assert "does not match" in opsig.check_stored(dict(sealed, jwk={"kty": "RSA"}), "r", "h", sealed["kid"])
    assert opsig.check_stored(None, "r", "h", "k") == "it carries no operator signature"


# --- stablecoin addresses ---------------------------------------------------------------------------

def test_address_validators():
    assert stablecoin.valid_solana("0OIl" * 9) is False
    assert stablecoin.valid_address("base", "0x" + "a" * 40) is True
    assert stablecoin.valid_address("base", "0x" + "a" * 39) is False
    assert stablecoin.valid_address("bitcoin", "1abc") is False
    assert stablecoin.valid_address("stellar", "GAAACAQDAQCQMBYIBEFAWDANBYHRAEISCMKBKFQXDAMRUGY4DUPB7JZX") is True


# --- invoices ---------------------------------------------------------------------------------------

def test_invoice_reader_edges(monkeypatch):
    monkeypatch.setattr(payables, "loads_lenient", lambda raw: [1])
    with pytest.raises(payables.InvoiceError, match="did not return an object"):
        payables.extract_model("Total 40", Chat("[1]"))
    monkeypatch.undo()
    assert payables._currency("Total: €40") == "EUR" and payables._currency("40 GBP due") == "GBP"
    f = payables.extract_rules("From: Acme <bills@acme.example>\nTotal: 40.00\nPay to billing@acme.example")
    assert f["merchant_name"] == "Acme"
    f = payables.extract_rules("From: bills@acme.example\nTotal: 40.00\nPay to billing@acme.example")
    assert f["merchant_name"] == "Acme"                      # the email alone is not a name: domain used
    assert payables._grounded("recipient", None, "x") is False
    assert payables._grounded("amount", "lots", "40") is False
    assert payables._grounded("merchant_url", "https://acme.example/", "see https://acme.example") is True
    with pytest.raises(payables.InvoiceError, match="too long"):
        payables.read_invoice("x" * 6001)
    with pytest.raises(ValueError):                           # a bare list has no object to read
        payables.extract_model("Total 40", Chat("[]"))

    class Down:
        model = "m"

        def complete(self, *a, **k):
            raise OSError("model down")
    out = payables.read_invoice("Acme\nTotal: 40.00\nPay to billing@acme.example", Down())
    assert out["source"].startswith("rules (model unavailable") and out["intent"]["amount"] == 40.0


# --- settlement fetches -----------------------------------------------------------------------------

def test_settlement_fetch_errors_and_path_payments(monkeypatch):
    monkeypatch.setattr(settlement.requests, "get", lambda *a, **k: SimpleNamespace(
        status_code=404, raise_for_status=lambda: None, json=lambda: {}))
    with pytest.raises(settlement.SettlementError, match="not found"):
        settlement._get("https://horizon.example/tx")

    def boom():
        raise OSError("horizon 500")
    monkeypatch.setattr(settlement.requests, "get", lambda *a, **k: SimpleNamespace(
        status_code=500, raise_for_status=boom, json=lambda: {}))
    with pytest.raises(OSError):
        settlement._get("https://horizon.example/tx")
    with pytest.raises(settlement.SettlementError, match="unsupported network"):
        settlement.fetch("a" * 64, "ethereum")
    with pytest.raises(settlement.SettlementError, match="64-character"):
        settlement.fetch("xyz")
    h = "b" * 64
    pages = {f"/transactions/{h}": {"successful": True, "created_at": "2026-11-09T00:00:00Z", "ledger": 1},
             f"/transactions/{h}/operations?limit=50": {"_embedded": {"records": [
                 {"type": "path_payment_strict_send", "asset_code": "USDC", "asset_issuer": "GI", "amount": "1.5",
                  "from": "GA", "to": "GB"},
                 {"type": "invoke_host_function", "asset_balance_changes": [
                     {"type": "transfer", "asset_code": "USDC", "asset_issuer": "GI", "amount": "2", "from": "GA", "to": "GC"},
                     {"type": "mint"}]}]}}}
    monkeypatch.setattr(settlement, "_get", lambda url: pages[url.split(".org", 1)[1]])
    tx = settlement.fetch(h, "stellar:testnet")
    assert [t["to"] for t in tx["transfers"]] == ["GB", "GC"] and tx["explorer"].endswith(h)
    res = settlement.compare({"body": {"terms": {"amountMinor": "abc"}}}, tx, None)
    findings = res["findings"] if isinstance(res, dict) else res
    assert findings[0]["passed"] is False and findings[0]["name"] == "attestation"


# --- seals ------------------------------------------------------------------------------------------

def test_seal_helpers_and_store_backed_find():
    assert seal.mask_email("plain") == "pl•••" and seal.mask_email("") == ""
    assert seal.mask_email("ab@x.example") == "a•••@x.example"
    assert seal._when("not a time") == "" and seal._host("https://shop.example:8443/x") == "shop.example"

    class Store:
        def find_seal(self, sid):
            return {"record": {"payload": {"seq": 1, "execution": {"seal": sid, "payout_batch_id": "B1"}}},
                    "prev": None,
                    "settlements": [{"payload": {"ts": 1700000000, "execution": {"item_status": "RETURNED"}}}]}
    found = seal.find(Store(), "SP-AAAAAAAAAA")
    v = seal.view(found, "SP-AAAAAAAAAA")
    assert v["payout_status"] == "RETURNED" and v["status_when"].startswith("2023-11-14") and v["linked"] is None


# --- the guardrail tolerates a broken cache -------------------------------------------------------------

class Web:
    enabled = True

    def __init__(self, hits):
        self.hits, self.calls = hits, 0

    def search(self, q, max_results=5):
        self.calls += 1
        return self.hits


class BrokenCache:
    def get(self, k): raise RuntimeError("get")
    def set(self, k, v): raise RuntimeError("set")
    def spent_today(self): raise RuntimeError("spent")
    def spend(self, n=1): raise RuntimeError("spend")


def test_guardrail_runs_checks_when_the_cache_is_down():
    web = Web([SearchHit("Acme", "https://a.example", "fine")])
    g = Guardrail(PolicyConfig(), web, None, cache=BrokenCache())
    d = g.evaluate(PaymentIntent(recipient="pay@acme.example", amount=10, merchant_name="Acme", agent_id="a"))
    assert d.verdict.value == "allow" and web.calls == 1


def test_soft_concern_is_only_logged_when_the_policy_says_so():
    web = Web([SearchHit("Acme", "https://a.example", "scam, fraud, ripoff")])
    g = Guardrail(PolicyConfig(hold_on_soft_risk=False), web, None)
    d = g.evaluate(PaymentIntent(recipient="pay@acme.example", amount=10, merchant_name="Acme", agent_id="a"))
    assert d.verdict.value == "allow" and d.reasons and d.reasons[0].startswith("merchant:")


# --- the toolkit gate's idempotency wrapper and replay window -------------------------------------------

def test_idempotent_post_wraps_both_client_generations(monkeypatch):
    import requests
    sent = []

    class Resp:
        def __init__(self, status, body):
            self.status_code, self._body = status, body

        def raise_for_status(self): pass

        def json(self):
            if self._body is None:
                raise ValueError
            return self._body
    answers = iter([Resp(201, {"id": "O1"}), Resp(204, None), Resp(200, None)])
    monkeypatch.setattr(requests, "post", lambda url, headers=None, json=None, timeout=None:
                        sent.append((url, headers, json)) or next(answers))

    class Client:
        base_url = "https://api-m.sandbox.paypal.com"

        def build_headers(self): return {"Authorization": "Bearer t"}

        def post(self, uri, payload): return {"original": True}
    api = SimpleNamespace(_paypal_client=Client())
    agent_toolkit.make_idempotent(api)
    agent_toolkit.make_idempotent(api)                        # wrapping twice is a no-op
    assert api._paypal_client.post("/v2/checkout/orders", {"a": 1}) == {"original": True}   # no request id: untouched
    token = agent_toolkit._REQUEST_ID.set("rid-1")
    try:
        assert api._paypal_client.post("/v2/checkout/orders", {"a": 1}) == {"id": "O1"}
        assert sent[0][1] == {"Authorization": "Bearer t", "PayPal-Request-Id": "rid-1"}
        assert api._paypal_client.post("/x", {}) == {} and api._paypal_client.post("/x", {}) == {}
    finally:
        agent_toolkit._REQUEST_ID.reset(token)
    assert agent_toolkit.make_idempotent(SimpleNamespace()) is not None       # no client: left alone

    class Old:
        base_url = "https://api-m.sandbox.paypal.com"

        def get_access_token(self): return "old-t"

        def post(self, uri, payload): return {}
    api2 = agent_toolkit.make_idempotent(SimpleNamespace(_paypal_client=Old()))
    answers = iter([Resp(201, {"id": "O2"})])
    token = agent_toolkit._REQUEST_ID.set("rid-2")
    try:
        api2._paypal_client.post("/y", {})
    finally:
        agent_toolkit._REQUEST_ID.reset(token)
    assert sent[-1][1]["Authorization"] == "Bearer old-t"


def test_replay_window_expires_and_is_bounded(monkeypatch):
    agent_toolkit.reset_replays()
    agent_toolkit._remember("r0", {"decision": {}}, 0)
    assert agent_toolkit._earlier("r0") is None                   # ttl 0: nothing kept
    agent_toolkit._remember("r1", {"decision": {"verdict": "allow"}}, 10)
    assert agent_toolkit._earlier("r1")["decision"]["verdict"] == "allow"
    real = time.time
    monkeypatch.setattr(agent_toolkit.time, "time", lambda: real() + 11)
    assert agent_toolkit._earlier("r1") is None
    monkeypatch.setattr(agent_toolkit, "_DONE_MAX", 2)
    for r in ("a", "b", "c"):
        agent_toolkit._remember(r, {"decision": {}}, 100)
    assert agent_toolkit._earlier("a") is None and agent_toolkit._earlier("c") is not None
    agent_toolkit.reset_replays()


def test_toolkit_api_needs_credentials():
    from safepay.config import Config
    cfg = Config(nebius_api_key=None, nebius_base_url="", nebius_model="", tavily_api_key=None,
                 paypal_client_id=None, paypal_secret=None, paypal_base_url="https://api-m.sandbox.paypal.com")
    gate = agent_toolkit.GuardedToolkit(config=cfg)
    why = "not set" if agent_toolkit.toolkit_available() else "not installed"
    with pytest.raises(agent_toolkit.ToolkitUnavailable, match=why):
        gate.api
    intent = PaymentIntent(recipient="v@x.example", amount=5, agent_id="a")
    assert gate.evaluate(intent).verdict.value == "allow"


# --- the FastMCP tool wrappers ----------------------------------------------------------------------------

def test_every_mcp_tool_answers(monkeypatch):
    from safepay import mcp_server as m
    from safepay.agent_toolkit import GuardedToolkit
    policy = PolicyConfig(per_tx_limit=500, daily_limit=2000, hold_above=100)
    agent = SafePayAgent(config=m._config, policy=policy)
    monkeypatch.setattr(m, "_agent", agent)
    monkeypatch.setattr(m, "_gate", GuardedToolkit(config=m._config, policy=policy, ledger=agent.guardrail.ledger))
    assert m._MCP is not None, "the mcp package is installed for this suite"
    names = [t.name for t in asyncio.run(m._MCP.list_tools())]
    assert len(names) == 17 and "safepay_pay" in names

    def call(name, **args):
        res = asyncio.run(m._MCP.call_tool(name, args))
        data = res[1] if isinstance(res, tuple) else res
        return data if isinstance(data, dict) else {"raw": data}
    assert call("safepay_evaluate", recipient="v@x.example", amount=10)["verdict"] == "allow"
    paid = call("safepay_pay", recipient="v@x.example", amount=10)
    assert paid["executed"] is True
    assert "error" in call("safepay_pay_invoice", text="hello")
    order = call("safepay_create_order", recipient="v@x.example", amount=10)
    assert order.get("verdict") == "allow" or "not installed" in order.get("error", "")
    opened = call("safepay_open_mandate", recipient="c@x.example", amount=50, task_goal="compute")
    auth = opened["mandate"]["authorization_id"]
    assert call("safepay_capture", authorization_id=auth, amount=5)["executed"] is True
    assert call("safepay_close_mandate", authorization_id=auth)["mandate"]["closed"]
    call("safepay_pay", recipient="v@x.example", amount=150)
    apr = call("safepay_pending_approvals")["pending"][0]["id"]
    assert call("safepay_reject", approval_id=apr, note="no")["verdict"] == "block"
    assert "error" in call("safepay_approve", approval_id=apr)
    assert call("safepay_payout_status", batch_id=paid["execution"]["payout_batch_id"])["status"] == "SUCCESS"
    assert call("safepay_draft_policy", text="ask me over 100")["policy"]["hold_above"] == 100.0
    assert any(a["agent_id"] == "mcp-agent" for a in call("safepay_agents")["agents"])
    assert call("safepay_pause_agent", agent_id="mcp-agent", reason="x")["status"] == "paused"
    assert call("safepay_resume_agent", agent_id="mcp-agent")["status"] == "active"
    assert call("safepay_reconcile", hours=1)["outside"] == []
    assert call("safepay_audit_status")["ok"] is True
    monkeypatch.setattr(m, "toolkit_available", lambda: False)
    assert "not installed" in m.create_order(recipient="v@x.example", amount=1)["error"]
