"""Core data types for SafePay.

A SafePay run turns one `PaymentIntent` (what an AI agent wants to pay, and why)
into one `Decision` (allow, hold, or block) backed by the evidence from each
check. Everything the guardrail produces is a plain dataclass so it serializes
cleanly into the signed audit trail and the demo UI.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields, asdict
from enum import Enum
from typing import Any


class Verdict(str, Enum):
    ALLOW = "allow"   # every check passed; release the payment
    HOLD = "hold"     # something is uncertain; needs a human to approve
    BLOCK = "block"   # a hard rule failed; never release


class Severity(str, Enum):
    OK = "ok"
    INFO = "info"
    WARN = "warn"
    CRITICAL = "critical"


@dataclass
class PaymentIntent:
    """What an agent is asking to pay, and the context the guardrail judges."""

    recipient: str                 # payout email / merchant id in the PayPal sandbox
    amount: float
    currency: str = "USD"
    purpose: str = ""              # the agent's stated reason for this payment
    category: str = "uncategorized"
    merchant_name: str | None = None
    merchant_url: str | None = None
    agent_id: str = "agent-demo"
    # The task the agent was actually given, so the reasoner can check that the
    # payment matches the goal (a travel agent paying a casino is a mismatch).
    task_goal: str = ""
    # Optional idempotency key for the execution. Set it per logical payment and
    # a retry of the same intent will not send a second payout (it rides the
    # PayPal-Request-Id header and a stable batch id). Left unset, each
    # execution gets a fresh id, as before.
    idempotency_key: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "PaymentIntent":
        names = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in names})


@dataclass
class CheckResult:
    """The outcome of one guardrail check."""

    name: str
    passed: bool
    severity: Severity
    detail: str
    # 0.0 (no concern) .. 1.0 (maximum concern), for ranking and display.
    risk: float = 0.0
    evidence: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["severity"] = self.severity.value
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "CheckResult":
        return cls(
            name=d["name"], passed=bool(d["passed"]),
            severity=Severity(d.get("severity", "info")), detail=d.get("detail", ""),
            risk=float(d.get("risk", 0.0)), evidence=list(d.get("evidence") or []),
        )


@dataclass
class Decision:
    """The aggregate verdict over all checks for one PaymentIntent."""

    verdict: Verdict
    intent: PaymentIntent
    checks: list[CheckResult] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    # Filled in after the payment is (or is not) executed.
    executed: bool = False
    execution: dict[str, Any] | None = None
    # What kind of link this is in the audit chain: a guardrail "decision"
    # (the default), or a "settlement", the later record of what PayPal says
    # happened to a released payout (succeeded, failed, returned).
    event: str = "decision"
    # The fingerprint (sha256) of the policy this decision was judged under.
    # Applying a policy writes a "policy" link with the same hash, so the chain
    # proves which rules governed every payment.
    policy_hash: str = ""

    @property
    def risk(self) -> float:
        """The highest single-check risk score drives the headline number."""
        return max((c.risk for c in self.checks), default=0.0)

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict.value,
            "risk": round(self.risk, 3),
            "intent": self.intent.to_dict(),
            "checks": [c.to_dict() for c in self.checks],
            "reasons": list(self.reasons),
            "executed": self.executed,
            "execution": self.execution,
            "event": self.event,
            "policy_hash": self.policy_hash,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Decision":
        return cls(
            verdict=Verdict(d["verdict"]),
            intent=PaymentIntent.from_dict(d["intent"]),
            checks=[CheckResult.from_dict(c) for c in d.get("checks") or []],
            reasons=list(d.get("reasons") or []),
            executed=bool(d.get("executed", False)),
            execution=d.get("execution"),
            event=str(d.get("event") or "decision"),
            policy_hash=str(d.get("policy_hash") or ""),
        )


@dataclass
class PolicyConfig:
    """The spending rules an operator sets for an agent. Hard limits, not advice."""

    per_tx_limit: float = 500.0
    daily_limit: float = 2000.0
    # Cumulative cap per recipient across the run (0 = no per-recipient cap).
    # Blunts a slow-drain attack that stays under the per-transaction limit but
    # funnels many payments to one payee.
    per_recipient_limit: float = 0.0
    # Velocity: at most this many payments inside the window (0 = no limit).
    max_payments_per_window: int = 0
    velocity_window_seconds: int = 3600
    allowed_categories: list[str] = field(default_factory=list)   # empty = any
    blocked_categories: list[str] = field(default_factory=list)
    recipient_allowlist: list[str] = field(default_factory=list)  # empty = any
    recipient_blocklist: list[str] = field(default_factory=list)
    allowed_currencies: list[str] = field(default_factory=lambda: ["USD"])
    # When true, a Tavily/Nemotron concern downgrades ALLOW to HOLD instead of
    # letting it through. On by default: SafePay is conservative on purpose.
    hold_on_soft_risk: bool = True
    # Approved vendor directory, keyed by merchant name (case-insensitive):
    # what a payment to that vendor must look like. Catches the two attacks
    # that stay inside every cap: a lookalike recipient (real vendor name,
    # swapped domain) and an inflated amount to a real vendor.
    #   {"Notion": {"domains": ["notion.so"], "expected_amount": 48,
    #               "aliases": ["Notion Labs"]}}
    vendors: dict[str, dict[str, Any]] = field(default_factory=dict)
    # An amount above expected_amount * this factor is an anomaly.
    vendor_amount_tolerance: float = 1.5
    # With a directory in place, hold any payee that is not in it for a human
    # (the "new beneficiary" control banks apply). Off by default so the soft
    # checks are what judges an unknown payee; on for a locked-down agent.
    hold_unknown_vendors: bool = False
    # "Ask me before anything over X": a payment above this amount (and still
    # inside per_tx_limit) is held for a human even when every check passes.
    # 0 = off. Small routine payments go through; big ones wait for a tap.
    hold_above: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "PolicyConfig":
        names = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in (d or {}).items() if k in names})
