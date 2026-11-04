"""Web console: policy in plain words, agent pause/resume, Telegram webhook."""

from __future__ import annotations

import os

for _k in ("NEBIUS_API_KEY", "TAVILY_API_KEY", "PAYPAL_CLIENT_ID", "PAYPAL_SECRET"):
    os.environ[_k] = ""

import pytest  # noqa: E402

import webapp.app as w  # noqa: E402
from safepay.telegram import TelegramNotifier  # noqa: E402


@pytest.fixture()
def client():
    w._reset_agent()
    return w.app.test_client()


def test_page_has_policy_editor_and_agents_panel(client):
    page = client.get("/").data
    assert b"policy-text" in page and b"agents-list" in page


def test_draft_then_apply_changes_this_run_only(client):
    d = client.post("/api/policy/draft", json={
        "text": "Up to $60 per payment, 300 a day, ask me over 40.", "run": "r1"}).get_json()
    assert d["policy"]["per_tx_limit"] == 60 and d["policy"]["hold_above"] == 40
    assert d["source"] == "rules" and d["rules"]
    a = client.post("/api/policy/apply", json={"run": "r1", "policy": d["policy"]}).get_json()
    assert a["ok"] and a["policy"]["per_tx_limit"] == 60
    assert client.get("/api/policy?run=r1").get_json()["custom"] is True
    assert client.get("/api/policy?run=r2").get_json()["custom"] is False
    blocked = client.post("/api/evaluate", json={
        "recipient": "billing@notion.so", "amount": 80, "category": "software",
        "merchant_name": "Notion", "agent_id": "ops:r1"}).get_json()
    assert blocked["decision"]["verdict"] == "block"
    held = client.post("/api/evaluate", json={
        "recipient": "billing@notion.so", "amount": 45, "category": "software",
        "merchant_name": "Notion", "agent_id": "ops:r1"}).get_json()
    assert held["decision"]["verdict"] == "hold"
    other = client.post("/api/evaluate", json={
        "recipient": "billing@notion.so", "amount": 65, "category": "software",
        "merchant_name": "Notion", "agent_id": "ops:r2"}).get_json()
    assert other["decision"]["verdict"] == "allow"
    r = client.post("/api/policy/reset", json={"run": "r1"}).get_json()
    assert r["ok"] and client.get("/api/policy?run=r1").get_json()["custom"] is False


def test_draft_rejects_bad_input(client):
    assert client.post("/api/policy/draft", json={"text": ""}).status_code == 400
    assert client.post("/api/policy/apply", json={"policy": {"per_tx_limit": 5}}).status_code == 400
    assert client.post("/api/policy/apply", json={"run": "r1", "policy": {"per_tx_limit": 0}}).status_code == 400


def test_agents_pause_and_resume(client):
    client.post("/api/evaluate", json={"recipient": "billing@notion.so", "amount": 10,
                                       "category": "software", "agent_id": "ops:r9"})
    rows = client.get("/api/agents?run=r9").get_json()["agents"]
    assert rows[0]["agent_id"] == "ops:r9" and rows[0]["status"] == "active"
    p = client.post("/api/agents/ops:r9/pause", json={"reason": "demo"}).get_json()
    assert p["ok"] and p["status"] == "paused"
    out = client.post("/api/evaluate", json={"recipient": "billing@notion.so", "amount": 10,
                                             "category": "software", "agent_id": "ops:r9"}).get_json()
    assert out["decision"]["verdict"] == "block" and out["decision"]["checks"][0]["name"] == "agent"
    assert client.post("/api/agents/ops:r9/resume").get_json()["status"] == "active"
    assert client.get("/api/agents").get_json()["agents"]


def test_telegram_webhook_requires_config_and_secret(client, monkeypatch):
    assert client.post("/api/telegram/webhook", json={}).status_code == 503
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "5")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "s3cret")
    calls = []
    w._agent.notifier = TelegramNotifier("t", "5", http=lambda u, p: calls.append(u) or {"ok": True})
    assert client.post("/api/telegram/webhook", json={}).status_code == 403
    r = client.post("/api/telegram/webhook", json={"message": {"text": "hi"}},
                    headers={"X-Telegram-Bot-Api-Secret-Token": "s3cret"})
    assert r.status_code == 200 and r.get_json()["ignored"]
