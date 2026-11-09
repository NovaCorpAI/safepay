"""Telegram transport, card text, the odd button taps, and the setup CLI."""

from __future__ import annotations

import json
from types import SimpleNamespace

import requests

from safepay import telegram as tg
from safepay.telegram import TelegramNotifier


def _resp(status, body=None, text=""):
    def _json():
        if body is None:
            raise ValueError("no json")
        return body
    return SimpleNamespace(status_code=status, text=text, json=_json)


def test_requests_post_normalizes_every_answer(monkeypatch):
    monkeypatch.setattr(requests, "post", lambda *a, **k: _resp(200, {"ok": True, "result": 1}))
    assert tg._requests_post("u", {}) == {"ok": True, "result": 1}
    monkeypatch.setattr(requests, "post", lambda *a, **k: _resp(502, text="<html>bad gateway</html>"))
    out = tg._requests_post("u", {})
    assert out["ok"] is False and "bad gateway" in out["description"]
    monkeypatch.setattr(requests, "post", lambda *a, **k: _resp(200, [1, 2]))
    assert tg._requests_post("u", {}) == {"ok": False, "description": "unexpected response"}
    monkeypatch.setattr(requests, "post", lambda *a, **k: _resp(400, {"description": "chat not found"}))
    assert tg._requests_post("u", {})["ok"] is False


def test_hold_text_lists_merchant_purpose_and_reasons():
    text = TelegramNotifier.hold_text("apr-1", {
        "intent": {"amount": 120, "currency": "EUR", "recipient": "v@x.example", "agent_id": "ops",
                   "merchant_name": "Acme", "purpose": "p" * 300},
        "reasons": ["over the ask-me line", "new payee"]})
    lines = text.split("\n")
    assert lines[0] == "SafePay HOLD · EUR 120.00 to v@x.example" and lines[1] == "Agent: ops"
    assert lines[2] == "Merchant: Acme" and lines[3] == "Purpose: " + "p" * 160
    assert lines[4] == "Why: over the ask-me line · new payee" and lines[5] == "Approval apr-1"
    bare = TelegramNotifier.hold_text("apr-2", {})
    assert "Merchant" not in bare and "Why: held for review" in bare


def test_send_alert_truncates_and_never_raises():
    calls = []
    bot = TelegramNotifier("tok", 5, http=lambda u, p: calls.append((u, p)) or {"ok": True})
    assert bot.chat_id == "5" and bot.api == "https://api.telegram.org/bottok"
    bot.send_alert("x" * 5000)
    assert calls[0][0].endswith("/sendMessage") and len(calls[0][1]["text"]) == 4000
    down = TelegramNotifier("tok", 5, http=lambda u, p: (_ for _ in ()).throw(OSError("dns")))
    assert down.send_alert("hi") == {"ok": False, "description": "dns"}


def _tap(data, chat=5, message=None):
    msg = {"message_id": 9, "chat": {"id": chat}, "text": "card"} if message is None else message
    return {"callback_query": {"id": "cq", "from": {"first_name": "Ana"}, "message": msg, "data": data}}


def test_unknown_buttons_are_answered_and_ignored():
    calls = []
    bot = TelegramNotifier("tok", 5, http=lambda u, p: calls.append((u.rsplit("/", 1)[1], p)) or {"ok": True})
    assert bot.handle_update(_tap("apr:x"), None)["ignored"] == "unknown callback"
    assert bot.handle_update(_tap("apr:x:explode"), None)["ignored"] == "unknown callback"
    assert bot.handle_update(_tap("nope:x:approve"), None)["ignored"] == "unknown callback"
    assert bot.handle_update(None, None)["ignored"] == "not a button tap"
    assert [m for m, _ in calls] == ["answerCallbackQuery"] * 3
    assert calls[0][1] == {"callback_query_id": "cq", "text": "Unknown button."}


