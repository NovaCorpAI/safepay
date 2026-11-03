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
        resp = requests.post(
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

        resp = requests.get(
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
        }

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
        resp = requests.post(
            f"{self._cfg.paypal_base_url}/v1/notifications/verify-webhook-signature",
            json=payload,
            headers={"Authorization": f"Bearer {self._access_token()}",
                     "Content-Type": "application/json"},
            timeout=30,
        )
        return resp.status_code == 200 and resp.json().get("verification_status") == "SUCCESS"
