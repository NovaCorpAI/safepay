"""Human-in-the-loop approvals for HOLD decisions.

A HOLD is not a dead end. When the guardrail is unsure (a soft check raised a
concern inside the policy limits), the payment waits here for a person. The
operator approves it, and SafePay releases the payment and charges the ledger
(so the daily and per-recipient caps reflect real spend), or rejects it, and the
money never moves. Either way the resolution is appended to the audit chain as
its own link, so "who approved this agent payment, and when" is provable.

In-memory like the rest of the demo; a deployment backs it with a datastore.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Any

from .models import Decision

PENDING, APPROVED, REJECTED = "pending", "approved", "rejected"


@dataclass
class Approval:
    id: str
    decision: Decision
    created_ts: float
    status: str = PENDING
    resolved_ts: float | None = None
    note: str = ""
    execution: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "status": self.status,
            "created_ts": self.created_ts,
            "resolved_ts": self.resolved_ts,
            "note": self.note,
            "decision": self.decision.to_dict(),
            "execution": self.execution,
        }


class ApprovalQueue:
    def __init__(self, keep: int = 500) -> None:
        self.keep = keep
        self._items: dict[str, Approval] = {}

    def enqueue(self, decision: Decision) -> Approval:
        approval = Approval(id=f"apr-{uuid.uuid4().hex[:10]}", decision=decision,
                            created_ts=time.time())
        self._items[approval.id] = approval
        self._trim()
        return approval

    def get(self, approval_id: str) -> Approval | None:
        return self._items.get(approval_id)

    def pending(self) -> list[Approval]:
        return [a for a in self._items.values() if a.status == PENDING]

    def all(self) -> list[Approval]:
        return list(self._items.values())

    def resolve(self, approval_id: str, status: str, note: str = "",
                execution: dict[str, Any] | None = None) -> Approval:
        approval = self._items.get(approval_id)
        if approval is None:
            raise KeyError(f"unknown approval '{approval_id}'")
        if approval.status != PENDING:
            raise ValueError(f"approval '{approval_id}' is already {approval.status}")
        approval.status = status
        approval.note = note
        approval.execution = execution
        approval.resolved_ts = time.time()
        return approval

    def _trim(self) -> None:
        # Drop the oldest resolved items first; never drop something pending.
        while len(self._items) > self.keep:
            for key, a in self._items.items():
                if a.status != PENDING:
                    del self._items[key]
                    break
            else:
                break
