"""Spend the web and the model only when the answer can change.

The merchant check (Tavily + model) and the reasoning check (Nemotron) are
the only parts of SafePay that cost money per call. Their answers are a
function of their inputs: the same payee looked up an hour ago gives the
same evidence, and the same task and payment get the same judgment. So:

- each result is cached under a hash of exactly the inputs it read, for a
  TTL (default 24 hours, SAFEPAY_CHECK_CACHE_SECONDS; 0 disables);
- real calls count against a daily budget (SAFEPAY_EXTERNAL_DAILY_BUDGET,
  default 400; 0 means unlimited). When the day's budget is spent, the check
  fails closed: the payment is held for a person, never waved through.

A hosted demo that judges run over and over then costs almost nothing, and
nobody can drain the keys by hammering it. The store is Supabase (shared by
every instance, in the settings table) when configured, memory otherwise.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from datetime import datetime, timezone
from typing import Any


def key_for(kind: str, *parts: Any) -> str:
    raw = json.dumps([kind, *parts], sort_keys=True, default=str)
    return f"cache:{kind}:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:40]


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def ttl_seconds() -> int:
    try:
        return max(0, int(os.environ.get("SAFEPAY_CHECK_CACHE_SECONDS", "86400")))
    except ValueError:
        return 86400


def daily_budget() -> int:
    try:
        return max(0, int(os.environ.get("SAFEPAY_EXTERNAL_DAILY_BUDGET", "400")))
    except ValueError:
        return 400


class MemoryCache:
    def __init__(self) -> None:
        self._d: dict[str, tuple[float, Any]] = {}

    def get(self, key: str) -> Any | None:
        hit = self._d.get(key)
        if not hit:
            return None
        ts, val = hit
        if ttl_seconds() and time.time() - ts > ttl_seconds():
            return None
        return val

    def set(self, key: str, value: Any) -> None:
        self._d[key] = (time.time(), value)

    def spend(self, n: int = 1) -> int:
        """Count n external calls for today; return today's total."""
        k = f"budget:{_today()}"
        total = int((self._d.get(k) or (0, 0))[1]) + n
        self._d[k] = (time.time(), total)
        return total

    def spent_today(self) -> int:
        return int((self._d.get(f"budget:{_today()}") or (0, 0))[1])


class SupabaseCache:
    """The same, in safepay_settings (key -> {"value", "ts"})."""

    def __init__(self, db: Any) -> None:
        self.db = db

    def _row(self, key: str) -> dict | None:
        rows = self.db.select("settings", {"select": "value,updated_ts", "key": f"eq.{key}"})
        return rows[0] if rows else None

    def get(self, key: str) -> Any | None:
        row = self._row(key)
        if not row:
            return None
        if ttl_seconds() and time.time() - float(row.get("updated_ts") or 0) > ttl_seconds():
            return None
        return (row.get("value") or {}).get("v")

    def set(self, key: str, value: Any) -> None:
        self.db.upsert("settings", {"key": key, "value": {"v": value}, "updated_ts": time.time()},
                       {"on_conflict": "key"})

    def spend(self, n: int = 1) -> int:
        k = f"budget:{_today()}"
        row = self._row(k)
        total = int(((row or {}).get("value") or {}).get("v") or 0) + n
        self.db.upsert("settings", {"key": k, "value": {"v": total}, "updated_ts": time.time()},
                       {"on_conflict": "key"})
        return total

    def spent_today(self) -> int:
        row = self._row(f"budget:{_today()}")
        return int(((row or {}).get("value") or {}).get("v") or 0)
