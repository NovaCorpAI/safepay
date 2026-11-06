"""Mandates: authorize a budget once, capture only what the task costs.

An agent booking a hotel, renting compute or buying a batch of reports often
does not know the final price when it starts. Paying the ceiling up front
over-pays; asking a person at every step defeats the agent. A mandate is the
middle path, built on PayPal's authorize and capture:

    open     the agent asks for a ceiling for a task. The ceiling goes through
             the full guardrail (policy, history, merchant, reasoning) like any
             payment; on ALLOW SafePay authorizes it with PayPal, and the whole
             ceiling counts against the agent's daily cap right away.
    capture  each draw is checked on its own: the mandate is open, the caller
             is the agent that opened it and is not frozen, and the amount fits
             in what is left. PayPal is called only when all three pass. Every
             capture carries its own seal in the note PayPal shows the payer.
    close    voids what was not used (or a capture marked final does), and the
             unused part is credited back to the agent's daily cap.

Each step is a link in the same signed chain (events "authorize", "capture"
and "void"), and the mandate's state is read back from those links, so there
is no second store that could disagree with the chain.
"""

from __future__ import annotations

from typing import Any

from .models import CheckResult, Severity

EVENTS = ("authorize", "capture", "void")


def _p(rec: dict[str, Any]) -> dict[str, Any]:
    return rec.get("payload") or {}


def _x(rec: dict[str, Any]) -> dict[str, Any]:
    x = _p(rec).get("execution")
    return x if isinstance(x, dict) else {}


def links(audit_log: Any, authorization_id: str,
          events: tuple[str, ...] = EVENTS) -> list[dict[str, Any]]:
    """Every chain link of one mandate, oldest first. Uses the store's own
    query when it has one (Supabase), else scans the records it holds."""
    if hasattr(audit_log, "find_mandate"):
        return audit_log.find_mandate(authorization_id, events)
    return [r for r in list(getattr(audit_log, "records", []) or [])
            if _p(r).get("event") in events and _x(r).get("authorization_id") == authorization_id]


def _at(ts: Any) -> str:
    from datetime import datetime, timezone
    try:
        return datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat().replace("+00:00", "Z")
    except (TypeError, ValueError, OverflowError, OSError):
        return ""


def state(audit_log: Any, authorization_id: str) -> dict[str, Any] | None:
    """The mandate as the chain records it, or None if it was never opened."""
    recs = links(audit_log, authorization_id)
    opened = next((r for r in recs if _p(r).get("event") == "authorize" and _p(r).get("executed")), None)
    if opened is None:
        return None
    p, x = _p(opened), _x(opened)
    i = p.get("intent") or {}
    authorized = round(float(i.get("amount") or 0), 2)
    captures, closed, voided = [], False, 0.0
    for r in recs:
        q, e = _p(r), _x(r)
        if q.get("event") == "capture" and q.get("executed"):
            amt = round(float((q.get("intent") or {}).get("amount") or 0), 2)
            captures.append({"seq": q.get("seq"), "amount": amt, "seal": e.get("seal"), "at": _at(q.get("ts")),
                             "capture_id": e.get("capture_id"), "final": bool(e.get("final")),
                             "purpose": (q.get("intent") or {}).get("purpose")})
            closed = closed or bool(e.get("final"))
        if q.get("event") == "void" and q.get("executed"):
            closed, voided = True, round(float(e.get("released") or 0), 2)
    captured = round(sum(c["amount"] for c in captures), 2)
    return {
        "authorization_id": authorization_id, "order_id": x.get("order_id"),
        "agent_id": i.get("agent_id"), "recipient": i.get("recipient"),
        "merchant": i.get("merchant_name"), "category": i.get("category"),
        "currency": i.get("currency") or "USD", "task": i.get("task_goal") or i.get("purpose"),
        "purpose": i.get("purpose"), "authorized": authorized, "captured": captured,
        "remaining": 0.0 if closed else round(max(0.0, authorized - captured), 2),
        "released": voided if voided else (round(authorized - captured, 2) if closed else 0.0),
        "closed": closed, "expires": x.get("expires"), "opened_seq": p.get("seq"),
        "policy_hash": p.get("policy_hash") or "", "mode": x.get("mode"), "captures": captures,
        "ap2": x.get("ap2"), "cart": x.get("ap2_cart"),
    }


def capture_checks(st: dict[str, Any], amount: float, agent_id: str,
                   frozen: str | None = None) -> list[CheckResult]:
    """The checks one capture must pass. Deterministic; the ceiling was
    already judged by the full guardrail when the mandate was opened."""
    checks: list[CheckResult] = []
    if frozen is not None:
        checks.append(CheckResult("agent", False, Severity.CRITICAL,
                                  f"agent '{agent_id}' is frozen ({frozen}); no capture", 1.0,
                                  [{"rule": "agent_paused"}]))
    else:
        checks.append(CheckResult("agent", True, Severity.INFO, f"agent '{agent_id}' is active"))
    from .ap2 import intent_checks
    for ok, detail in intent_checks(st.get("ap2")):
        checks.append(CheckResult("ap2_intent", ok, Severity.INFO if ok else Severity.CRITICAL, detail,
                                  0.0 if ok else 1.0, [] if ok else [{"rule": "ap2_intent_expired"}]))
    if st["closed"]:
        checks.append(CheckResult("mandate", False, Severity.CRITICAL,
                                  "the mandate is closed; nothing is left to capture", 1.0,
                                  [{"rule": "mandate_closed"}]))
    elif agent_id != st["agent_id"]:
        checks.append(CheckResult("mandate", False, Severity.CRITICAL,
                                  f"only '{st['agent_id']}', the agent that opened this mandate, "
                                  f"can draw on it; '{agent_id}' asked", 1.0,
                                  [{"rule": "mandate_owner", "owner": st["agent_id"]}]))
    else:
        checks.append(CheckResult("mandate", True, Severity.INFO,
                                  f"open, for: {st['task'] or st['purpose'] or 'its task'}"))
    amount = round(float(amount or 0), 2)
    cur = st["currency"]
    if amount <= 0:
        checks.append(CheckResult("amount", False, Severity.CRITICAL,
                                  "a capture must be a positive amount", 1.0, [{"rule": "amount_positive"}]))
    elif amount > st["remaining"] + 1e-9:
        checks.append(CheckResult("amount", False, Severity.CRITICAL,
                                  f"{cur} {amount:.2f} is more than the {cur} {st['remaining']:.2f} left of "
                                  f"the {cur} {st['authorized']:.2f} authorized", 1.0,
                                  [{"rule": "mandate_remaining", "remaining": st["remaining"]}]))
    else:
        checks.append(CheckResult("amount", True, Severity.INFO,
                                  f"{cur} {amount:.2f} of {cur} {st['remaining']:.2f} left",
                                  round(amount / st["remaining"], 3) * 0.3 if st["remaining"] else 0.0))
    return checks