def test_approval_that_did_not_pay_says_so():
    calls = []
    bot = TelegramNotifier("tok", 5, http=lambda u, p: calls.append((u.rsplit("/", 1)[1], p)) or {"ok": True})

    class Agent:
        def approve(self, approval_id, note):
            assert note == "via Telegram by Ana"
            return {"decision": {"verdict": "allow", "executed": False,
                                 "execution": {"error": "PayPal 503"}},
                    "audit": {"payload": {"seq": 4}}}
    out = bot.handle_update(_tap("apr:apr-1:approve"), Agent())
    assert out == {"ok": True, "approval_id": "apr-1", "action": "approve",
                   "verdict": "allow", "executed": False, "seq": 4}
    answer = dict(calls)["answerCallbackQuery"]["text"]
    assert answer.startswith("Approved by Ana, but the payout did not go out: PayPal 503")
    assert answer.endswith("chain link #4")
    assert dict(calls)["editMessageText"] == {"chat_id": "5", "message_id": 9,
                                              "text": "card\n\n" + answer}


def test_reject_without_a_message_id_skips_the_edit():
    calls = []
    bot = TelegramNotifier("tok", 5, http=lambda u, p: calls.append(u.rsplit("/", 1)[1]) or {"ok": True})

    class Agent:
        def reject(self, approval_id, note):
            return {"decision": {"verdict": "block", "executed": False}, "audit": {}}
    out = bot.handle_update(_tap("apr:apr-1:reject", message={"chat": {"id": 5}}), Agent())
    assert out["action"] == "reject" and out["seq"] is None
    assert calls == ["answerCallbackQuery"]


def test_setup_helpers_send_the_right_methods():
    calls = []
    bot = TelegramNotifier("tok", 5, http=lambda u, p: calls.append((u.rsplit("/", 1)[1], p)) or {"ok": True})
    bot.set_webhook("https://h.example/api/telegram/webhook", "s3cret")
    bot.get_updates()
    assert calls[0] == ("setWebhook", {"url": "https://h.example/api/telegram/webhook",
                                       "secret_token": "s3cret",
                                       "allowed_updates": ["callback_query", "message"]})
    assert calls[1] == ("getUpdates", {"timeout": 0})


# --- the setup CLI -----------------------------------------------------------------

def test_cli_new_secret_and_missing_token(monkeypatch, capsys):
    assert tg.main(["--new-secret"]) == 0
    assert len(capsys.readouterr().out.strip()) >= 30
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "")
    assert tg.main(["--chat-id"]) == 2
    assert "TELEGRAM_BOT_TOKEN is not set" in capsys.readouterr().err


def test_cli_chat_id_lists_each_chat_once(monkeypatch, capsys):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    updates = {"ok": True, "result": [
        {"message": {"chat": {"id": 11, "first_name": "Ana"}}},
        {"edited_message": {"chat": {"id": 11, "first_name": "Ana"}}},
        {"message": {"chat": {"id": 22, "type": "group"}}},
        {"channel_post": {}},
    ]}
    monkeypatch.setattr(tg, "_requests_post", lambda u, p: updates)
    assert tg.main(["--chat-id"]) == 0
    out = capsys.readouterr().out
    assert out.count("chat id 11") == 1 and "chat id 22  (group)" in out
    monkeypatch.setattr(tg, "_requests_post", lambda u, p: {"ok": True, "result": []})
    assert tg.main(["--chat-id"]) == 0
    assert "no messages yet" in capsys.readouterr().out


def test_cli_set_webhook(monkeypatch, capsys):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "")
    assert tg.main(["--set-webhook", "https://h.example/hook"]) == 2
    assert "TELEGRAM_WEBHOOK_SECRET is not set" in capsys.readouterr().err
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "s3cret")
    sent = []
    monkeypatch.setattr(tg, "_requests_post", lambda u, p: sent.append(p) or {"ok": True})
    assert tg.main(["--set-webhook", "https://h.example/hook"]) == 0
    assert sent[0]["secret_token"] == "s3cret" and capsys.readouterr().out.strip() == "ok"
    monkeypatch.setattr(tg, "_requests_post", lambda u, p: {"ok": False, "description": "bad url"})
    assert tg.main(["--set-webhook", "http://h.example/hook"]) == 1
    assert "failed: bad url" in capsys.readouterr().out


def test_cli_without_flags_prints_help(monkeypatch, capsys):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    assert tg.main([]) == 0
    assert "--set-webhook" in capsys.readouterr().out
