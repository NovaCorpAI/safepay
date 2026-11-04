"""SafePay as an MCP server: a drop-in payment guardrail for any MCP agent.

Instead of living inside one codebase, SafePay exposes itself over the Model
Context Protocol, so any MCP client (Claude Desktop, Cursor, an agent framework)
can route its payments through the guardrail. The agent calls `safepay_evaluate`
to get a verdict, `safepay_pay` to evaluate and release a payout only on ALLOW,
or `safepay_create_order` to gate the PayPal Agent Toolkit's order flow. Every
call is appended to the tamper-evident audit chain, which `safepay_audit_status`
re-verifies.

This turns SafePay from an app into infrastructure: the safety layer an agent
operator installs once, in front of the agent's wallet.

The `mcp` package is optional; importing this module never requires it. Install
it with `pip install -r requirements-mcp.txt`, then run the server over stdio:

    python -m safepay.mcp_server

and point your MCP client's config at that command (see README).
"""

from __future__ import annotations

from typing import Any

from .agent import SafePayAgent
from .agent_toolkit import GuardedToolkit, toolkit_available
from .config import Config
from .models import PaymentIntent, PolicyConfig

# One process-wide agent and toolkit gate, so the audit chain and the spend
# ledger are shared across tool calls (the daily cap holds across a burst).
_config = Config.from_env()
_policy = PolicyConfig()
_agent = SafePayAgent(config=_config, policy=_policy)
_gate = GuardedToolkit(config=_config, policy=_policy, ledger=_agent.guardrail.ledger)


def _intent(
    recipient: str, amount: float, currency: str = "USD",
    category: str = "uncategorized", purpose: str = "", task_goal: str = "",
    merchant_name: str | None = None, agent_id: str = "mcp-agent",
) -> PaymentIntent:
    return PaymentIntent(
        recipient=recipient, amount=float(amount), currency=currency,
        category=category, purpose=purpose, task_goal=task_goal,
        merchant_name=merchant_name, agent_id=agent_id,
    )


# -- plain callables (testable without the mcp package) --------------------

def evaluate_payment(**kw: Any) -> dict[str, Any]:
    """Judge a payment without executing it. Returns the verdict and the checks."""
    decision = _agent.guardrail.evaluate(_intent(**kw))
    return {
        "verdict": decision.verdict.value,
        "risk": round(decision.risk, 3),
        "reasons": decision.reasons,
        "checks": [
            {"name": c.name, "passed": c.passed, "detail": c.detail, "risk": c.risk}
            for c in decision.checks
        ],
    }


def pay(**kw: Any) -> dict[str, Any]:
    """Evaluate and, only on ALLOW, release the payment via PayPal Payouts."""
    out = _agent.pay(_intent(**kw))
    dec = out["decision"]
    return {
        "verdict": dec["verdict"],
        "risk": dec["risk"],
        "executed": dec["executed"],
        "reasons": dec["reasons"],
        "execution": dec["execution"],
        "stamp": out["stamp"],
        "audit_seq": out["audit"]["payload"].get("seq"),
        "signed": out["audit"]["signature"]["signed"],
        "paypal_mode": out["paypal_mode"],
    }


def create_order(**kw: Any) -> dict[str, Any]:
    """Gate a PayPal Agent Toolkit order: create it for real only on ALLOW."""
    if not toolkit_available():
        return {"error": "paypal-agent-toolkit not installed (requirements-agent.txt)"}
    out = _gate.create_order(_intent(**kw))
    dec = out["decision"]
    return {
        "verdict": dec["verdict"],
        "risk": dec["risk"],
        "executed": dec["executed"],
        "reasons": dec["reasons"],
        "order": out.get("result"),
        "stamp": out["stamp"],
        "signed": out["audit"]["signature"]["signed"],
    }


def pending_approvals() -> dict[str, Any]:
    """Payments held for a human decision."""
    items = _agent.pending_approvals()
    return {"count": len(items), "pending": items}


def resolve_approval(approval_id: str, approve: bool, note: str = "") -> dict[str, Any]:
    """Approve (execute and charge the ledger) or reject a held payment."""
    try:
        out = _agent.approve(approval_id, note) if approve else _agent.reject(approval_id, note)
    except ValueError as exc:
        return {"error": str(exc)}
    dec = out["decision"]
    return {
        "approval_id": approval_id,
        "verdict": dec["verdict"],
        "executed": dec["executed"],
        "reasons": dec["reasons"],
        "execution": dec["execution"],
        "audit_seq": out["audit"]["payload"].get("seq"),
        "signed": out["audit"]["signature"]["signed"],
    }


def payout_status(batch_id: str) -> dict[str, Any]:
    """What PayPal says happened to a released payout; appended to the chain."""
    try:
        out = _agent.settle(batch_id, source="mcp")
    except ValueError as exc:
        return {"error": str(exc)}
    except Exception as exc:
        return {"error": f"PayPal lookup failed: {exc}"}
    return {"payout_batch_id": batch_id, "status": out["status"],
            "settlement": out["settlement"], "audit_seq": out["seq"],
            "already_recorded": out["duplicate"]}


def draft_policy_text(text: str) -> dict[str, Any]:
    """Plain words -> a validated policy draft (nothing applied)."""
    try:
        return _agent.draft_policy(text)
    except ValueError as exc:
        return {"error": str(exc)}


def list_agents() -> dict[str, Any]:
    return {"agents": _agent.agents()}


