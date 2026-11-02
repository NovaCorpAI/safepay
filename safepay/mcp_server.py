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
