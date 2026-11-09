"""The PayPal client against a scripted HTTP layer: every REST call SafePay
makes (OAuth, Payouts, Orders v2 authorize/capture/void, Vault v3, payout
lookup, Transaction Search, webhook verification) with the exact request
shape PayPal expects and every error path. No network, no keys."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
import requests

from safepay.config import Config
from safepay.paypal_client import PayPalClient, PayPalError

SANDBOX = "https://api-m.sandbox.paypal.com"


def _cfg(base=SANDBOX, client_id="cid", secret="sec"):
    return Config(nebius_api_key=None, nebius_base_url="", nebius_model="",
                  tavily_api_key=None, paypal_client_id=client_id,
                  paypal_secret=secret, paypal_base_url=base)


def _resp(status=200, body=None, text=None):
    raw = json.dumps(body) if body is not None else (text or "")

    def _json():
        if body is None:
            raise ValueError("not json")
        return body
    return SimpleNamespace(status_code=status, text=raw, content=raw.encode(), json=_json)


class Http:
    """Records every requests.request call and answers from a script."""

    def __init__(self, *responses):
        self.calls = []
        self.responses = list(responses)

    def __call__(self, method, url, headers=None, **kw):
        self.calls.append({"method": method, "url": url, "headers": headers or {}, **kw})
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def _live(monkeypatch, *responses, base=SANDBOX):
    c = PayPalClient(_cfg(base=base))
    monkeypatch.setattr(c, "_access_token", lambda: "tok")
    http = Http(*responses)
    monkeypatch.setattr(requests, "request", http)
    return c, http


# --- modes and OAuth ---------------------------------------------------------

def test_mode_follows_credentials():
    assert PayPalClient(_cfg(client_id=None, secret=None)).mode == "simulated"
    assert PayPalClient(_cfg()).mode == "sandbox"
    assert PayPalClient(_cfg()).live is True


def test_oauth_token_is_minted_once_and_reused(monkeypatch):
    c = PayPalClient(_cfg())
    posts = []

    def fake_post(url, auth=None, data=None, headers=None, timeout=None):
        posts.append((url, auth, data))
        return _resp(200, {"access_token": "T1", "expires_in": 3000})
    monkeypatch.setattr(requests, "post", fake_post)
    assert c._access_token() == "T1"
    assert c._access_token() == "T1"
    assert len(posts) == 1
    url, auth, data = posts[0]
    assert url == f"{SANDBOX}/v1/oauth2/token" and auth == ("cid", "sec")
    assert data == {"grant_type": "client_credentials"}


def test_oauth_failure_is_a_paypal_error(monkeypatch):
    c = PayPalClient(_cfg())
    monkeypatch.setattr(requests, "post", lambda *a, **k: _resp(401, text="bad client"))
    with pytest.raises(PayPalError, match="OAuth failed \\(401\\)"):
        c._access_token()


def test_expired_token_is_minted_again(monkeypatch):
    c = PayPalClient(_cfg())
    tokens = iter(["T1", "T2"])
    monkeypatch.setattr(requests, "post",
                        lambda *a, **k: _resp(200, {"access_token": next(tokens), "expires_in": 10}))
    assert c._access_token() == "T1"      # expires_in 10 s is inside the 60 s margin
    assert c._access_token() == "T2"


def test_connect_timeout_is_retried_then_raised(monkeypatch):
    c, http = _live(monkeypatch, requests.exceptions.ConnectTimeout(),
                    requests.exceptions.ConnectTimeout(), _resp(200, {}))
    assert c._send("get", "https://x.example/y").status_code == 200
    assert len(http.calls) == 3
    c, http = _live(monkeypatch, *([requests.exceptions.ConnectTimeout()] * 3))
    with pytest.raises(requests.exceptions.ConnectTimeout):
        c._send("get", "https://x.example/y")


# --- payouts -------------------------------------------------------------------

def test_simulated_payout_is_deterministic_with_a_key():
    c = PayPalClient(_cfg(client_id=None, secret=None))
    a = c.send_payout("v@x.example", 42, idempotency_key="abcdef0123456789xyz")
    b = c.send_payout("v@x.example", 42, idempotency_key="abcdef0123456789xyz")
    assert a == b and a["payout_batch_id"] == "SAFEPAY-abcdef012345"
    assert a["mode"] == "simulated" and a["batch_status"] == "SUCCESS"
    assert a["amount"] == {"value": "42.00", "currency": "USD"}
    fresh = c.send_payout("v@x.example", 42)
    assert fresh["payout_batch_id"] != c.send_payout("v@x.example", 42)["payout_batch_id"]


def test_live_payout_sends_the_payouts_payload_and_request_id(monkeypatch):
    c, http = _live(monkeypatch, _resp(201, {"batch_header": {
        "payout_batch_id": "B1", "batch_status": "PENDING"}}))
    out = c.send_payout("v@x.example", 12.5, note="hosting", idempotency_key="req-0001-abcd")
    call = http.calls[0]
    assert call["method"] == "POST" and call["url"] == f"{SANDBOX}/v1/payments/payouts"
    assert call["headers"]["PayPal-Request-Id"] == "req-0001-abcd"
    assert call["headers"]["Authorization"] == "Bearer tok"
    body = call["json"]
    assert body["sender_batch_header"]["sender_batch_id"] == "SAFEPAY-req-0001-abc"
    item = body["items"][0]
    assert item == {"recipient_type": "EMAIL", "amount": {"value": "12.50", "currency": "USD"},
                    "receiver": "v@x.example", "note": "hosting", "sender_item_id": "req-0001-abcd"}
    assert out["mode"] == "sandbox" and out["payout_batch_id"] == "B1"
    assert out["batch_status"] == "PENDING" and out["raw"]["payout_batch_id"] == "B1"


def test_live_payout_without_key_has_no_request_id(monkeypatch):
    c, http = _live(monkeypatch, _resp(201, {"batch_header": {}}))
    out = c.send_payout("v@x.example", 1)
    assert "PayPal-Request-Id" not in http.calls[0]["headers"]
    assert out["payout_batch_id"].startswith("SAFEPAY-") and out["batch_status"] == "PENDING"


def test_live_payout_error_carries_status_and_text(monkeypatch):
    c, _ = _live(monkeypatch, _resp(422, text="INSUFFICIENT_FUNDS"))
    with pytest.raises(PayPalError, match="payout failed \\(422\\): INSUFFICIENT_FUNDS"):
        c.send_payout("v@x.example", 1)


# --- _post / get_json ------------------------------------------------------------

def test_post_reports_paypals_issue_name_or_text(monkeypatch):
    c, _ = _live(monkeypatch, _resp(422, {"details": [{"issue": "INSTRUMENT_DECLINED"}]}))
    with pytest.raises(PayPalError, match="orders failed \\(422\\): INSTRUMENT_DECLINED"):
        c._post("/v2/checkout/orders", {}, None)
    c, _ = _live(monkeypatch, _resp(400, {"name": "INVALID_REQUEST"}))
    with pytest.raises(PayPalError, match="INVALID_REQUEST"):
        c._post("/v2/checkout/orders", {}, None)
    c, _ = _live(monkeypatch, _resp(500, text="<html>gateway</html>"))
    with pytest.raises(PayPalError, match="gateway"):
        c._post("/v2/checkout/orders", {}, None)


def test_post_sends_request_id_and_handles_empty_204(monkeypatch):
    c, http = _live(monkeypatch, _resp(204))
    assert c._post("/v2/payments/authorizations/A1/void", None, "k" * 150) == {}
    assert len(http.calls[0]["headers"]["PayPal-Request-Id"]) == 100
    c, http = _live(monkeypatch, _resp(201, {"id": "X"}))
    c._post("/v2/checkout/orders", {"a": 1}, None)
    assert http.calls[0]["json"] == {"a": 1} and http.calls[0]["headers"]["PayPal-Request-Id"]


def test_get_json(monkeypatch):
    c, http = _live(monkeypatch, _resp(200, {"id": "A1"}))
    assert c.get_json("/v2/payments/authorizations/A1") == {"id": "A1"}
    assert http.calls[0]["method"] == "GET"
    c, _ = _live(monkeypatch, _resp(404))
    with pytest.raises(PayPalError, match="GET failed \\(404\\)"):
        c.get_json("/v2/payments/authorizations/A1")


# --- vault, authorize, capture, void ----------------------------------------------

def test_vault_card_simulated_and_live(monkeypatch):
    sim = PayPalClient(_cfg(client_id=None, secret=None)).vault_card()
    assert sim["mode"] == "simulated" and sim["vault_id"].startswith("SIMVAULT")
    assert sim["last_digits"] == "1111"
    c, http = _live(monkeypatch, _resp(201, {"id": "SETUP1"}),
                    _resp(201, {"id": "VAULT1", "payment_source": {"card": {"brand": "VISA", "last_digits": "1111"}}}))
    out = c.vault_card()
    assert out == {"mode": "sandbox", "vault_id": "VAULT1", "brand": "VISA", "last_digits": "1111"}
    assert http.calls[0]["url"].endswith("/v3/vault/setup-tokens")
    assert http.calls[0]["json"]["payment_source"]["card"]["number"] == "4111111111111111"
    assert http.calls[1]["json"] == {"payment_source": {"token": {"id": "SETUP1", "type": "SETUP_TOKEN"}}}


def test_vault_card_refuses_a_live_host_and_an_empty_token(monkeypatch):
    c, _ = _live(monkeypatch, base="https://api-m.paypal.com")
    with pytest.raises(PayPalError, match="sandbox test card"):
        c.vault_card()
    c, _ = _live(monkeypatch, _resp(201, {"id": "SETUP1"}), _resp(201, {}))
    with pytest.raises(PayPalError, match="no payment token"):
        c.vault_card()


def test_authorize_simulated_has_a_29_day_expiry():
    out = PayPalClient(_cfg(client_id=None, secret=None)).authorize(80, description="hotel")
    assert out["mode"] == "simulated" and out["status"] == "CREATED"
    assert out["order_id"].startswith("SIM-ORDER-") and out["authorization_id"].startswith("SIMAUTH")
    assert out["amount"] == {"value": "80.00", "currency": "USD"} and out["expires"].endswith("Z")


def test_authorize_live_with_card_and_with_vault(monkeypatch):
    order = {"id": "O1", "status": "COMPLETED", "purchase_units": [{"payments": {"authorizations": [
        {"id": "AUTH1", "status": "CREATED", "amount": {"currency_code": "USD", "value": "80.00"},
         "expiration_time": "2026-12-08T00:00:00Z"}]}}]}
    c, http = _live(monkeypatch, _resp(201, order))
    out = c.authorize(80, description="x" * 200, idempotency_key="m-1")
    body = http.calls[0]["json"]
    assert body["intent"] == "AUTHORIZE" and body["purchase_units"][0]["amount"]["value"] == "80.00"
    assert len(body["purchase_units"][0]["description"]) == 127
    assert body["payment_source"]["card"]["number"] == "4111111111111111"
    assert http.calls[0]["headers"]["PayPal-Request-Id"] == "m-1"
    assert out == {"mode": "sandbox", "order_id": "O1", "authorization_id": "AUTH1",
                   "status": "CREATED", "amount": {"currency_code": "USD", "value": "80.00"},
                   "expires": "2026-12-08T00:00:00Z", "payment_source": "card"}
    c, http = _live(monkeypatch, _resp(201, order))
    assert c.authorize(80, vault_id="VAULT1")["payment_source"] == "vault"
    assert http.calls[0]["json"]["payment_source"] == {"card": {"vault_id": "VAULT1"}}


def test_authorize_errors(monkeypatch):
    c, _ = _live(monkeypatch, base="https://api-m.paypal.com")
    with pytest.raises(PayPalError, match="production needs a vaulted"):
        c.authorize(5)
    c, _ = _live(monkeypatch, _resp(201, {"id": "O1", "status": "PAYER_ACTION_REQUIRED"}))
    with pytest.raises(PayPalError, match="no authorization \\(order PAYER_ACTION_REQUIRED\\)"):
        c.authorize(5)


def test_capture_and_void(monkeypatch):
    sim = PayPalClient(_cfg(client_id=None, secret=None))
    cap = sim.capture_authorization("A", 10, final=True)
    assert cap["mode"] == "simulated" and cap["status"] == "COMPLETED" and cap["final"] is True
    assert sim.void_authorization("A") == {"mode": "simulated", "status": "VOIDED"}

    c, http = _live(monkeypatch, _resp(201, {"id": "CAP1", "status": "COMPLETED"}))
    out = c.capture_authorization("AUTH1", 12.345, note="n" * 300, invoice_id="i" * 200,
                                  final=False, idempotency_key="cap-1")
    body = http.calls[0]["json"]
    assert http.calls[0]["url"].endswith("/v2/payments/authorizations/AUTH1/capture")
    assert body["amount"] == {"currency_code": "USD", "value": "12.35"} and body["final_capture"] is False
    assert len(body["note_to_payer"]) == 255 and len(body["invoice_id"]) == 127
    assert out == {"mode": "sandbox", "capture_id": "CAP1", "status": "COMPLETED",
                   "amount": {"value": "12.35", "currency": "USD"}, "final": False}
    c, http = _live(monkeypatch, _resp(201, {"id": "CAP2", "status": "COMPLETED"}))
    assert "note_to_payer" not in c.capture_authorization("AUTH1", 1) and "invoice_id" not in http.calls[0]["json"]
    c, http = _live(monkeypatch, _resp(204))
    assert c.void_authorization("AUTH1") == {"mode": "sandbox", "status": "VOIDED"}
    assert http.calls[0]["url"].endswith("/v2/payments/authorizations/AUTH1/void")


# --- settlement --------------------------------------------------------------------

def test_get_payout_simulated_is_final_success():
    out = PayPalClient(_cfg(client_id=None, secret=None)).get_payout("B9")
    assert out["final"] is True and out["item_status"] == "SUCCESS" and out["payout_batch_id"] == "B9"


@pytest.mark.parametrize("status,final", [("SUCCESS", True), ("PENDING", False),
                                          ("UNCLAIMED", False), ("RETURNED", True), ("FAILED", True)])
def test_get_payout_live_normalizes_the_item(monkeypatch, status, final):
    body = {"batch_header": {"payout_batch_id": "B1", "batch_status": "PROCESSING",
                             "time_completed": "2026-11-09T01:00:00Z"},
            "items": [{"transaction_status": status, "transaction_id": "T1",
                       "errors": {"name": "RECEIVER_UNREGISTERED"} if status == "FAILED" else None,
                       "payout_item": {"receiver": "v@x.example", "note": "hi",
                                       "amount": {"value": "12.50", "currency": "USD"}}}]}
    c, http = _live(monkeypatch, _resp(200, body))
    out = c.get_payout("B1")
    assert http.calls[0]["url"].endswith("/v1/payments/payouts/B1")
    assert out["item_status"] == status and out["final"] is final
    assert out["amount"] == 12.5 and out["currency"] == "USD" and out["receiver"] == "v@x.example"
    assert out["transaction_id"] == "T1" and out["time_processed"] == "2026-11-09T01:00:00Z"
    assert (out["errors"] is not None) == (status == "FAILED")


def test_get_payout_without_items_falls_back_to_the_batch(monkeypatch):
    c, _ = _live(monkeypatch, _resp(200, {"batch_header": {"batch_status": "PENDING"}}))
    out = c.get_payout("B1")
    assert out["item_status"] == "PENDING" and out["final"] is False and out["amount"] is None
    c, _ = _live(monkeypatch, _resp(404, text="nope"))
    with pytest.raises(PayPalError, match="lookup failed \\(404\\)"):
        c.get_payout("B1")


def test_list_transactions(monkeypatch):
    assert PayPalClient(_cfg(client_id=None, secret=None)).list_transactions("a", "b") == []
    body = {"transaction_details": [
        {"transaction_info": {"transaction_id": "T1", "transaction_event_code": "T0001",
                              "transaction_status": "S", "transaction_amount": {"value": "-5.00", "currency_code": "USD"},
                              "transaction_initiation_date": "2026-11-09T00:00:00Z", "transaction_subject": "hosting"},
         "payer_info": {"email_address": "v@x.example"}},
        {"transaction_info": {"transaction_id": "T2", "transaction_amount": {"value": None}}},
        {"transaction_info": {"transaction_id": "T3", "transaction_amount": {"value": "x"}}},
    ]}
    c, http = _live(monkeypatch, _resp(200, body))
    out = c.list_transactions("2026-11-08T00:00:00Z", "2026-11-09T00:00:00Z")
    assert http.calls[0]["params"]["start_date"] == "2026-11-08T00:00:00Z"
    assert out == [{"transaction_id": "T1", "event_code": "T0001", "status": "S", "amount": -5.0,
                    "currency": "USD", "time": "2026-11-09T00:00:00Z", "counterparty": "v@x.example",
                    "subject": "hosting"}]
    c, _ = _live(monkeypatch, _resp(403, text="NOT_AUTHORIZED"))
    with pytest.raises(PayPalError, match="Transaction Search is not enabled"):
        c.list_transactions("a", "b")
    c, _ = _live(monkeypatch, _resp(500, text="boom"))
    with pytest.raises(PayPalError, match="transaction search failed \\(500\\)"):
        c.list_transactions("a", "b")


# --- webhooks ------------------------------------------------------------------------

HEADERS = {"paypal-transmission-id": "tid", "Paypal-Transmission-Time": "t",
           "PAYPAL-CERT-URL": "https://api.sandbox.paypal.com/cert", "PAYPAL-AUTH-ALGO": "SHA256withRSA",
           "PAYPAL-TRANSMISSION-SIG": "sig"}


def test_verify_webhook(monkeypatch, capsys):
    assert PayPalClient(_cfg(client_id=None, secret=None)).verify_webhook(HEADERS, {}, "W") is False
    c, http = _live(monkeypatch, _resp(200, {"verification_status": "SUCCESS"}))
    assert c.verify_webhook(HEADERS, {"event_type": "PAYMENT.PAYOUTS-ITEM.SUCCEEDED"}, "WH1") is True
    sent = http.calls[0]["json"]
    assert sent["transmission_id"] == "tid" and sent["webhook_id"] == "WH1"
    assert sent["webhook_event"] == {"event_type": "PAYMENT.PAYOUTS-ITEM.SUCCEEDED"}
    assert http.calls[0]["url"].endswith("/v1/notifications/verify-webhook-signature")
    # A delivery missing a signature header is never sent to PayPal.
    c, http = _live(monkeypatch)
    assert c.verify_webhook({k: v for k, v in HEADERS.items() if "SIG" not in k}, {}, "WH1") is False
    assert http.calls == []
    # A FAILURE answer is False and explained on stderr, without secrets.
    c, _ = _live(monkeypatch, _resp(200, {"verification_status": "FAILURE"}))
    assert c.verify_webhook(HEADERS, {"event_type": "X"}, "WH1") is False
    err = capsys.readouterr().err
    assert "webhook verification failed for X (tid): HTTP 200" in err and "tok" not in err
