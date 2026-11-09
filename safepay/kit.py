"""The SafePay kit: a box on the counter with a light and three buttons.

A HOLD should reach the owner where they stand, not only on a phone or a web
page. The kit is a Raspberry Pi (any model with GPIO) running this module: an
amber light comes on when the agent's payment is waiting for a person, the
green button approves it, the red button rejects it, and the black button
freezes the agent (press again to thaw it). Every press goes through the same
`/api/approvals/<id>/approve|reject` and `/api/agents/<id>/pause|resume`
endpoints as the console, the MCP tools and Telegram, so the audit chain shows
"via kit <device>" on the link, signed like any other human decision.

The same code runs without hardware: with no GPIO library the kit falls back
to the keyboard (a = approve, r = reject, f = freeze, q = quit) and prints the
card it would show. That is how it is tested, and how you try it on a laptop
before soldering anything. Wiring, parts and the five setup steps are in
docs/kit.md.

Run it:
    safepay-kit --url https://safepay.agentposhq.com --run <session id>
    safepay-kit --url http://localhost:5000 --board keys        # no hardware
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from typing import Any, Callable, Protocol

HOLD, IDLE, FROZEN, OK, ERROR = "hold", "idle", "frozen", "ok", "error"
APPROVE, REJECT, FREEZE, QUIT = "approve", "reject", "freeze", "quit"

# Default BCM pins (see docs/kit.md for the wiring table).
PINS = {"amber": 17, "green": 27, "red": 22, "approve": 5, "reject": 6, "freeze": 13}

Http = Callable[[str, str, dict[str, Any] | None], tuple[int, dict[str, Any]]]


# --- the console, over HTTP ---------------------------------------------------

def _requests_http(base: str, headers: dict[str, str]) -> Http:
    import requests

    def call(method: str, path: str, body: dict[str, Any] | None) -> tuple[int, dict[str, Any]]:
        resp = requests.request(method, base.rstrip("/") + path, json=body,
                                headers=headers, timeout=15)
        try:
            data = resp.json()
        except ValueError:
            data = {"error": resp.text[:200]}
        return resp.status_code, data if isinstance(data, dict) else {"data": data}

    return call


class Console:
    """The few console calls the kit needs. `http` is injectable so the tests
    drive the real Flask app without a socket."""

    def __init__(self, url: str, run: str = "", operator_token: str = "",
                 device: str = "kit", http: Http | None = None):
        self.url = url.rstrip("/")
        self.run = run.strip()
        self.device = device or "kit"
        headers = {"X-SafePay-Operator": operator_token} if operator_token else {}
        self._http = http or _requests_http(self.url, headers)

    def pending(self) -> list[dict[str, Any]]:
        path = "/api/approvals" + (f"?run={self.run}" if self.run else "")
        status, data = self._http("GET", path, None)
        if status != 200:
            raise RuntimeError(data.get("error") or f"HTTP {status}")
        return list(data.get("pending") or [])

    def approve(self, approval_id: str) -> dict[str, Any]:
        return self._resolve(approval_id, APPROVE)

    def reject(self, approval_id: str) -> dict[str, Any]:
        return self._resolve(approval_id, REJECT)

    def _resolve(self, approval_id: str, action: str) -> dict[str, Any]:
        status, data = self._http("POST", f"/api/approvals/{approval_id}/{action}",
                                  {"note": f"via kit {self.device}"})
        if status != 200:
            raise RuntimeError(data.get("error") or f"HTTP {status}")
        return data

    def agent_status(self, agent_id: str) -> str:
        path = "/api/agents" + (f"?run={self.run}" if self.run else "")
        status, data = self._http("GET", path, None)
        for row in (data.get("agents") or []) if status == 200 else []:
            if row.get("agent_id") == agent_id:
                return str(row.get("status") or "active")
        return "active"

    def freeze(self, agent_id: str) -> dict[str, Any]:
        status, data = self._http("POST", f"/api/agents/{agent_id}/pause",
                                  {"reason": f"frozen from kit {self.device}"})
        if status != 200:
            raise RuntimeError(data.get("error") or f"HTTP {status}")
        return data

    def thaw(self, agent_id: str) -> dict[str, Any]:
        status, data = self._http("POST", f"/api/agents/{agent_id}/resume",
                                  {"reason": f"thawed from kit {self.device}"})
        if status != 200:
            raise RuntimeError(data.get("error") or f"HTTP {status}")
        return data


# --- boards: what the person sees and presses --------------------------------

class Board(Protocol):
    def light(self, state: str) -> None: ...
    def show(self, text: str) -> None: ...
    def wait_button(self, timeout: float) -> str | None: ...
    def close(self) -> None: ...


class KeyBoard:
    """No hardware: the light is a line on the terminal, the buttons are keys.
    Works on Windows (msvcrt) and POSIX (select on stdin)."""

    KEYS = {"a": APPROVE, "r": REJECT, "f": FREEZE, "q": QUIT}

    def __init__(self, out=None):
        self.out = out or sys.stdout
        self.state = IDLE

    def light(self, state: str) -> None:
        if state != self.state:
            self.state = state
            self.out.write(f"[light] {state}\n")
            self.out.flush()

    def show(self, text: str) -> None:
        self.out.write(text + "\n")
        self.out.flush()

    def wait_button(self, timeout: float) -> str | None:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            key = self._key(min(0.2, max(0.0, end - time.monotonic())))
            if key is None:
                continue
            action = self.KEYS.get(key.lower())
            if action:
                return action
        return None

    @staticmethod
    def _key(timeout: float) -> str | None:  # pragma: no cover (terminal only)
        if os.name == "nt":
            import msvcrt
            end = time.monotonic() + timeout
            while time.monotonic() < end:
                if msvcrt.kbhit():
                    ch = msvcrt.getwch()
                    return ch if ch.isprintable() else None
                time.sleep(0.02)
            return None
        import select
        ready, _, _ = select.select([sys.stdin], [], [], timeout)
        return sys.stdin.read(1) if ready else None

    def close(self) -> None:
        pass


class GpioBoard:
    """A Raspberry Pi: three LEDs and three buttons on the pins in PINS
    (BCM numbering), through gpiozero. Buttons are wired to ground with the
    internal pull-up; LEDs through a 220 ohm resistor to ground."""

    def __init__(self, pins: dict[str, int] | None = None):
        from gpiozero import LED, Button  # type: ignore

        p = dict(PINS, **(pins or {}))
        self.leds = {"amber": LED(p["amber"]), "green": LED(p["green"]), "red": LED(p["red"])}
        self.buttons = {APPROVE: Button(p["approve"], pull_up=True, bounce_time=0.05),
                        REJECT: Button(p["reject"], pull_up=True, bounce_time=0.05),
                        FREEZE: Button(p["freeze"], pull_up=True, bounce_time=0.05)}
        self.state = ""

    def light(self, state: str) -> None:
        self.state = state
        for led in self.leds.values():
            led.off()
        if state == HOLD:
            self.leds["amber"].blink(on_time=0.5, off_time=0.5)
        elif state == FROZEN:
            self.leds["red"].on()
        elif state == OK:
            self.leds["green"].blink(on_time=0.2, off_time=0.2, n=3)
        elif state == ERROR:
            self.leds["red"].blink(on_time=0.1, off_time=0.1, n=5)
        else:
            self.leds["green"].on()

    def show(self, text: str) -> None:
        print(text, flush=True)   # a display HAT can replace this line

    def wait_button(self, timeout: float) -> str | None:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            for action, button in self.buttons.items():
                if button.is_pressed:
                    while button.is_pressed:   # wait for the release
                        time.sleep(0.02)
                    return action
            time.sleep(0.02)
        return None

    def close(self) -> None:
        for led in self.leds.values():
            led.close()
        for button in self.buttons.values():
            button.close()


def make_board(kind: str = "auto", pins: dict[str, int] | None = None) -> Board:
    if kind in ("auto", "gpio"):
        try:
            return GpioBoard(pins)
        except Exception as exc:
            if kind == "gpio":
                raise SystemExit(f"GPIO board unavailable: {exc} (pip install 'safepay[kit]' on the Pi)")
    return KeyBoard()


# --- the kit itself ------------------------------------------------------------

def card(approval: dict[str, Any]) -> str:
    """The card the kit shows for a hold: what, to whom, by which agent, why."""
    d = approval.get("decision") or {}
    i = d.get("intent") or {}
    lines = [f"HOLD {i.get('currency', 'USD')} {float(i.get('amount') or 0):.2f} "
             f"to {i.get('merchant_name') or i.get('recipient', '?')}",
             f"agent {i.get('agent_id', '?')}  ·  {approval.get('id', '')}"]
    if i.get("purpose"):
        lines.append(f"for: {str(i['purpose'])[:80]}")
    reasons = [r for r in (d.get("reasons") or []) if r]
    if reasons:
        lines.append(f"why: {str(reasons[-1])[:80]}")
    lines.append("[green] approve   [red] reject   [black] freeze agent")
    return "\n".join(lines)


class Kit:
    def __init__(self, console: Console, board: Board, agent_id: str = "",
                 interval: float = 3.0, log: Callable[[str], None] | None = None):
        self.console = console
        self.board = board
        self.agent_id = agent_id          # the agent the freeze button acts on when nothing is held
        self.interval = interval
        self.log = log or (lambda s: None)
        self.frozen: set[str] = set()
        self.shown: str | None = None

    def tick(self) -> str:
        """One poll and, if something is held, one button wait. Returns what
        happened, for the log and the tests."""
        try:
            pending = self.console.pending()
        except Exception as exc:
            self.board.light(ERROR)
            self.log(f"console unreachable: {exc}")
            return "error"
        if not pending:
            self.shown = None
            self.board.light(FROZEN if self.frozen else IDLE)
            action = self.board.wait_button(self.interval)
            if action == FREEZE and self.agent_id:
                return self._toggle_freeze(self.agent_id)
            return "quit" if action == QUIT else "idle"
        hold = pending[0]
        if hold.get("id") != self.shown:
            self.board.show(card(hold))
            self.shown = hold.get("id")
        self.board.light(HOLD)
        action = self.board.wait_button(self.interval)
        if action is None:
            return "waiting"
        if action == QUIT:
            return "quit"
        agent_id = str((hold.get("decision") or {}).get("intent", {}).get("agent_id") or self.agent_id)
        if action == FREEZE:
            return self._toggle_freeze(agent_id)
        try:
            out = self.console.approve(hold["id"]) if action == APPROVE else self.console.reject(hold["id"])
        except Exception as exc:
            self.board.light(ERROR)
            self.log(f"{action} failed: {exc}")
            return "error"
        self.shown = None
        self.board.light(OK)
        verdict = (out.get("decision") or {}).get("verdict", out.get("verdict", "?"))
        done = "approved" if action == APPROVE else "rejected"
        self.log(f"{done} {hold['id']}: {verdict}")
        return done

    def _toggle_freeze(self, agent_id: str) -> str:
        try:
            if agent_id in self.frozen or self.console.agent_status(agent_id) == "paused":
                self.console.thaw(agent_id)
                self.frozen.discard(agent_id)
                self.board.light(IDLE)
                self.log(f"thawed {agent_id}")
                return "thawed"
            self.console.freeze(agent_id)
            self.frozen.add(agent_id)
            self.board.light(FROZEN)
            self.log(f"froze {agent_id}")
            return "frozen"
        except Exception as exc:
            self.board.light(ERROR)
            self.log(f"freeze failed: {exc}")
            return "error"

    def run(self, ticks: int | None = None) -> None:
        n = 0
        try:
            while ticks is None or n < ticks:
                if self.tick() == "quit":
                    break
                n += 1
        finally:
            self.board.close()


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="safepay-kit", description=__doc__.split("\n\n")[0])
    ap.add_argument("--url", default=os.environ.get("SAFEPAY_URL", "http://localhost:5000"))
    ap.add_argument("--run", default=os.environ.get("SAFEPAY_RUN", ""),
                    help="console session id; only that session's holds light the kit")
    ap.add_argument("--agent", default=os.environ.get("SAFEPAY_KIT_AGENT", ""),
                    help="agent the freeze button acts on while nothing is held")
    ap.add_argument("--device", default=os.environ.get("SAFEPAY_KIT_DEVICE", "counter-1"))
    ap.add_argument("--operator", default=os.environ.get("SAFEPAY_OPERATOR_TOKEN", ""))
    ap.add_argument("--board", choices=["auto", "gpio", "keys"], default="auto")
    ap.add_argument("--interval", type=float, default=3.0)
    for name, pin in PINS.items():
        ap.add_argument(f"--pin-{name}", type=int, default=pin, help=f"BCM pin (default {pin})")
    a = ap.parse_args(argv)
    pins = {name: getattr(a, f"pin_{name}") for name in PINS}
    board = make_board(a.board, pins)
    console = Console(a.url, a.run, a.operator, a.device)
    print(f"safepay-kit {a.device}: watching {a.url}" + (f" session {a.run}" if a.run else "")
          + f" with {type(board).__name__}", flush=True)
    Kit(console, board, a.agent, a.interval, log=lambda s: print(s, flush=True)).run()


if __name__ == "__main__":  # pragma: no cover
    main()
