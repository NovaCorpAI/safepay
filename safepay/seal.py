"""The SafePay seal: proof a payee can check without trusting the payer.

Every payout SafePay releases carries a seal id in the note PayPal shows the
recipient: "Sealed by SafePay SP-3F9A0C21BE. Verify: <host>/v/SP-3F9A0C21BE".
The seal id is written into the signed chain link of that payment, so the
public page at /v/<id> can show, to anyone holding the id, that this payment
was judged and released by SafePay, under which policy, which checks passed,
that the link's hash (and signature) is intact and that it is chained to the
link before it, and what PayPal later said happened to the money.

The page is public on purpose and shows only what the payee already knows:
amount, currency, merchant, date. The recipient address is masked.
"""

from __future__ import annotations

import hashlib
import os
import re
import secrets
import time
from typing import Any

from . import attest

PREFIX = "SP-"
SEAL_RE = re.compile(r"^SP-[0-9A-F]{10}$")
DEFAULT_PUBLIC_URL = "https://safepay.agentposhq.com"


def public_url() -> str:
    return (os.environ.get("SAFEPAY_PUBLIC_URL") or DEFAULT_PUBLIC_URL).rstrip("/")


def new_seal_id(idempotency_key: str | None = None) -> str:
    """A short public id. Derived from the idempotency key when there is one,
    so a retried payment keeps the seal of the original; random otherwise."""
    key = (idempotency_key or "").strip()
    raw = hashlib.sha256(key.encode("utf-8")).hexdigest()[:10] if key else secrets.token_hex(5)
    return PREFIX + raw.upper()


def normalize(seal_id: str) -> str | None:
    """'sp-3f9a0c21be' -> 'SP-3F9A0C21BE'; None when it is not a seal id."""
    s = (seal_id or "").strip().upper()
    if not s.startswith(PREFIX):
        s = PREFIX + s
    return s if SEAL_RE.match(s) else None


def verify_link(seal_id: str) -> str:
    host = public_url().split("://", 1)[-1]
    return f"{host}/v/{seal_id}"


def seal_note(purpose: str, seal_id: str, limit: int = 1000) -> str:
    """The note PayPal shows the recipient: the purpose, then the seal."""
    tail = f"Sealed by SafePay {seal_id}. Verify: {verify_link(seal_id)}"
    purpose = (purpose or "").strip()
    room = limit - len(tail) - 3
    if room <= 0 or not purpose:
        return tail
    if len(purpose) > room:
        purpose = purpose[: room - 1].rstrip() + "…"
    return f"{purpose} | {tail}"


def mask_email(addr: str | None) -> str:
    """'billing@notion.so' -> 'b•••g@notion.so'. The domain stays: it is what
    tells a payee the money went to the right organisation."""
    s = (addr or "").strip()
    if "@" not in s:
        return (s[:2] + "•••") if s else ""
    local, dom = s.rsplit("@", 1)
    if len(local) <= 2:
        shown = local[:1] + "•••"
    else:
        shown = local[0] + "•••" + local[-1]
    return f"{shown}@{dom}"


# --- lookup ---------------------------------------------------------------

def _seal_of(rec: dict[str, Any]) -> str:
    ex = (rec.get("payload") or {}).get("execution") or {}
    return str(ex.get("seal") or "") if isinstance(ex, dict) else ""


def find(audit_log: Any, seal_id: str) -> dict[str, Any] | None:
    """The chain link that carries this seal, the link before it, and the
    settlement links for its payout. Uses the store's own query when it has
    one (Supabase), else scans the records it holds."""
    if hasattr(audit_log, "find_seal"):
        return audit_log.find_seal(seal_id)
    records = list(getattr(audit_log, "records", []) or [])
    for i, rec in enumerate(records):
        if _seal_of(rec) == seal_id:
            prev = records[i - 1] if i > 0 else None
            batch = ((rec.get("payload") or {}).get("execution") or {}).get("payout_batch_id")
            settles = [r for r in records[i + 1:]
                       if (r.get("payload") or {}).get("event") == "settlement"
                       and ((r.get("payload") or {}).get("execution") or {}).get("payout_batch_id") == batch]
            return {"record": rec, "prev": prev, "settlements": settles}
    return None


def _when(ts: Any) -> str:
    try:
        return time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(float(ts)))
    except (TypeError, ValueError):
        return ""


def view(found: dict[str, Any], seal_id: str) -> dict[str, Any]:
    """What the public page shows: facts the payee already knows, plus the
    proofs. Nothing here identifies the payer's agent or other payments."""
    rec = found["record"]
    p = rec.get("payload") or {}
    intent = p.get("intent") or {}
    ex = p.get("execution") or {}
    prev = found.get("prev")
    intact = attest.verify_record(rec)
    if prev is not None:
        linked: bool | None = attest.verify_record(prev) and prev.get("sha256") == p.get("prev")
    else:
        linked = None   # the link before it is outside the window held here
    status, status_ts = str(ex.get("batch_status") or "PENDING"), None
    for s in found.get("settlements") or []:
        sp = s.get("payload") or {}
        sx = sp.get("execution") or {}
        status = str(sx.get("item_status") or sx.get("batch_status") or status)
        status_ts = sp.get("ts")
    return {
        "seal": seal_id,
        "verdict": p.get("verdict"),
        "executed": bool(p.get("executed")),
        "amount": intent.get("amount"),
        "currency": intent.get("currency"),
        "merchant": intent.get("merchant_name") or "",
        "recipient": mask_email(intent.get("recipient")),
        "invoice_ref": intent.get("invoice_ref") or "",
        "ts": p.get("ts"),
        "when": _when(p.get("ts")),
        "checks": [{"name": c.get("name"), "passed": bool(c.get("passed"))}
                   for c in p.get("checks") or []],
        "policy_hash": p.get("policy_hash") or "",
        "seq": p.get("seq"),
        "sha256": rec.get("sha256"),
        "prev": p.get("prev"),
        "signed": bool((rec.get("signature") or {}).get("signed")),
        "intact": intact,
        "linked": linked,
        "paypal_mode": ex.get("mode"),
        "payout_status": status,
        "status_ts": status_ts,
        "status_when": _when(status_ts) if status_ts else "",
    }
