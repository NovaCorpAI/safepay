"""Seal receipts anyone can verify offline: Ed25519, public key published.

The chain is HMAC-signed, which only SafePay can check. A merchant (or an
auditor, or a court) should not have to trust SafePay's server to know that a
sealed payment is real. So every seal also has a receipt, in the same
envelope as AgentPOS merchant receipts (spec: agent-commerce-receipts):

    {"body": {...}, "hash": sha256(canonical(body)), "signature": Ed25519}

signed over  "safepay-receipt-1" LF hash.  Canonical JSON = keys sorted at
every depth, no insignificant whitespace, arrays in order. Amounts are decimal
strings in minor units. The signer's public key is inside the body and also
published at /.well-known/safepay-seal-keys.json, so a verifier pins it once
and then checks any receipt with any Ed25519 library (or the browser's
WebCrypto), without calling SafePay again.

The receipt names the payee by domain and by the sha256 of its address: the
payee can match it (hash its own address) without the receipt exposing the
address to anyone else who holds the seal id. It carries the chain link's
seq, sha256 and prev, tying it to SafePay's hash chain.

The signing key comes from SAFEPAY_SEAL_ED25519_SEED (32 bytes, base64url),
or, when unset, is derived from SAFEPAY_SIGNING_KEY with a fixed label, so an
existing deployment gets a stable key with no new secret. It signs
statements only; it never holds or moves funds. Without either, no receipts.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
from datetime import datetime, timezone
from typing import Any

VERSION = "safepay-receipt-1"
PRECHECK = "safepay-precheck-1"   # a verdict given BEFORE a payment; no money moved
VERSIONS = (VERSION, PRECHECK)
KEYS_PATH = "/.well-known/safepay-seal-keys.json"
_ZERO_DECIMAL = {"JPY", "KRW", "CLP", "VND", "HUF", "TWD", "ISK", "UGX", "XAF", "XOF"}


def _b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _b64u_dec(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def canonical(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _seed() -> bytes | None:
    raw = (os.environ.get("SAFEPAY_SEAL_ED25519_SEED") or "").strip()
    if raw:
        try:
            b = _b64u_dec(raw)
        except ValueError:
            b = b""
        if len(b) == 32:
            return b
    base = (os.environ.get("SAFEPAY_SIGNING_KEY") or "").strip()
    if base:
        return hashlib.sha256(b"safepay-seal-ed25519/v1|" + base.encode()).digest()
    return None


def _key():
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    seed = _seed()
    return Ed25519PrivateKey.from_private_bytes(seed) if seed else None


def public_jwk() -> dict[str, str] | None:
    from cryptography.hazmat.primitives import serialization
    k = _key()
    if k is None:
        return None
    x = k.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return {"kty": "OKP", "crv": "Ed25519", "x": _b64u(x), "kid": _b64u(hashlib.sha256(x).digest())[:16],
            "alg": "EdDSA", "use": "sig"}


def keyset() -> dict[str, Any]:
    j = public_jwk()
    return {"keys": [j] if j else [], "issuer": "SafePay by NovaCorp Labs", "format": VERSION,
            "signs": f'Ed25519 over "{VERSION}" LF sha256(canonical(body)), hex',
            "spec": "AgentPOS agent-commerce-receipts, merchant receipt envelope"}


def _host(v: Any) -> str:
    s = str(v or "").strip().lower()
    if "@" in s:
        return s.rsplit("@", 1)[1]
    if "://" in s:
        s = s.split("://", 1)[1]
    return s.split("/", 1)[0].split(":", 1)[0]


def minor(amount: Any, currency: str) -> str:
    places = 0 if (currency or "").upper() in _ZERO_DECIMAL else 2
    return str(int(round(float(amount or 0) * (10 ** places))))


def _iso(ts: Any) -> str | None:
    try:
        return datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat().replace("+00:00", "Z")
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def body(found: dict[str, Any], seal_id: str) -> dict[str, Any] | None:
    """What the receipt states, from the sealed chain link."""
    j = public_jwk()
    if j is None:
        return None
    rec = found["record"]
    p = rec.get("payload") or {}
    i = p.get("intent") or {}
    x = p.get("execution") or {}
    payee = str(i.get("recipient") or "").strip().lower()
    cur = str(i.get("currency") or "USD").upper()
    kind = "capture" if x.get("method") == "capture" else "payout"
    b = {
        "v": VERSION,
        "seal": seal_id,
        "event": "payment.released" if p.get("executed") else "payment.decided",
        "at": _iso(p.get("ts")),
        "issuer": {"name": "SafePay", "url": "https://safepay.agentposhq.com", "signer": j["x"], "kid": j["kid"]},
        "chain": {"seq": p.get("seq"), "hash": rec.get("sha256"), "prev": p.get("prev")},
        "decision": {"verdict": p.get("verdict"), "policyHash": p.get("policy_hash")},
        "terms": {"amountMinor": minor(i.get("amount"), cur), "currency": cur,
                  "payeeDomain": _host(payee) or None,
                  "payeeSha256": hashlib.sha256(payee.encode()).hexdigest() if payee else None,
                  "merchantDomain": _host(i.get("merchant_url")) or None},
        "payment": {"rail": f"paypal.{kind}", "id": x.get("capture_id") or x.get("payout_batch_id")},
    }
    return _prune(b)


def _prune(o: Any) -> Any:
    """Canonical JSON omits undefined members; None stands in for undefined here."""
    if isinstance(o, dict):
        return {k: _prune(v) for k, v in o.items() if v is not None}
    return o


def _message(h: str, version: str = VERSION) -> bytes:
    return f"{version}\n{h}".encode()


def sign_envelope(body: dict[str, Any]) -> dict[str, Any] | None:
    """{body, hash, signature} for any body whose `v` is one of VERSIONS,
    signed over `v` LF hash, exactly like a seal receipt."""
    k = _key()
    if k is None or body.get("v") not in VERSIONS:
        return None
    b = _prune(body)
    h = hashlib.sha256(canonical(b)).hexdigest()
    return {"body": b, "hash": h, "signature": base64.b64encode(k.sign(_message(h, b["v"]))).decode()}


def issue(found: dict[str, Any], seal_id: str) -> dict[str, Any] | None:
    k = _key()
    b = body(found, seal_id)
    if k is None or b is None:
        return None
    h = hashlib.sha256(canonical(b)).hexdigest()
    return {"body": b, "hash": h, "signature": base64.b64encode(k.sign(_message(h))).decode()}


def verify(receipt: dict[str, Any], signer: str | None = None) -> tuple[bool, str]:
    """(ok, code) per the spec's offline steps. `signer` (base64url public
    key) pins the key; otherwise the one inside the body is used, which only
    proves integrity, not origin."""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    try:
        b, h, sig = receipt["body"], receipt["hash"], receipt["signature"]
        if b.get("v") not in VERSIONS:
            return False, "RECEIPT_MALFORMED"
        key = (b.get("issuer") or {}).get("signer")
    except (KeyError, TypeError, AttributeError):
        return False, "RECEIPT_MALFORMED"
    if hashlib.sha256(canonical(b)).hexdigest() != h:
        return False, "RECEIPT_TAMPERED"
    if signer and signer != key:
        return False, "RECEIPT_SIGNATURE_INVALID"
    try:
        Ed25519PublicKey.from_public_bytes(_b64u_dec(key)).verify(base64.b64decode(sig), _message(h, b["v"]))
    except (InvalidSignature, ValueError, TypeError):
        return False, "RECEIPT_SIGNATURE_INVALID"
    return True, "OK"


def _main(argv: list[str]) -> int:
    """python -m safepay.receipt receipt.json [safepay-seal-keys.json]   (offline)"""
    from pathlib import Path
    r = json.loads(Path(argv[0]).read_text(encoding="utf-8"))
    keys = json.loads(Path(argv[1]).read_text(encoding="utf-8")).get("keys", []) if len(argv) > 1 else []
    signer = ((r.get("body") or {}).get("issuer") or {}).get("signer")
    if keys and signer not in {k.get("x") for k in keys}:
        print("RECEIPT_SIGNATURE_INVALID: not signed by a published SafePay key")
        return 1
    ok, code = verify(r, signer if keys else None)
    print(code)
    if ok:
        b = r["body"]
        print(f"{b['seal']}: {b['terms']['currency']} {b['terms']['amountMinor']} (minor units), {b['event']}, "
              f"chain link #{b['chain'].get('seq')}" + ("" if keys else " (integrity only: pass the keys file to pin the signer)"))
    return 0 if ok else 1


if __name__ == "__main__":
    import sys
    sys.exit(_main(sys.argv[1:]))
