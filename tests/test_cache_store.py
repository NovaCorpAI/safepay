"""The check cache and the external-call budget: TTL expiry, bad settings,
and the Supabase-backed variant against a recorded fake table."""

from __future__ import annotations

import time

from safepay import cache
from safepay.cache import MemoryCache, SupabaseCache, daily_budget, key_for, ttl_seconds


def test_settings_fall_back_on_garbage(monkeypatch):
    monkeypatch.setenv("SAFEPAY_CHECK_CACHE_SECONDS", "lots")
    monkeypatch.setenv("SAFEPAY_EXTERNAL_DAILY_BUDGET", "many")
    assert ttl_seconds() == 86400 and daily_budget() == 400
    monkeypatch.setenv("SAFEPAY_CHECK_CACHE_SECONDS", "-5")
    monkeypatch.setenv("SAFEPAY_EXTERNAL_DAILY_BUDGET", "-1")
    assert ttl_seconds() == 0 and daily_budget() == 0
    monkeypatch.setenv("SAFEPAY_CHECK_CACHE_SECONDS", "60")
    monkeypatch.setenv("SAFEPAY_EXTERNAL_DAILY_BUDGET", "7")
    assert ttl_seconds() == 60 and daily_budget() == 7


def test_key_is_stable_and_input_sensitive():
    a = key_for("merchant", "v@x.example", {"b": 1, "a": 2})
    assert a == key_for("merchant", "v@x.example", {"a": 2, "b": 1})
    assert a != key_for("merchant", "w@x.example", {"a": 2, "b": 1})
    assert a.startswith("cache:merchant:") and len(a) == len("cache:merchant:") + 40


def test_memory_cache_expires_by_ttl(monkeypatch):
    monkeypatch.setenv("SAFEPAY_CHECK_CACHE_SECONDS", "100")
    c = MemoryCache()
    assert c.get("k") is None
    now = time.time()
    monkeypatch.setattr(cache.time, "time", lambda: now)
    c.set("k", {"v": 1})
    assert c.get("k") == {"v": 1}
    monkeypatch.setattr(cache.time, "time", lambda: now + 101)
    assert c.get("k") is None
    monkeypatch.setenv("SAFEPAY_CHECK_CACHE_SECONDS", "0")   # 0: never expires
    assert c.get("k") == {"v": 1}


def test_memory_budget_counts_today():
    c = MemoryCache()
    assert c.spent_today() == 0
    assert c.spend() == 1 and c.spend(3) == 4 and c.spent_today() == 4


class FakeDb:
    def __init__(self):
        self.rows, self.ups = {}, []

    def select(self, table, params):
        key = params["key"].removeprefix("eq.")
        return [self.rows[key]] if key in self.rows else []

    def upsert(self, table, row, params):
        assert table == "settings" and params == {"on_conflict": "key"}
        self.rows[row["key"]] = dict(row)
        self.ups.append(row["key"])


def test_supabase_cache_round_trip_and_expiry(monkeypatch):
    monkeypatch.setenv("SAFEPAY_CHECK_CACHE_SECONDS", "50")
    db = FakeDb()
    c = SupabaseCache(db)
    assert c.get("k") is None
    c.set("k", [1, 2])
    assert db.rows["k"]["value"] == {"v": [1, 2]} and c.get("k") == [1, 2]
    db.rows["k"]["updated_ts"] = time.time() - 51
    assert c.get("k") is None
    db.rows["k"]["updated_ts"] = None                     # a row without a stamp is stale
    assert c.get("k") is None


def test_supabase_budget_accumulates_in_the_settings_table():
    db = FakeDb()
    c = SupabaseCache(db)
    assert c.spent_today() == 0
    assert c.spend() == 1 and c.spend(2) == 3 and c.spent_today() == 3
    assert db.ups == [f"budget:{cache._today()}"] * 2
