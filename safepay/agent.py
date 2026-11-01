"""The autonomous payment agent, wrapped by SafePay.

This is the piece a hackathon judge runs: give it a payment intent and it does
the whole loop, guardrail first. On ALLOW it releases the payment through the
PayPal sandbox (or the simulated client) and records a signed audit entry; on
HOLD or BLOCK it records the decision and the money never moves. The return is
always a decision plus its audit record, so the UI and the CLI render the same
truth.
"""

from __future__ import annotations

from typing import Any

from . import attest
from .config import Config
from .guardrail import Guardrail
from .models import Decision, PaymentIntent, PolicyConfig, Verdict
from .nebius_client import NebiusChat
from .paypal_client import PayPalClient
from .search import WebSearch


class SafePayAgent:
    def __init__(self, config: Config | None = None, policy: PolicyConfig | None = None):
        self.config = config or Config.from_env()
        self.policy = policy or PolicyConfig()
        web = WebSearch(self.config.tavily_api_key)
        chat = NebiusChat(self.config) if self.config.nebius_live else None
        self.guardrail = Guardrail(self.policy, web, chat)
        self.paypal = PayPalClient(self.config)

    def pay(self, intent: PaymentIntent) -> dict[str, Any]:
        """Evaluate one intent and, if allowed, execute the payout."""
        decision = self.guardrail.evaluate(intent)

        if decision.verdict == Verdict.ALLOW:
            try:
                result = self.paypal.send_payout(
                    recipient=intent.recipient,
                    amount=intent.amount,
                    currency=intent.currency,
                    note=intent.purpose,
                )
                decision.executed = True
                decision.execution = result
                self.guardrail.commit_spend(decision)
            except Exception as exc:
                decision.executed = False
                decision.execution = {"error": str(exc)}
                decision.reasons.append(f"payout error: {exc}")

        record = attest.audit_record(decision)
        return {
            "decision": decision.to_dict(),
            "audit": record,
            "stamp": attest.stamp(decision),
            "paypal_mode": self.paypal.mode,
        }

    def pay_many(self, intents: list[PaymentIntent]) -> list[dict[str, Any]]:
        """Run a sequence, so the daily cap is enforced across the burst."""
        return [self.pay(i) for i in intents]
