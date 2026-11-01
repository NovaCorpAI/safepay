"""Intent and anomaly reasoning (NVIDIA Nemotron on Nebius Token Factory).

The policy engine checks numbers; the merchant check reads the web. This check
reads the *situation*: does paying this recipient, this amount, in this
category, actually serve the task the agent was given? A travel-booking agent
paying a crypto wallet, or a groceries agent sending four figures to a new
payee, is the kind of goal/payment mismatch a static rule set misses. Nemotron
returns a structured verdict with a short reason, which SafePay turns into a
soft risk signal (can raise a HOLD, never a silent block on its own).
"""

from __future__ import annotations

import json

from .models import CheckResult, PaymentIntent, Severity
from .nebius_client import NebiusChat

_SYSTEM = (
    "You are SafePay, a payment-safety reviewer sitting between an autonomous AI "
    "agent and the execution of a real payment. You are given the task the agent "
    "was assigned and the payment it now wants to make. Decide whether the "
    "payment plausibly serves that task and looks non-anomalous. Be strict about "
    "goal/payment mismatches, unusual recipients, and amounts that do not fit the "
    "stated purpose. Respond with STRICT JSON only, no prose, with keys: "
    '"aligned" (boolean), "risk" (number 0 to 1), "reason" (one sentence).'
)


def _prompt(intent: PaymentIntent) -> str:
    return (
        "Task the agent was given:\n"
        f"  {intent.task_goal or '(none provided)'}\n\n"
        "Payment the agent wants to make:\n"
        f"  recipient: {intent.recipient}\n"
        f"  amount: {intent.currency} {intent.amount:.2f}\n"
        f"  category: {intent.category}\n"
        f"  stated purpose: {intent.purpose or '(none)'}\n"
        f"  merchant: {intent.merchant_name or intent.merchant_url or '(unknown)'}\n\n"
        "Return the JSON verdict."
    )


def _parse(raw: str) -> dict:
    """Pull the JSON object out of the model's reply, tolerant of stray text."""
    raw = raw.strip()
    start = raw.find("{")
    end = raw.rfind("}")
    if start != -1 and end != -1 and end > start:
        raw = raw[start : end + 1]
    return json.loads(raw)


def check_reasoning(intent: PaymentIntent, chat: NebiusChat | None) -> CheckResult:
    if chat is None:
        return CheckResult(
            name="reasoning",
            passed=True,
            severity=Severity.INFO,
            detail="reasoning skipped (no Nebius key configured)",
            risk=0.0,
        )

    try:
        raw = chat.complete(_SYSTEM, _prompt(intent), temperature=0.1)
        data = _parse(raw)
    except Exception as exc:  # a reviewer that errors must fail safe, not open.
        return CheckResult(
            name="reasoning",
            passed=False,
            severity=Severity.WARN,
            detail=f"reasoning unavailable ({exc}); recommend human review",
            risk=0.5,
        )

    aligned = bool(data.get("aligned", False))
    risk = float(data.get("risk", 0.0))
    risk = max(0.0, min(1.0, risk))
    reason = str(data.get("reason", "")).strip() or "no reason given"

    passed = aligned and risk < 0.5
    severity = Severity.OK if passed else Severity.WARN
    return CheckResult(
        name="reasoning",
        passed=passed,
        severity=severity,
        detail=reason,
        risk=round(risk, 3),
        evidence=[{"model": chat.model, "aligned": aligned}],
    )
