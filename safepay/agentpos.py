"""Buying from an agent-ready store (AgentPOS), with SafePay as the buyer's
guardrail and a PayPal mandate as the rail.

AgentPOS (an open-source project from the same team) makes an existing store
buyable by AI agents: it publishes the catalog (UCP, MCP, OpenAPI) and quotes
a cart. Here SafePay's buyer agent reads the real catalog of AgentPOS's demo
store, asks the store for a real quote, and then pays through SafePay: the
quote total is opened as a mandate (judged by the full guardrail, authorized
with PayPal) and captured once. The amount always comes from the store's own
quote, never from the agent.

Honest about the rail: AgentPOS settles in USDC on Stellar. In this demo the
catalog and the quote are the store's real ones; the payment is a PayPal
sandbox authorization and capture at the quoted amount (1 USDC = 1 USD).

Only the configured store is ever called (AGENTPOS_STORE, default the demo
store), so this cannot be used to make SafePay fetch arbitrary URLs.
"""

from __future__ import annotations

import os
import re
import time
from typing import Any
from urllib.parse import urlparse

DEFAULT_STORE = "https://demo.agentposhq.com"
_CACHE: dict[str, tuple[float, Any]] = {}
TTL = 600


class StoreError(RuntimeError):
    pass


def store_url() -> str:
    url = (os.environ.get("AGENTPOS_STORE") or DEFAULT_STORE).rstrip("/")
    if urlparse(url).scheme != "https":
        raise StoreError("AGENTPOS_STORE must be an https URL")
    return url


def _get(path: str) -> Any:
    import requests

    r = requests.get(store_url() + path, timeout=15, headers={"Accept": "application/json",
                                                              "User-Agent": "SafePay-buyer/0.6"})
    if r.status_code != 200:
        raise StoreError(f"store answered {r.status_code}")
    return r.json()


def catalog() -> dict[str, Any]:
    """The store's catalog, cached for ten minutes."""
    hit = _CACHE.get("catalog")
    if hit and time.time() - hit[0] < TTL:
        return hit[1]
    d = _get("/agentpos/catalog")
    site = d.get("site") or {}
    items = [{"id": str(p.get("id")), "title": str(p.get("title") or "")[:120],
              "description": str(p.get("description") or "")[:240],
              "price": str((p.get("price") or {}).get("amount") or ""),
              "asset": str((p.get("price") or {}).get("asset") or ""),
              "url": str(p.get("url") or "")} for p in (d.get("items") or [])[:50]]
    out = {"store": {"name": str(site.get("name") or "AgentPOS store")[:80],
                     "url": str(site.get("url") or store_url())},
           "rail": d.get("payment") or {}, "items": items}
    _CACHE["catalog"] = (time.time(), out)
    return out


_ID = re.compile(r"^[a-z0-9-]{1,120}$")


def quote(lines: list[dict[str, Any]]) -> dict[str, Any]:
    """Ask the store for a real quote; return it plus the intent SafePay judges."""
    import requests

    clean = []
    for ln in (lines or [])[:10]:
        iid, qty = str(ln.get("itemId") or ""), int(ln.get("quantity") or 0)
        if _ID.match(iid) and 0 < qty <= 20:
            clean.append({"itemId": iid, "quantity": qty})
    if not clean:
        raise StoreError("choose at least one item")
    r = requests.post(store_url() + "/agentpos/cart", json={"items": clean}, timeout=15,
                      headers={"User-Agent": "SafePay-buyer/0.6"})
    if r.status_code not in (200, 201):
        raise StoreError(f"the store refused the cart ({r.status_code})")
    d = r.json()
    q = d.get("quote") or {}
    total = float(q.get("total") or 0)
    if total <= 0:
        raise StoreError("the store quoted nothing")
    cat = catalog()
    host = urlparse(cat["store"]["url"]).hostname or "store"
    what = ", ".join(f"{ln.get('quantity')} x {ln.get('title')}" for ln in q.get("lines") or [])
    return {
        "cart_id": d.get("cartId"), "expires": d.get("expiresAt"), "total": total,
        "asset": q.get("asset"), "network": q.get("network"), "lines": q.get("lines") or [],
        "store_policy": (d.get("policy") or {}).get("decision"),
        "intent": {"recipient": f"orders@{host}", "amount": round(total, 2), "currency": "USD",
                   "category": "digital goods", "merchant_name": cat["store"]["name"],
                   "merchant_url": cat["store"]["url"],
                   "purpose": f"AgentPOS cart {d.get('cartId')}: {what}"[:240]},
    }
