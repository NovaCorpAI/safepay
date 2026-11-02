"""Signed audit trail for each SafePay decision.

Every decision (allow, hold, or block) and its execution result is recorded as
a canonical JSON record with a tamper-evident signature, so an operator can
later prove what the guardrail decided, on what evidence, and whether the money
moved. The signature is HMAC-SHA256 over the canonical bytes when a signing key
is configured; without one the record is still emitted but marked unsigned,
honest by construction. This record is the seam we extend for the Concordium
Trusted Agents build: the same canonical payload, signed by a verified-human
identity instead of a shared secret.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from typing import Any

from .models import Decision

PRODUCT = "SafePay"
MAKER = "NovaCorp Labs"
SITE = "novacorplabs.com"


def _canonical(payload: dict[str, Any]) -> bytes:
    """Stable byte representation: sorted keys, no incidental whitespace."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def audit_record(
    decision: Decision, prev: str | None = None, seq: int | None = None
) -> dict[str, Any]:
    """Build the signed record for one decision.

    When `prev` (the previous record's sha256) and `seq` are given, they are
    folded into the signed payload, which chains this record to the one before
    it: altering or dropping any earlier record breaks every hash that follows.
    Omitting them yields a standalone record (backward compatible).
    """
    payload: dict[str, Any] = {
        "product": PRODUCT,
        "maker": MAKER,
        "ts": int(time.time()),
        "verdict": decision.verdict.value,
        "risk": round(decision.risk, 3),
        "intent": decision.intent.to_dict(),
        "checks": [c.to_dict() for c in decision.checks],
        "executed": decision.executed,
        "execution": decision.execution,
    }
    if prev is not None:
        payload["prev"] = prev
    if seq is not None:
        payload["seq"] = seq
    body = _canonical(payload)
    digest = hashlib.sha256(body).hexdigest()

    key = os.environ.get("SAFEPAY_SIGNING_KEY", "").strip()
    if key:
        sig = hmac.new(key.encode("utf-8"), body, hashlib.sha256).hexdigest()
        signature = {"alg": "HMAC-SHA256", "value": sig, "signed": True}
    else:
        signature = {"alg": "none", "value": None, "signed": False}

    return {"payload": payload, "sha256": digest, "signature": signature}


def verify_record(record: dict[str, Any], key: str | None = None) -> bool:
    """Recompute the hash and (if signed) the HMAC, and compare."""
    payload = record.get("payload")
    if not isinstance(payload, dict):
        return False
    body = _canonical(payload)
    if hashlib.sha256(body).hexdigest() != record.get("sha256"):
        return False
    sig = record.get("signature", {})
    if not sig.get("signed"):
        return True  # integrity-only when unsigned; nothing more to check
    key = (key or os.environ.get("SAFEPAY_SIGNING_KEY", "")).strip()
    if not key:
        return False
    expected = hmac.new(key.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, sig.get("value", ""))


def stamp(decision: Decision) -> str:
    """One-line human-readable summary, for logs and the PR-style surface."""
    v = decision.verdict.value.upper()
    i = decision.intent
    return (
        f"{PRODUCT}: {v} {i.currency} {i.amount:.2f} to {i.recipient} "
        f"(risk {decision.risk:.2f}) · {MAKER} · {SITE}"
    )
