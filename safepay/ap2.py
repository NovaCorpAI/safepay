"""AP2 mandates on SafePay's PayPal mandates.

AP2 (the Agent Payments Protocol) carries three signed objects through an
agentic purchase. SafePay produces and checks them with the field names of the
AP2 SDK (ap2.models.mandate / payment_request), around its PayPal
authorize-and-capture mandates:

    IntentMandate   what the person asked the agent to do: a description, the
                    merchants it may buy from, when the intent expires. The
                    operator signs it in the browser with the same P-256 key
                    that signs the policy (ES256 JWS, the hash of the mandate
                    in `transaction_data`, as AP2's user authorization does).
                    SafePay verifies it, against the session's pinned operator
                    key when there is one, BEFORE authorizing with PayPal, and
                    then enforces it: the payee must be one of its merchants,
                    and no capture is allowed after `intent_expiry`.
    CartMandate     the merchant's cart, as a W3C PaymentRequest, built from
                    the AgentPOS store's own quote. SafePay checks that its
                    total is the ceiling being authorized and that it comes
                    from the payee's domain. The store does not sign AP2 carts
                    yet, so `merchant_authorization` is null, said plainly.
    PaymentMandate  one per PayPal capture, bound to the intent (and cart) by
                    their hashes. AP2 leaves room for whoever vouches for the
                    payment to sign it; SafePay signs it with its published
                    Ed25519 seal key in `safepay_authorization` (an extension
                    field) and leaves `user_authorization` null, because the
                    person signed the intent, not each capture.

Hashes are sha256 over canonical JSON (keys sorted, no whitespace), the same
canonical form as the seal receipts.
"""

from __future__ import annotations

import base64
import hashlib
import json
import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

from . import opsig, receipt

AUD = "safepay"


class MandateError(ValueError):
    """An AP2 object is malformed, does not verify, or does not allow this."""


def _b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _b64u_dec(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def digest(obj: Any) -> str:
    return hashlib.sha256(receipt.canonical(obj)).hexdigest()


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_iso(s: str) -> float:
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError) as exc:
        raise MandateError(f"intent_expiry is not an ISO 8601 time: {s!r}") from exc


def host(v: Any) -> str:
    s = str(v or "").strip().lower()
    if "@" in s and "://" not in s:
        return s.rsplit("@", 1)[1]
    return (urlparse(s if "://" in s else "https://" + s).hostname or "").removeprefix("www.")


# --- IntentMandate (signed by the operator) ---------------------------------

def intent_mandate(description: str, merchants: list[str] | None, expiry: str,
                   confirm_cart: bool = False) -> dict[str, Any]:
    return {"user_cart_confirmation_required": bool(confirm_cart),
            "natural_language_description": str(description or "")[:500],
            "merchants": merchants or None, "skus": None,
            "requires_refundability": False, "intent_expiry": expiry}


def _check_intent_shape(m: Any) -> dict[str, Any]:
    if not isinstance(m, dict):
        raise MandateError("intent_mandate must be an object")
    for k in ("user_cart_confirmation_required", "natural_language_description", "intent_expiry"):
        if k not in m:
            raise MandateError(f"intent_mandate lacks {k}")
    if m.get("merchants") is not None and not (isinstance(m["merchants"], list) and
                                               all(isinstance(x, str) for x in m["merchants"])):
        raise MandateError("intent_mandate.merchants must be a list of strings")
    return m


def verify_intent(signed: dict[str, Any], payee: str, pinned_kid: str | None,
                  now: float | None = None) -> dict[str, Any]:
    """Check a {intent_mandate, user_authorization} the operator signed.
    Returns what is chained with the authorize link; raises MandateError."""
    now = time.time() if now is None else now
    m = _check_intent_shape((signed or {}).get("intent_mandate"))
    tok = str((signed or {}).get("user_authorization") or "")
    try:
        h64, p64, s64 = tok.split(".")
        header, claims = json.loads(_b64u_dec(h64)), json.loads(_b64u_dec(p64))
    except (ValueError, TypeError) as exc:
        raise MandateError("user_authorization is not a compact JWS") from exc
    if header.get("alg") != "ES256":
        raise MandateError("user_authorization must be ES256 (the operator's P-256 key)")
    try:
        jwk = opsig.public_jwk(header.get("jwk") or {})
    except opsig.SignatureError as exc:
        raise MandateError(str(exc)) from exc
    kid = opsig.key_id(jwk)
    if not opsig.verify_bytes(jwk, f"{h64}.{p64}".encode(), s64):
        raise MandateError("the operator's signature on the intent mandate does not verify")
    if pinned_kid and kid != pinned_kid:
        raise MandateError(f"signed by key {kid}, but this session is pinned to operator key {pinned_kid}")
    h = digest(m)
    if h not in (claims.get("transaction_data") or []):
        raise MandateError("the signature is over a different intent mandate (hash not in transaction_data)")
    if claims.get("aud") != AUD:
        raise MandateError(f"the signature is not addressed to {AUD} (aud)")
    exp = _parse_iso(m["intent_expiry"])
    if exp <= now:
        raise MandateError(f"the intent expired at {m['intent_expiry']}")
    merchants = [host(x) for x in (m.get("merchants") or [])]
    if merchants and host(payee) not in merchants:
        raise MandateError(f"the payee {host(payee)} is not one of the intent's merchants ({', '.join(merchants)})")
    return {"intent_mandate": m, "user_authorization": tok, "intent_hash": h, "kid": kid,
            "pinned": bool(pinned_kid), "merchants": merchants, "expires_ts": exp}


