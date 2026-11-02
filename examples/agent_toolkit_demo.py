"""SafePay gating the PayPal Agent Toolkit, end to end against the sandbox.

Shows SafePay as the automatic approver in front of the toolkit's money-moving
tools. Each intent is judged by the guardrail first; only on ALLOW does the
PayPal Agent Toolkit create a real sandbox order. Over-limit and daily-cap
breaches are stopped before the toolkit is ever called.

Run (needs PAYPAL_CLIENT_ID / PAYPAL_SECRET in the environment, plus the
toolkit installed via requirements-agent.txt):

    python examples/agent_toolkit_demo.py
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# UTF-8 stdout so model/PayPal strings never crash a cp1252 console.
for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, v = s.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


_load_dotenv(ROOT / ".env")

from safepay.agent_toolkit import GuardedToolkit, toolkit_available  # noqa: E402
from safepay.models import PaymentIntent, PolicyConfig  # noqa: E402


# One agent, one task. A tight policy so the gate is easy to see: the amounts
# below trip the per-transaction limit and the daily cap on different lines.
POLICY = PolicyConfig(per_tx_limit=500, daily_limit=800, allowed_currencies=["USD"])
GOAL = "Pay the team's approved software and cloud invoices for this month"

INTENTS = [
    PaymentIntent(recipient="billing@notion.so", amount=48, category="software",
                  merchant_name="Notion", purpose="Team plan, monthly",
                  agent_id="ops-agent-7", task_goal=GOAL),
    PaymentIntent(recipient="billing@bigcompute.example", amount=900, category="cloud",
                  merchant_name="BigCompute", purpose="GPU cluster, monthly",
                  agent_id="ops-agent-7", task_goal=GOAL),
    PaymentIntent(recipient="billing@aws.amazon.com", amount=420, category="cloud",
                  merchant_name="AWS", purpose="Compute, monthly",
                  agent_id="ops-agent-7", task_goal=GOAL),
    PaymentIntent(recipient="billing@google.com", amount=420, category="cloud",
                  merchant_name="Google Cloud", purpose="Compute, monthly",
                  agent_id="ops-agent-7", task_goal=GOAL),
]


def _approve_link(result_json: str | None) -> str:
    """Pull the buyer approval link out of a create_order response."""
    if not result_json:
        return ""
    try:
        data = json.loads(result_json)
    except (TypeError, ValueError):
        return ""
    for link in data.get("links", []):
        if link.get("rel") in ("approve", "payer-action"):
            return link.get("href", "")
    return ""


def _order_id(result_json: str | None) -> str:
    if not result_json:
        return ""
    try:
        return json.loads(result_json).get("id", "")
    except (TypeError, ValueError):
        return ""


def main() -> int:
    if not toolkit_available():
        print("paypal-agent-toolkit is not installed. Install it first:")
        print("    pip install -r requirements-agent.txt")
        return 2

    gate = GuardedToolkit(policy=POLICY)  # reasoning/web off: deterministic policy gate
    try:
        _ = gate.api  # fail fast with a clear message if creds are missing
    except Exception as exc:
        print(f"Cannot reach the PayPal sandbox: {exc}")
        return 2

    print("SafePay gating the PayPal Agent Toolkit (sandbox)\n")
    print(f"Policy: per-tx {POLICY.per_tx_limit:.0f}, daily {POLICY.daily_limit:.0f} USD")
    print(f"Agent task: {GOAL}\n")

    created = 0
    for intent in INTENTS:
        out = gate.create_order(intent)
        d = out["decision"]
        verdict = d["verdict"].upper()
        signed = "signed" if out["audit"]["signature"]["signed"] else "unsigned"
        line = f"[{verdict:5}] USD {intent.amount:>6.2f}  {intent.merchant_name}"

        if out["result"]:
            created += 1
            oid = _order_id(out["result"])
            link = _approve_link(out["result"])
            print(f"{line}\n         toolkit created order {oid} ({signed} audit)")
            if link:
                print(f"         buyer approval link: {link}")
        else:
            reason = "; ".join(d["reasons"]) or "stopped"
            print(f"{line}\n         toolkit NOT called: {reason} ({signed} audit)")
        print()

    print(f"Done. {created} real sandbox orders created; the rest were stopped "
          f"by SafePay before the toolkit was ever called.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
