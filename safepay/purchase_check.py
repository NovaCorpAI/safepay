"""Purchase check: SafePay's history rules for an app that records supplier
purchases, before the record is saved and before anyone pays it.

An app like Impulsa (a SaaS for micro-businesses in Chile) records each
supplier document: supplier, document number, the unit cost of each item. It
sends the document it is about to save together with that business's recent
purchases, and SafePay answers with the same rules it applies to agent
payments:

    duplicate document   the same supplier's same document number already
                         recorded: BLOCK (paying or counting it twice)
    price above history  a unit cost above 2x the median of at least two
                         earlier purchases of the same product: HOLD

Nothing is paid here. The check is chained like any decision, but the public
link carries no names, no document numbers and no amounts: only a hash of
the business, the verdict and which rule fired. The full findings go back
only to the app that asked.
"""

from __future__ import annotations

import hashlib
from statistics import median
from typing import Any

from .models import CheckResult, Decision, PaymentIntent, Severity, Verdict
from .policy import norm_name

MAX_HISTORY = 500
PRICE_FACTOR = 2.0


def _h(s: Any) -> str:
    return hashlib.sha256(str(s or "").encode()).hexdigest()[:12]


def _f(v: Any) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _doc(s: Any) -> str:
    return "".join(ch for ch in str(s or "").upper() if ch.isalnum())


def evaluate(body: dict[str, Any], source: str) -> tuple[Decision, list[dict[str, Any]]]:
    """Returns (the decision to chain, the detailed findings for the caller)."""
    vendor = str(body.get("vendor") or "").strip()
    doc = _doc(body.get("invoice_ref"))
    items = [i for i in (body.get("items") or []) if isinstance(i, dict)][:100]
    history = [h for h in (body.get("history") or []) if isinstance(h, dict)][:MAX_HISTORY]
    if not items:
        raise ValueError("send at least one item")
    findings: list[dict[str, Any]] = []
    checks: list[CheckResult] = []

    # Duplicate document: same supplier (as SafePay normalizes names), same number.
    dup = None
    if vendor and doc:
        nv = norm_name(vendor)
        dup = next((h for h in history if _doc(h.get("invoice_ref")) == doc
                    and norm_name(h.get("vendor")) == nv), None)
    if dup is not None:
        findings.append({"rule": "duplicate_document", "severity": "block",
                         "detail": f"{vendor} document {body.get('invoice_ref')} was already recorded"
                                   + (f" on {dup.get('date')}" if dup.get("date") else "")})
        checks.append(CheckResult("duplicate", False, Severity.CRITICAL,
                                  "this supplier's document number was already recorded", 1.0,
                                  [{"rule": "duplicate_document"}]))
    else:
        checks.append(CheckResult("duplicate", True, Severity.INFO,
                                  "no earlier record of this supplier's document" if doc else
                                  "no document number to compare"))

    # Unit cost against this product's own history.
    jumps = 0
    for it in items:
        pid, cost = str(it.get("product") or ""), _f(it.get("unit_cost"))
        prior = [_f(h.get("unit_cost")) for h in history
                 if str(h.get("product") or "") == pid and _f(h.get("unit_cost")) > 0]
        if pid and cost > 0 and len(prior) >= 2:
            med = median(prior)
            if med > 0 and cost > med * PRICE_FACTOR:
                jumps += 1
                findings.append({"rule": "price_above_history", "severity": "hold",
                                 "product": pid, "unit_cost": cost, "median": med,
                                 "detail": f"unit cost {cost:g} is {cost / med:.1f}x the median "
                                           f"{med:g} of {len(prior)} earlier purchases"})
    if jumps:
        checks.append(CheckResult("price", False, Severity.WARN,
                                  f"{jumps} item(s) priced above {PRICE_FACTOR:g}x their purchase history",
                                  0.6, [{"rule": "price_above_history", "items": jumps}]))
    else:
        checks.append(CheckResult("price", True, Severity.INFO, "unit costs in line with history"))

    verdict = Verdict.BLOCK if dup is not None else Verdict.HOLD if jumps else Verdict.ALLOW
    business = _h(body.get("business"))
    intent = PaymentIntent(recipient=f"{source}:{business}", amount=0.0,
                           currency=str(body.get("currency") or "CLP")[:3].upper(),
                           category="supplier-purchase", agent_id=source,
                           purpose=f"{source} purchase check (no payment)")
    decision = Decision(verdict=verdict, intent=intent, checks=checks,
                        reasons=[f"{c.name}: {c.detail}" for c in checks if not c.passed],
                        event="check",
                        execution={"source": source, "business": business, "items": len(items),
                                   "history": len(history), "rules": [f["rule"] for f in findings]})
    return decision, findings
