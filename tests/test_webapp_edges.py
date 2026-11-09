"""The console's edges: the rate limiter, every endpoint's refusal paths
(429, 400, 401, 403, 404, 409, 413, 502, 503), store outages swallowed where
they must be, and the PayPal webhook's branches, all offline."""

from __future__ import annotations

import json
import os
from collections import defaultdict, deque
from pathlib import Path

for _k in ("NEBIUS_API_KEY", "TAVILY_API_KEY", "PAYPAL_CLIENT_ID", "PAYPAL_SECRET",
           "SAFEPAY_SIGNING_KEY", "SAFEPAY_SEAL_ED25519_SEED", "SAFEPAY_OPERATOR_TOKEN"):
    os.environ[_k] = ""

import pytest  # noqa: E402
import requests  # noqa: E402

import webapp.app as appmod  # noqa: E402
from webapp.app import app, _reset_agent  # noqa: E402


@pytest.fixture
def client(monkeypatch):
    _reset_agent()
    monkeypatch.setattr(appmod, "_RL_MAX", 0)
    monkeypatch.setenv("SAFEPAY_OPERATOR_TOKEN", "")
    return app.test_client()


@pytest.fixture
def limited(monkeypatch):
    monkeypatch.setattr(appmod, "_rate_limited", lambda: True)


def _pay(client, amount=48, agent_id="a1"):
    return client.post("/api/evaluate", json={
        "recipient": "billing@notion.so", "amount": amount, "category": "software",
        "merchant_name": "Notion", "task_goal": "pay invoices", "agent_id": agent_id}).get_json()


BAD_KEY = {"Authorization": "Bearer spk_nope"}


# --- plumbing -------------------------------------------------------------------------

def test_dotenv_loader_skips_comments_and_keeps_existing_values(tmp_path, monkeypatch):
    appmod._load_dotenv(tmp_path / "missing")            # no file: nothing happens
    f = tmp_path / ".env"
    f.write_text("# comment\n\nSAFEPAY_T_X = one \nnot-a-pair\nSAFEPAY_T_Y=two\n", "utf-8")
    monkeypatch.setenv("SAFEPAY_T_Y", "keep")
    monkeypatch.delenv("SAFEPAY_T_X", raising=False)
    try:
        appmod._load_dotenv(f)
        assert os.environ["SAFEPAY_T_X"] == "one" and os.environ["SAFEPAY_T_Y"] == "keep"
    finally:
        os.environ.pop("SAFEPAY_T_X", None)


def test_rate_limit_counts_per_ip_and_expires(monkeypatch):
    _reset_agent()
    monkeypatch.setattr(appmod, "_RL_MAX", 2)
    hits = defaultdict(deque)
    monkeypatch.setattr(appmod, "_rl_hits", hits)
    c = app.test_client()
    body = {"text": "ask me over 100"}
    h = {"X-Forwarded-For": "9.9.9.9, 10.0.0.1"}
    assert c.post("/api/policy/draft", json=body, headers=h).status_code == 200
    assert c.post("/api/policy/draft", json=body, headers=h).status_code == 200
    assert c.post("/api/policy/draft", json=body, headers=h).status_code == 429
    assert c.post("/api/policy/draft", json=body, headers={"X-Forwarded-For": "8.8.8.8"}).status_code == 200
    hits["9.9.9.9"] = deque([t - 1000 for t in hits["9.9.9.9"]])   # the window passed
    assert c.post("/api/policy/draft", json=body, headers=h).status_code == 200


