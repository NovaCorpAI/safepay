"""SafePay in front of PayPal's official MCP server.

An agent that already uses PayPal's MCP server (`npx @paypal/mcp`) changes one
line of its MCP config, the command, and every tool it had is still there,
same names, same arguments. The difference: the five tools that move money or
commit to a charge now pass through the SafePay guardrail first, and reach
PayPal only on ALLOW. Everything else (invoices, products, tracking, reads)
passes straight through.

    create_order          an agent purchase: amount from the line items
    pay_order             capture: amount and payee read from PayPal's own order
    create_refund         money out to a buyer: amount from the call, or held
    accept_dispute_claim  money out: amount read from PayPal's own dispute
    create_subscription   a recurring charge: price read from PayPal's plan

The amount is read from PayPal whenever PayPal has it, never only from what
the agent says. Each gated call becomes a link in the same signed chain the
console and the ledger show (event "order"), under the same policy, with the
same history rules and checks. HOLD and BLOCK return a clear refusal to the
agent with the reasons; the upstream tool is never called.

Run it where you would run PayPal's server:

    {"mcpServers": {"paypal": {"command": "safepay-mcp-proxy",
        "env": {"PAYPAL_CLIENT_ID": "...", "PAYPAL_CLIENT_SECRET": "..."}}}}

It starts `npx -y @paypal/mcp --tools=all` itself (override with
SAFEPAY_UPSTREAM_CMD) with an access token minted from the client
credentials, pinned to the sandbox unless PAYPAL_ENVIRONMENT=PRODUCTION is set
explicitly. Needs Node (for PayPal's server) and `pip install safepay[mcp]`.
"""

from __future__ import annotations

import json
import os
import re
import shlex
from typing import Any, Awaitable, Callable

from .models import Decision, PaymentIntent, Verdict

GATED = ("create_order", "pay_order", "create_refund", "accept_dispute_claim",
         "create_subscription")
# The obligation is created here, so the daily ledger is charged here; a
# capture (pay_order) completes an order create_order already counted.
COMMITS = ("create_order", "create_subscription", "create_refund", "accept_dispute_claim")

# Context an agent may add to a gated call (stripped before PayPal sees it).
CONTEXT_FIELDS = {
    "safepay_purpose": {"type": "string", "description": "Why this payment is being made (SafePay judges it)."},
    "safepay_task": {"type": "string", "description": "The task you were given (SafePay checks the payment serves it)."},
    "safepay_merchant": {"type": "string", "description": "Who is being paid, by name."},
    "safepay_recipient": {"type": "string", "description": "The payee's email, when known."},
}

Upstream = Callable[[str, dict[str, Any]], Awaitable[Any]]


def _json(result: Any) -> Any:
    """The JSON an upstream tool returned (first text block), or None."""
    for block in getattr(result, "content", None) or []:
        text = getattr(block, "text", None)
        if text:
            try:
                return json.loads(text)
            except ValueError:
                m = re.search(r"\{.*\}", text, re.S)
                if m:
                    try:
                        return json.loads(m.group(0))
                    except ValueError:
                        return None
    return None


def _f(v: Any) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-") or "merchant"


