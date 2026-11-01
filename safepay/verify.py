"""Merchant verification against the live web (Tavily).

A policy engine knows the rules an operator wrote down; it does not know that a
recipient domain was registered last week or shows up on scam-report pages.
This check pulls fresh web evidence about the merchant and asks whether anything
there looks like fraud. It is deliberately a *soft* signal: on its own it raises
risk and can trigger a HOLD, but it never silently blocks, because the live web
is noisy and a false "scam" hit should land in front of a human, not auto-reject.
"""

from __future__ import annotations

from .models import CheckResult, PaymentIntent, Severity
from .search import WebSearch

# Words that, when they co-occur with the merchant in fresh results, suggest the
# recipient is risky. Kept small and explicit so the signal is auditable.
_RISK_TERMS = [
    "scam", "fraud", "fraudulent", "phishing", "chargeback",
    "stolen", "fake store", "complaint", "scammer", "do not buy",
    "reported", "blacklist", "ripoff",
]


def check_merchant(intent: PaymentIntent, web: WebSearch) -> CheckResult:
    target = intent.merchant_name or intent.merchant_url or intent.recipient
    if not web.enabled:
        return CheckResult(
            name="merchant",
            passed=True,
            severity=Severity.INFO,
            detail=(
                "web verification skipped (no Tavily key configured); "
                "falling back to policy and reasoning only"
            ),
            risk=0.0,
        )
    if not target:
        return CheckResult(
            name="merchant",
            passed=True,
            severity=Severity.INFO,
            detail="no merchant name, url or recipient to verify",
            risk=0.0,
        )

    query = f"{target} reviews scam fraud legit"
    hits = web.search(query, max_results=5)

    evidence = [{"title": h.title, "url": h.url} for h in hits]
    if not hits:
        # No footprint at all is itself a mild flag for a payee you are about to
        # send money to, but not a block.
        return CheckResult(
            name="merchant",
            passed=True,
            severity=Severity.WARN,
            detail=(
                f"no live web footprint found for '{target}'; an unknown payee "
                f"warrants a second look but is not blocked"
            ),
            risk=0.35,
            evidence=evidence,
        )

    corpus = " ".join(
        f"{h.title} {h.content}" for h in hits
    ).lower()
    found = sorted({t for t in _RISK_TERMS if t in corpus})

    if found:
        hits_density = min(1.0, 0.4 + 0.15 * len(found))
        return CheckResult(
            name="merchant",
            passed=False,
            severity=Severity.WARN,
            detail=(
                f"live web results for '{target}' mention risk terms "
                f"({', '.join(found)}); recommend a human review before paying"
            ),
            risk=round(hits_density, 3),
            evidence=evidence,
        )

    return CheckResult(
        name="merchant",
        passed=True,
        severity=Severity.OK,
        detail=f"live web results for '{target}' show no fraud signals",
        risk=0.05,
        evidence=evidence,
    )