def test_key_helpers_never_raise_on_a_store_outage(client, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("store down")
    with app.test_request_context(headers={"Authorization": "Bearer spk_x"}):
        monkeypatch.setattr(appmod._agent.control, "agent_for_key", boom)
        assert appmod._holds_key("a") is False
        assert appmod._connector_owned("") is False
        monkeypatch.setattr(appmod._agent.control, "has_key", boom)
        assert appmod._connector_owned("r1") is False
    with app.test_request_context():
        assert appmod._holds_key("a") is False                 # no key at all


def test_connector_guard_trusts_a_pinned_policy(client):
    appmod._agent.issue_key("connector:r2")
    with app.test_request_context():
        assert appmod._connector_guard("r2") is not None       # owned, unpinned, no key: refused
        appmod._agent.control.set_setting("opkey:r2", {"kid": "K1"})
        assert appmod._connector_guard("r2") is None           # pinned: the signature protects it
        resp, status = appmod._connector_guard("r2", pinned_ok=False)
        assert status == 403 and "present its key" in resp.get_json()["error"]


# --- purchase checks (Impulsa) -----------------------------------------------------------

def test_purchase_check_refusals(client, limited, monkeypatch):
    assert client.post("/api/check/purchase", json={}).status_code == 429
    monkeypatch.setattr(appmod, "_rate_limited", lambda: False)
    assert client.post("/api/check/purchase", json={}).status_code == 401
    assert client.post("/api/check/purchase", json={}, headers=BAD_KEY).status_code == 401
    key = appmod._agent.issue_key("impulsa")["key"]
    h = {"Authorization": f"Bearer {key}"}
    r = client.post("/api/check/purchase", json={"business": "emp-1"}, headers=h)
    assert r.status_code == 400 and "item" in r.get_json()["error"]
    from safepay import impact
    monkeypatch.setattr(impact, "record_check", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    body = {"business": "emp-1", "vendor": "Distribuidora El Sol", "invoice_ref": "F-1300", "currency": "CLP",
            "items": [{"name": "harina", "quantity": 1, "unit_price": 1000}]}
    r = client.post("/api/check/purchase", json=body, headers=h)
    assert r.status_code == 200 and r.get_json()["verdict"] in ("allow", "hold", "block")


def test_purchase_outcome_refusals(client, limited, monkeypatch):
    assert client.post("/api/check/purchase/outcome", json={}).status_code == 429
    monkeypatch.setattr(appmod, "_rate_limited", lambda: False)
    assert client.post("/api/check/purchase/outcome", json={}, headers=BAD_KEY).status_code == 401
    assert client.get("/api/check/purchase/summary", headers=BAD_KEY).status_code == 401
    key = appmod._agent.issue_key("impulsa")["key"]
    h = {"Authorization": f"Bearer {key}"}
    r = client.post("/api/check/purchase/outcome", json={"outcome": "nope"}, headers=h)
    assert r.status_code == 400 and "outcome must be" in r.get_json()["error"]
    r = client.post("/api/check/purchase/outcome", json={"outcome": "corrected", "seq": "x"}, headers=h)
    assert r.status_code == 400 and "numbers" in r.get_json()["error"]
    r = client.post("/api/check/purchase/outcome", json={"outcome": "corrected", "seq": 7, "amount": 1}, headers=h)
    assert r.status_code == 404
    # The same lookup on a store-backed chain asks the table by seq.
    from tests.test_store_supabase import FakeDB

    class StoreLog:
        db = FakeDB()
        records = []
    monkeypatch.setattr(appmod._agent, "audit_log", StoreLog())
    assert appmod._link_by_seq(3) is None
    StoreLog.db.insert("audit", {"seq": 3, "record": {"payload": {"seq": 3}}})
    assert appmod._link_by_seq(3) == {"payload": {"seq": 3}}


def test_impact_and_agentpos_report_outages(client, monkeypatch):
    from safepay import agentpos, impact
    monkeypatch.setattr(impact, "totals", lambda *a: (_ for _ in ()).throw(RuntimeError("table missing")))
    r = client.get("/api/impact")
    assert r.status_code == 503 and "table missing" in r.get_json()["error"]
    monkeypatch.setattr(agentpos, "catalog", lambda: {"items": [{"sku": "s1"}]})
    assert client.get("/api/agentpos/catalog").get_json() == {"items": [{"sku": "s1"}]}
    monkeypatch.setattr(agentpos, "catalog", lambda: (_ for _ in ()).throw(OSError("timeout")))
    assert client.get("/api/agentpos/catalog").status_code == 502
    monkeypatch.setattr(agentpos, "quote", lambda items: (_ for _ in ()).throw(agentpos.StoreError("empty cart")))
    r = client.post("/api/agentpos/quote", json={"items": []})
    assert r.status_code == 400 and r.get_json()["error"] == "empty cart"
    monkeypatch.setattr(agentpos, "quote", lambda items: (_ for _ in ()).throw(OSError("down")))
    assert client.post("/api/agentpos/quote", json={"items": [1]}).status_code == 502
    monkeypatch.setattr(appmod, "_rate_limited", lambda: True)
    assert client.post("/api/agentpos/quote", json={}).status_code == 429


# --- the Studio assistant proxy -------------------------------------------------------------

def test_studio_proxy_edges(client, monkeypatch, tmp_path):
    monkeypatch.setattr(appmod, "_rate_limited", lambda: True)
    assert client.post("/api/studio/responses", json={"input": []}).status_code == 429
    monkeypatch.setattr(appmod, "_rate_limited", lambda: False)
    monkeypatch.setenv("NEBIUS_API_KEY", "k")
    monkeypatch.setenv("SAFEPAY_STUDIO_DAILY_TURNS", "5")
    monkeypatch.setattr(appmod._agent.cache, "get", lambda k: (_ for _ in ()).throw(RuntimeError("cache")))
    big = b'{"input": "' + b"x" * 600_001 + b'"}'
    assert client.post("/api/studio/responses", data=big, content_type="application/json").status_code == 413
    monkeypatch.setattr(appmod._agent.cache, "set", lambda k, v: (_ for _ in ()).throw(RuntimeError("cache")))
    dbg = tmp_path / "studio.log"
    monkeypatch.setenv("SAFEPAY_STUDIO_DEBUG", str(dbg))
    monkeypatch.setattr(requests, "post", lambda *a, **k: (_ for _ in ()).throw(OSError("dns")))
    r = client.post("/api/studio/responses", json={"input": [{"type": "message", "role": "user", "content": "q"}]})
    assert r.status_code == 502 and "model unreachable: dns" in r.get_json()["error"]["message"]
    assert json.loads(dbg.read_text("utf-8").splitlines()[0])["input"][0]["role"] == "user"

    class Up:
        status_code = 503
    monkeypatch.setattr(requests, "post", lambda *a, **k: Up())
    r = client.post("/api/studio/responses", json={"input": []})
    assert r.status_code == 502 and "model answered 503" in r.get_json()["error"]["message"]


# --- proof, security, seals ------------------------------------------------------------------

def test_proof_pages_and_checks(client, monkeypatch):
    assert client.get("/proof").status_code == 200
    _pay(client)
    j = client.get("/api/proof").get_json()
    assert j["chain_ok"] is True and j["links"] == 1 and j["items"] == []    # simulated ids never count
    assert set(j["counts"]) == {"payout", "order", "authorization", "capture"}
    r = client.post("/api/proof/check", json={"kind": "nope", "id": "x"})
    assert r.status_code == 400
    from safepay import proof
    monkeypatch.setattr(proof, "check", lambda *a: (_ for _ in ()).throw(OSError("timeout")))
    assert client.post("/api/proof/check", json={"kind": "payout", "id": "4B4NJKRHZCU5W"}).status_code == 502
    monkeypatch.setattr(appmod, "_rate_limited", lambda: True)
    assert client.post("/api/proof/check", json={}).status_code == 429


def test_security_page_without_benchmark_results(client, monkeypatch):
    real = Path.read_text

    def read_text(self, *a, **k):
        if self.name == "results.json":
            raise OSError("missing")
        return real(self, *a, **k)
    monkeypatch.setattr(Path, "read_text", read_text)
    assert client.get("/security").status_code == 200
    assert appmod._bench_published() == {}
    assert client.get("/api/benchmark/cases").get_json()["published"] == {}


def test_seal_lookups_report_store_trouble_and_bad_amounts(client, monkeypatch):
    out = _pay(client)
    sid = out["decision"]["execution"]["seal"]
    assert client.get(f"/api/v/{sid}/receipt").status_code == 503      # no signing key configured
    r = client.post("/api/merchant/verify", json={"seal": sid, "amount": "lots"})
    assert r.status_code == 400 and "number" in r.get_json()["error"]
    from safepay import receipt
    monkeypatch.setattr(receipt, "issue", lambda *a: (_ for _ in ()).throw(RuntimeError("no key")))
    assert client.post("/api/merchant/verify", json={"seal": sid, "amount": 48}).get_json()["receipt"] is None
    assert client.post("/api/merchant/verify", json={"seal": "nope"}).status_code == 400
    monkeypatch.setattr(appmod.sealmod, "find", lambda *a: (_ for _ in ()).throw(RuntimeError("db")))
    r = client.get(f"/api/v/{sid}")
    assert r.status_code == 404 and "could not be read" in r.get_json()["error"]
    monkeypatch.setattr(appmod, "_rate_limited", lambda: True)
    assert client.post("/api/merchant/verify", json={"seal": sid}).status_code == 429


def test_static_proofs_and_scenarios(client):
    r = client.get("/.well-known/mcp-registry-auth")
    assert r.status_code == 200 and r.content_type.startswith("text/plain")
    s = client.get("/api/scenarios").get_json()
    assert s["policy"]["per_tx_limit"] == 500 and len(s["intents"]) >= 5
    r = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
    assert r.status_code == 200


# --- every endpoint says 429 when the limiter trips ----------------------------------------------

@pytest.mark.parametrize("method,path,body", [
    ("post", "/api/policy/draft", {"text": "x"}), ("post", "/api/policy/learn", {}),
    ("post", "/api/policy/apply", {"run": "r1"}), ("post", "/api/evaluate", {}),
    ("post", "/api/toolkit/order", {}), ("post", "/api/payables/read", {"text": "x"}),
    ("post", "/api/payables/pay", {"text": "x"}), ("post", "/api/mandates", {}),
    ("post", "/api/mandates/AUTH-1234/capture", {}), ("post", "/api/mandates/AUTH-1234/close", {}),
    ("post", "/api/payouts/B1/settle", {}), ("post", "/api/reconcile", {}),
    ("post", "/api/early-access", {}), ("post", "/api/agents/a1/key", {}),
    ("post", "/api/agents/a1/pause", {}), ("post", "/api/approvals/apr-1/approve", {}),
    ("post", "/api/benchmark/step", {"mode": "strict"}), ("post", "/mcp", {"jsonrpc": "2.0", "id": 1, "method": "ping"}),
])
def test_rate_limited_endpoints(client, limited, method, path, body):
    r = getattr(client, method)(path, json=body)
    assert r.status_code == 429, path


# --- policy endpoints ----------------------------------------------------------------------------

def test_policy_endpoint_errors(client, monkeypatch):
    from safepay.opsig import SignatureError
    monkeypatch.setattr(appmod._agent, "apply_policy", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db")))
    r = client.post("/api/policy/apply", json={"run": "r1", "policy": {"per_tx_limit": 10}})
    assert r.status_code == 503 and "could not save" in r.get_json()["error"]
    monkeypatch.setattr(appmod._agent, "policy_status", lambda run: (_ for _ in ()).throw(RuntimeError("db")))
    assert client.get("/api/policy?run=r1").status_code == 200
    assert client.post("/api/policy/message", json={}).status_code == 400
    r = client.post("/api/policy/message", json={"run": "r1", "policy": {"per_tx_limit": -5}})
    assert r.status_code == 400 and "above zero" in r.get_json()["error"]
    assert client.post("/api/policy/tamper", json={"run": "r1"}).status_code == 400   # nothing stored
    monkeypatch.setenv("SAFEPAY_TAMPER_DEMO", "off")
    assert client.post("/api/policy/tamper", json={"run": "r1"}).status_code == 404
    assert client.post("/api/audit/tamper").status_code == 403
    monkeypatch.setattr(appmod._agent, "clear_policy", lambda *a, **k: (_ for _ in ()).throw(SignatureError("bad sig")))
    assert client.post("/api/policy/reset", json={"run": "r1"}).status_code == 400
    monkeypatch.setattr(appmod._agent, "clear_policy", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db")))
    assert client.post("/api/policy/reset", json={"run": "r1"}).status_code == 503


def test_agent_endpoints_report_store_errors(client, monkeypatch):
    boom = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db"))
    monkeypatch.setattr(appmod._agent, "agents", boom)
    assert client.get("/api/agents").status_code == 503
    monkeypatch.setattr(appmod._agent, "pause_agent", boom)
    assert client.post("/api/agents/a1/pause", json={}).status_code == 503
    monkeypatch.setattr(appmod._agent.control, "has_key", boom)
    assert client.post("/api/agents/connector:zz/key", json={}).status_code == 200   # unknown: not taken
    monkeypatch.setattr(appmod._agent, "issue_key", boom)
    assert client.post("/api/agents/a1/key", json={}).status_code == 503
    monkeypatch.setattr(appmod._agent, "revoke_key", boom)
    assert client.delete("/api/agents/a1/key").status_code == 503


# --- payments, mandates, settlement ----------------------------------------------------------------

def test_bad_agent_keys_are_refused_everywhere(client):
    assert client.post("/api/evaluate", json={"recipient": "v@x.example", "amount": 1}, headers=BAD_KEY).status_code == 401
    assert client.post("/api/benchmark/step", json={"mode": "rules", "case_id": "x"}, headers=BAD_KEY).status_code == 404
    case = appmod._BENCH["cases"][0]["id"]
    assert client.post("/api/benchmark/step", json={"mode": "rules", "case_id": case}, headers=BAD_KEY).status_code == 401
    assert client.post("/api/mandates", json={"recipient": "v@x.example", "amount": 1}, headers=BAD_KEY).status_code == 401
    assert client.post("/api/mandates/AUTH-1234/capture", json={"amount": 1}, headers=BAD_KEY).status_code == 401
    assert client.post("/api/mandates/AUTH-1234/close", json={}, headers=BAD_KEY).status_code == 401
    assert client.post("/api/payables/pay", json={"text": "x"}, headers=BAD_KEY).status_code == 401
    assert client.post("/api/toolkit/order", json={"recipient": "v@x.example", "amount": 1}, headers=BAD_KEY).status_code == 401


def test_mandate_endpoint_validation(client):
    body = {"recipient": "v@x.example", "amount": 10, "category": "software"}
    assert client.post("/api/mandates", json=dict(body, cart="nope")).status_code == 400
    assert client.get("/api/mandates/bad id/ap2").status_code == 400
    assert client.get("/api/mandates/AUTH-1234/ap2").status_code == 404
    assert client.post("/api/mandates/bad id/capture", json={}).status_code == 400
    assert client.post("/api/mandates/AUTH-1234/capture", json={"amount": "x"}).status_code == 400
    assert client.post("/api/mandates/AUTH-1234/capture", json={"amount": 1}).status_code == 404
    assert client.post("/api/mandates/bad id/close", json={}).status_code == 400
    assert client.post("/api/mandates/AUTH-1234/close", json={}).status_code == 404


def test_settle_endpoint_errors(client, monkeypatch):
    assert client.post("/api/payouts/NOPE/settle").status_code == 404
    monkeypatch.setattr(appmod._agent, "settle", lambda *a, **k: (_ for _ in ()).throw(OSError("paypal down")))
    r = client.post("/api/payouts/B1/settle")
    assert r.status_code == 502 and "paypal down" in r.get_json()["error"]
    r = client.post("/api/reconcile", json={"hours": "abc"})
    assert r.status_code == 200 and r.get_json()["window_hours"] == 24


def test_paypal_webhook_branches(client, monkeypatch):
    monkeypatch.setenv("PAYPAL_WEBHOOK_ID", "")
    assert client.post("/api/paypal/webhook", json={}).status_code == 503
    monkeypatch.setenv("PAYPAL_WEBHOOK_ID", "WH1")
    assert client.post("/api/paypal/webhook", json={}).status_code == 403     # simulated: never verified
    monkeypatch.setattr(appmod._agent.paypal, "verify_webhook", lambda h, e, w: True)
    monkeypatch.setattr(appmod._agent, "settle_payment_event",
                        lambda et, res, source: {"duplicate": False, "seq": 4, "status": "COMPLETED"})
    r = client.post("/api/paypal/webhook", json={"event_type": "PAYMENT.CAPTURE.COMPLETED", "resource": {"id": "C1"}})
    assert r.get_json() == {"duplicate": False, "seq": 4, "status": "COMPLETED", "ok": True}
    monkeypatch.setattr(appmod._agent, "settle_payment_event",
                        lambda *a, **k: (_ for _ in ()).throw(ValueError("event without a resource id")))
    r = client.post("/api/paypal/webhook", json={"event_type": "PAYMENT.AUTHORIZATION.VOIDED", "resource": {}})
    assert r.get_json()["ignored"] == "event without a resource id"
    monkeypatch.setattr(appmod._agent, "settle_payment_event",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("paypal down")))
    assert client.post("/api/paypal/webhook", json={"event_type": "PAYMENT.CAPTURE.DENIED", "resource": {"id": "C1"}}).status_code == 502
    r = client.post("/api/paypal/webhook", json={"event_type": "BILLING.PLAN.CREATED", "resource": {}})
    assert r.get_json() == {"ok": True, "ignored": "BILLING.PLAN.CREATED"}
    # A payout SafePay never released is flagged as outside the firewall...
    monkeypatch.setattr(appmod._agent, "flag_outside", lambda b, source: {"seq": 9, "duplicate": False})
    r = client.post("/api/paypal/webhook", json={"event_type": "PAYMENT.PAYOUTS-ITEM.SUCCEEDED",
                                                 "resource": {"payout_batch_id": "B-UNKNOWN"}})
    assert r.get_json() == {"ok": True, "outside": True, "seq": 9, "duplicate": False}
    # ...unless PayPal cannot be asked about it.
    monkeypatch.setattr(appmod._agent, "flag_outside", lambda b, source: (_ for _ in ()).throw(OSError("down")))
    assert client.post("/api/paypal/webhook", json={"resource": {"batch_header": {"payout_batch_id": "B-X"}}}).status_code == 502
    monkeypatch.setattr(appmod._agent, "settle", lambda b, source: (_ for _ in ()).throw(OSError("down")))
    assert client.post("/api/paypal/webhook", json={"resource": {"payout_batch_id": "B-X"}}).status_code == 502
    # A released payout settles normally.
    monkeypatch.undo()
    monkeypatch.setenv("PAYPAL_WEBHOOK_ID", "WH1")
    monkeypatch.setattr(appmod, "_RL_MAX", 0)
    monkeypatch.setattr(appmod._agent.paypal, "verify_webhook", lambda h, e, w: True)
    batch = _pay(client)["decision"]["execution"]["payout_batch_id"]
    r = client.post("/api/paypal/webhook", json={"resource": {"payout_batch_id": batch}})
    assert r.get_json() == {"ok": True, "status": "SUCCESS", "duplicate": False}


def test_audit_tamper_edges(client):
    assert client.post("/api/audit/tamper").status_code == 400        # nothing to tamper with
    _pay(client)
    appmod._agent.audit_log.records.append({"payload": {"seq": 1}})   # a link with no intent
    r = client.post("/api/audit/tamper")
    assert r.status_code == 500 and r.get_json()["error"] == "could not tamper"
