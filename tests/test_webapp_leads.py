"""Web console early-access endpoint (offline, in-memory lead store)."""

from __future__ import annotations

import pytest

import webapp.app as w
from safepay.leads import MemoryLeadStore


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr(w, "_leads", MemoryLeadStore())
    monkeypatch.setattr(w, "_RL_MAX", 0)
    return w.app.test_client()


def test_page_has_early_access_form(client):
    html = client.get("/").data
    assert b"Early access" in html and b'id="early-form"' in html


def test_signup_then_repeat(client):
    r = client.post("/api/early-access", json={
        "email": "Ana@Example.com", "company": "Acme", "use_case": "SaaS invoices",
        "monthly_volume": "1k_10k", "source": "forged",
    })
    assert r.status_code == 200 and r.get_json() == {"ok": True, "new": True}
    r2 = client.post("/api/early-access", json={"email": "ana@example.com"})
    assert r2.get_json() == {"ok": True, "new": False}
    assert w._leads.count() == 1
    assert w._leads._rows["ana@example.com"]["source"] == "console"   # client cannot set it


def test_bad_email_is_400_and_not_stored(client):
    r = client.post("/api/early-access", json={"email": "not-an-email"})
    assert r.status_code == 400 and r.get_json()["ok"] is False
    assert client.post("/api/early-access", data="x").status_code == 400
    assert w._leads.count() == 0


def test_no_public_listing(client):
    assert client.get("/api/early-access").status_code == 405
    assert client.get("/api/leads").status_code == 404


def test_store_failure_is_503(client, monkeypatch):
    class Broken:
        def add(self, lead):
            raise RuntimeError("db down")
    monkeypatch.setattr(w, "_leads", Broken())
    r = client.post("/api/early-access", json={"email": "a@b.co"})
    assert r.status_code == 503 and r.get_json()["ok"] is False
