"""PayPal sandbox client (Payouts API), with a simulated mode when no keys.

SafePay models an agent that autonomously *sends* money to a recipient, which
maps to PayPal's Payouts API: no buyer-approval redirect, so the whole flow is
backend and demoable end to end. The host is pinned to the sandbox in config
and we never default to the live host, so a misconfigured run cannot move real
money. Without credentials the client returns a deterministic simulated batch,
so the guardrail and demo run offline and switch to the real sandbox the moment
PAYPAL_CLIENT_ID / PAYPAL_SECRET are set.
"""

from __future__ import annotations

import sys
import time
import uuid
from typing import Any

from .config import Config


class PayPalError(RuntimeError):
    pass


class PayPalClient:
    def __init__(self, config: Config) -> None:
        self._cfg = config
        self._token: str | None = None
        self._token_exp: float = 0.0

    @property
    def live(self) -> bool:
        return self._cfg.paypal_live

    @property
    def mode(self) -> str:
        return "sandbox" if self.live else "simulated"

    # --- auth -------------------------------------------------------------
    def _access_token(self) -> str:
        if self._token and time.time() < self._token_exp - 60:
            return self._token
        import requests

        resp = requests.post(
            f"{self._cfg.paypal_base_url}/v1/oauth2/token",
            auth=(self._cfg.paypal_client_id, self._cfg.paypal_secret),
            data={"grant_type": "client_credentials"},
            headers={"Accept": "application/json"},
            timeout=30,
        )
        if resp.status_code != 200:
            raise PayPalError(
                f"PayPal OAuth failed ({resp.status_code}): {resp.text[:300]}"
            )
        data = resp.json()
        self._token = data["access_token"]
        self._token_exp = time.time() + float(data.get("expires_in", 3000))
        return self._token

    def _send(self, method: str, url: str, headers: dict[str, str] | None = None, **kw: Any) -> Any:
        """One authenticated call. PayPal reuses an app token until it expires,
        so a token revoked or minted before a scope change is answered 401:
        drop it, mint a new one, and try once more."""
        import requests

        for attempt in (1, 2):
            h = dict(headers or {})
            h["Authorization"] = f"Bearer {self._access_token()}"
            for tries in range(3):
                try:
                    resp = requests.request(method.upper(), url, headers=h, **kw)
                    break
                except requests.exceptions.ConnectTimeout:
                    # The connection never opened, so PayPal never saw the
                    # request: retrying cannot pay twice.
                    if tries == 2:
                        raise
            if resp.status_code != 401 or attempt == 2:
                return resp
            self._token, self._token_exp = None, 0.0
        return resp

    # --- payouts ----------------------------------------------------------
    def send_payout(
        self,
        recipient: str,
        amount: float,
        currency: str = "USD",
        note: str = "",
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Send a single-item payout. Returns the batch header (or a simulation).

        When `idempotency_key` is given, the batch id, the item id and PayPal's
        `PayPal-Request-Id` header are all derived from it, so retrying the same
        payment does not create a second payout. Without it, each call gets fresh
        random ids.
        """
        stable = (idempotency_key or "").strip()
        batch_id = f"SAFEPAY-{stable[:12]}" if stable else f"SAFEPAY-{uuid.uuid4().hex[:12]}"
        item_id = stable[:16] if stable else uuid.uuid4().hex[:16]
        if not self.live:
            return {
                "mode": "simulated",
                "payout_batch_id": batch_id,
                "batch_status": "SUCCESS",
                "amount": {"value": f"{amount:.2f}", "currency": currency},
                "receiver": recipient,
                "note": "simulated payout (no PayPal credentials configured)",
            }

        import requests

        payload = {
            "sender_batch_header": {
                "sender_batch_id": batch_id,
                "email_subject": "A payment released by SafePay",
                "email_message": note or "Payment approved by SafePay guardrail.",
            },
            "items": [
                {
                    "recipient_type": "EMAIL",
                    "amount": {"value": f"{amount:.2f}", "currency": currency},
                    "receiver": recipient,
                    "note": note or "Approved by SafePay.",
                    "sender_item_id": item_id,
                }
            ],
        }
        headers = {
            "Authorization": f"Bearer {self._access_token()}",
            "Content-Type": "application/json",
        }
        if stable:
            # PayPal's idempotency mechanism: a repeated request with the same id
            # returns the original result instead of paying twice.
            headers["PayPal-Request-Id"] = stable
        resp = self._send("post", 
            f"{self._cfg.paypal_base_url}/v1/payments/payouts",
            json=payload,
            headers=headers,
            timeout=60,
        )
        if resp.status_code not in (200, 201, 202):
            raise PayPalError(
                f"PayPal payout failed ({resp.status_code}): {resp.text[:400]}"
            )
        body = resp.json()
        header = body.get("batch_header", {})
        return {
            "mode": "sandbox",
            "payout_batch_id": header.get("payout_batch_id", batch_id),
            "batch_status": header.get("batch_status", "PENDING"),
            "amount": {"value": f"{amount:.2f}", "currency": currency},
            "receiver": recipient,
            "raw": header,
        }

    # --- authorize, then capture (Orders v2) ------------------------------
    # A mandate reserves a budget on a payment source, then draws it down in
    # captures and voids what the task did not use. PayPal's published sandbox
    # test card stands in for the company card an agent would hold a vaulted
    # token for; it is only ever sent to the sandbox host.

    SANDBOX_CARD = {"number": "4111111111111111", "expiry": "2030-12",
                    "security_code": "123", "name": "SafePay Agent Card"}

    def _post(self, path: str, body: dict[str, Any] | None, key: str | None) -> Any:
        import requests

        headers = {"Authorization": f"Bearer {self._access_token()}",
                   "Content-Type": "application/json",
                   "PayPal-Request-Id": (key or uuid.uuid4().hex)[:100]}
        resp = self._send("post", f"{self._cfg.paypal_base_url}{path}", json=body,
                             headers=headers, timeout=60)
        if resp.status_code not in (200, 201, 204):
            try:
                det = resp.json()
                issue = ((det.get("details") or [{}])[0].get("issue") or det.get("name") or "")
            except ValueError:
                issue = resp.text[:200]
            raise PayPalError(f"PayPal {path.split('/')[-1]} failed ({resp.status_code}): {issue}")
        return resp.json() if resp.content else {}

    def get_json(self, path: str) -> dict[str, Any]:
        """GET a PayPal REST resource (sandbox host), JSON body or PayPalError."""
        import requests

        resp = self._send("get", f"{self._cfg.paypal_base_url}{path}",
                            headers={"Authorization": f"Bearer {self._access_token()}"}, timeout=30)
        if resp.status_code != 200:
            raise PayPalError(f"PayPal GET failed ({resp.status_code})")
        return resp.json()

    def vault_card(self) -> dict[str, Any]:
        """Save the agent card in PayPal's Vault (v3): a setup token, then a
        payment token. The card number is sent once, here; every mandate
        after that is authorized with the token. Returns {vault_id, brand,
        last_digits, mode}."""
        if not self.live:
            return {"mode": "simulated", "vault_id": f"SIMVAULT{uuid.uuid4().hex[:10].upper()}",
                    "brand": "VISA", "last_digits": self.SANDBOX_CARD["number"][-4:]}
        if "sandbox" not in self._cfg.paypal_base_url:
            raise PayPalError("the vaulted card here is PayPal's sandbox test card")
        setup = self._post("/v3/vault/setup-tokens",
                           {"payment_source": {"card": dict(self.SANDBOX_CARD)}}, None)
        token = self._post("/v3/vault/payment-tokens",
                           {"payment_source": {"token": {"id": setup.get("id"), "type": "SETUP_TOKEN"}}},
                           None)
        card = (token.get("payment_source") or {}).get("card") or {}
        if not token.get("id"):
            raise PayPalError("PayPal returned no payment token")
        return {"mode": "sandbox", "vault_id": token["id"], "brand": card.get("brand"),
                "last_digits": card.get("last_digits")}

    def authorize(self, amount: float, currency: str = "USD", description: str = "",
                  idempotency_key: str | None = None, vault_id: str | None = None) -> dict[str, Any]:
        """Create an order with intent AUTHORIZE and authorize it at once,
        charging the vaulted card when `vault_id` is given.
        Returns {mode, order_id, authorization_id, status, amount, expires}."""
        if not self.live:
            tag = uuid.uuid4().hex[:12].upper()
            return {"mode": "simulated", "order_id": f"SIM-ORDER-{tag}",
                    "authorization_id": f"SIMAUTH{tag}", "status": "CREATED",
                    "amount": {"value": f"{amount:.2f}", "currency": currency},
                    "expires": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + 29 * 86400))}
        if "sandbox" not in self._cfg.paypal_base_url:
            raise PayPalError("mandates use PayPal's sandbox test card; production needs a vaulted payment token")
        body = {"intent": "AUTHORIZE",
                "purchase_units": [{"amount": {"currency_code": currency, "value": f"{amount:.2f}"},
                                    "description": (description or "SafePay mandate")[:127]}],
                "payment_source": {"card": {"vault_id": vault_id} if vault_id
                                   else dict(self.SANDBOX_CARD)}}
        order = self._post("/v2/checkout/orders", body, idempotency_key)
        auths = (((order.get("purchase_units") or [{}])[0].get("payments") or {})
                 .get("authorizations") or [])
        if not auths:
            raise PayPalError(f"PayPal returned no authorization (order {order.get('status')})")
        a = auths[0]
        return {"mode": "sandbox", "order_id": order.get("id"), "authorization_id": a.get("id"),
                "status": a.get("status"), "amount": a.get("amount"),
                "expires": a.get("expiration_time"),
                "payment_source": "vault" if vault_id else "card"}

    def capture_authorization(self, authorization_id: str, amount: float, currency: str = "USD",
                              final: bool = False, note: str = "", invoice_id: str = "",
                              idempotency_key: str | None = None) -> dict[str, Any]:
        """Capture part (or the last part) of an authorization. A final capture
        releases whatever was left back to the payment source."""
        if not self.live:
            return {"mode": "simulated", "capture_id": f"SIMCAP{uuid.uuid4().hex[:12].upper()}",
                    "status": "COMPLETED", "amount": {"value": f"{amount:.2f}", "currency": currency},
                    "final": final}
        body: dict[str, Any] = {"amount": {"currency_code": currency, "value": f"{amount:.2f}"},
                                "final_capture": bool(final)}
        if note:
            body["note_to_payer"] = note[:255]
        if invoice_id:
            body["invoice_id"] = invoice_id[:127]
        cap = self._post(f"/v2/payments/authorizations/{authorization_id}/capture", body, idempotency_key)
        return {"mode": "sandbox", "capture_id": cap.get("id"), "status": cap.get("status"),
                "amount": {"value": f"{amount:.2f}", "currency": currency}, "final": final}

    def void_authorization(self, authorization_id: str) -> dict[str, Any]:
        """Void what is left of an authorization (works after partial captures)."""
        if not self.live:
            return {"mode": "simulated", "status": "VOIDED"}
        self._post(f"/v2/payments/authorizations/{authorization_id}/void", None, None)
        return {"mode": "sandbox", "status": "VOIDED"}

    # --- settlement ---------------------------------------------------------
    # A payout is accepted as PENDING; the money moves (or bounces) later.
    # These close the loop: what PayPal says actually happened to the batch.

    FINAL_OK = ("SUCCESS",)
    FINAL_FAILED = ("FAILED", "RETURNED", "BLOCKED", "DENIED", "CANCELED",
                    "REFUNDED", "REVERSED")

    def get_payout(self, batch_id: str) -> dict[str, Any]:
        """Current state of a payout batch and its (single) item, normalized:
        {payout_batch_id, batch_status, item_status, transaction_id,
         time_processed, errors, final}. Simulated batches are SUCCESS."""
        if not self.live:
            return {
                "mode": "simulated", "payout_batch_id": batch_id,
                "batch_status": "SUCCESS", "item_status": "SUCCESS",
                "transaction_id": None, "time_processed": None, "errors": None,
                "final": True,
            }
        import requests

        resp = self._send("get", 
            f"{self._cfg.paypal_base_url}/v1/payments/payouts/{batch_id}",
            headers={"Authorization": f"Bearer {self._access_token()}"},
            timeout=30,
        )
        if resp.status_code != 200:
            raise PayPalError(
                f"PayPal payout lookup failed ({resp.status_code}): {resp.text[:300]}"
            )
        body = resp.json()
        header = body.get("batch_header", {})
        items = body.get("items") or []
        item = items[0] if items else {}
        pi = item.get("payout_item") or {}
        amt = pi.get("amount") or {}
        status = str(item.get("transaction_status") or header.get("batch_status") or "PENDING")
        return {
            "mode": "sandbox",
            "payout_batch_id": header.get("payout_batch_id", batch_id),
            "batch_status": header.get("batch_status", "PENDING"),
            "item_status": status,
            "transaction_id": item.get("transaction_id"),
            "time_processed": item.get("time_processed") or header.get("time_completed"),
            "errors": item.get("errors"),
            "final": status in self.FINAL_OK or status in self.FINAL_FAILED,
            "receiver": pi.get("receiver"),
            "amount": float(amt["value"]) if amt.get("value") not in (None, "") else None,
            "currency": amt.get("currency"),
            "note": pi.get("note"),
        }

    def list_transactions(self, start: str, end: str) -> list[dict[str, Any]]:
        """Debits the account made in a window, from Transaction Search
        (`/v1/reporting/transactions`), normalized to {transaction_id, event_code,
        status, amount, currency, time, counterparty, subject}. The app needs the
        Transaction Search feature enabled in the developer dashboard; PayPal
        answers 403 otherwise and the error says so. Data lags by up to a few
        hours on PayPal's side. Simulated mode has no account: empty list."""
        if not self.live:
            return []
        import requests

        resp = self._send("get", 
            f"{self._cfg.paypal_base_url}/v1/reporting/transactions",
            headers={"Authorization": f"Bearer {self._access_token()}"},
            params={"start_date": start, "end_date": end, "page_size": 500,
                    "fields": "transaction_info,payer_info"},
            timeout=60,
        )
        if resp.status_code == 403:
            raise PayPalError(
                "Transaction Search is not enabled for this app (PayPal 403): turn on "
                "'Transaction search' under the app's features in the developer dashboard")
        if resp.status_code != 200:
            raise PayPalError(
                f"PayPal transaction search failed ({resp.status_code}): {resp.text[:300]}")
        out = []
        for t in resp.json().get("transaction_details") or []:
            ti = t.get("transaction_info") or {}
            amt = ti.get("transaction_amount") or {}
            try:
                value = float(amt.get("value"))
            except (TypeError, ValueError):
                continue
            out.append({
                "transaction_id": ti.get("transaction_id"),
                "event_code": ti.get("transaction_event_code"),
                "status": ti.get("transaction_status"),
                "amount": value, "currency": amt.get("currency_code"),
                "time": ti.get("transaction_initiation_date"),
                "counterparty": (t.get("payer_info") or {}).get("email_address"),
                "subject": ti.get("transaction_subject") or ti.get("transaction_note"),
            })
        return out

    def verify_webhook(self, headers: dict[str, str], event: dict[str, Any],
                       webhook_id: str) -> bool:
        """Ask PayPal whether a webhook delivery is genuine (signature over the
        transmission). Only a verified event is acted on."""
        if not self.live:
            return False
        import requests

        h = {k.upper(): v for k, v in headers.items()}
        payload = {
            "transmission_id": h.get("PAYPAL-TRANSMISSION-ID"),
            "transmission_time": h.get("PAYPAL-TRANSMISSION-TIME"),
            "cert_url": h.get("PAYPAL-CERT-URL"),
            "auth_algo": h.get("PAYPAL-AUTH-ALGO"),
            "transmission_sig": h.get("PAYPAL-TRANSMISSION-SIG"),
            "webhook_id": webhook_id,
            "webhook_event": event,
        }
        if not all(payload[k] for k in ("transmission_id", "transmission_time",
                                        "cert_url", "auth_algo", "transmission_sig")):
            return False
        resp = self._send("post", 
            f"{self._cfg.paypal_base_url}/v1/notifications/verify-webhook-signature",
            json=payload,
            headers={"Authorization": f"Bearer {self._access_token()}",
                     "Content-Type": "application/json"},
            timeout=30,
        )
        ok = resp.status_code == 200 and resp.json().get("verification_status") == "SUCCESS"
        if not ok:
            # Operator diagnostics: PayPal's answer says whether the webhook id,
            # the transmission or the event body is what failed. No secrets here.
            print(f"safepay: webhook verification failed for {event.get('event_type')} "
                  f"({payload['transmission_id']}): HTTP {resp.status_code} {resp.text[:300]}",
                  file=sys.stderr)
        return ok
