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

from . import attest, seal
from .approvals import APPROVED, REJECTED, ApprovalQueue
from .auditlog import AuditLog
from .config import Config
from .control import ACTIVE, PAUSED, MemoryControl, run_of
from .guardrail import Guardrail
from .models import CheckResult, Decision, PaymentIntent, PolicyConfig, Severity, Verdict
from .nebius_client import NebiusChat
from .paypal_client import PayPalClient
from .policy_draft import coerce_policy, describe_policy, draft_policy
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

        # State that must outlive a request: the spend ledger, the audit chain
        # and the approval queue. With a Supabase project configured they
        # persist there and are shared by every instance of the service;
        # otherwise they live in memory (local runs, tests), with the audit
        # chain optionally mirrored to the file in SAFEPAY_AUDIT_LOG.
        if self.config.supabase_live:
            from .control import SupabaseControl
            from .store_supabase import (
                PostgREST, SupabaseApprovalQueue, SupabaseAuditLog, SupabaseSpendLedger,
            )
            db = PostgREST(self.config.supabase_url, self.config.supabase_key)
            ledger = SupabaseSpendLedger(db)
            self.audit_log = audit_log or SupabaseAuditLog(db)
            self.approvals = SupabaseApprovalQueue(db)
            self.control = SupabaseControl(db)
            from .cache import SupabaseCache
            self.cache = SupabaseCache(db)
            self.store = "supabase"
        else:
            ledger = None  # Guardrail builds an in-memory SpendLedger
            self.audit_log = audit_log or AuditLog(os.environ.get("SAFEPAY_AUDIT_LOG"))
            self.approvals = ApprovalQueue()
            self.control = MemoryControl()
            from .cache import MemoryCache
            self.cache = MemoryCache()
            self.store = "memory"

        self.guardrail = Guardrail(self.policy, web, chat, ledger=ledger, control=self.control,
                                   cache=self.cache)
        self.paypal = PayPalClient(self.config)
        # One-tap approvals: a HOLD is also pushed to a Telegram chat when
        # configured. Best effort: a chat outage never changes a verdict.
        self.notifier = None
        if self.config.telegram_live:
            from .telegram import TelegramNotifier
            self.notifier = TelegramNotifier(self.config.telegram_token,
                                             self.config.telegram_chat_id)

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
        # The seal: an id in the note PayPal shows the payee, written into
        # this payment's chain link, verifiable at /v/<id> (see seal.py).
        seal_id = seal.new_seal_id(intent.idempotency_key)
        try:
            result = self.paypal.send_payout(
                recipient=intent.recipient,
                amount=intent.amount,
                currency=intent.currency,
                note=seal.seal_note(intent.purpose, seal_id),
                idempotency_key=intent.idempotency_key,
            )
            decision.executed = True
            decision.execution = dict(result, seal=seal_id)
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

    def policy_for(self, agent_id: str) -> PolicyConfig:
        """The policy that applies to an agent: the one its console run set,
        else the default. Falls back to the default if the store is down."""
        run = run_of(agent_id)
        try:
            raw = self.control.policy_for(run) if run else None
        except Exception:
            raw = None
        return PolicyConfig.from_dict(raw) if raw else self.policy

    def pay(self, intent: PaymentIntent) -> dict[str, Any]:
        """Evaluate one intent; execute on ALLOW, queue for a human on HOLD."""
        decision = self.guardrail.evaluate(intent, policy=self.policy_for(intent.agent_id))
        approval_id = None
        if decision.verdict == Verdict.ALLOW:
            self._release(decision)
        elif decision.verdict == Verdict.HOLD:
            approval_id = self.approvals.enqueue(decision).id
            if self.notifier is not None:
                self.notifier.send_hold(approval_id, decision.to_dict())
        record = self.audit_log.append(decision)
        return self._result(decision, record, approval_id)

    # --- operator controls: policy in plain words, freeze / resume an agent --

    def draft_policy(self, text: str, run: str = "") -> dict[str, Any]:
        """Plain words -> a validated policy draft (nothing applied)."""
        base = self.policy_for(f"console:{run}") if run else self.policy
        return draft_policy(text, self.guardrail.chat, base)

    def _policy_link(self, run: str, policy: PolicyConfig, what: str,
                     source: str) -> dict[str, Any]:
        """Chain a policy change. The link carries the full policy and its
        hash; every decision judged under it carries the same hash, so the
        chain proves which rules governed which payment, and when they changed."""
        h = attest.policy_hash(policy)
        rules = describe_policy(policy)
        decision = Decision(
            verdict=Verdict.ALLOW,
            intent=PaymentIntent(recipient=f"run:{run}", amount=0.0, category="policy",
                                 purpose=what, agent_id=f"operator:{run}"),
            checks=[], reasons=[f"{what}: {len(rules)} rules in force, hash {h[:12]}"],
            executed=False,
            execution={"policy_hash": h, "policy": policy.to_dict(), "run": run,
                       "source": source, "rules": rules},
            event="policy", policy_hash=h,
        )
        return self.audit_log.append(decision)

    def apply_policy(self, run: str, raw: dict[str, Any],
                     source: str = "console") -> dict[str, Any]:
        """Validate a policy dict and make it the policy for a console run.
        The change is appended to the audit chain as a "policy" link."""
        if not run:
            raise ValueError("a run id is required")
        policy = coerce_policy(raw, self.policy)
        self.control.set_policy(run, policy.to_dict())
        record = self._policy_link(run, policy, "policy applied", source)
        return {"run": run, "policy": policy.to_dict(), "rules": describe_policy(policy),
                "policy_hash": attest.policy_hash(policy), "audit": record,
                "seq": record["payload"].get("seq")}

    def clear_policy(self, run: str, source: str = "console") -> dict[str, Any]:
        out = {"run": run, "policy": self.policy.to_dict(),
               "rules": describe_policy(self.policy),
               "policy_hash": attest.policy_hash(self.policy)}
        if run and self.control.policy_for(run) is not None:
            self.control.clear_policy(run)
            record = self._policy_link(run, self.policy, "policy reset to default", source)
            out["audit"] = record
            out["seq"] = record["payload"].get("seq")
        return out

    def agents(self, run: str | None = None) -> list[dict[str, Any]]:
        return self.control.agents(run)

    def pause_agent(self, agent_id: str, reason: str = "") -> dict[str, Any]:
        return self.control.set_agent_status(agent_id, PAUSED, reason or "frozen by the operator")

    def resume_agent(self, agent_id: str, reason: str = "") -> dict[str, Any]:
        return self.control.set_agent_status(agent_id, ACTIVE, reason)

    # --- agent keys -------------------------------------------------------

    def issue_key(self, agent_id: str, label: str = "") -> dict[str, Any]:
        """Bind an agent id to a key. Returned once; only its hash is kept."""
        return self.control.issue_key(agent_id, label)

    def revoke_key(self, agent_id: str) -> bool:
        return self.control.revoke_key(agent_id)

    def resolve_agent(self, claimed_agent_id: str, key: str | None) -> str:
        """The agent id a request is allowed to act as. With a key, it is the
        agent the key names, whatever the body claims. Without one, an agent
        that has a key is refused (ValueError); an unregistered id passes."""
        if key:
            bound = self.control.agent_for_key(key)
            if not bound:
                raise ValueError("invalid agent key")
            return bound
        if claimed_agent_id and self.control.has_key(claimed_agent_id):
            raise ValueError(f"agent '{claimed_agent_id}' has a key and must present it "
                             "(Authorization: Bearer spk_...)")
        return claimed_agent_id

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
            policy_hash=held.decision.policy_hash,
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
            policy_hash=held.decision.policy_hash,
        )
        record = self.audit_log.append(decision)
        self.approvals.resolve(approval_id, REJECTED, note)
        return self._result(decision, record, approval_id)

    def pending_approvals(self) -> list[dict[str, Any]]:
        return [a.to_dict() for a in self.approvals.pending()]

    # --- settlement: what PayPal says happened to a released payout ----------

    def _links_for_batch(self, batch_id: str) -> list[dict[str, Any]]:
        out = []
        for rec in self.audit_log.records:
            p = rec.get("payload", {})
            if (p.get("execution") or {}).get("payout_batch_id") == batch_id:
                out.append(p)
        return out

    def settle(self, batch_id: str, source: str = "poll") -> dict[str, Any]:
        """Close the loop on a released payout: ask PayPal for the batch's
        current state and append it to the audit chain as a settlement link.

        A payout is accepted as PENDING and only later succeeds, fails or is
        returned. SUCCESS becomes an ALLOW settlement (money moved); a final
        failure becomes a BLOCK settlement and releases the budget the payout
        had charged (the money never left); anything else is a HOLD (still in
        flight). Idempotent: the same state for the same batch is not appended
        twice. `source` says who asked (console, webhook, mcp, poll)."""
        links = self._links_for_batch(batch_id)
        origin = next((p for p in links if p.get("event", "decision") == "decision"), None)
        if origin is None:
            raise ValueError(f"no released payout '{batch_id}' in this audit log")
        state = self.paypal.get_payout(batch_id)
        status = str(state.get("item_status") or state.get("batch_status") or "PENDING")
        prior = [p for p in links if p.get("event") == "settlement"]
        same = next((p for p in prior
                     if (p.get("execution") or {}).get("item_status") == status), None)
        if same is not None:
            return {"settlement": same["execution"], "status": status,
                    "audit": None, "duplicate": True, "seq": same.get("seq")}

        intent = PaymentIntent.from_dict(origin["intent"])
        if status in self.paypal.FINAL_OK:
            verdict, executed = Verdict.ALLOW, True
            why = f"settlement: payout {status}, money moved ({source})"
        elif status in self.paypal.FINAL_FAILED:
            verdict, executed = Verdict.BLOCK, False
            why = f"settlement: payout {status}, money did not move; budget released ({source})"
            if not any((p.get("execution") or {}).get("item_status") in self.paypal.FINAL_FAILED
                       for p in prior):
                self.guardrail.ledger.record(intent.agent_id, -intent.amount, intent.recipient,
                                             merchant=intent.merchant_name,
                                             invoice_ref=intent.invoice_ref)
        else:
            verdict, executed = Verdict.HOLD, False
            why = f"settlement: payout {status}, still in flight ({source})"
        execution = dict(state, source=source)
        decision = Decision(verdict=verdict, intent=intent, checks=[], reasons=[why],
                            executed=executed, execution=execution, event="settlement")
        record = self.audit_log.append(decision)
        return {"settlement": execution, "status": status, "audit": record,
                "duplicate": False, "seq": record["payload"].get("seq")}

    # --- outside the firewall: money that moved without a SafePay decision --

    def _known_batches(self) -> dict[str, dict[str, Any]]:
        """payout_batch_id -> the chain payload that first recorded it."""
        out: dict[str, dict[str, Any]] = {}
        for rec in self.audit_log.records:
            p = rec.get("payload", {})
            b = (p.get("execution") or {}).get("payout_batch_id")
            if b and b not in out:
                out[b] = p
        return out

    def flag_outside(self, batch_id: str, source: str = "webhook",
                     state: dict[str, Any] | None = None) -> dict[str, Any]:
        """A payout left the PayPal account without passing through SafePay:
        PayPal reported it (webhook) or reconciliation found it. Record it as
        an "outside" link (verdict BLOCK: not authorized by the firewall), and
        alert the operator. Idempotent per batch."""
        known = self._known_batches()
        if batch_id in known:
            p = known[batch_id]
            return {"outside": p.get("event") == "outside", "duplicate": True,
                    "seq": p.get("seq"), "batch": batch_id}
        state = state or self.paypal.get_payout(batch_id)
        status = str(state.get("item_status") or state.get("batch_status") or "PENDING")
        amount = float(state.get("amount") or 0.0)
        receiver = state.get("receiver") or "unknown recipient"
        why = (f"payout {batch_id} left the PayPal account without a SafePay decision "
               f"(reported by {source}; PayPal status {status})")
        check = CheckResult(name="firewall", passed=False, severity=Severity.CRITICAL,
                            detail=why, risk=1.0,
                            evidence=[{"rule": "outside_firewall", "source": source,
                                       "payout_batch_id": batch_id, "status": status}])
        decision = Decision(
            verdict=Verdict.BLOCK,
            intent=PaymentIntent(recipient=receiver, amount=amount,
                                 currency=state.get("currency") or "USD",
                                 category="outside", purpose=state.get("note") or "",
                                 agent_id="outside-safepay"),
            checks=[check], reasons=[f"firewall: {why}"],
            executed=status in self.paypal.FINAL_OK,
            execution=dict(state, payout_batch_id=batch_id, source=source, outside=True),
            event="outside", policy_hash=attest.policy_hash(self.policy),
        )
        record = self.audit_log.append(decision)
        if self.notifier is not None:
            self.notifier.send_alert(
                f"SafePay ALERT · money moved outside the firewall\n"
                f"{decision.intent.currency} {amount:.2f} to {receiver}\n"
                f"Payout {batch_id}, PayPal status {status}, reported by {source}.\n"
                f"No agent asked SafePay for this payment. Chain link #{record['payload'].get('seq')}.")
        return {"outside": True, "duplicate": False, "seq": record["payload"].get("seq"),
                "batch": batch_id, "audit": record, "status": status, "amount": amount,
                "receiver": receiver}

    def reconcile(self, hours: float = 24.0, source: str = "reconcile") -> dict[str, Any]:
        """Compare PayPal's own transaction list with the chain: every debit
        PayPal shows that no SafePay decision released is flagged as outside
        the firewall. Needs Transaction Search enabled for the app."""
        import datetime as _dt
        end = _dt.datetime.now(_dt.timezone.utc)
        start = end - _dt.timedelta(hours=max(1.0, float(hours)))
        fmt = "%Y-%m-%dT%H:%M:%S-0000"
        txs = self.paypal.list_transactions(start.strftime(fmt), end.strftime(fmt))
        # Transaction ids SafePay knows: settlements record them; for released
        # payouts not yet settled, ask PayPal once per batch.
        known_tx: set[str] = set()
        for batch, p in self._known_batches().items():
            tid = (p.get("execution") or {}).get("transaction_id")
            if not tid:
                try:
                    tid = self.paypal.get_payout(batch).get("transaction_id")
                except Exception:
                    tid = None
            if tid:
                known_tx.add(str(tid))
        debits = [t for t in txs if t["amount"] < 0 and str(t.get("event_code") or "").startswith("T00")]
        outside = []
        for t in debits:
            if str(t.get("transaction_id")) in known_tx:
                continue
            state = {"item_status": t.get("status"), "batch_status": t.get("status"),
                     "transaction_id": t.get("transaction_id"), "amount": -t["amount"],
                     "currency": t.get("currency"), "receiver": t.get("counterparty"),
                     "note": t.get("subject"), "time_processed": t.get("time"), "mode": "sandbox"}
            res = self.flag_outside(f"tx:{t.get('transaction_id')}", source, state)
            outside.append({"transaction_id": t.get("transaction_id"), "amount": -t["amount"],
                            "counterparty": t.get("counterparty"), "seq": res.get("seq"),
                            "duplicate": res.get("duplicate")})
        return {"window_hours": hours, "transactions": len(txs), "debits": len(debits),
                "known": len(known_tx), "outside": outside}

    def pay_many(self, intents: list[PaymentIntent]) -> list[dict[str, Any]]:
        """Run a sequence, so the daily cap is enforced across the burst."""
        return [self.pay(i) for i in intents]
