"""The guardrail: turn a PaymentIntent into a Decision.

Order matters. The deterministic policy runs first and short-circuits to BLOCK,
because a hard limit breach should never spend a model call or a web search.
If policy passes, the two soft checks (merchant web verification and Nemotron
reasoning) run and their risk is combined: a failed soft check downgrades an
otherwise-ALLOW to HOLD when the policy says to (the conservative default), so
a human sees it instead of the money leaving.
"""

from __future__ import annotations

from .models import CheckResult, Decision, PaymentIntent, PolicyConfig, Severity, Verdict
from .nebius_client import NebiusChat
from .policy import SpendLedger, check_policy
from .reason import check_reasoning
from .search import WebSearch
from .verify import check_merchant


class Guardrail:
    def __init__(
        self,
        policy: PolicyConfig,
        web: WebSearch,
        chat: NebiusChat | None,
        ledger: SpendLedger | None = None,
    ) -> None:
        self.policy = policy
        self.web = web
        self.chat = chat
        self.ledger = ledger or SpendLedger()

    def evaluate(self, intent: PaymentIntent) -> Decision:
        checks: list[CheckResult] = []

        # 1. Hard rules first. A failure here is final.
        pol = check_policy(intent, self.policy, self.ledger)
        checks.append(pol)
        if not pol.passed:
            return Decision(
                verdict=Verdict.BLOCK,
                intent=intent,
                checks=checks,
                reasons=[f"policy: {pol.detail}"],
            )

        # 2. Soft signals: live-web merchant check and model reasoning.
        merchant = check_merchant(intent, self.web, self.chat)
        checks.append(merchant)
        reasoning = check_reasoning(intent, self.chat)
        checks.append(reasoning)

        soft_failed = [c for c in (merchant, reasoning) if not c.passed]

        if not soft_failed:
            return Decision(
                verdict=Verdict.ALLOW,
                intent=intent,
                checks=checks,
                reasons=["all checks passed"],
            )

        reasons = [f"{c.name}: {c.detail}" for c in soft_failed]
        # A critical soft finding (rare: reserved for an explicit scam match that
        # a future rule may mark critical) blocks; otherwise hold for a human.
        if any(c.severity == Severity.CRITICAL for c in soft_failed):
            verdict = Verdict.BLOCK
        elif self.policy.hold_on_soft_risk:
            verdict = Verdict.HOLD
        else:
            verdict = Verdict.ALLOW
        return Decision(verdict=verdict, intent=intent, checks=checks, reasons=reasons)

    def commit_spend(self, decision: Decision) -> None:
        """Record approved spend so the daily cap holds across a burst of intents."""
        if decision.verdict == Verdict.ALLOW:
            self.ledger.record(decision.intent.agent_id, decision.intent.amount)
