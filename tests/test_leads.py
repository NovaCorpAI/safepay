"""Early-access leads: validation, in-memory dedupe, and the Supabase upsert
against a fake PostgREST (no network)."""

from __future__ import annotations

import pytest

from safepay.leads import (
    LeadError, MemoryLeadStore, SupabaseLeadStore, lead_store_from_config, normalize_lead,
)


def test_normalize_trims_lowercases_and_defaults():
    lead = normalize_lead({"email": "  Ana@Example.COM ", "company": " Acme ",
                           "use_case": "x" * 600, "monthly_volume": "bogus"})
    assert lead["email"] == "ana@example.com"
    assert lead["company"] == "Acme"
    assert len(lead["use_case"]) == 500
    assert lead["monthly_volume"] == "unknown"
    assert lead["source"] == "console"


@pytest.mark.parametrize("bad", [None, {}, {"email": ""}, {"email": "nope"},
                                 {"email": "a@b"}, {"email": "a b@c.com"}])
def test_normalize_rejects_bad_email(bad):
    with pytest.raises(LeadError):
        normalize_lead(bad)


def test_memory_store_dedupes_on_email():
    s = MemoryLeadStore()
    assert s.add(normalize_lead({"email": "a@b.co"})) is True
    assert s.add(normalize_lead({"email": "A@B.CO", "company": "Acme"})) is False
    assert s.add(normalize_lead({"email": "a@b.co", "use_case": "payouts"})) is False
    assert s.count() == 1
    row = s._rows["a@b.co"]
    assert row["company"] == "Acme" and row["use_case"] == "payouts"   # blanks never wipe


class FakeDB:
    def __init__(self):
        self.rows: list[dict] = []
        self.upserts: list[tuple[dict, dict]] = []

    def select(self, table, params):
        assert table == "leads"
        email = params.get("email", "").removeprefix("eq.")
        return [r for r in self.rows if not email or r["email"] == email]

    def upsert(self, table, row, params=None):
        assert table == "leads" and params["on_conflict"] == "email"
        self.upserts.append((row, params))
        for r in self.rows:
            if r["email"] == row["email"]:
                r.update(row)
                return r
        self.rows.append(dict(row))
        return row


def test_supabase_store_upserts_and_reports_new():
    db = FakeDB()
    s = SupabaseLeadStore(db)
    assert s.add(normalize_lead({"email": "a@b.co"})) is True
    assert s.add(normalize_lead({"email": "a@b.co", "company": "Acme"})) is False
    assert s.add(normalize_lead({"email": "a@b.co", "monthly_volume": "1k_10k"})) is False
    assert s.count() == 1
    assert db.rows[0]["company"] == "Acme" and db.rows[0]["monthly_volume"] == "1k_10k"
    assert len(db.upserts) == 3 and db.upserts[-1][0]["company"] == "Acme"   # merged row sent


def test_store_picks_backend_from_config():
    class Cfg:
        supabase_live = False
    assert isinstance(lead_store_from_config(Cfg()), MemoryLeadStore)

    class Live:
        supabase_live = True
        supabase_url = "https://x.supabase.co"
        supabase_key = "k"
    assert isinstance(lead_store_from_config(Live()), SupabaseLeadStore)
