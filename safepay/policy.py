"""Deterministic policy checks.

These are the hard rules an operator sets: spending limits, allowed categories,
recipient allow/block lists, currency. They are pure and fast, run first, and a
failure here is a hard BLOCK that no amount of model reasoning can override.
The spend ledger tracks what an agent has already paid today, so the daily cap
is enforced across a sequence of intents, not just one in isolation.
"""

from __future__ import annotations

from .models import CheckResult, PaymentIntent, PolicyConfig, Severity


class SpendLedger:
    """In-memory tally of approved spend per agent (per process run).

    A real deployment would back this with a datastore; for the hackathon demo
    it lives for the length of the run, which is enough to show the daily cap
    stopping the fourth payment in a burst.
    """

    def __init__(self) -> None:
        self._spent: dict[str, float] = {}

    def spent_today(self, agent_id: str) -> float:
        return self._spent.get(agent_id, 0.0)

    def record(self, agent_id: str, amount: float) -> None:
        self._spent[agent_id] = self._spent.get(agent_id, 0.0) + amount


def check_policy(
    intent: PaymentIntent, policy: PolicyConfig, ledger: SpendLedger
) -> CheckResult:
    """Run every deterministic rule and return a single, explained result."""

    failures: list[str] = []
    evidence: list[dict] = []

    # Per-transaction limit.
    if intent.amount > policy.per_tx_limit:
        failures.append(
            f"amount {intent.currency} {intent.amount:.2f} exceeds the "
            f"per-transaction limit of {intent.currency} {policy.per_tx_limit:.2f}"
        )
    evidence.append({"rule": "per_tx_limit", "limit": policy.per_tx_limit,
                     "amount": intent.amount})

    # Daily limit, counting what this agent already spent in the run.
    already = ledger.spent_today(intent.agent_id)
    if already + intent.amount > policy.daily_limit:
        failures.append(
            f"this payment would bring today's spend to "
            f"{intent.currency} {already + intent.amount:.2f}, over the daily "
            f"limit of {intent.currency} {policy.daily_limit:.2f}"
        )
    evidence.append({"rule": "daily_limit", "limit": policy.daily_limit,
                     "spent_before": already, "amount": intent.amount})

    # Currency.
    if policy.allowed_currencies and intent.currency not in policy.allowed_currencies:
        failures.append(
            f"currency {intent.currency} is not in the allowed set "
            f"{policy.allowed_currencies}"
        )

    # Category rules.
    cat = (intent.category or "uncategorized").lower()
    blocked = [c.lower() for c in policy.blocked_categories]
    allowed = [c.lower() for c in policy.allowed_categories]
    if cat in blocked:
        failures.append(f"category '{intent.category}' is blocked by policy")
    if allowed and cat not in allowed:
        failures.append(
            f"category '{intent.category}' is not in the allowed set "
            f"{policy.allowed_categories}"
        )

    # Recipient rules.
    rcpt = (intent.recipient or "").lower()
    if any(rcpt == b.lower() for b in policy.recipient_blocklist):
        failures.append(f"recipient '{intent.recipient}' is on the blocklist")
    if policy.recipient_allowlist and not any(
        rcpt == a.lower() for a in policy.recipient_allowlist
    ):
        failures.append(
            f"recipient '{intent.recipient}' is not on the allowlist"
        )

    if failures:
        return CheckResult(
            name="policy",
            passed=False,
            severity=Severity.CRITICAL,
            detail="; ".join(failures),
            risk=1.0,
            evidence=evidence,
        )
    return CheckResult(
        name="policy",
        passed=True,
        severity=Severity.OK,
        detail="within per-transaction, daily, currency, category and recipient rules",
        risk=0.0,
        evidence=evidence,
    )