class PaymentProxy:
    """The decision half of the proxy: no MCP and no network of its own.

    `upstream(name, args)` calls PayPal's server. `agent` is a SafePayAgent
    (its guardrail, policy and audit chain are the ones every other entry
    point uses)."""

    def __init__(self, agent: Any, upstream: Upstream, agent_id: str = "mcp-proxy") -> None:
        self.agent = agent
        self.upstream = upstream
        self.agent_id = agent_id

    @staticmethod
    def augment(tool: dict[str, Any]) -> dict[str, Any]:
        """A gated tool, as the client sees it: same schema plus optional
        SafePay context fields, and a description that says it is gated."""
        if tool["name"] not in GATED:
            return tool
        schema = json.loads(json.dumps(tool.get("inputSchema") or {"type": "object"}))
        schema.setdefault("properties", {}).update(CONTEXT_FIELDS)
        return dict(tool, inputSchema=schema,
                    description="[Gated by SafePay: evaluated before PayPal is called; held or "
                                "blocked calls never reach PayPal] " + (tool.get("description") or ""))

    async def intent_for(self, name: str, args: dict[str, Any], ctx: dict[str, str]) -> PaymentIntent:
        merchant = ctx.get("safepay_merchant") or ""
        recipient = ctx.get("safepay_recipient") or ""
        currency, amount, category = "USD", 0.0, "purchase"
        if name == "create_order":
            items = args.get("items") or []
            amount = sum(_f(i.get("itemTotal") or _f(i.get("itemCost")) * _f(i.get("quantity") or 1))
                         for i in items) + _f(args.get("shippingCost")) - _f(args.get("discount"))
            currency = args.get("currencyCode") or "USD"
            merchant = merchant or (items[0].get("name") if items else "")
        elif name == "pay_order":
            order = _json(await self.upstream("get_order", {"id": args.get("id")})) or {}
            pu = (order.get("purchase_units") or [{}])[0]
            amt = pu.get("amount") or {}
            amount, currency = _f(amt.get("value")), amt.get("currency_code") or "USD"
            payee = pu.get("payee") or {}
            recipient = recipient or payee.get("email_address") or ""
            items = pu.get("items") or []
            merchant = merchant or (items[0].get("name") if items else "") or payee.get("merchant_id", "")
        elif name == "create_refund":
            amt = args.get("amount") or {}
            amount, currency, category = _f(amt.get("value")), amt.get("currency_code") or "USD", "refunds"
            recipient = recipient or f"buyer-of-capture:{args.get('capture_id')}"
            merchant = merchant or "refund to buyer"
        elif name == "accept_dispute_claim":
            dispute = _json(await self.upstream("get_dispute", {"dispute_id": args.get("dispute_id")})) or {}
            amt = dispute.get("dispute_amount") or {}
            amount, currency, category = _f(amt.get("value")), amt.get("currency_code") or "USD", "disputes"
            recipient = recipient or f"buyer-of-dispute:{args.get('dispute_id')}"
            merchant = merchant or "dispute settlement"
        elif name == "create_subscription":
            plan = _json(await self.upstream("show_subscription_plan_details",
                                             {"plan_id": args.get("plan_id")})) or {}
            for cycle in plan.get("billing_cycles") or []:
                price = ((cycle.get("pricing_scheme") or {}).get("fixed_price") or {})
                if price.get("value"):
                    amount, currency = _f(price["value"]) * max(1.0, _f(args.get("quantity") or 1)), \
                        price.get("currency_code") or "USD"
            category = "subscriptions"
            merchant = merchant or plan.get("name") or f"plan {args.get('plan_id')}"
        recipient = recipient or f"orders@{_slug(merchant)}.example"
        return PaymentIntent(
            recipient=recipient, amount=round(amount, 2), currency=currency, category=category,
            merchant_name=merchant or None, agent_id=self.agent_id,
            purpose=ctx.get("safepay_purpose") or f"PayPal MCP {name}",
            task_goal=ctx.get("safepay_task") or "")

    async def call(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        """Gate one tool call. Returns {"forward": bool, "result": upstream
        result or None, "safepay": summary or None}."""
        args = dict(args or {})
        ctx = {k: str(args.pop(k)) for k in list(args) if k in CONTEXT_FIELDS}
        if name not in GATED:
            return {"forward": True, "result": await self.upstream(name, args), "safepay": None}

        intent = await self.intent_for(name, args, ctx)
        decision: Decision
        if intent.amount <= 0:
            decision = Decision(verdict=Verdict.HOLD, intent=intent, reasons=[
                f"{name}: the amount could not be established from PayPal or the call; "
                "a person must look before money moves"])
        else:
            decision = self.agent.guardrail.evaluate(
                intent, policy=self.agent.policy_for(intent.agent_id))
        result = None
        if decision.verdict == Verdict.ALLOW:
            try:
                result = await self.upstream(name, args)
                failed = bool(getattr(result, "isError", False))
                decision.executed = not failed
                decision.execution = {"method": name, "via": "paypal-mcp",
                                      "result": _json(result) if not failed else None,
                                      "error": None if not failed else "upstream tool error"}
                if not failed and name in COMMITS:
                    self.agent.guardrail.commit_spend(decision)
            except Exception as exc:
                decision.executed = False
                decision.execution = {"method": name, "via": "paypal-mcp", "error": str(exc)}
                decision.reasons.append(f"upstream error: {exc}")
        decision.event = "order"
        record = self.agent.audit_log.append(decision)
        summary = {
            "verdict": decision.verdict.value, "executed": decision.executed,
            "amount": intent.amount, "currency": intent.currency, "reasons": decision.reasons,
            "chain_seq": (record.get("payload") or {}).get("seq"), "sha256": record.get("sha256"),
        }
        return {"forward": decision.verdict == Verdict.ALLOW, "result": result, "safepay": summary}


# --- the MCP wiring (needs `pip install safepay[mcp]` and Node) ------------

def _upstream_command() -> tuple[str, list[str]]:
    raw = os.environ.get("SAFEPAY_UPSTREAM_CMD") or "npx -y @paypal/mcp --tools=all"
    parts = shlex.split(raw, posix=os.name != "nt")
    cmd = parts[0]
    if os.name == "nt" and cmd in ("npx", "npm"):
        cmd += ".cmd"
    return cmd, parts[1:]


async def serve() -> None:  # pragma: no cover - exercised end to end, not in unit tests
    import mcp.types as types
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    from mcp.server import Server
    from mcp.server.stdio import stdio_server

    from .agent import SafePayAgent

    agent = SafePayAgent()
    env = dict(os.environ)
    if not env.get("PAYPAL_ACCESS_TOKEN"):
        env["PAYPAL_ACCESS_TOKEN"] = agent.paypal._access_token()
    env["PAYPAL_ENVIRONMENT"] = "PRODUCTION" if env.get("PAYPAL_ENVIRONMENT", "").upper() == "PRODUCTION" \
        else "SANDBOX"
    cmd, argv = _upstream_command()
    params = StdioServerParameters(command=cmd, args=argv, env=env)

    async with stdio_client(params) as (r, w), ClientSession(r, w) as up:
        await up.initialize()
        tools = (await up.list_tools()).tools

        async def upstream(name: str, args: dict[str, Any]) -> Any:
            return await up.call_tool(name, args)

        proxy = PaymentProxy(agent, upstream, os.environ.get("SAFEPAY_AGENT_ID", "mcp-proxy"))
        server = Server("safepay-paypal")

        @server.list_tools()
        async def _list() -> list[types.Tool]:
            out = []
            for t in tools:
                d = PaymentProxy.augment({"name": t.name, "description": t.description,
                                          "inputSchema": t.inputSchema})
                out.append(types.Tool(name=d["name"], description=d["description"],
                                      inputSchema=d["inputSchema"]))
            return out

        @server.call_tool()
        async def _call(name: str, arguments: dict[str, Any]) -> list[Any]:
            got = await proxy.call(name, arguments or {})
            blocks: list[Any] = []
            if got["safepay"] is not None:
                s = got["safepay"]
                head = (f"SafePay: {s['verdict'].upper()} {s['currency']} {s['amount']:.2f}"
                        f" (chain link #{s['chain_seq']})")
                if not got["forward"]:
                    head += ". PayPal was NOT called. Reasons: " + "; ".join(s["reasons"])
                blocks.append(types.TextContent(type="text", text=head))
            if got["result"] is not None:
                blocks.extend(getattr(got["result"], "content", None) or [])
            return blocks

        async with stdio_server() as (sr, sw):
            await server.run(sr, sw, server.create_initialization_options())


def main() -> None:  # pragma: no cover
    import asyncio
    asyncio.run(serve())


if __name__ == "__main__":  # pragma: no cover
    main()
