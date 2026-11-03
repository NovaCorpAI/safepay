"""Deterministic policy checks.

These are the hard rules an operator sets: spending limits, allowed categories,
recipient allow/block lists, currency. They are pure and fast, run first, and a
failure here is a hard BLOCK that no amount of model reasoning can override.
The spend ledger tracks what an agent has already paid today, so the daily cap
is enforced across a sequence of intents, not just one in isolation.
"""

from __future__ import annotations

import time

from .models import CheckResult, PaymentIntent, PolicyConfig, Severity


class SpendLedger:
    """In-memory tally of approved spend per agent (per process run).

    Tracks total spend per agent, spend per (agent, recipient) for the
    per-recipient cap, and payment timestamps per agent for the velocity rule.
    A real deployment would back this with a datastore; for the hackathon demo
    it lives for the length of the run, which is enough to show the caps
    stopping a payment in a burst.
    """

    def __init__(self) -> None:
        self._spent: dict[str, float] = {}
        self._by_recipient: dict[tuple[str, str], float] = {}
        self._events: dict[str, list[float]] = {}

    def spent_today(self, agent_id: str) -> float:
        return self._spent.get(agent_id, 0.0)

    def spent_recipient(self, agent_id: str, recipient: str | None) -> float:
        return self._by_recipient.get((agent_id, (recipient or "").lower()), 0.0)

    def recent_count(self, agent_id: str, window_seconds: int, now: float | None = None) -> int:
        now = time.time() if now is None else now
        return sum(1 for t in self._events.get(agent_id, []) if now - t <= window_seconds)

    def record(
        self, agent_id: str, amount: float, recipient: str | None = None,
        ts: float | None = None,
    ) -> None:
        self._spent[agent_id] = self._spent.get(agent_id, 0.0) + amount
        key = (agent_id, (recipient or "").lower())
        self._by_recipient[key] = self._by_recipient.get(key, 0.0) + amount
        if amount > 0:   # a released budget (failed payout) is not a payment event
            self._events.setdefault(agent_id, []).append(time.time() if ts is None else ts)


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

    # Cumulative per-recipient cap.
    if policy.per_recipient_limit > 0:
        to_recipient = ledger.spent_recipient(intent.agent_id, intent.recipient)
        if to_recipient + intent.amount > policy.per_recipient_limit:
            failures.append(
                f"this payment would bring spend to '{intent.recipient}' to "
                f"{intent.currency} {to_recipient + intent.amount:.2f}, over the "
                f"per-recipient limit of {intent.currency} "
                f"{policy.per_recipient_limit:.2f}"
            )
        evidence.append({"rule": "per_recipient_limit",
                         "limit": policy.per_recipient_limit,
                         "spent_before": to_recipient, "amount": intent.amount})

    # Velocity: too many payments too fast.
    if policy.max_payments_per_window > 0:
        recent = ledger.recent_count(intent.agent_id, policy.velocity_window_seconds)
        if recent + 1 > policy.max_payments_per_window:
            failures.append(
                f"this is payment {recent + 1} within "
                f"{policy.velocity_window_seconds}s, over the velocity limit of "
                f"{policy.max_payments_per_window}"
            )
        evidence.append({"rule": "velocity",
                         "limit": policy.max_payments_per_window,
                         "recent": recent,
                         "window_seconds": policy.velocity_window_seconds})

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

    # Vendor directory: lookalike recipient and amount anomaly.
    failures += check_vendor(intent, policy, evidence)

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
        detail="within per-transaction, daily, currency, category, recipient and vendor rules",
        risk=0.0,
        evidence=evidence,
    )


def domain_of(address: str | None) -> str:
    """The lowercase domain of an email ('billing@notion.so' -> 'notion.so')
    or a URL ('https://www.notion.so/x' -> 'notion.so'); '' when unknown."""
    s = (address or "").strip().lower()
    if not s:
        return ""
    if "@" in s:
        s = s.rsplit("@", 1)[1]
    elif "://" in s:
        s = s.split("://", 1)[1]
    s = s.split("/", 1)[0].split(":", 1)[0]
    return s[4:] if s.startswith("www.") else s


