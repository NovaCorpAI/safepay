"""Core data types for SafePay.

A SafePay run turns one `PaymentIntent` (what an AI agent wants to pay, and why)
into one `Decision` (allow, hold, or block) backed by the evidence from each
check. Everything the guardrail produces is a plain dataclass so it serializes
cleanly into the signed audit trail and the demo UI.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
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

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


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
        }


@dataclass
class PolicyConfig:
    """The spending rules an operator sets for an agent. Hard limits, not advice."""

    per_tx_limit: float = 500.0
    daily_limit: float = 2000.0
    allowed_categories: list[str] = field(default_factory=list)   # empty = any
    blocked_categories: list[str] = field(default_factory=list)
    recipient_allowlist: list[str] = field(default_factory=list)  # empty = any
    recipient_blocklist: list[str] = field(default_factory=list)
    allowed_currencies: list[str] = field(default_factory=lambda: ["USD"])
    # When true, a Tavily/Nemotron concern downgrades ALLOW to HOLD instead of
    # letting it through. On by default: SafePay is conservative on purpose.
    hold_on_soft_risk: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
