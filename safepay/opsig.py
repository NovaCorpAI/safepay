"""Operator-signed policy: the rules an agent pays under, signed in the
operator's browser with a key the server never holds.

The operator's browser makes an ECDSA P-256 key with WebCrypto; the private
half is non-extractable and stays in that browser. Applying a policy signs

    safepay-policy:v1:<run>:<sha256 of the policy>

and sends the signature with the public key (a JWK). SafePay verifies it, and
the first signed policy of a session pins that key: from then on an unsigned
change, or one signed by another key, is refused. The signature is stored with
the policy and written into its chain link, and the stored policy is checked
again every time a payment is judged under it. So a leaked operator token, or
someone editing the policy row in the database, cannot quietly loosen the
rules: a policy that does not verify against the pinned key blocks every
payment until the operator signs again.
"""

from __future__ import annotations

import base64
import hashlib
import json
from typing import Any

PREFIX = "safepay-policy:v1"
ALG = "ES256"


class SignatureError(ValueError):
    """The signature is missing, malformed or does not verify."""


def _b64u_decode(s: str) -> bytes:
    s = (s or "").strip()
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def public_jwk(jwk: dict[str, Any]) -> dict[str, str]:
    """Only the public members of a P-256 JWK, validated."""
    if not isinstance(jwk, dict) or jwk.get("kty") != "EC" or jwk.get("crv") != "P-256":
        raise SignatureError("the operator key must be an EC P-256 public key (JWK)")
    if jwk.get("d"):
        raise SignatureError("that is a private key; send only the public half")
    x, y = str(jwk.get("x") or ""), str(jwk.get("y") or "")
    if len(_b64u_decode(x)) != 32 or len(_b64u_decode(y)) != 32:
        raise SignatureError("the operator key is not a valid P-256 point")
    return {"kty": "EC", "crv": "P-256", "x": x, "y": y}


def key_id(jwk: dict[str, Any]) -> str:
    """RFC 7638 thumbprint of the public key, base64url, first 16 chars."""
    pub = public_jwk(jwk)
    canon = json.dumps({"crv": pub["crv"], "kty": pub["kty"], "x": pub["x"], "y": pub["y"]},
                       separators=(",", ":"), sort_keys=True)
    return _b64u(hashlib.sha256(canon.encode()).digest())[:16]


def message(run: str, policy_hash: str) -> str:
    return f"{PREFIX}:{run}:{policy_hash}"


def verify(jwk: dict[str, Any], run: str, policy_hash: str, sig: str) -> bool:
    """True when `sig` (WebCrypto's raw r||s, base64url) is the operator key's
    ECDSA P-256 / SHA-256 signature over message(run, policy_hash)."""
    return verify_bytes(jwk, message(run, policy_hash).encode(), sig)


def verify_bytes(jwk: dict[str, Any], data: bytes, sig: str) -> bool:
    """ECDSA P-256 / SHA-256 over arbitrary bytes; `sig` is raw r||s base64url
    (WebCrypto's output, and JWS ES256's)."""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature

    pub = public_jwk(jwk)
    try:
        raw = _b64u_decode(sig)
    except (ValueError, TypeError):
        return False
    if len(raw) != 64:
        return False
    der = encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big"))
    try:
        key = ec.EllipticCurvePublicNumbers(
            int.from_bytes(_b64u_decode(pub["x"]), "big"),
            int.from_bytes(_b64u_decode(pub["y"]), "big"), ec.SECP256R1()).public_key()
        key.verify(der, data, ec.ECDSA(hashes.SHA256()))
        return True
    except (InvalidSignature, ValueError):
        return False


def seal_policy(run: str, policy_hash: str, signature: dict[str, Any]) -> dict[str, Any]:
    """Verify a {jwk, sig} the browser sent; return what is stored and chained."""
    if not isinstance(signature, dict):
        raise SignatureError("the signature must be {jwk, sig}")
    jwk, sig = signature.get("jwk"), str(signature.get("sig") or "")
    pub = public_jwk(jwk or {})
    if not verify(pub, run, policy_hash, sig):
        raise SignatureError("the operator signature does not verify for this policy and session")
    return {"alg": ALG, "kid": key_id(pub), "jwk": pub, "sig": sig,
            "msg": message(run, policy_hash)}


APPROVAL_PREFIX = "safepay-approval:v1"


def approval_message(action: str, approval_id: str, intent: dict[str, Any]) -> str:
    """What the operator's device signs to approve or reject one held
    payment: the action, the approval id and the payment itself (currency,
    amount, recipient), so a signature cannot be replayed for another
    payment, another amount or the other action."""
    amount = float(intent.get("amount") or 0)
    return (f"{APPROVAL_PREFIX}:{action}:{approval_id}:{intent.get('currency') or 'USD'}:"
            f"{amount:.2f}:{intent.get('recipient') or ''}")


def seal_approval(action: str, approval_id: str, intent: dict[str, Any],
                  signature: dict[str, Any]) -> dict[str, Any]:
    """Verify a {jwk, sig} the browser sent over approval_message(); return
    what is written into the approval's chain link."""
    if not isinstance(signature, dict):
        raise SignatureError("the signature must be {jwk, sig}")
    jwk, sig = signature.get("jwk"), str(signature.get("sig") or "")
    pub = public_jwk(jwk or {})
    msg = approval_message(action, approval_id, intent)
    if not verify_bytes(pub, msg.encode(), sig):
        raise SignatureError(f"the operator signature does not verify for this {action}")
    return {"alg": ALG, "kid": key_id(pub), "jwk": pub, "sig": sig, "msg": msg}


def check_stored(stored: dict[str, Any] | None, run: str, policy_hash: str,
                 pinned_kid: str) -> str | None:
    """Why a stored policy fails against the pinned key, or None if it holds."""
    if not stored:
        return "it carries no operator signature"
    if stored.get("kid") != pinned_kid:
        return f"it is signed by key {stored.get('kid')}, not the pinned operator key {pinned_kid}"
    try:
        ok = key_id(stored.get("jwk") or {}) == pinned_kid and \
            verify(stored["jwk"], run, policy_hash, str(stored.get("sig") or ""))
    except SignatureError:
        ok = False
    return None if ok else "its signature does not match its rules (the policy was changed after it was signed)"
