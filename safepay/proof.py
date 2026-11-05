"""Proof: every PayPal object the chain says SafePay created, with a way to
ask PayPal about each one right now.

A judge should not have to trust a screenshot. This reads the audit chain for
the sandbox ids PayPal returned (payout batches, orders, authorizations,
captures) and, on request, re-reads each one from PayPal's own API, so the
status shown is PayPal's, not ours.
"""

from __future__ import annotations

import json
import re
from typing import Any

ID_RE = re.compile(r"^[A-Z0-9][A-Z0-9-]{7,39}$")
KINDS = ("payout", "order", "authorization", "capture")


def _real(i: Any) -> bool:
    s = str(i or "")
    return bool(ID_RE.match(s)) and not s.startswith(("SIM", "SAFEPAY-"))


def _order_id(res: Any) -> str:
    if isinstance(res, str):
        try:
            res = json.loads(res)
        except ValueError:
            m = re.search(r'"id"\s*:\s*"([A-Z0-9]{10,20})"', res)
            return m.group(1) if m else ""
    return str((res or {}).get("id") or "") if isinstance(res, dict) else ""


def items(records: list[dict[str, Any]], limit: int = 200) -> list[dict[str, Any]]:
    """PayPal sandbox objects named in the chain, newest first."""
    out: list[dict[str, Any]] = []
    for rec in reversed(records or []):
        p = rec.get("payload") or {}
        x = p.get("execution")
        if not isinstance(x, dict) or x.get("mode") == "simulated":
            continue
        i = p.get("intent") or {}
        ev = p.get("event") or "decision"
        found: list[tuple[str, str]] = []
        if x.get("payout_batch_id") and ev in ("decision", "outside"):
            found.append(("payout", x["payout_batch_id"]))
        if ev == "authorize" and x.get("authorization_id"):
            found.append(("authorization", x["authorization_id"]))
        if ev == "capture" and x.get("capture_id"):
            found.append(("capture", x["capture_id"]))
        if x.get("method") in ("create_order", "pay_order") or ev == "order":
            oid = _order_id(x.get("result"))
            if oid:
                found.append(("order", oid))
        for kind, pid in found:
            if not _real(pid):
                continue
            out.append({
                "seq": p.get("seq"), "ts": p.get("ts"), "event": ev, "verdict": p.get("verdict"),
                "kind": kind, "paypal_id": pid, "amount": i.get("amount"),
                "currency": i.get("currency") or "USD", "seal": x.get("seal") or "",
                "recorded": x.get("batch_status") or x.get("status") or "",
                "via": x.get("via") or x.get("method") or "",
            })
            if len(out) >= limit:
                return out
    return out


def check(paypal: Any, kind: str, pid: str) -> dict[str, Any]:
    """Ask PayPal for the object's current status."""
    if kind not in KINDS or not _real(pid):
        raise ValueError("unknown kind or not a PayPal sandbox id")
    if not getattr(paypal, "live", False):
        raise ValueError("this instance has no PayPal credentials")
    if kind == "payout":
        s = paypal.get_payout(pid)
        return {"status": s.get("item_status") or s.get("batch_status"), "detail": s.get("batch_status")}
    path = {"order": f"/v2/checkout/orders/{pid}",
            "authorization": f"/v2/payments/authorizations/{pid}",
            "capture": f"/v2/payments/captures/{pid}"}[kind]
    body = paypal.get_json(path)
    amt = body.get("amount") or ((body.get("purchase_units") or [{}])[0].get("amount") or {})
    return {"status": body.get("status"),
            "detail": f"{amt.get('currency_code', '')} {amt.get('value', '')}".strip()}
