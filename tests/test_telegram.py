"""Telegram one-tap approvals, with a fake HTTP layer and the real agent."""

from __future__ import annotations

from safepay.agent import SafePayAgent
from safepay.models import PaymentIntent, PolicyConfig
from safepay.telegram import TelegramNotifier


class FakeHttp:
    def __init__(self):
        self.calls = []

    def __call__(self, url, payload):
        self.calls.append((url.rsplit("/", 1)[1], payload))
        return {"ok": True, "result": {"message_id": 7}}


def _hold_agent():
    agent = SafePayAgent(policy=PolicyConfig(per_tx_limit=500, daily_limit=2000, hold_above=50))
    http = FakeHttp()
    agent.notifier = TelegramNotifier("tok", "123", http=http)
    return agent, http


def _tap(approval_id, action, chat="123", name="anael"):
    return {"callback_query": {"id": "cq1", "from": {"username": name},
                               "message": {"message_id": 7, "chat": {"id": int(chat)},
                                           "text": "SafePay HOLD"},
                               "data": f"apr:{approval_id}:{action}"}}


def test_hold_sends_card_with_two_buttons():
    agent, http = _hold_agent()
    out = agent.pay(PaymentIntent(recipient="v@x.example", amount=120, agent_id="ops:r1",
                                  purpose="hosting invoice"))
    assert out["decision"]["verdict"] == "hold"
    method, payload = http.calls[-1]
    assert method == "sendMessage" and payload["chat_id"] == "123"
    assert "USD 120.00 to v@x.example" in payload["text"] and out["approval_id"] in payload["text"]
    buttons = payload["reply_markup"]["inline_keyboard"][0]
    assert [b["callback_data"] for b in buttons] == [f"apr:{out['approval_id']}:approve",
                                                     f"apr:{out['approval_id']}:reject"]


def test_tap_approve_pays_and_chains_with_the_tapper_name():
    agent, http = _hold_agent()
    out = agent.pay(PaymentIntent(recipient="v@x.example", amount=120, agent_id="ops:r1"))
    res = agent.notifier.handle_update(_tap(out["approval_id"], "approve"), agent)
    assert res["action"] == "approve" and res["verdict"] == "allow" and res["executed"] is True
    assert agent.pending_approvals() == []
    last = agent.audit_log.records[-1]["payload"]
    assert last["verdict"] == "allow" and last["executed"] is True
    assert agent.approvals.all()[0].note == "via Telegram by anael"
    methods = [m for m, _ in http.calls]
    assert "answerCallbackQuery" in methods and "editMessageText" in methods
    # A second tap on the same card is refused, not paid twice.
    again = agent.notifier.handle_update(_tap(out["approval_id"], "approve"), agent)
    assert "error" in again and len(agent.audit_log.records) == 2


def test_tap_reject_and_foreign_chat():
    agent, http = _hold_agent()
    out = agent.pay(PaymentIntent(recipient="v@x.example", amount=120, agent_id="ops:r1"))
    foreign = agent.notifier.handle_update(_tap(out["approval_id"], "approve", chat="999"), agent)
    assert foreign["ignored"] == "foreign chat" and agent.pending_approvals()
    res = agent.notifier.handle_update(_tap(out["approval_id"], "reject"), agent)
    assert res["verdict"] == "block" and agent.pending_approvals() == []
    assert agent.notifier.handle_update({"message": {"text": "hi"}}, agent)["ignored"]


def test_chat_outage_never_changes_a_verdict():
    agent = SafePayAgent(policy=PolicyConfig(per_tx_limit=500, daily_limit=2000, hold_above=50))

    def boom(url, payload):
        raise RuntimeError("telegram down")
    agent.notifier = TelegramNotifier("tok", "123", http=boom)
    out = agent.pay(PaymentIntent(recipient="v@x.example", amount=120, agent_id="ops:r1"))
    assert out["decision"]["verdict"] == "hold" and out["approval_id"]
