"""SafePay as the approval gate in front of the PayPal Agent Toolkit.

The PayPal Agent Toolkit (`paypal-agent-toolkit`) gives an AI agent real PayPal
tools: `create_order`, `capture_order`, `create_subscription`, `send_invoice`,
and so on, dispatched through `PayPalAPI.run(method, params)`. PayPal's own
guidance for the order flow is explicit: the agent should create an order, but
"invoke pay_order only after the user approves." SafePay is that approver, made
automatic and principled.

`GuardedToolkit` wraps the toolkit's `PayPalAPI`. For a money-moving tool call it
runs the SafePay guardrail first (deterministic policy, then the optional
live-web merchant check and intent reasoning). On ALLOW it forwards the call to
the real toolkit, so an actual sandbox order is created; on HOLD or BLOCK the
toolkit is never called and nothing moves. Either way it returns the decision
and a signed audit record, so every attempted agent payment leaves a trace.

Read-only tool calls (get/list/show) pass straight through: there is nothing to
gate.

The toolkit is an optional dependency. Importing this module never requires it;
only building a `GuardedToolkit` with a live PayPal config does. Install it with
`pip install -r requirements-agent.txt` (ideally in a virtualenv, because its
newer releases pin heavy agent-framework dependencies).
"""

from __future__ import annotations

from typing import Any

from . import attest
from .config import Config
from .guardrail import Guardrail
from .models import Decision, PaymentIntent, PolicyConfig, Verdict
from .nebius_client import NebiusChat
from .policy import SpendLedger
from .search import WebSearch

try:  # The toolkit is optional; the rest of SafePay does not need it.
    from paypal_agent_toolkit.shared.api import PayPalAPI
    from paypal_agent_toolkit.shared.configuration import Context

    _TOOLKIT_IMPORT_ERROR: Exception | None = None
except Exception as exc:  # pragma: no cover - exercised only without the package
    PayPalAPI = None  # type: ignore[assignment]
    Context = None  # type: ignore[assignment]
    _TOOLKIT_IMPORT_ERROR = exc


class ToolkitUnavailable(RuntimeError):
    """Raised when a toolkit-backed call is attempted but the package or the
    PayPal credentials are missing."""


# Tool methods that move money or create a standing financial obligation. These
# are gated by the guardrail; everything else (get/list/show) passes through.
MONEY_METHODS = frozenset(
    {"create_order", "capture_order", "create_subscription", "send_invoice"}
)

# Of those, the ones that create the obligation (and so should be charged to the
# daily spend ledger exactly once). `capture_order` completes an order that
# `create_order` already counted, so it must NOT be charged again, or a single
# purchase would be double counted against the daily cap.
COMMIT_METHODS = frozenset({"create_order", "create_subscription", "send_invoice"})


def toolkit_available() -> bool:
    """True when the paypal-agent-toolkit package is importable."""
    return PayPalAPI is not None


def order_params_from_intent(intent: PaymentIntent) -> dict[str, Any]:
    """Build Agent-Toolkit `create_order` params from a SafePay intent.

    One line item for the whole amount. The guardrail's policy already pins the
    currency to the allowed set (USD by default), which matches the toolkit's
    `CreateOrderParameters` currency literal, so an allowed intent is always a
    valid order.
    """
    amount = round(float(intent.amount), 2)
    name = intent.merchant_name or (intent.purpose or "Agent purchase")
    return {
        "currency_code": intent.currency,
        "items": [
            {
                "name": name[:127],
                "quantity": 1,
                "item_cost": amount,
                "item_total": amount,
                "tax_percent": 0,
                "description": (intent.purpose or None),
            }
        ],
    }


