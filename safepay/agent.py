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

from . import attest, mandate, opsig, payables, seal
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
        pol = PolicyConfig.from_dict(raw) if raw else self.policy
        # A session whose policy was signed by an operator key is pinned to
        # that key: the stored rules are re-verified every time they are used.
        # If they no longer verify (edited in the store, signature stripped,
        # row deleted) the guardrail blocks until the operator signs again.
        pin = self._pin(run)
        if pin:
            why = opsig.check_stored((raw or {}).get("_sig"), run, attest.policy_hash(pol), pin["kid"])
            if why:
                pol = PolicyConfig.from_dict(pol.to_dict())
                pol._tamper = why  # type: ignore[attr-defined]
        return pol

    def _pin(self, run: str) -> dict[str, Any] | None:
        if not run:
            return None
        try:
            pin = self.control.setting(f"opkey:{run}")
        except Exception:
            return None
        return pin if isinstance(pin, dict) and pin.get("kid") else None

    def _sign_check(self, run: str, h: str, signature: dict[str, Any] | None) -> dict[str, Any] | None:
        """Verify an operator signature for (run, hash) against the pin.
        Returns the sealed signature (or None when unsigned and unpinned).
        Pins the key on the first signed policy of a session."""
        pin = self._pin(run)
        if signature:
            sealed = opsig.seal_policy(run, h, signature)
            if pin and sealed["kid"] != pin["kid"]:
                raise PermissionError(f"this session's policy is pinned to operator key {pin['kid']}; "
                                      f"key {sealed['kid']} cannot change it")
            if not pin:
                self.control.set_setting(f"opkey:{run}", {"kid": sealed["kid"], "jwk": sealed["jwk"]})
                sealed = dict(sealed, pinned_now=True)
            return sealed
        if pin:
            raise PermissionError(f"this session's policy is signed by operator key {pin['kid']}; "
                                  "an unsigned change is refused")
        return None

    def policy_status(self, run: str) -> dict[str, Any]:
        """Signed by whom, pinned to whom, and whether it still verifies."""
        pin = self._pin(run)
        pol = self.policy_for(f"console:{run}") if run else self.policy
        raw = (self.control.policy_for(run) if run else None) or {}
        sig = raw.get("_sig") or {}
        return {"pinned": pin["kid"] if pin else None, "signed_by": sig.get("kid"),
                "tamper": getattr(pol, "_tamper", None)}

    def pay(self, intent: PaymentIntent,
            extra_checks: list[CheckResult] | None = None) -> dict[str, Any]:
        """Evaluate one intent; execute on ALLOW, queue for a human on HOLD.

        `extra_checks` are failed findings a caller adds (the payables
        agent's invoice flags): they can turn an ALLOW into a HOLD, never
        loosen a HOLD or a BLOCK."""
        decision = self.guardrail.evaluate(intent, policy=self.policy_for(intent.agent_id))
        flagged = [c for c in (extra_checks or []) if not c.passed]
        if flagged and decision.verdict != Verdict.BLOCK:
            decision.checks.extend(flagged)
            if decision.verdict == Verdict.ALLOW:
                decision.verdict = Verdict.HOLD
                decision.reasons = []
            decision.reasons += [f"{c.name}: {c.detail}" for c in flagged]
        approval_id = None
        if decision.verdict == Verdict.ALLOW:
            self._release(decision)
        elif decision.verdict == Verdict.HOLD:
            approval_id = self.approvals.enqueue(decision).id
            if self.notifier is not None:
                self.notifier.send_hold(approval_id, decision.to_dict())
        record = self.audit_log.append(decision)
        return self._result(decision, record, approval_id)

    # --- SafePay's own payables agent: invoice text in, judged payment out --

    def read_invoice(self, text: str) -> dict[str, Any]:
        """Read an invoice into payment fields (nothing is paid)."""
        return payables.read_invoice(text, self.guardrail.chat)

    def pay_invoice(self, text: str, agent_id: str = "payables-agent",
                    task_goal: str = "") -> dict[str, Any]:
        """Read an invoice and pay it through the guardrail like any agent:
        same policy, same history rules, same checks, same seal. The reader's
        flags (instructions in the invoice, a bank-details change) hold it."""
        read = self.read_invoice(text)
        intent = payables.to_intent(read, agent_id, task_goal)
        out = self.pay(intent, extra_checks=payables.flag_checks(read))
        out["invoice"] = read
        return out

    # --- mandates: authorize a budget, capture what the task costs ----------

    def open_mandate(self, intent: PaymentIntent, ap2_intent: dict[str, Any] | None = None,
                     cart: dict[str, Any] | None = None) -> dict[str, Any]:
        """Judge a ceiling like any payment; on ALLOW authorize it with
        PayPal. The ceiling counts against the daily cap from now on.
        With an AP2 IntentMandate signed by the operator (and optionally the
        store's cart), both are verified first and a failure blocks."""
        from . import ap2 as ap2mod
        decision = self.guardrail.evaluate(intent, policy=self.policy_for(intent.agent_id))
        decision.event = "authorize"
        extra: dict[str, Any] = {}
        mandate_checks: list[CheckResult] = []
        if ap2_intent is not None:
            pin = self._pin(run_of(intent.agent_id))
            try:
                v = ap2mod.verify_intent(ap2_intent, intent.recipient, (pin or {}).get("kid"))
                extra["ap2"] = v
                mandate_checks.append(CheckResult(
                    "ap2_intent", True, Severity.INFO,
                    f"AP2 IntentMandate signed by operator key {v['kid']}"
                    f"{' (the pinned key)' if v['pinned'] else ''}; payee is one of its merchants; "
                    f"valid until {v['intent_mandate']['intent_expiry']}"))
            except ap2mod.MandateError as exc:
                mandate_checks.append(CheckResult("ap2_intent", False, Severity.CRITICAL, str(exc), 1.0,
                                                  [{"rule": "ap2_intent"}]))
        if cart is not None:
            try:
                extra["ap2_cart"] = ap2mod.cart_mandate(cart, intent.recipient, intent.amount)
                c = extra["ap2_cart"]["contents"]
                mandate_checks.append(CheckResult(
                    "ap2_cart", True, Severity.INFO,
                    f"AP2 CartMandate {c['id']} from {c['merchant_name']}: its total is the ceiling authorized"))
            except ap2mod.MandateError as exc:
                mandate_checks.append(CheckResult("ap2_cart", False, Severity.CRITICAL, str(exc), 1.0,
                                                  [{"rule": "ap2_cart"}]))
        if mandate_checks:
            decision.checks = mandate_checks + list(decision.checks)
            failed = [c for c in mandate_checks if not c.passed]
            if failed:
                decision.verdict = Verdict.BLOCK
                decision.reasons = [f"{c.name}: {c.detail}" for c in failed] + list(decision.reasons)
        if decision.verdict == Verdict.ALLOW:
            if not self.config.execute_payments:
                decision.execution = {"mode": "disabled",
                                      "note": "payment execution disabled on this instance"}
            else:
                try:
                    card = self._vaulted_card()
                    res = self.paypal.authorize(intent.amount, intent.currency,
                                                intent.purpose or intent.task_goal,
                                                intent.idempotency_key,
                                                vault_id=(card or {}).get("vault_id"))
                    if card:
                        res = dict(res, card={"brand": card.get("brand"),
                                              "last_digits": card.get("last_digits")})
                    decision.executed = True
                    decision.execution = dict(res, method="authorize", **extra)
                    self.guardrail.commit_spend(decision)
                except Exception as exc:
                    decision.execution = {"error": str(exc)}
                    decision.reasons.append(f"authorize error: {exc}")
        elif decision.verdict == Verdict.HOLD:
            decision.reasons.append("mandate not opened: a person must look at it first "
                                    "(lower the ceiling, or raise the policy and ask again)")
        record = self.audit_log.append(decision)
        out = self._result(decision, record)
        auth = (decision.execution or {}).get("authorization_id")
        out["mandate"] = mandate.state(self.audit_log, auth) if decision.executed and auth else None
        return out

    def _vaulted_card(self) -> dict[str, Any] | None:
        """The agent card saved in PayPal's Vault, created on first use and
        kept in the control store. None if the Vault is not available (then
        the mandate is authorized with the card itself)."""
        key = f"paypal:vault:{self.paypal.mode}"
        try:
            saved = self.control.setting(key)
            if isinstance(saved, dict) and saved.get("vault_id"):
                return saved
            card = self.paypal.vault_card()
            self.control.set_setting(key, card)
            return card
        except Exception:
            return None

    def settle_payment_event(self, event_type: str, resource: dict[str, Any],
                             source: str = "webhook") -> dict[str, Any]:
        """A PayPal capture or authorization event for a mandate. The state
        is re-read from PayPal (never trusted from the event) and chained as
        a settlement link of the mandate. A capture of an authorization
        SafePay never opened is money that moved outside the firewall."""
        is_capture = event_type.startswith("PAYMENT.CAPTURE.")
        rid = str(resource.get("id") or "")
        if not rid:
            raise ValueError("event without a resource id")
        body = self.paypal.get_json(("/v2/payments/captures/" if is_capture
                                     else "/v2/payments/authorizations/") + rid)
        status = str(body.get("status") or "")
        amt = body.get("amount") or {}
        auth_id = rid if not is_capture else str(
            ((body.get("supplementary_data") or {}).get("related_ids") or {}).get("authorization_id")
            or ((resource.get("supplementary_data") or {}).get("related_ids") or {}).get("authorization_id")
            or "")
        st = mandate.state(self.audit_log, auth_id) if auth_id else None
        if st is None:
            if not is_capture:
                return {"ignored": True, "reason": "not a SafePay mandate", "status": status}
            why = (f"capture {rid} of authorization {auth_id or '?'} that SafePay never opened "
                   f"(reported by {source}; PayPal status {status})")
            decision = Decision(
                verdict=Verdict.BLOCK,
                intent=PaymentIntent(recipient="unknown", amount=float(amt.get("value") or 0),
                                     currency=amt.get("currency_code") or "USD", category="outside",
                                     agent_id="outside-safepay"),
                checks=[CheckResult("firewall", False, Severity.CRITICAL, why, 1.0,
                                    [{"rule": "outside_firewall", "capture_id": rid}])],
                reasons=[f"firewall: {why}"], executed=status == "COMPLETED",
                execution={"capture_id": rid, "authorization_id": auth_id, "status": status,
                           "source": source, "outside": True},
                event="outside")
            record = self.audit_log.append(decision)
            return {"outside": True, "seq": record["payload"].get("seq"), "status": status}
        prior = mandate.links(self.audit_log, auth_id, ("settlement",))
        for r in prior:
            x = (r.get("payload") or {}).get("execution") or {}
            if x.get("resource_id") == rid and x.get("status") == status:
                return {"duplicate": True, "seq": (r.get("payload") or {}).get("seq"), "status": status}
        ok = status in ("COMPLETED", "VOIDED", "CAPTURED", "PARTIALLY_CAPTURED")
        bad = status in ("DECLINED", "DENIED", "FAILED", "REVERSED", "REFUNDED", "PARTIALLY_REFUNDED")
        verdict = Verdict.ALLOW if ok else Verdict.BLOCK if bad else Verdict.HOLD
        what = "capture" if is_capture else "authorization"
        intent = PaymentIntent(recipient=st["recipient"] or "", amount=float(amt.get("value") or 0),
                               currency=amt.get("currency_code") or st["currency"],
                               category=st["category"] or "purchase", merchant_name=st["merchant"],
                               agent_id=st["agent_id"] or "", purpose=f"PayPal {event_type}")
        decision = Decision(verdict=verdict, intent=intent, checks=[],
                            reasons=[f"settlement: {what} {rid} is {status} at PayPal ({source})"],
                            executed=ok and is_capture, event="settlement",
                            policy_hash=st["policy_hash"],
                            execution={"authorization_id": auth_id, "resource_id": rid,
                                       "capture_id": rid if is_capture else None,
                                       "status": status, "event_type": event_type, "source": source})
        record = self.audit_log.append(decision)
        return {"duplicate": False, "seq": record["payload"].get("seq"), "status": status,
                "authorization_id": auth_id}

    def _frozen(self, agent_id: str) -> str | None:
        try:
            reg = self.control.touch_agent(agent_id)
        except Exception:
            return None
        return (reg.get("reason") or "frozen by the operator") if reg.get("status") == PAUSED else None

    def capture(self, authorization_id: str, amount: float, agent_id: str,
                purpose: str = "", final: bool = False) -> dict[str, Any]:
        """Draw on an open mandate. BLOCK unless the caller owns it, is not
        frozen and the amount fits in what is left; only then is PayPal called."""
        st = mandate.state(self.audit_log, authorization_id)
        if st is None:
            raise LookupError(f"no mandate with authorization id {authorization_id}")
        amount = round(float(amount or 0), 2)
        intent = PaymentIntent(recipient=st["recipient"], amount=amount, currency=st["currency"],
                               category=st["category"] or "purchase", merchant_name=st["merchant"],
                               purpose=purpose or st["purpose"] or "capture", agent_id=agent_id,
                               task_goal=st["task"] or "")
        checks = mandate.capture_checks(st, amount, agent_id, self._frozen(agent_id))
        failed = [c for c in checks if not c.passed]
        decision = Decision(verdict=Verdict.BLOCK if failed else Verdict.ALLOW, intent=intent,
                            checks=checks, reasons=[f"{c.name}: {c.detail}" for c in failed],
                            event="capture", policy_hash=st["policy_hash"])
        base = {"authorization_id": authorization_id, "method": "capture"}
        if not failed:
            final = bool(final) or abs(amount - st["remaining"]) < 0.005
            seal_id = seal.new_seal_id(None)
            try:
                res = self.paypal.capture_authorization(
                    authorization_id, amount, st["currency"], final=final,
                    note=seal.seal_note(intent.purpose, seal_id), invoice_id=seal_id)
                decision.executed = True
                decision.execution = dict(res, **base, seal=seal_id, final=final)
                unused = round(st["remaining"] - amount, 2)
                if final and unused > 0:
                    self.guardrail.record_settlement(st["agent_id"], -unused, st["recipient"])
                    decision.execution["released"] = unused
            except Exception as exc:
                decision.execution = dict(base, error=str(exc))
                decision.reasons.append(f"capture error: {exc}")
        else:
            decision.execution = dict(base)
        record = self.audit_log.append(decision)
        out = self._result(decision, record)
        out["mandate"] = mandate.state(self.audit_log, authorization_id)
        return out

    def close_mandate(self, authorization_id: str, agent_id: str, reason: str = "") -> dict[str, Any]:
        """Void what is left and credit it back to the agent's daily cap.
        The agent that opened the mandate, or an operator, may close it."""
        st = mandate.state(self.audit_log, authorization_id)
        if st is None:
            raise LookupError(f"no mandate with authorization id {authorization_id}")
        if agent_id != st["agent_id"] and not agent_id.startswith("operator:"):
            raise PermissionError("only the agent that opened the mandate, or an operator, can close it")
        if st["closed"]:
            raise ValueError("the mandate is already closed")
        left = st["remaining"]
        intent = PaymentIntent(recipient=st["recipient"], amount=left, currency=st["currency"],
                               category=st["category"] or "purchase", merchant_name=st["merchant"],
                               purpose=reason or "close mandate: release what was not used",
                               agent_id=agent_id, task_goal=st["task"] or "")
        decision = Decision(verdict=Verdict.ALLOW, intent=intent, event="void",
                            policy_hash=st["policy_hash"],
                            reasons=[f"released {st['currency']} {left:.2f} of {st['currency']} "
                                     f"{st['authorized']:.2f}; {st['currency']} {st['captured']:.2f} captured"])
        try:
            res = self.paypal.void_authorization(authorization_id)
            decision.executed = True
            decision.execution = dict(res, authorization_id=authorization_id, method="void", released=left)
            if left > 0:
                self.guardrail.record_settlement(st["agent_id"], -left, st["recipient"])
        except Exception as exc:
            decision.execution = {"authorization_id": authorization_id, "method": "void", "error": str(exc)}
            decision.reasons.append(f"void error: {exc}")
        record = self.audit_log.append(decision)
        out = self._result(decision, record)
        out["mandate"] = mandate.state(self.audit_log, authorization_id)
        return out

    def get_mandate(self, authorization_id: str) -> dict[str, Any] | None:
        return mandate.state(self.audit_log, authorization_id)

    def mandate_ap2(self, authorization_id: str) -> dict[str, Any] | None:
        """The mandate's AP2 objects (intent, cart, one PaymentMandate per
        capture), rebuilt from its chain links."""
        from . import ap2 as ap2mod
        st = mandate.state(self.audit_log, authorization_id)
        if st is None:
            return None
        return ap2mod.bundle(st, st.get("ap2"), st.get("cart"))

    # --- operator controls: policy in plain words, freeze / resume an agent --

    def draft_policy(self, text: str, run: str = "") -> dict[str, Any]:
        """Plain words -> a validated policy draft (nothing applied)."""
        base = self.policy_for(f"console:{run}") if run else self.policy
        return draft_policy(text, self.guardrail.chat, base)

    def _policy_link(self, run: str, policy: PolicyConfig, what: str,
                     source: str, sealed: dict[str, Any] | None = None) -> dict[str, Any]:
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
                       "source": source, "rules": rules,
                       **({"operator_sig": sealed} if sealed else {})},
            event="policy", policy_hash=h,
        )
        return self.audit_log.append(decision)

    def apply_policy(self, run: str, raw: dict[str, Any], source: str = "console",
                     signature: dict[str, Any] | None = None) -> dict[str, Any]:
        """Validate a policy dict and make it the policy for a console run.
        The change is appended to the audit chain as a "policy" link. With an
        operator signature (see opsig.py) the policy is stored signed and the
        session is pinned to that key; a pinned session refuses unsigned or
        foreign-key changes (PermissionError)."""
        if not run:
            raise ValueError("a run id is required")
        policy = coerce_policy(raw, self.policy)
        h = attest.policy_hash(policy)
        sealed = self._sign_check(run, h, signature)
        stored = policy.to_dict()
        if sealed:
            stored["_sig"] = {k: v for k, v in sealed.items() if k != "pinned_now"}
        self.control.set_policy(run, stored)
        record = self._policy_link(run, policy, "policy applied" + (" (operator-signed)" if sealed else ""),
                                   source, sealed)
        return {"run": run, "policy": policy.to_dict(), "rules": describe_policy(policy),
                "policy_hash": h, "audit": record, "seq": record["payload"].get("seq"),
                "signed_by": sealed["kid"] if sealed else None,
                "pinned_now": bool(sealed and sealed.get("pinned_now"))}

    def policy_message(self, run: str, raw: dict[str, Any] | None, reset: bool = False) -> dict[str, Any]:
        """What the operator's browser signs for this policy and session."""
        policy = self.policy if reset else coerce_policy(raw or {}, self.policy)
        h = attest.policy_hash(policy)
        return {"policy_hash": h, "message": opsig.message(run, h)}

    def clear_policy(self, run: str, source: str = "console",
                     signature: dict[str, Any] | None = None) -> dict[str, Any]:
        out = {"run": run, "policy": self.policy.to_dict(),
               "rules": describe_policy(self.policy),
               "policy_hash": attest.policy_hash(self.policy)}
        if run and self._pin(run):
            # A pinned session goes back to the default by a signed change too.
            h = out["policy_hash"]
            sealed = self._sign_check(run, h, signature)
            self.control.set_policy(run, dict(self.policy.to_dict(), _sig=sealed))
            record = self._policy_link(run, self.policy, "policy reset to default (operator-signed)",
                                       source, sealed)
            out["audit"], out["seq"], out["signed_by"] = record, record["payload"].get("seq"), sealed["kid"]
            return out
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
