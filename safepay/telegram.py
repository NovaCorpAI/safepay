"""One-tap approvals over Telegram.

A HOLD should reach the person wherever they are, not wait in a web page they
have to open. With a bot token and a chat id configured, every HOLD is sent to
that chat as a card with two buttons, Approve and pay or Reject, and the tap
comes back through PayPal-style webhook delivery (Telegram POSTs the update to
`/api/telegram/webhook`, authenticated with the secret token Telegram echoes
in a header). The decision is resolved through the same `approve` / `reject`
path as the console and the MCP tools, so the audit chain shows who approved
it ("via Telegram by <name>") and the ledger is charged the same way.

Setup (once):
    1. Create a bot with @BotFather, copy the token.
    2. Open a chat with the bot, send it anything, and read your chat id
       (`python -m safepay.telegram --chat-id`).
    3. Set TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID and TELEGRAM_WEBHOOK_SECRET.
    4. Register the webhook: `python -m safepay.telegram --set-webhook
       https://<host>/api/telegram/webhook`.
"""

from __future__ import annotations

import argparse
import os
import secrets
import sys
from typing import Any, Callable

HttpPost = Callable[[str, dict[str, Any]], dict[str, Any]]


def _requests_post(url: str, payload: dict[str, Any]) -> dict[str, Any]:
    import requests

    resp = requests.post(url, json=payload, timeout=15)
    try:
        data = resp.json()
    except ValueError:
        data = {"ok": False, "description": resp.text[:200]}
    if not isinstance(data, dict):
        data = {"ok": False, "description": "unexpected response"}
    data.setdefault("ok", resp.status_code < 400)
    return data


