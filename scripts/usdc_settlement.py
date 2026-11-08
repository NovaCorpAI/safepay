"""A real USDC purchase on Stellar, judged before and verified after by SafePay.

SafePay never moves the money: the buyer's own wallet (for example the
AgentPOS buyer agent paying an AgentPOS store through x402) pays. This script
does the two SafePay steps through the public MCP endpoint:

  1. check   : judge the payment (store, amount, payee address) and save the
               signed precheck attestation to a file.
     python scripts/usdc_settlement.py check --key spk_... --payee G... --amount 1 \
            --merchant "AgentPOS demo store" --url https://demo.agentposhq.com \
            --task "Buy the mug from the demo store" --out precheck.json

  2. (pay)   : the buyer pays that payee that amount in USDC on Stellar, within
               15 minutes, and notes the transaction hash.

  3. verify  : check the transaction on the public ledger against the precheck.
     python scripts/usdc_settlement.py verify --key spk_... --att precheck.json --tx <hash>

The key comes from https://safepay.agentposhq.com/connect; its session policy
must allow USDC (Currencies: USD, USDC) on stellar.
"""

from __future__ import annotations

import argparse
import io
import json
import sys
import urllib.request

BASE = "https://safepay.agentposhq.com/mcp"
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")


def call(key: str, name: str, args: dict, base: str) -> dict:
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args}}
    req = urllib.request.Request(base, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"})
    with urllib.request.urlopen(req, timeout=90) as r:
        res = json.load(r)
    if "error" in res:
        sys.exit(f"MCP error: {res['error']}")
    out = res["result"].get("structuredContent") or {}
    if res["result"].get("isError"):
        sys.exit(f"tool error: {out.get('error')}")
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("step", choices=["check", "verify"])
    ap.add_argument("--key", required=True)
    ap.add_argument("--base", default=BASE)
    ap.add_argument("--payee"); ap.add_argument("--amount", type=float)
    ap.add_argument("--merchant", default="AgentPOS demo store")
    ap.add_argument("--url", default="https://demo.agentposhq.com")
    ap.add_argument("--task", default="Buy the mug from the AgentPOS demo store")
    ap.add_argument("--out", default="precheck.json")
    ap.add_argument("--att"); ap.add_argument("--tx")
    ap.add_argument("--network", default="stellar:pubnet")
    a = ap.parse_args()

    if a.step == "check":
        if not a.payee or not a.amount:
            sys.exit("--payee and --amount are required")
        d = call(a.key, "safepay_check_payment", {"merchant_name": a.merchant, "merchant_url": a.url,
                 "amount": a.amount, "currency": "USDC", "network": "stellar", "payee": a.payee,
                 "task": a.task, "category": "shopping"}, a.base)
        print(f"verdict {d['verdict'].upper()}  check {d['check_id']}  valid until {d['valid_until']}")
        for c in d.get("checks", []):
            print(f"  {'ok ' if c['passed'] else 'NO '} {c['name']}: {c['detail']}")
        if d["verdict"] != "allow":
            sys.exit("not approved: do not pay. Reasons: " + "; ".join(d.get("reasons", [])))
        with open(a.out, "w", encoding="utf-8") as f:
            json.dump(d["attestation"], f, indent=1)
        print(f"precheck attestation saved to {a.out}; pay within 15 minutes, then run verify")
    else:
        if not a.att or not a.tx:
            sys.exit("--att and --tx are required")
        att = json.load(open(a.att, encoding="utf-8"))
        d = call(a.key, "safepay_verify_settlement", {"attestation": att, "tx_hash": a.tx,
                                                      "network": a.network}, a.base)
        print(("MATCH" if d["match"] else "MISMATCH") + f"  paid {d['paid']} {d['currency']}  ledger {d['ledger']}")
        for f_ in d["findings"]:
            print(f"  {'ok ' if f_['passed'] else 'NO '} {f_['name']}: {f_['detail']}")
        print(d["explorer"])
        with open(a.att.replace(".json", "") + "-settlement.json", "w", encoding="utf-8") as f:
            json.dump(d["attestation"], f, indent=1)


if __name__ == "__main__":
    main()
