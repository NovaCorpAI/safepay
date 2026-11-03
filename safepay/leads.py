"""Early-access sign-ups from the web console.

A judge or a visitor who wants SafePay in front of their own agents leaves an
email (and optionally the company, what the agent pays for and the monthly
volume). Leads are write-only from the web: there is no public endpoint that
lists them. With Supabase configured they persist in `safepay_leads` (email is
unique, so a repeat sign-up updates the row instead of duplicating it);
otherwise they live in memory, which is fine for a local run.
"""

from __future__ import annotations

import re
import time
from typing import Any

_EMAIL = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]{2,}$")
VOLUMES = ("under_1k", "1k_10k", "10k_100k", "over_100k", "unknown")
_LIMITS = {"company": 120, "role": 80, "use_case": 500, "source": 40}


class LeadError(ValueError):
    pass


def normalize_lead(data: dict[str, Any] | None) -> dict[str, Any]:
    """Validate and trim a sign-up. Raises LeadError with a message meant for
    the form (no stack traces, no field dumps)."""
    data = data or {}
    email = str(data.get("email") or "").strip().lower()
    if not email or len(email) > 254 or not _EMAIL.match(email):
        raise LeadError("enter a valid email address")
    lead: dict[str, Any] = {"email": email}
    for field, limit in _LIMITS.items():
        lead[field] = str(data.get(field) or "").strip()[:limit]
    lead["source"] = lead["source"] or "console"
    vol = str(data.get("monthly_volume") or "unknown").strip()
    lead["monthly_volume"] = vol if vol in VOLUMES else "unknown"
    return lead


def merge_lead(old: dict, new: dict) -> dict:
    """A repeat sign-up refreshes what it states and keeps what it left blank."""
    out = dict(old)
    for k, v in new.items():
        if v and v != "unknown":
            out[k] = v
    return out


class MemoryLeadStore:
    """Local runs and tests: leads keyed by email in this process."""

    def __init__(self):
        self._rows: dict[str, dict] = {}

    def add(self, lead: dict) -> bool:
        """Store a normalized lead. Returns True if the email was new."""
        old = self._rows.get(lead["email"])
        if old is None:
            self._rows[lead["email"]] = dict(lead, created_at=time.time())
            return True
        self._rows[lead["email"]] = merge_lead(old, lead)
        return False

    def count(self) -> int:
        return len(self._rows)


class SupabaseLeadStore:
    """Leads in Supabase, upserted on email so a repeat sign-up refreshes the row."""

    _COLS = "email,company,role,use_case,monthly_volume,source"

    def __init__(self, db):
        self.db = db

    def add(self, lead: dict) -> bool:
        rows = self.db.select("leads", {"select": self._COLS, "email": f"eq.{lead['email']}"})
        row = merge_lead(rows[0], lead) if rows else dict(lead)
        self.db.upsert("leads", row, {"on_conflict": "email"})
        return not rows

    def count(self) -> int:
        rows = self.db.select("leads", {"select": "email"})
        return len(rows)


def lead_store_from_config(config) -> MemoryLeadStore | SupabaseLeadStore:
    if getattr(config, "supabase_live", False):
        from .store_supabase import PostgREST

        return SupabaseLeadStore(PostgREST(config.supabase_url, config.supabase_key))
    return MemoryLeadStore()
