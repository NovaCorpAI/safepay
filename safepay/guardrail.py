"""The guardrail: turn a PaymentIntent into a Decision.

Order matters. The deterministic policy runs first and short-circuits to BLOCK,
because a hard limit breach should never spend a model call or a web search.
If policy passes, the two soft checks (merchant web verification and Nemotron
reasoning) run and their risk is combined: a failed soft check downgrades an
otherwise-ALLOW to HOLD when the policy says to (the conservative default), so
a human sees it instead of the money leaving.
"""

from __future__ import annotations

from typing import Any

from .control import PAUSED
from .models import CheckResult, Decision, PaymentIntent, PolicyConfig, Severity, Verdict
from .nebius_client import NebiusChat
from .policy import SpendLedger, check_policy, is_known_vendor
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
        control: Any | None = None,
    ) -> None:
        self.policy = policy
        self.web = web
        self.chat = chat
        self.ledger = ledger or SpendLedger()
        # The agent registry (see control.py): a paused agent is refused before
        # any rule runs. None in library use, where there is no operator UI.
        self.control = control

    def evaluate(self, intent: PaymentIntent, policy: PolicyConfig | None = None) -> Decision:
        """Judge one intent. `policy` overrides the guardrail's default for
        this call (the console applies a per-run policy that way)."""
        checks: list[CheckResult] = []
        rules = policy or self.policy

        # 0. Is this agent allowed to ask at all? An operator's pause is the
        # one rule that beats every other: nothing is evaluated, nothing moves.
        if self.control is not None:
            reg = self.control.touch_agent(intent.agent_id)
            if reg.get("status") == PAUSED:
                why = reg.get("reason") or "paused by the operator"
                checks.append(CheckResult(
                    name="agent", passed=False, severity=Severity.CRITICAL,
                    detail=f"agent '{intent.agent_id}' is paused ({why}); nothing is evaluated",
                    risk=1.0, evidence=[{"rule": "agent_paused", "status": PAUSED}],
                ))
                return Decision(verdict=Verdict.BLOCK, intent=intent, checks=checks,
                                reasons=[f"agent: {checks[0].detail}"])

        # 1. Hard rules first. A failure here is final.
        pol = check_policy(intent, rules, self.ledger)
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
        soft = [merchant, reasoning]

        # 2b. Optional deterministic "new payee" control: with a vendor
        # directory and hold_unknown_vendors on, a payee outside the directory
        # is held for a human regardless of what the web and the model say.
        if rules.hold_unknown_vendors and rules.vendors \
                and not is_known_vendor(intent, rules):
            who = intent.merchant_name or intent.recipient
            vendor = CheckResult(
                name="vendor", passed=False, severity=Severity.WARN,
                detail=f"'{who}' is not in the approved vendor directory; new payee held for a human",
                risk=0.6, evidence=[{"rule": "hold_unknown_vendors"}],
            )
            checks.append(vendor)
            soft.append(vendor)

        # 2c. "Ask me before anything over X": inside the hard cap, but big
        # enough that the operator wants a tap. Deterministic, like 2b.
        if rules.hold_above and intent.amount > rules.hold_above:
            ask = CheckResult(
                name="approval", passed=False, severity=Severity.WARN,
                detail=(f"amount {intent.currency} {intent.amount:.2f} is over the ask-a-human "
                        f"threshold of {intent.currency} {rules.hold_above:.2f}"),
                risk=0.5, evidence=[{"rule": "hold_above", "threshold": rules.hold_above}],
            )
            checks.append(ask)
            soft.append(ask)

        soft_failed = [c for c in soft if not c.passed]

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
        elif rules.hold_on_soft_risk or any(c.name == "approval" for c in soft_failed):
            verdict = Verdict.HOLD
        else:
            verdict = Verdict.ALLOW
        return Decision(verdict=verdict, intent=intent, checks=checks, reasons=reasons)

    def commit_spend(self, decision: Decision) -> None:
        """Record auto-approved spend so the caps hold across a burst of intents.

        Only ALLOW decisions are recorded here. A HOLD that a human later
        approves and pays out of band must be fed back with `record_settlement`,
        or the day's real spend is undercounted.
        """
        if decision.verdict == Verdict.ALLOW:
            self.ledger.record(
                decision.intent.agent_id, decision.intent.amount,
                decision.intent.recipient,
            )

    def record_settlement(
        self, agent_id: str, amount: float, recipient: str | None = None
    ) -> None:
        """Record spend an operator released out of band (e.g. after approving a
        HOLD), so the daily and per-recipient caps reflect actual money moved,
        not only what SafePay auto-approved."""
        self.ledger.record(agent_id, amount, recipient)