def registrable(domain: str) -> str:
    """The part of a domain a registrant actually owns, so 'cloud.google.com'
    and 'google.com' compare equal while 'notion-payments.example' and
    'notion.so' do not. Two labels, three under a country second-level like
    'co.uk' or 'com.br'."""
    parts = [p for p in domain.lower().split(".") if p]
    if len(parts) >= 3 and parts[-2] in ("co", "com", "org", "net", "gov", "ac", "edu") \
            and len(parts[-1]) == 2:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:]) if len(parts) >= 2 else domain.lower()


def _vendor_for(intent: PaymentIntent, policy: PolicyConfig) -> tuple[str, dict] | None:
    name = (intent.merchant_name or "").strip().lower()
    if not name:
        return None
    for key, spec in policy.vendors.items():
        names = {key.strip().lower()} | {a.strip().lower() for a in (spec or {}).get("aliases", [])}
        if name in names:
            return key, spec or {}
    return None


def is_known_vendor(intent: PaymentIntent, policy: PolicyConfig) -> bool:
    """True when the merchant name is in the directory, or the recipient's
    domain belongs to a directory vendor (a known domain with no merchant
    name stated is still a known payee)."""
    if _vendor_for(intent, policy):
        return True
    rd = registrable(domain_of(intent.recipient))
    return any(rd and rd == registrable(domain_of(d))
               for spec in policy.vendors.values() for d in (spec or {}).get("domains", []))


def check_vendor(intent: PaymentIntent, policy: PolicyConfig,
                 evidence: list[dict]) -> list[str]:
    """Deterministic rules against the approved vendor directory and the
    stated merchant URL. Both attacks these catch stay inside every cap:

    - lookalike recipient: the intent names a real vendor but pays a domain
      that is not that vendor's (billing@notion-payments.example for Notion);
    - amount anomaly: a real vendor, real domain, but an amount far above
      what the operator expects to pay it (USD 480 for a USD 48 plan).
    """
    failures: list[str] = []
    rcpt_dom = domain_of(intent.recipient)
    found = _vendor_for(intent, policy)
    if found:
        key, spec = found
        domains = [registrable(domain_of(d)) for d in spec.get("domains", []) if d]
        if domains and rcpt_dom and registrable(rcpt_dom) not in domains:
            failures.append(
                f"recipient domain '{rcpt_dom}' is not an approved domain for "
                f"vendor '{key}' ({', '.join(domains)}); possible lookalike payee"
            )
        expected = spec.get("expected_amount")
        if expected:
            ceiling = float(expected) * policy.vendor_amount_tolerance
            if intent.amount > ceiling:
                failures.append(
                    f"amount {intent.currency} {intent.amount:.2f} is "
                    f"{intent.amount / float(expected):.1f}x the expected "
                    f"{intent.currency} {float(expected):.2f} for vendor '{key}' "
                    f"(tolerance {policy.vendor_amount_tolerance:g}x)"
                )
        evidence.append({"rule": "vendor_directory", "vendor": key, "domains": domains,
                         "expected_amount": expected, "recipient_domain": rcpt_dom})
    # Independently of the directory: a stated merchant URL must agree with
    # the recipient's domain. A real vendor's site with a different payee
    # domain is the lookalike pattern again.
    url_dom = domain_of(intent.merchant_url)
    if url_dom and rcpt_dom and "@" in (intent.recipient or "") \
            and registrable(url_dom) != registrable(rcpt_dom):
        failures.append(
            f"recipient domain '{rcpt_dom}' does not match the merchant site "
            f"'{url_dom}'; possible lookalike payee"
        )
        evidence.append({"rule": "merchant_url_match", "merchant_domain": url_dom,
                         "recipient_domain": rcpt_dom})
    return failures
