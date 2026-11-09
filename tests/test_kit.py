"""The counter kit (safepay-kit) against the real web console: a scripted
board presses the buttons, the Flask test client answers the HTTP calls."""

from __future__ import annotations

import json
import os

for _k in ("NEBIUS_API_KEY", "TAVILY_API_KEY", "PAYPAL_CLIENT_ID", "PAYPAL_SECRET"):
    os.environ[_k] = ""

import pytest  # noqa: E402

import webapp.app as w  # noqa: E402
from safepay.kit import (APPROVE, ERROR, FREEZE, FROZEN, HOLD, IDLE, OK, QUIT, REJECT,  # noqa: E402
                         Console, KeyBoard, Kit, card, main)


class HoldingChat:
    model = "fake"

    def complete(self, system, user, temperature=0.1):
        return json.dumps({"aligned": False, "risk": 0.9, "reason": "off-goal"})


class ScriptedBoard:
    """Presses the queued buttons in order; records lights and cards."""

    def __init__(self, presses):
        self.presses = list(presses)
        self.lights, self.cards, self.closed = [], [], False

    def light(self, state):
        if not self.lights or self.lights[-1] != state:
            self.lights.append(state)

    def show(self, text):
        self.cards.append(text)

    def wait_button(self, timeout):
        return self.presses.pop(0) if self.presses else None

    def close(self):
        self.closed = True


@pytest.fixture()
def client():
    w._reset_agent()
    w._agent.guardrail.chat = HoldingChat()
    return w.app.test_client()


def _http(client):
    def call(method, path, body):
        r = client.open(path, method=method, json=body)
        return r.status_code, (r.get_json() or {})
    return call


def _hold(client, agent_id="shop-bot:k1", amount=120):
    r = client.post("/api/evaluate", json={
        "recipient": "payouts@casino.example", "amount": amount, "category": "entertainment",
        "merchant_name": "Casino", "purpose": "weekend promo", "task_goal": "pay invoices",
        "agent_id": agent_id,
    }).get_json()
    assert r["decision"]["verdict"] == "hold"
    return r["approval_id"]


def test_card_reads_like_a_receipt(client):
    aid = _hold(client)
    text = card(client.get("/api/approvals").get_json()["pending"][0])
    assert text.startswith("HOLD USD 120.00 to Casino")
    assert "agent shop-bot:k1" in text and aid in text and "for: weekend promo" in text
    assert "[green] approve" in text


def test_green_button_approves_and_chains_the_device(client):
    aid = _hold(client)
    board = ScriptedBoard([APPROVE])
    kit = Kit(Console("http://x", run="k1", device="counter-7", http=_http(client)), board)
    assert kit.tick() == "approved"
    assert board.lights == [HOLD, OK] and len(board.cards) == 1
    assert client.get("/api/approvals").get_json()["count"] == 0
    last = w._agent.audit_log.records[-1]["payload"]
    assert last["verdict"] == "allow" and "approved by human: via kit counter-7" in last["reasons"]
    assert w._agent.approvals.get(aid).note == "via kit counter-7"
    # Nothing left: the light goes back to idle and no card is shown again.
    assert kit.tick() == "idle" and board.lights[-1] == IDLE and len(board.cards) == 1


def test_red_button_rejects_without_paying(client):
    _hold(client)
    kit = Kit(Console("http://x", http=_http(client)), ScriptedBoard([REJECT]))
    assert kit.tick() == "rejected"
    last = w._agent.audit_log.records[-1]["payload"]
    assert last["verdict"] == "block" and last["executed"] is False
    assert "rejected by human: via kit kit" in last["reasons"]


def test_black_button_freezes_the_held_agent_then_thaws_it(client):
    _hold(client, agent_id="shop-bot:k1")
    board = ScriptedBoard([FREEZE, None, FREEZE])
    kit = Kit(Console("http://x", run="k1", http=_http(client)), board)
    assert kit.tick() == "frozen" and board.lights[-1] == FROZEN
    rows = {a["agent_id"]: a for a in client.get("/api/agents?run=k1").get_json()["agents"]}
    assert rows["shop-bot:k1"]["status"] == "paused" and "kit" in rows["shop-bot:k1"]["reason"]
    # Frozen agent: a new payment is blocked before any rule runs.
    r = client.post("/api/evaluate", json={"recipient": "v@x.example", "amount": 5,
                                           "agent_id": "shop-bot:k1"}).get_json()
    assert r["decision"]["verdict"] == "block" and r["decision"]["checks"][0]["name"] == "agent"
    assert kit.tick() == "waiting"          # the hold is still there, nobody pressed
    assert kit.tick() == "thawed"
    rows = {a["agent_id"]: a for a in client.get("/api/agents?run=k1").get_json()["agents"]}
    assert rows["shop-bot:k1"]["status"] == "active"


def test_freeze_with_nothing_held_acts_on_the_configured_agent(client):
    kit = Kit(Console("http://x", http=_http(client)), ScriptedBoard([FREEZE]), agent_id="shop-bot:k1")
    assert kit.tick() == "frozen"
    rows = {a["agent_id"]: a for a in client.get("/api/agents?run=k1").get_json()["agents"]}
    assert rows["shop-bot:k1"]["status"] == "paused"


def test_only_the_kits_session_lights_up(client):
    _hold(client, agent_id="other-bot:zz")
    board = ScriptedBoard([])
    assert Kit(Console("http://x", run="k1", http=_http(client)), board).tick() == "idle"
    assert board.lights == [IDLE] and board.cards == []


def test_operator_token_travels_in_the_header(client, monkeypatch):
    monkeypatch.setenv("SAFEPAY_OPERATOR_TOKEN", "sekret")
    _hold(client)
    seen = {}

    def http(method, path, body):
        r = client.open(path, method=method, json=body, headers={"X-SafePay-Operator": seen.get("tok", "")})
        return r.status_code, (r.get_json() or {})

    kit = Kit(Console("http://x", http=http), ScriptedBoard([APPROVE, APPROVE]))
    assert kit.tick() == "error" and kit.board.lights[-1] == ERROR   # refused without the token
    seen["tok"] = "sekret"
    assert kit.tick() == "approved"


def test_console_down_shows_the_error_light_and_keeps_going():
    def http(method, path, body):
        raise ConnectionError("no route")
    board = ScriptedBoard([])
    kit = Kit(Console("http://x", http=http), board)
    assert kit.tick() == "error" and board.lights == [ERROR]


def test_run_stops_on_quit_and_closes_the_board(client):
    board = ScriptedBoard([QUIT])
    Kit(Console("http://x", http=_http(client)), board).run()
    assert board.closed is True


def test_keyboard_board_prints_light_changes_once(capsys):
    b = KeyBoard()
    b.light(HOLD); b.light(HOLD); b.light(IDLE); b.show("card")
    out = capsys.readouterr().out
    assert out.count("[light] hold") == 1 and "[light] idle" in out and "card" in out


def test_main_falls_back_to_keys_and_quits(client, monkeypatch, capsys):
    monkeypatch.setattr("safepay.kit._requests_http", lambda base, headers: _http(client))
    monkeypatch.setattr(KeyBoard, "wait_button", lambda self, timeout: QUIT)
    main(["--url", "http://x", "--board", "keys", "--device", "bench"])
    assert "safepay-kit bench: watching http://x with KeyBoard" in capsys.readouterr().out