def set_agent_paused(agent_id: str, paused: bool, reason: str = "") -> dict[str, Any]:
    row = _agent.pause_agent(agent_id, reason) if paused else _agent.resume_agent(agent_id, reason)
    return {"agent_id": agent_id, "status": row.get("status"), "reason": row.get("reason", "")}


def audit_status() -> dict[str, Any]:
    """Re-verify the hash-chained audit log of every decision so far."""
    ok, bad = _agent.audit_log.verify()
    return {
        "ok": ok,
        "first_bad_seq": bad,
        "count": len(_agent.audit_log.records),
        "head": _agent.audit_log.head,
    }


# -- MCP wiring (only when the optional mcp package is installed) -----------

try:
    from mcp.server.fastmcp import FastMCP

    _MCP: "FastMCP | None" = FastMCP("safepay")

    @_MCP.tool()
    def safepay_evaluate(
        recipient: str, amount: float, currency: str = "USD",
        category: str = "uncategorized", purpose: str = "", task_goal: str = "",
        merchant_name: str | None = None, agent_id: str = "mcp-agent",
    ) -> dict[str, Any]:
        """Judge whether a payment is safe, WITHOUT sending it. Call this before
        any payment. `task_goal` is the task the agent was given, used to catch
        goal/payment mismatches. Returns verdict (allow/hold/block), risk, and
        the policy, merchant and reasoning checks."""
        return evaluate_payment(
            recipient=recipient, amount=amount, currency=currency, category=category,
            purpose=purpose, task_goal=task_goal, merchant_name=merchant_name,
            agent_id=agent_id,
        )

    @_MCP.tool()
    def safepay_pay(
        recipient: str, amount: float, currency: str = "USD",
        category: str = "uncategorized", purpose: str = "", task_goal: str = "",
        merchant_name: str | None = None, agent_id: str = "mcp-agent",
    ) -> dict[str, Any]:
        """Evaluate a payment and release it via PayPal Payouts only if the
        verdict is ALLOW. On HOLD or BLOCK the money never moves. Returns the
        verdict, whether it executed, and a signed audit entry."""
        return pay(
            recipient=recipient, amount=amount, currency=currency, category=category,
            purpose=purpose, task_goal=task_goal, merchant_name=merchant_name,
            agent_id=agent_id,
        )

    @_MCP.tool()
    def safepay_create_order(
        recipient: str, amount: float, currency: str = "USD",
        category: str = "uncategorized", purpose: str = "", task_goal: str = "",
        merchant_name: str | None = None, agent_id: str = "mcp-agent",
    ) -> dict[str, Any]:
        """Gate a PayPal Agent Toolkit order: SafePay evaluates first and creates
        the order for real only on ALLOW. Returns the order (with its approval
        link) or the reason it was held or blocked."""
        return create_order(
            recipient=recipient, amount=amount, currency=currency, category=category,
            purpose=purpose, task_goal=task_goal, merchant_name=merchant_name,
            agent_id=agent_id,
        )

    @_MCP.tool()
    def safepay_pending_approvals() -> dict[str, Any]:
        """List payments SafePay held for a human decision (verdict HOLD),
        with the approval id, the intent and the reasons."""
        return pending_approvals()

    @_MCP.tool()
    def safepay_approve(approval_id: str, note: str = "") -> dict[str, Any]:
        """A human approves a held payment: SafePay executes it, charges the
        spend ledger, and appends the approval to the audit chain."""
        return resolve_approval(approval_id, True, note)

    @_MCP.tool()
    def safepay_reject(approval_id: str, note: str = "") -> dict[str, Any]:
        """A human rejects a held payment: nothing moves; the rejection is
        appended to the audit chain."""
        return resolve_approval(approval_id, False, note)

    @_MCP.tool()
    def safepay_payout_status(batch_id: str) -> dict[str, Any]:
        """Close the loop on a payout SafePay released: ask PayPal whether the
        money actually moved (SUCCESS), failed or was returned, and append that
        settlement to the audit chain. A failed payout releases its budget."""
        return payout_status(batch_id)

    @_MCP.tool()
    def safepay_draft_policy(text: str) -> dict[str, Any]:
        """Turn an operator's plain-language spending rules ("$300 per payment,
        1,000 a day, ask me over 100, no gambling") into a validated SafePay
        policy draft with a summary and open questions. Nothing is applied:
        the operator reviews it and applies it in the console."""
        return draft_policy_text(text)

    @_MCP.tool()
    def safepay_agents() -> dict[str, Any]:
        """Every agent id the guardrail has seen, with its status (active or
        paused), how many decisions it asked for, and when it was last seen."""
        return list_agents()

    @_MCP.tool()
    def safepay_pause_agent(agent_id: str, reason: str = "") -> dict[str, Any]:
        """Pause an agent: its next payment request is refused before any
        check runs, and the refusal is chained. For incidents and kill
        switches. Resume with safepay_resume_agent."""
        return set_agent_paused(agent_id, True, reason)

    @_MCP.tool()
    def safepay_resume_agent(agent_id: str) -> dict[str, Any]:
        """Resume a paused agent."""
        return set_agent_paused(agent_id, False)

    @_MCP.tool()
    def safepay_audit_status() -> dict[str, Any]:
        """Re-verify the tamper-evident audit chain of every decision so far.
        Returns ok=true when the chain is intact, or the first broken record."""
        return audit_status()

except Exception:  # pragma: no cover - exercised only without the mcp package
    _MCP = None


def main() -> None:
    if _MCP is None:
        raise SystemExit(
            "The 'mcp' package is not installed. Run: pip install -r requirements-mcp.txt"
        )
    _MCP.run()


if __name__ == "__main__":
    main()
