"""The GPIO board of the counter kit, driven through a fake gpiozero: which
LED does what for each light state, button presses and releases, custom pins,
and the fallback to the keyboard when the Pi library is missing."""

from __future__ import annotations

import sys
import types

import pytest

from safepay import kit
from safepay.kit import APPROVE, ERROR, FREEZE, HOLD, IDLE, OK, QUIT, REJECT, FROZEN, GpioBoard, KeyBoard


class FakeLED:
    def __init__(self, pin):
        self.pin, self.log = pin, []

    def on(self): self.log.append("on")
    def off(self): self.log.append("off")
    def close(self): self.log.append("close")

    def blink(self, on_time=1, off_time=1, n=None):
        self.log.append(("blink", on_time, off_time, n))


class FakeButton:
    presses = {}

    def __init__(self, pin, pull_up=True, bounce_time=None):
        self.pin, self.pull_up, self.bounce_time, self.closed = pin, pull_up, bounce_time, False
        self._reads = list(FakeButton.presses.get(pin, []))

    @property
    def is_pressed(self):
        return self._reads.pop(0) if self._reads else False

    def close(self): self.closed = True


@pytest.fixture
def gpiozero(monkeypatch):
    mod = types.ModuleType("gpiozero")
    mod.LED, mod.Button = FakeLED, FakeButton
    monkeypatch.setitem(sys.modules, "gpiozero", mod)
    FakeButton.presses = {}
    monkeypatch.setattr(kit.time, "sleep", lambda s: None)
    return mod


def test_default_and_custom_pins(gpiozero):
    b = GpioBoard()
    assert {k: v.pin for k, v in b.leds.items()} == {"amber": 17, "green": 27, "red": 22}
    assert {k: v.pin for k, v in b.buttons.items()} == {APPROVE: 5, REJECT: 6, FREEZE: 13}
    assert all(x.pull_up and x.bounce_time == 0.05 for x in b.buttons.values())
    c = GpioBoard({"approve": 19, "amber": 4})
    assert c.buttons[APPROVE].pin == 19 and c.leds["amber"].pin == 4 and c.leds["red"].pin == 22


def test_each_light_state_drives_one_led(gpiozero):
    b = GpioBoard()
    b.light(HOLD)
    assert b.leds["amber"].log == ["off", ("blink", 0.5, 0.5, None)]
    assert b.leds["green"].log == ["off"] and b.leds["red"].log == ["off"]
    b.light(FROZEN)
    assert b.leds["red"].log[-1] == "on" and b.leds["amber"].log[-1] == "off"
    b.light(OK)
    assert b.leds["green"].log[-1] == ("blink", 0.2, 0.2, 3)
    b.light(ERROR)
    assert b.leds["red"].log[-1] == ("blink", 0.1, 0.1, 5)
    b.light(IDLE)
    assert b.leds["green"].log[-1] == "on" and b.state == IDLE


def test_button_press_waits_for_the_release(gpiozero):
    FakeButton.presses = {6: [True, True, False]}
    b = GpioBoard()
    assert b.wait_button(1.0) == REJECT
    assert b.buttons[REJECT]._reads == []        # held twice, released, consumed


def test_no_press_times_out(gpiozero, monkeypatch):
    clock = iter([0.0, 0.0, 0.1, 0.2, 5.0, 5.0])
    monkeypatch.setattr(kit.time, "monotonic", lambda: next(clock))
    assert GpioBoard().wait_button(0.3) is None


def test_show_prints_and_close_releases_everything(gpiozero, capsys):
    b = GpioBoard()
    b.show("HOLD USD 5.00")
    assert capsys.readouterr().out == "HOLD USD 5.00\n"
    b.close()
    assert all(l.log[-1] == "close" for l in b.leds.values()) and all(x.closed for x in b.buttons.values())


def test_make_board_chooses_gpio_then_keys(gpiozero, monkeypatch):
    assert isinstance(kit.make_board("auto"), GpioBoard)
    assert isinstance(kit.make_board("gpio", {"red": 21}), GpioBoard)
    assert isinstance(kit.make_board("keys"), KeyBoard)
    monkeypatch.setitem(sys.modules, "gpiozero", None)     # import now fails
    assert isinstance(kit.make_board("auto"), KeyBoard)
    with pytest.raises(SystemExit, match="GPIO board unavailable"):
        kit.make_board("gpio")


def test_keyboard_ignores_other_keys_and_times_out(monkeypatch):
    keys = iter(["x", None, "A"])
    monkeypatch.setattr(KeyBoard, "_key", staticmethod(lambda timeout: next(keys)))
    assert KeyBoard().wait_button(1.0) == APPROVE
    monkeypatch.setattr(KeyBoard, "_key", staticmethod(lambda timeout: None))
    clock = iter([0.0, 0.0, 0.1, 0.5, 5.0, 5.0])
    monkeypatch.setattr(kit.time, "monotonic", lambda: next(clock))
    assert KeyBoard().wait_button(0.4) is None
    assert KeyBoard.KEYS == {"a": APPROVE, "r": REJECT, "f": FREEZE, "q": QUIT}


def test_console_http_wrapper_handles_non_json(monkeypatch):
    import requests

    class R:
        status_code = 502
        text = "<html>bad</html>"

        def json(self): raise ValueError
    seen = {}
    monkeypatch.setattr(requests, "request", lambda m, u, json=None, headers=None, timeout=None:
                        seen.update(m=m, u=u, h=headers) or R())
    call = kit._requests_http("https://c.example/", {"X-SafePay-Operator": "t"})
    assert call("GET", "/api/approvals", None) == (502, {"error": "<html>bad</html>"})
    assert seen == {"m": "GET", "u": "https://c.example/api/approvals", "h": {"X-SafePay-Operator": "t"}}

    class L(R):
        status_code = 200

        def json(self): return [1, 2]
    monkeypatch.setattr(requests, "request", lambda *a, **k: L())
    assert call("GET", "/x", None) == (200, {"data": [1, 2]})
