"""The autonomous payment agent, wrapped by SafePay.

This is the piece a hackathon judge runs: give it a payment intent and it does
the whole loop, guardrail first. On ALLOW it releases the payment through the
PayPal sandbox (or the simulated client) and records a signed audit entry; on
HOLD or BLOCK it records the decision and the money never moves. The return is
always a decision plus its audit record, so the UI and the CLI render the same
truth.
"""

from __future__ import annotations

import os
from typing import Any

from . import attest
from .approvals import APPROVED, REJECTED, ApprovalQueue
from .auditlog import AuditLog
from .config import Config
from .guardrail import Guardrail
from .models import Decision, PaymentIntent, PolicyConfig, Verdict
from .nebius_client import NebiusChat
from .paypal_client import PayPalClient
from .search import WebSearch


class SafePayAgent:
    def __init__(
        self,
        config: Config | None = None,
        policy: PolicyConfig | None = None,
        audit_log: AuditLog | None = None,
    ):
        self.config = config or Config.from_env()
        self.policy = policy or PolicyConfig()
        web = WebSearch(self.config.tavily_api_key)
        chat = NebiusChat(self.config) if self.config.nebius_live else None
        self.guardrail = Guardrail(self.policy, web, chat)
        self.paypal = PayPalClient(self.config)
        # Every decision is appended to a hash-chained audit log. A given log is
        # used if passed; otherwise one is built, backed by the file in
        # SAFEPAY_AUDIT_LOG when that is set, or in-memory only.
        self.audit_log = audit_log or AuditLog(os.environ.get("SAFEPAY_AUDIT_LOG"))
        # HOLD decisions wait here for a human; see approve()/reject().
        self.approvals = ApprovalQueue()

    def _release(self, decision: Decision) -> None:
        """Execute an ALLOW decision's payment and charge the ledger."""
        intent = decision.intent
        if not self.config.execute_payments:
            # Evaluation-only instance: do not move money, but still charge
            # the budget so the caps behave as they would in production.
            decision.executed = False
            decision.execution = {
                "mode": "disabled",
                "note": "payment execution disabled on this instance "
                        "(SAFEPAY_EXECUTE off)",
            }
            self.guardrail.commit_spend(decision)
            return
        try:
            result = self.paypal.send_payout(
                recipient=intent.recipient,
                amount=intent.amount,
                currency=intent.currency,
                note=intent.purpose,
                idempotency_key=intent.idempotency_key,
            )
            decision.executed = True
            decision.execution = result
            self.guardrail.commit_spend(decision)
        except Exception as exc:
            decision.executed = False
            decision.execution = {"error": str(exc)}
            decision.reasons.append(f"payout error: {exc}")

    def _result(self, decision: Decision, record: dict[str, Any],
                approval_id: str | None = None) -> dict[str, Any]:
        return {
            "decision": decision.to_dict(),
            "audit": record,
            "stamp": attest.stamp(decision),
            "paypal_mode": self.paypal.mode,
            "approval_id": approval_id,
        }

    def pay(self, intent: PaymentIntent) -> dict[str, Any]:
        """Evaluate one intent; execute on ALLOW, queue for a human on HOLD."""
        decision = self.guardrail.evaluate(intent)
        approval_id = None
        if decision.verdict == Verdict.ALLOW:
            self._release(decision)
        elif decision.verdict == Verdict.HOLD:
            approval_id = self.approvals.enqueue(decision).id
        record = self.audit_log.append(decision)
        return self._result(decision, record, approval_id)

    def approve(self, approval_id: str, note: str = "") -> dict[str, Any]:
        """A human releases a held payment: execute it, charge the ledger, and
        append the approval to the audit chain as its own link."""
        held = self.approvals.get(approval_id)
        if held is None or held.status != "pending":
            raise ValueError(f"no pending approval '{approval_id}'")
        why = "approved by human" + (f": {note}" if note else "")
        decision = Decision(
            verdict=Verdict.ALLOW, intent=held.decision.intent,
            checks=list(held.decision.checks),
            reasons=list(held.decision.reasons) + [why],
        )
        self._release(decision)
        record = self.audit_log.append(decision)
        self.approvals.resolve(approval_id, APPROVED, note, decision.execution)
        return self._result(decision, record, approval_id)

    def reject(self, approval_id: str, note: str = "") -> dict[str, Any]:
        """A human refuses a held payment: nothing moves; the refusal is
        appended to the audit chain."""
        held = self.approvals.get(approval_id)
        if held is None or held.status != "pending":
            raise ValueError(f"no pending approval '{approval_id}'")
        why = "rejected by human" + (f": {note}" if note else "")
        decision = Decision(
            verdict=Verdict.BLOCK, intent=held.decision.intent,
            checks=list(held.decision.checks),
            reasons=list(held.decision.reasons) + [why],
        )
        record = self.audit_log.append(decision)
        self.approvals.resolve(approval_id, REJECTED, note)
        return self._result(decision, record, approval_id)

    def pending_approvals(self) -> list[dict[str, Any]]:
        return [a.to_dict() for a in self.approvals.pending()]

    def pay_many(self, intents: list[PaymentIntent]) -> list[dict[str, Any]]:
        """Run a sequence, so the daily cap is enforced across the burst."""
        return [self.pay(i) for i in intents]