class TelegramNotifier:
    def __init__(self, token: str, chat_id: str, http: HttpPost | None = None):
        self.token = token
        self.chat_id = str(chat_id)
        self._http = http or _requests_post
        self.api = f"https://api.telegram.org/bot{token}"

    def _call(self, method: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            return self._http(f"{self.api}/{method}", payload)
        except Exception as exc:  # never let a chat outage break a payment path
            return {"ok": False, "description": str(exc)}

    # --- outbound: a HOLD card ----------------------------------------------

    @staticmethod
    def hold_text(approval_id: str, decision: dict[str, Any]) -> str:
        i = decision.get("intent") or {}
        why = " · ".join(decision.get("reasons") or []) or "held for review"
        lines = [
            f"SafePay HOLD · {i.get('currency', 'USD')} {float(i.get('amount', 0)):.2f} "
            f"to {i.get('recipient', '?')}",
            f"Agent: {i.get('agent_id', '?')}",
        ]
        if i.get("merchant_name"):
            lines.append(f"Merchant: {i['merchant_name']}")
        if i.get("purpose"):
            lines.append(f"Purpose: {str(i['purpose'])[:160]}")
        lines.append(f"Why: {why[:400]}")
        lines.append(f"Approval {approval_id}")
        return "\n".join(lines)

    def send_hold(self, approval_id: str, decision: dict[str, Any]) -> dict[str, Any]:
        return self._call("sendMessage", {
            "chat_id": self.chat_id,
            "text": self.hold_text(approval_id, decision),
            "reply_markup": {"inline_keyboard": [[
                {"text": "Approve and pay", "callback_data": f"apr:{approval_id}:approve"},
                {"text": "Reject", "callback_data": f"apr:{approval_id}:reject"},
            ]]},
        })

    # --- inbound: a tap --------------------------------------------------

    def handle_update(self, update: dict[str, Any], agent: Any) -> dict[str, Any]:
        """Resolve the approval a button tap names. `agent` is a SafePayAgent
        (anything with approve(id, note) and reject(id, note))."""
        cq = (update or {}).get("callback_query")
        if not isinstance(cq, dict):
            return {"ok": True, "ignored": "not a button tap"}
        msg = cq.get("message") or {}
        chat = str((msg.get("chat") or {}).get("id", ""))
        if chat != self.chat_id:
            self._call("answerCallbackQuery", {"callback_query_id": cq.get("id"),
                                               "text": "This chat is not SafePay's approver."})
            return {"ok": True, "ignored": "foreign chat"}
        data = str(cq.get("data") or "")
        parts = data.split(":")
        if len(parts) != 3 or parts[0] != "apr" or parts[2] not in ("approve", "reject"):
            self._call("answerCallbackQuery", {"callback_query_id": cq.get("id"),
                                               "text": "Unknown button."})
            return {"ok": True, "ignored": "unknown callback"}
        _, approval_id, action = parts
        who = (cq.get("from") or {})
        name = who.get("username") or who.get("first_name") or "someone"
        note = f"via Telegram by {name}"
        try:
            out = agent.approve(approval_id, note) if action == "approve" \
                else agent.reject(approval_id, note)
        except ValueError as exc:
            self._call("answerCallbackQuery", {"callback_query_id": cq.get("id"),
                                               "text": str(exc)[:180]})
            self._edit(msg, f"{msg.get('text', '')}\n\n(already resolved)")
            return {"ok": True, "approval_id": approval_id, "error": str(exc)}
        dec = out.get("decision") or {}
        seq = ((out.get("audit") or {}).get("payload") or {}).get("seq")
        if action == "approve":
            ex = dec.get("execution") or {}
            done = (f"Approved and paid by {name}" if dec.get("executed")
                    else f"Approved by {name}, but the payout did not go out: "
                         f"{ex.get('error') or ex.get('note') or 'see console'}")
        else:
            done = f"Rejected by {name}"
        if seq is not None:
            done += f" · chain link #{seq}"
        self._call("answerCallbackQuery", {"callback_query_id": cq.get("id"), "text": done[:180]})
        self._edit(msg, f"{msg.get('text', '')}\n\n{done}")
        return {"ok": True, "approval_id": approval_id, "action": action,
                "verdict": dec.get("verdict"), "executed": dec.get("executed"), "seq": seq}

    def _edit(self, msg: dict[str, Any], text: str) -> None:
        if not msg.get("message_id"):
            return
        self._call("editMessageText", {"chat_id": self.chat_id,
                                       "message_id": msg["message_id"], "text": text[:4000]})

    # --- setup helpers -----------------------------------------------------

    def set_webhook(self, url: str, secret: str) -> dict[str, Any]:
        return self._call("setWebhook", {"url": url, "secret_token": secret,
                                         "allowed_updates": ["callback_query", "message"]})

    def get_updates(self) -> dict[str, Any]:
        return self._call("getUpdates", {"timeout": 0})


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m safepay.telegram",
                                description="Telegram approvals setup for SafePay")
    p.add_argument("--set-webhook", metavar="URL",
                   help="register URL as the bot's webhook (uses TELEGRAM_WEBHOOK_SECRET)")
    p.add_argument("--chat-id", action="store_true",
                   help="print the chat ids that have messaged the bot (send it a message first)")
    p.add_argument("--new-secret", action="store_true", help="print a fresh webhook secret")
    args = p.parse_args(argv)
    if args.new_secret:
        print(secrets.token_urlsafe(24))
        return 0
    token = (os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
    if not token:
        print("TELEGRAM_BOT_TOKEN is not set", file=sys.stderr)
        return 2
    bot = TelegramNotifier(token, os.environ.get("TELEGRAM_CHAT_ID") or "")
    if args.chat_id:
        res = bot.get_updates()
        seen = []
        for u in res.get("result") or []:
            m = u.get("message") or u.get("edited_message") or {}
            c = m.get("chat") or {}
            if c.get("id") and c["id"] not in seen:
                seen.append(c["id"])
                print(f"chat id {c['id']}  ({c.get('first_name') or c.get('title') or c.get('type')})")
        if not seen:
            print("no messages yet: open the bot in Telegram, send it 'hi', then run this again")
        return 0
    if args.set_webhook:
        secret = (os.environ.get("TELEGRAM_WEBHOOK_SECRET") or "").strip()
        if not secret:
            print("TELEGRAM_WEBHOOK_SECRET is not set (make one with --new-secret)", file=sys.stderr)
            return 2
        res = bot.set_webhook(args.set_webhook, secret)
        print("ok" if res.get("ok") else f"failed: {res.get('description')}")
        return 0 if res.get("ok") else 1
    p.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