class GuardedToolkit:
    """A PayPal Agent Toolkit whose money-moving tools pass through SafePay.

    Build it once per agent (the spend ledger is shared, so the daily cap holds
    across a burst of calls). The reasoning and web checks are off by default,
    which keeps the gate fully deterministic and offline; turn them on to add
    the Nemotron intent check and the Tavily merchant check.
    """

    def __init__(
        self,
        config: Config | None = None,
        policy: PolicyConfig | None = None,
        *,
        reasoning: bool = False,
        web: bool = False,
        ledger: SpendLedger | None = None,
        guardrail: Guardrail | None = None,
        audit_log: Any | None = None,
    ) -> None:
        self.config = config or Config.from_env()
        self.policy = policy or PolicyConfig()

        if guardrail is not None:
            # Share an agent's guardrail: same ledger, registry, web and model,
            # so a toolkit order counts against the same caps as a payout.
            self.guardrail = guardrail
        else:
            chat = NebiusChat(self.config) if (reasoning and self.config.nebius_live) else None
            search = WebSearch(self.config.tavily_api_key) if web else WebSearch(None)
            self.guardrail = Guardrail(self.policy, search, chat, ledger=ledger)
        # With an audit log, gated calls are appended to that chain instead
        # of producing a standalone record.
        self.audit_log = audit_log

        self._api = None  # built lazily on the first toolkit-backed call

    # -- the PayPal Agent Toolkit handle -----------------------------------

    @property
    def api(self):
        """The underlying toolkit `PayPalAPI`, built on first use."""
        if self._api is None:
            if not toolkit_available():
                raise ToolkitUnavailable(
                    "paypal-agent-toolkit is not installed "
                    f"({_TOOLKIT_IMPORT_ERROR}); run "
                    "`pip install -r requirements-agent.txt`"
                )
            if not self.config.paypal_live:
                raise ToolkitUnavailable(
                    "PAYPAL_CLIENT_ID / PAYPAL_SECRET are not set, so the "
                    "Agent Toolkit cannot reach the sandbox"
                )
            self._api = PayPalAPI(
                client_id=self.config.paypal_client_id,
                secret=self.config.paypal_secret,
                context=Context(sandbox=True),
            )
        return self._api

    # -- the gate ----------------------------------------------------------

    def evaluate(self, intent: PaymentIntent) -> Decision:
        """Run the guardrail without touching PayPal."""
        return self.guardrail.evaluate(intent)

    def run(
        self, method: str, params: dict[str, Any], intent: PaymentIntent | None = None,
        policy: PolicyConfig | None = None,
    ) -> dict[str, Any]:
        """Gate one toolkit tool call.

        For a money-moving method an `intent` is required: the guardrail judges
        it, and the toolkit is invoked only on ALLOW. For a read-only method the
        call passes straight through. The return always carries the toolkit
        result (when one was produced) and, for a gated call, the decision and a
        signed audit record.
        """
        if method not in MONEY_METHODS:
            # Nothing to gate: forward to the toolkit as-is.
            return {"gated": False, "method": method, "result": self.api.run(method, params)}

        if intent is None:
            raise ValueError(
                f"method '{method}' moves money; a PaymentIntent is required to gate it"
            )

        decision = self.guardrail.evaluate(intent, policy=policy)
        executed_result: Any = None

        if decision.verdict == Verdict.ALLOW:
            try:
                executed_result = self.api.run(method, params)
                decision.executed = True
                decision.execution = {"method": method, "result": executed_result}
                # Charge the daily ledger once, when the obligation is created;
                # a later capture of the same order must not be charged again.
                if method in COMMIT_METHODS:
                    self.guardrail.commit_spend(decision)
            except Exception as exc:  # the toolkit call itself failed
                decision.executed = False
                decision.execution = {"method": method, "error": str(exc)}
                decision.reasons.append(f"toolkit error: {exc}")

        decision.event = "order"
        record = (self.audit_log.append(decision) if self.audit_log is not None
                  else attest.audit_record(decision))
        return {
            "gated": True,
            "method": method,
            "decision": decision.to_dict(),
            "audit": record,
            "stamp": attest.stamp(decision),
            "result": executed_result,
        }

    def create_order(
        self, intent: PaymentIntent, order_params: dict[str, Any] | None = None,
        policy: PolicyConfig | None = None,
    ) -> dict[str, Any]:
        """The headline flow: gate, then create a real sandbox order on ALLOW.

        The agent wants to transact. SafePay evaluates the intent first. If the
        verdict is ALLOW, the PayPal Agent Toolkit creates the order for real and
        the result carries the order id and the buyer approval link (the Orders
        flow's own human step stays intact). If the verdict is HOLD or BLOCK, the
        toolkit is never called and no order exists.
        """
        params = order_params or order_params_from_intent(intent)
        return self.run("create_order", params, intent=intent, policy=policy)
