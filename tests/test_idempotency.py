"""Idempotency of payout execution (simulated mode, no network)."""

from __future__ import annotations

from safepay.config import Config
from safepay.paypal_client import PayPalClient


def _client() -> PayPalClient:
    # No PayPal keys -> simulated mode, deterministic and offline.
    return PayPalClient(Config.from_env())


def test_same_key_yields_same_batch_id():
    c = _client()
    a = c.send_payout("v@x.example", 10, "USD", "note", idempotency_key="pay-abc")
    b = c.send_payout("v@x.example", 10, "USD", "note", idempotency_key="pay-abc")
    assert a["payout_batch_id"] == b["payout_batch_id"] == "SAFEPAY-pay-abc"


def test_no_key_yields_distinct_batch_ids():
    c = _client()
    a = c.send_payout("v@x.example", 10, "USD", "note")
    b = c.send_payout("v@x.example", 10, "USD", "note")
    assert a["payout_batch_id"] != b["payout_batch_id"]
