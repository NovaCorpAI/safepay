"""Command-line runner for SafePay.

Run a single intent from flags, or a batch from a JSON scenario file:

    python -m safepay --scenarios examples/scenarios.json
    python -m safepay --recipient vendor@example.com --amount 42 \
        --purpose "monthly API bill" --goal "pay the team's SaaS invoices"

Prints a readable verdict per intent and a one-line signed stamp. With
--json it emits the full decision+audit records instead.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import fields

from .agent import SafePayAgent
from .models import PaymentIntent, PolicyConfig

_COLOR = {"allow": "\033[92m", "hold": "\033[93m", "block": "\033[91m"}
_RESET = "\033[0m"


def _intent_from_dict(d: dict) -> PaymentIntent:
    allowed = {f.name for f in fields(PaymentIntent)}
    return PaymentIntent(**{k: v for k, v in d.items() if k in allowed})


def _print_human(result: dict) -> None:
    dec = result["decision"]
    v = dec["verdict"]
    color = _COLOR.get(v, "")
    i = dec["intent"]
    print(f"{color}[{v.upper()}]{_RESET} {i['currency']} {i['amount']:.2f} "
          f"to {i['recipient']}  (risk {dec['risk']:.2f})")
    for c in dec["checks"]:
        mark = "ok " if c["passed"] else "!! "
        print(f"    {mark}{c['name']:<9} {c['detail']}")
    if dec["executed"]:
        ex = dec["execution"] or {}
        print(f"    -> paid via PayPal [{result['paypal_mode']}] "
              f"batch {ex.get('payout_batch_id', '?')} ({ex.get('batch_status', '?')})")
    elif v != "allow":
        print(f"    -> not executed ({v})")
    signed = result["audit"]["signature"]["signed"]
    print(f"    {result['stamp']}  [{'signed' if signed else 'unsigned'}]")
    print()


def main(argv: list[str] | None = None) -> int:
    # Model output can contain non-ASCII (smart quotes, unicode hyphens); make
    # the console robust instead of crashing on a narrow codepage like cp1252.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    p = argparse.ArgumentParser(prog="safepay", description="SafePay payment guardrail")
    p.add_argument("--scenarios", help="path to a JSON file with a list of intents")
    p.add_argument("--recipient")
    p.add_argument("--amount", type=float)
    p.add_argument("--currency", default="USD")
    p.add_argument("--purpose", default="")
    p.add_argument("--category", default="uncategorized")
    p.add_argument("--merchant-name", default=None)
    p.add_argument("--merchant-url", default=None)
    p.add_argument("--goal", default="", help="the task the agent was given")
    p.add_argument("--per-tx-limit", type=float, default=500.0)
    p.add_argument("--daily-limit", type=float, default=2000.0)
    p.add_argument("--json", action="store_true", help="emit full JSON records")
    args = p.parse_args(argv)

    policy = PolicyConfig(per_tx_limit=args.per_tx_limit, daily_limit=args.daily_limit)

    intents: list[PaymentIntent] = []
    policy_override: PolicyConfig | None = None
    if args.scenarios:
        with open(args.scenarios, encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            if "policy" in data:
                pf = {f.name for f in fields(PolicyConfig)}
                policy_override = PolicyConfig(
                    **{k: v for k, v in data["policy"].items() if k in pf}
                )
            data = data.get("intents", [])
        intents = [_intent_from_dict(d) for d in data]
    elif args.recipient and args.amount is not None:
        intents = [PaymentIntent(
            recipient=args.recipient, amount=args.amount, currency=args.currency,
            purpose=args.purpose, category=args.category,
            merchant_name=args.merchant_name, merchant_url=args.merchant_url,
            task_goal=args.goal,
        )]
    else:
        p.error("give --scenarios, or --recipient with --amount")

    agent = SafePayAgent(policy=policy_override or policy)
    results = agent.pay_many(intents)

    if args.json:
        json.dump(results, sys.stdout, indent=2)
        print()
    else:
        mode = results[0]["paypal_mode"] if results else "?"
        print(f"\nSafePay guardrail · PayPal mode: {mode}\n")
        for r in results:
            _print_human(r)
    return 0
