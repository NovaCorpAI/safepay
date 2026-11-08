"""SafePay Check: a remote MCP connector any agent calls before it pays.

Agents increasingly pay through their own checkout (Meta's Muse checks out
with PayPal, Shop Pay or Stripe Link; ChatGPT with ACP). SafePay does not need
to hold the money to protect it: the agent asks first, SafePay judges the
payment with the same guardrail as every other one, chains the verdict, and
answers with a signed pre-payment attestation the merchant (or anyone) can
verify offline. No tool here moves money, which is also what agent platforms
require today (Meta's Muse Connector Platform does not approve money-movement
tools, section 4.6 of its guidelines; it does accept read and check tools).

Transport: MCP streamable HTTP, stateless, JSON responses (POST /mcp). A
plain Flask route serves it, so it runs on the same serverless deployment as
the console. Auth: `Authorization: Bearer spk_...`, an agent key; the key
names the account, so each caller sees only its own policy and checks.

Tools
    safepay_check_payment   judge a payment before checkout; signed verdict
    safepay_verify          verify a seal id, a seal receipt or a precheck
    safepay_policy          the rules this key is judged under, in plain words
    safepay_recent_checks   this key's latest checks
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
import time
from dataclasses import replace
from typing import Any

from . import receipt, seal
from .models import PaymentIntent
from .policy_draft import describe_policy

PROTOCOL = "2025-06-18"
SUPPORTED = ("2025-06-18", "2025-03-26", "2024-11-05")
VALID_FOR = 15 * 60          # a precheck stands for 15 minutes
SERVER = {"name": "safepay", "title": "SafePay Check", "version": "0.7.0"}

_NO_MONEY = {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True}
_READ = {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}

TOOLS: list[dict[str, Any]] = [
    {
        "name": "safepay_check_payment",
        "title": "Check a payment before paying",
        "description": (
            "Checks a planned payment before it is made. Judges the merchant, amount and task against "
            "the account's payment rules, the payee's payment history, a live web check of the merchant "
            "and an intent check, and returns allow, hold or block with the reasons, a check id and a "
            "signed attestation valid for 15 minutes. Moves no money and stores no payee address or "
            "task text, only their hashes."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "merchant_name": {"type": "string", "description": "Who is being paid, as shown at checkout"},
                "amount": {"type": "number", "exclusiveMinimum": 0, "description": "Total to pay"},
                "currency": {"type": "string", "default": "USD", "description": "ISO 4217 code, or a dollar stablecoin: PYUSD or USDC"},
                "network": {"type": "string", "description": "For PYUSD or USDC: the network (stellar, ethereum, solana, arbitrum, base, polygon); payee is then the wallet address"},
                "merchant_url": {"type": "string", "description": "The store or checkout URL"},
                "payee": {"type": "string", "description": "The payee account if known (email, PayPal id, or wallet address for a stablecoin)"},
                "task": {"type": "string", "description": "What the user asked the agent to do, in their words"},
                "purpose": {"type": "string", "description": "What this payment is for"},
                "category": {"type": "string", "description": "e.g. software, travel, groceries"},
                "invoice_ref": {"type": "string", "description": "Invoice or order number, if any"},
            },
            "required": ["merchant_name", "amount", "task"],
        },
        "annotations": dict(_NO_MONEY, title="Check a payment before paying"),
    },
    {
        "name": "safepay_verify",
        "title": "Verify a SafePay seal or attestation",
        "description": ("Verifies a SafePay seal id (SP-XXXXXXXXXX), a signed receipt or a precheck "
                        "attestation against SafePay's published Ed25519 key. Returns whether it is "
                        "genuine and intact, and what it states."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "seal": {"type": "string", "description": "A seal id such as SP-3F9A0C21BE"},
                "attestation": {"type": "object", "description": "A {body, hash, signature} SafePay document"},
            },
        },
        "annotations": dict(_READ, title="Verify a SafePay seal or attestation"),
    },
    {
        "name": "safepay_verify_settlement",
        "title": "Check a stablecoin settlement against its SafePay check",
        "description": ("Reads a Stellar transaction from the public ledger (Horizon) and compares it with "
                        "a SafePay precheck attestation: whether it succeeded, paid the approved payee, in "
                        "Circle's USDC, no more than the approved amount, while the check was valid. "
                        "Returns the findings and a signed result. Reads the ledger only."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "attestation": {"type": "object", "description": "The precheck attestation safepay_check_payment returned"},
                "tx_hash": {"type": "string", "description": "The 64-character Stellar transaction hash"},
                "network": {"type": "string", "enum": ["stellar:pubnet", "stellar:testnet"], "default": "stellar:pubnet"},
            },
            "required": ["attestation", "tx_hash"],
        },
        "annotations": dict(_READ, title="Check a stablecoin settlement", openWorldHint=True),
    },
    {
        "name": "safepay_policy",
        "title": "Show the payment rules",
        "description": "Returns the payment rules this account is judged under, in plain words, with their fingerprint.",
        "inputSchema": {"type": "object", "properties": {}},
        "annotations": dict(_READ, title="Show the payment rules"),
    },
    {
        "name": "safepay_recent_checks",
        "title": "Recent checks",
        "description": "Returns this account's latest payment checks: verdict, merchant, amount and time.",
        "inputSchema": {"type": "object", "properties": {
            "limit": {"type": "integer", "minimum": 1, "maximum": 20, "default": 10}}},
        "annotations": dict(_READ, title="Recent checks"),
    },
]


class ToolError(ValueError):
    """A tool call the caller can fix (reported as isError, not a protocol error)."""


def _host(v: Any) -> str:
    return receipt._host(v)


def _iso(ts: float) -> str:
    return receipt._iso(ts) or ""


def _h(v: str) -> str:
    return "sha256:" + hashlib.sha256(v.strip().lower().encode()).hexdigest()[:16]


def _for_chain(decision: Any, payee: str, task: str, purpose: str, ref: str | None) -> Any:
    """The link the chain keeps: no payee address, task text, purpose or
    reference, only their hashes; check details scrubbed of them; evidence
    reduced to the rule names. Merchant, amount and verdict stay."""
    secrets = [s for s in (payee, task, purpose, ref or "") if s and len(s) >= 4]
    pat = re.compile("|".join(re.escape(s) for s in sorted(secrets, key=len, reverse=True)), re.I) if secrets else None
    scrub = (lambda t: pat.sub("[redacted]", t)) if pat else (lambda t: t)
    d = copy.copy(decision)
    d.intent = replace(decision.intent, recipient=_h(payee), task_goal=_h(task),
                       purpose=_h(purpose) if purpose else "", invoice_ref=_h(ref) if ref else None,
                       merchant_url=(f"https://{_host(decision.intent.merchant_url)}"
                                     if decision.intent.merchant_url else None))
    d.checks = [replace(c, detail=scrub(c.detail),
                        evidence=[{"rule": e["rule"]} for e in c.evidence if isinstance(e, dict) and e.get("rule")])
                for c in decision.checks]
    d.reasons = [scrub(r) for r in decision.reasons]
    return d


def check_payment(agent: Any, account: str, args: dict[str, Any]) -> dict[str, Any]:
    try:
        amount = float(args.get("amount"))
    except (TypeError, ValueError):
        raise ToolError("amount must be a number")
    if amount <= 0:
        raise ToolError("amount must be positive")
    merchant = str(args.get("merchant_name") or "").strip()[:200]
    task = str(args.get("task") or "").strip()[:1000]
    if not merchant or not task:
        raise ToolError("merchant_name and task are required")
    url = str(args.get("merchant_url") or "").strip()[:500] or None
    payee = str(args.get("payee") or "").strip()[:200] or (f"checkout@{_host(url)}" if url and _host(url) else merchant)
    intent = PaymentIntent(
        recipient=payee, amount=round(amount, 2), currency=str(args.get("currency") or "USD").upper()[:8],
        purpose=str(args.get("purpose") or "")[:500], category=str(args.get("category") or "uncategorized")[:60],
        merchant_name=merchant, merchant_url=url, agent_id=account, task_goal=task,
        invoice_ref=(str(args.get("invoice_ref"))[:80] if args.get("invoice_ref") else None),
        network=(str(args.get("network")).strip().lower()[:20] if args.get("network") else None))
    policy = agent.policy_for(account)
    decision = agent.guardrail.evaluate(intent, policy=policy)
    decision.event = "precheck"
    decision.execution = {"method": "precheck", "client": "mcp", "moved_money": False}
    record = agent.audit_log.append(_for_chain(decision, payee, task, intent.purpose, intent.invoice_ref))
    p = record.get("payload") or {}
    now = time.time()
    check_id = "SC-" + hashlib.sha256(f"{p.get('seq')}|{record.get('sha256')}".encode()).hexdigest()[:10].upper()
    payee_l = payee.strip().lower()
    j = receipt.public_jwk()
    att = receipt.sign_envelope({
        "v": receipt.PRECHECK, "check": check_id, "at": _iso(now), "validUntil": _iso(now + VALID_FOR),
        "issuer": {"name": "SafePay", "url": "https://safepay.agentposhq.com",
                   "signer": (j or {}).get("x"), "kid": (j or {}).get("kid")},
        "chain": {"seq": p.get("seq"), "hash": record.get("sha256"), "prev": p.get("prev")},
        "decision": {"verdict": decision.verdict.value, "risk": round(decision.risk, 3),
                     "policyHash": p.get("policy_hash")},
        "terms": {"amountMinor": receipt.minor(intent.amount, intent.currency), "currency": intent.currency,
                  "payeeDomain": _host(payee_l) or None, "payeeSha256": hashlib.sha256(payee_l.encode()).hexdigest(),
                  "merchantDomain": _host(url) or None,
                  **({"network": intent.network} if intent.network else {})},
        "movedMoney": False,
    }) if j else None
    todo = {"allow": "No rule or check objected to this merchant and amount.",
            "hold": "A person should review this payment before it is made; see the reasons.",
            "block": "The account's rules do not allow this payment; see the reasons."}[decision.verdict.value]
    return {
        "verdict": decision.verdict.value, "recommendation": todo, "check_id": check_id,
        "risk": round(decision.risk, 3), "reasons": list(decision.reasons),
        "checks": [{"name": c.name, "passed": c.passed, "detail": c.detail} for c in decision.checks],
        "moved_money": False, "valid_until": _iso(now + VALID_FOR), "chain_seq": p.get("seq"),
        "attestation": att, "keys": "https://safepay.agentposhq.com" + receipt.KEYS_PATH,
    }


def verify_settlement(args: dict[str, Any]) -> dict[str, Any]:
    from safepay import settlement
    att = args.get("attestation")
    if isinstance(att, str):
        try:
            att = json.loads(att)
        except ValueError:
            raise ToolError("attestation must be the JSON SafePay returned")
    if not isinstance(att, dict):
        raise ToolError("attestation is required")
    try:
        return settlement.verify_settlement(att, str(args.get("tx_hash") or "").strip().lower(),
                                            str(args.get("network") or "stellar:pubnet"))
    except settlement.SettlementError as exc:
        raise ToolError(str(exc))
    except Exception as exc:
        raise ToolError(f"could not read the ledger: {exc}")


def verify(agent: Any, args: dict[str, Any]) -> dict[str, Any]:
    att = args.get("attestation")
    if isinstance(att, str):
        try:
            att = json.loads(att)
        except ValueError:
            raise ToolError("attestation must be the JSON SafePay returned")
    if isinstance(att, dict):
        j = receipt.public_jwk()
        ok, code = receipt.verify(att, signer=(j or {}).get("x"))
        b = att.get("body") or {}
        out = {"genuine": ok, "code": code, "kind": "precheck" if b.get("v") == receipt.PRECHECK else "receipt",
               "states": {k: b.get(k) for k in ("check", "seal", "decision", "terms", "validUntil", "event", "at")
                          if b.get(k) is not None}}
        if ok and b.get("validUntil"):
            out["expired"] = b["validUntil"] < _iso(time.time())
        return out
    sid = seal.normalize(str(args.get("seal") or ""))
    if not sid:
        raise ToolError("pass a seal id (SP-XXXXXXXXXX) or an attestation")
    found = seal.find(agent.audit_log, sid)
    if not found:
        return {"genuine": False, "code": "RECEIPT_NOT_FOUND", "seal": sid}
    v = seal.view(found, sid)
    return {"genuine": bool(v.get("intact")), "seal": sid, "kind": "seal",
            "states": {k: v.get(k) for k in ("amount", "currency", "merchant", "recipient", "kind",
                                              "payout_status", "when", "seq", "linked")},
            "page": f"https://safepay.agentposhq.com/v/{sid}"}


def policy(agent: Any, account: str) -> dict[str, Any]:
    from .attest import policy_hash
    pol = agent.policy_for(account)
    return {"rules": describe_policy(pol), "fingerprint": policy_hash(pol)[:16],
            "tampered": getattr(pol, "_tamper", None)}


def recent(agent: Any, account: str, limit: Any = 10) -> dict[str, Any]:
    try:
        n = max(1, min(20, int(limit or 10)))
    except (TypeError, ValueError):
        n = 10
    rows = []
    for r in reversed(list(getattr(agent.audit_log, "records", []) or [])):
        p = r.get("payload") or {}
        i = p.get("intent") or {}
        if p.get("event") != "precheck" or i.get("agent_id") != account:
            continue
        rows.append({"seq": p.get("seq"), "verdict": p.get("verdict"), "merchant": i.get("merchant_name"),
                     "amount": i.get("amount"), "currency": i.get("currency"), "at": _iso(p.get("ts") or 0)})
        if len(rows) >= n:
            break
    return {"checks": rows}


# --- MCP over streamable HTTP (stateless, JSON responses) -----------------

def _ok(mid: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def _err(mid: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}


def _tool_result(data: dict[str, Any], is_error: bool = False) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": json.dumps(data, ensure_ascii=False)}],
            "structuredContent": data, "isError": is_error}


def handle(msg: Any, agent: Any, key: str | None) -> dict[str, Any] | None:
    """One JSON-RPC message in, one response out (None for notifications)."""
    if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0" or not isinstance(msg.get("method"), str):
        return _err(msg.get("id") if isinstance(msg, dict) else None, -32600, "invalid request")
    mid, method, params = msg.get("id"), msg["method"], msg.get("params") or {}
    if "id" not in msg:                       # notification (e.g. notifications/initialized)
        return None
    if method == "initialize":
        asked = str(params.get("protocolVersion") or "")
        return _ok(mid, {"protocolVersion": asked if asked in SUPPORTED else PROTOCOL,
                         "capabilities": {"tools": {"listChanged": False}}, "serverInfo": SERVER,
                         "instructions": ("SafePay Check judges planned payments and verifies SafePay seals "
                                          "and attestations. No tool moves money.")})
    if method == "ping":
        return _ok(mid, {})
    if method == "tools/list":
        return _ok(mid, {"tools": TOOLS})
    if method != "tools/call":
        return _err(mid, -32601, f"method not found: {method}")
    name, args = params.get("name"), params.get("arguments") or {}
    if not key:
        return _ok(mid, _tool_result({"error": "an agent key is required: Authorization: Bearer spk_... "
                                               "(get one at https://safepay.agentposhq.com/connect)"}, True))
    try:
        account = agent.resolve_agent("", key)
    except ValueError:
        return _ok(mid, _tool_result({"error": "invalid agent key"}, True))
    try:
        if name == "safepay_check_payment":
            data = check_payment(agent, account, args)
        elif name == "safepay_verify":
            data = verify(agent, args)
        elif name == "safepay_verify_settlement":
            data = verify_settlement(args)
        elif name == "safepay_policy":
            data = policy(agent, account)
        elif name == "safepay_recent_checks":
            data = recent(agent, account, args.get("limit"))
        else:
            return _err(mid, -32602, f"unknown tool: {name}")
    except ToolError as exc:
        return _ok(mid, _tool_result({"error": str(exc)}, True))
    return _ok(mid, _tool_result(data))
