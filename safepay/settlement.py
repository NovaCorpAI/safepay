"""Close the loop on a stablecoin payment: did what settled on chain match what
SafePay approved?

SafePay judges a payment before it is made and signs a precheck attestation
(amount, currency, a hash of the payee). For a USDC payment on Stellar the
money then moves on a public ledger, for example an AgentPOS store paid by an
agent through x402 on Stellar mainnet. This module reads that transaction from
Horizon (public, read only) and compares it with the signed precheck:

- the attestation is genuine (SafePay's Ed25519 key) and its verdict was allow;
- the transaction succeeded on the named network;
- it moved the approved token, issued by Circle (the known USDC issuer);
- to the approved payee (its address hashes to the attested payeeSha256);
- no more than the approved amount;
- before the check expired (plus a short grace for ledger close).

The answer is signed too, so a merchant, an auditor or the person can keep it.
SafePay moves no money here: it reads the ledger.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timedelta, timezone
from typing import Any

import requests

from safepay import receipt

SETTLEMENT = receipt.SETTLEMENT

HORIZON = {"stellar:pubnet": "https://horizon.stellar.org",
           "stellar:testnet": "https://horizon-testnet.stellar.org"}
# Circle's USDC issuers on Stellar (the same constants AgentPOS uses).
USDC_ISSUER = {"stellar:pubnet": "GA5ZSEJYB37JRC5AVCIA5MOP4RHTM335X2KGX3IHOJAPP5RE34K4KZVN",
               "stellar:testnet": "GBBD47IF6LWK7P7MDEVSCWR7DPUWV3NY3DTQEVFL4NAT4AQH3ZLLFLA5"}
EXPLORER = {"stellar:pubnet": "https://stellar.expert/explorer/public/tx/",
            "stellar:testnet": "https://stellar.expert/explorer/testnet/tx/"}
GRACE = timedelta(minutes=10)


class SettlementError(ValueError):
    pass


def _get(url: str) -> dict[str, Any]:
    r = requests.get(url, timeout=15, headers={"Accept": "application/json"})
    if r.status_code == 404:
        raise SettlementError("transaction not found on this network")
    r.raise_for_status()
    return r.json()


def fetch(tx_hash: str, network: str = "stellar:pubnet") -> dict[str, Any]:
    """The transaction and every token transfer in it (classic payments and
    Soroban token transfers, which is how x402 pays on Stellar)."""
    if network not in HORIZON:
        raise SettlementError(f"unsupported network {network}; use stellar:pubnet or stellar:testnet")
    if not re.fullmatch(r"[0-9a-f]{64}", tx_hash or ""):
        raise SettlementError("tx_hash must be the 64-character hex transaction hash")
    base = HORIZON[network]
    tx = _get(f"{base}/transactions/{tx_hash}")
    ops = _get(f"{base}/transactions/{tx_hash}/operations?limit=50")["_embedded"]["records"]
    transfers = []
    for o in ops:
        if o.get("type") in ("payment", "path_payment_strict_send", "path_payment_strict_receive"):
            transfers.append({"asset": o.get("asset_code") or "XLM", "issuer": o.get("asset_issuer"),
                              "amount": o.get("amount"), "from": o.get("from"), "to": o.get("to")})
        for c in o.get("asset_balance_changes") or []:
            if c.get("type") == "transfer":
                transfers.append({"asset": c.get("asset_code") or "XLM", "issuer": c.get("asset_issuer"),
                                  "amount": c.get("amount"), "from": c.get("from"), "to": c.get("to")})
    return {"hash": tx_hash, "network": network, "successful": bool(tx.get("successful")),
            "created_at": tx.get("created_at"), "ledger": tx.get("ledger"), "transfers": transfers,
            "explorer": EXPLORER[network] + tx_hash}


def _minor7(amount: str) -> int:
    whole, _, frac = (amount or "0").partition(".")
    return int(whole) * 10**7 + int((frac + "0000000")[:7])


def compare(att: dict[str, Any], tx: dict[str, Any], signer: str | None) -> dict[str, Any]:
    """Findings for one attestation against one fetched transaction."""
    ok, code = receipt.verify(att, signer=signer)
    b = att.get("body") or {}
    findings: list[dict[str, Any]] = []

    def f(name: str, passed: bool, detail: str) -> None:
        findings.append({"name": name, "passed": passed, "detail": detail})

    f("attestation", ok and b.get("v") == receipt.PRECHECK,
      "genuine SafePay precheck" if ok else f"attestation does not verify ({code})")
    verdict = (b.get("decision") or {}).get("verdict")
    f("approved", verdict == "allow", f"SafePay's verdict was {verdict}")
    f("succeeded", tx["successful"], "the transaction succeeded" if tx["successful"] else "the transaction failed")

    terms = b.get("terms") or {}
    cur = (terms.get("currency") or "").upper()
    issuer = USDC_ISSUER.get(tx["network"])
    payee_sha = terms.get("payeeSha256")
    to_payee = [t for t in tx["transfers"]
                if payee_sha and hashlib.sha256((t["to"] or "").lower().encode()).hexdigest() == payee_sha]
    f("payee", bool(to_payee), "paid the approved payee" if to_payee
      else "no transfer in this transaction went to the approved payee")
    right = [t for t in to_payee if t["asset"] == cur and t["issuer"] == issuer]
    f("token", bool(right) or not to_payee,
      f"{cur} issued by Circle ({issuer[:6]}...)" if right else
      f"the payee received a token other than Circle's {cur}")
    paid = sum(_minor7(t["amount"]) for t in right)
    try:
        approved = int(terms.get("amountMinor") or 0) * 10**5      # 2-decimal minor -> 7
    except (TypeError, ValueError):
        approved = 0
    if right:
        f("amount", paid <= approved,
          f"paid {paid / 1e7:.2f} {cur}, approved {approved / 1e7:.2f}" if paid <= approved
          else f"paid {paid / 1e7:.2f} {cur}, more than the approved {approved / 1e7:.2f}")
    when = tx.get("created_at")
    until = b.get("validUntil")
    if when and until:
        t_paid = datetime.fromisoformat(when.replace("Z", "+00:00"))
        t_until = datetime.fromisoformat(until.replace("Z", "+00:00"))
        t_at = datetime.fromisoformat((b.get("at") or until).replace("Z", "+00:00"))
        in_window = t_at - GRACE <= t_paid <= t_until + GRACE
        f("timing", in_window, "settled while the check was valid" if in_window
          else "settled outside the check's validity window")
    match = all(x["passed"] for x in findings)
    return {"match": match, "findings": findings, "paid": f"{paid / 1e7:.7f}".rstrip("0").rstrip("."),
            "currency": cur, "check": b.get("check")}


def verify_settlement(att: dict[str, Any], tx_hash: str, network: str = "stellar:pubnet") -> dict[str, Any]:
    tx = fetch(tx_hash, network)
    j = receipt.public_jwk()
    res = compare(att, tx, (j or {}).get("x"))
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    signed = receipt.sign_envelope({
        "v": SETTLEMENT, "check": res["check"], "at": now,
        "issuer": {"name": "SafePay", "url": "https://safepay.agentposhq.com",
                   "signer": (j or {}).get("x"), "kid": (j or {}).get("kid")},
        "settlement": {"network": network, "tx": tx_hash, "ledger": tx["ledger"],
                       "settledAt": tx["created_at"], "paid": res["paid"], "currency": res["currency"]},
        "match": res["match"], "findings": [{"name": x["name"], "passed": x["passed"]} for x in res["findings"]],
        "movedMoney": False,
    }) if j else None
    return dict(res, network=network, tx=tx_hash, ledger=tx["ledger"], settled_at=tx["created_at"],
                explorer=tx["explorer"], attestation=signed, moved_money=False)