def intent_checks(ap2: dict[str, Any] | None, now: float | None = None) -> list[tuple[bool, str]]:
    """What a capture must satisfy under the signed intent: (ok, detail)."""
    if not ap2:
        return []
    now = time.time() if now is None else now
    exp = float(ap2.get("expires_ts") or 0)
    if exp and now >= exp:
        return [(False, f"the operator's AP2 intent expired at {ap2['intent_mandate'].get('intent_expiry')}; "
                        "no capture after it")]
    return [(True, f"within the operator's AP2 intent (key {ap2.get('kid')}), until "
                   f"{ap2['intent_mandate'].get('intent_expiry')}")]


# --- CartMandate (from the AgentPOS quote) ----------------------------------

def _item(label: str, value: float, currency: str = "USD") -> dict[str, Any]:
    return {"label": label, "amount": {"currency": currency, "value": round(float(value), 2)},
            "pending": None, "refund_period": 30}


def cart_mandate(quote: dict[str, Any], payee: str, amount: float) -> dict[str, Any]:
    """A CartMandate from the store's quote. Raises if the cart is not the
    payment being authorized."""
    i = (quote or {}).get("intent") or {}
    lines = (quote or {}).get("lines") or []
    total = round(float((quote or {}).get("total") or 0), 2)
    if not quote or not quote.get("cart_id") or not lines:
        raise MandateError("the cart needs a cart_id and lines")
    if abs(total - round(float(amount), 2)) > 0.005:
        raise MandateError(f"the cart totals {total:.2f} but {float(amount):.2f} is being authorized")
    if host(i.get("merchant_url")) != host(payee):
        raise MandateError(f"the cart comes from {host(i.get('merchant_url'))}, the payee is {host(payee)}")
    items = [_item(f"{ln.get('quantity')} x {ln.get('title')}", float(ln.get("lineTotal") or 0)) for ln in lines]
    if abs(sum(x["amount"]["value"] for x in items) - total) > 0.005:
        raise MandateError("the cart's lines do not add up to its total")
    contents = {
        "id": str(quote["cart_id"]), "user_cart_confirmation_required": False,
        "payment_request": {
            "method_data": [{"supported_methods": "https://www.paypal.com", "data": {"intent": "AUTHORIZE"}}],
            "details": {"id": str(quote["cart_id"]), "display_items": items, "shipping_options": None,
                        "modifiers": None, "total": _item("Total", total)},
            "options": None, "shipping_address": None},
        "cart_expiry": quote.get("expires") or _iso(time.time() + 900),
        "merchant_name": i.get("merchant_name") or host(payee),
    }
    return {"contents": contents, "merchant_authorization": None, "cart_hash": digest(contents)}


# --- PaymentMandate (one per capture, signed by SafePay) --------------------

def _ed25519_jwt(claims: dict[str, Any]) -> str | None:
    k = receipt._key()
    j = receipt.public_jwk()
    if k is None or j is None:
        return None
    h = _b64u(receipt.canonical({"alg": "EdDSA", "kid": j["kid"], "typ": "JWT"}))
    p = _b64u(receipt.canonical(claims))
    return f"{h}.{p}.{_b64u(k.sign(f'{h}.{p}'.encode()))}"


def payment_mandate(st: dict[str, Any], cap: dict[str, Any], ap2: dict[str, Any] | None,
                    cart: dict[str, Any] | None) -> dict[str, Any]:
    cur = st.get("currency") or "USD"
    contents = {
        "payment_mandate_id": str(cap.get("capture_id") or f"seq-{cap.get('seq')}"),
        "payment_details_id": (cart or {}).get("contents", {}).get("id") or st["authorization_id"],
        "payment_details_total": _item(cap.get("purpose") or "Capture", cap["amount"], cur),
        "payment_response": {"request_id": (cart or {}).get("contents", {}).get("id") or st["authorization_id"],
                             "method_name": "https://www.paypal.com",
                             "details": {"authorization_id": st["authorization_id"],
                                         "capture_id": cap.get("capture_id"), "seal": cap.get("seal")},
                             "shipping_address": None, "shipping_option": None,
                             "payer_name": None, "payer_email": None, "payer_phone": None},
        "merchant_agent": st.get("merchant") or host(st.get("recipient")),
        "timestamp": cap.get("at") or "",
    }
    bound = [h for h in ((ap2 or {}).get("intent_hash"), (cart or {}).get("cart_hash"), digest(contents)) if h]
    token = _ed25519_jwt({"iss": "https://safepay.agentposhq.com", "aud": "payment-network",
                          "sub": contents["payment_mandate_id"], "transaction_data": bound,
                          "chain_seq": cap.get("seq")})
    return {"payment_mandate_contents": contents, "user_authorization": None,
            "safepay_authorization": token}


def bundle(st: dict[str, Any], ap2: dict[str, Any] | None, cart: dict[str, Any] | None) -> dict[str, Any]:
    """Every AP2 object of one mandate, rebuilt from the chain."""
    return {
        "protocol": "AP2 (field names from the AP2 SDK, ap2.models.mandate)",
        "intent_mandate": (ap2 or {}).get("intent_mandate"),
        "intent": {"user_authorization": (ap2 or {}).get("user_authorization"), "hash": (ap2 or {}).get("intent_hash"),
                   "operator_key": (ap2 or {}).get("kid"), "pinned": (ap2 or {}).get("pinned")} if ap2 else None,
        "cart_mandate": cart,
        "payment_mandates": [payment_mandate(st, c, ap2, cart) for c in st.get("captures") or []],
        "keys": receipt.KEYS_PATH,
    }
