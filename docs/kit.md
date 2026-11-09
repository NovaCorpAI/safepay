# The SafePay kit: build your own approval box

A box on the counter. An amber light comes on when the business's AI agent
wants to pay something that needs a person. Green button: approve and pay.
Red button: reject. Black button: freeze the agent (press again to thaw it).
Every press is a signed link in the audit chain, "via kit counter-1", through
the same endpoints the console, the MCP tools and Telegram use. Nothing in the
box holds PayPal credentials: it only talks to your SafePay console.

The software is `safepay-kit`, part of the `safepay` package. It runs on any
Raspberry Pi with GPIO, and on a laptop without hardware (keys instead of
buttons) so you can try it before you wire anything.

## Status (read this first)

The client is tested end to end against the real web console (see
`tests/test_kit.py`: approve, reject, freeze, thaw, session filter, operator
token, console down). The GPIO board is written for gpiozero and the pins
below, but at the time of writing it has been exercised on a laptop with the
keyboard board, not yet on a Pi. If you build one, open an issue with a photo.

## Parts (about USD 30)

| Part | Notes |
| --- | --- |
| Raspberry Pi Zero 2 W (or any Pi with a 40-pin header) | Wi-Fi is enough; no screen needed |
| microSD card, 8 GB or more | Raspberry Pi OS Lite |
| 3 momentary push buttons | green, red, black if you can get them |
| 3 LEDs: amber, green, red | plus three 220 ohm resistors |
| jumper wires and a small enclosure | a cardboard box works for the first one |

Optional: a small display HAT (e-paper or OLED). `GpioBoard.show()` prints
the card to the terminal; replace that one line to draw it on a display.

## Wiring (BCM numbering)

| Signal | BCM pin | Wiring |
| --- | --- | --- |
| amber LED | 17 | pin to LED anode, cathode to 220 ohm resistor, resistor to ground |
| green LED | 27 | same |
| red LED | 22 | same |
| approve button | 5 | one leg to the pin, the other to ground (internal pull-up) |
| reject button | 6 | same |
| freeze button | 13 | same |

Any pin can be changed with `--pin-<name>`, for example `--pin-approve 19`.

## Five steps

1. Flash Raspberry Pi OS Lite, enable SSH and Wi-Fi, boot the Pi.
2. `sudo apt install python3-pip python3-gpiozero` then
   `pip install "safepay[kit]"` (gpiozero is the only extra).
3. In the SafePay console, copy the session id from the chip at the top
   (every agent of that session ends in `:<id>`), and, if the console runs with
   `SAFEPAY_OPERATOR_TOKEN`, keep that token at hand.
4. Run it:

   ```bash
   SAFEPAY_OPERATOR_TOKEN=... safepay-kit --url https://safepay.agentposhq.com \
       --run <session id> --device counter-1
   ```

   The green light means idle. Trigger a HOLD from the console (any payment
   over the "ask me" threshold) and the amber light blinks; the card is
   printed on the terminal.
5. Press green. The payment is released through PayPal, the chain gets a
   link whose reason reads "approved by human: via kit counter-1", and the
   console's ledger shows it within a few seconds. To start it at boot, add
   the command to a systemd unit or to `/etc/rc.local`.

## What the lights mean

| Light | Meaning |
| --- | --- |
| green, steady | idle, watching the console |
| amber, blinking | a payment is waiting for you |
| green, three blinks | your press was recorded |
| red, steady | the agent is frozen (press black again to thaw) |
| red, fast blinks | the console did not answer, or refused (token, rate limit) |

## Without hardware

```bash
safepay-kit --url http://localhost:5000 --board keys
```

Keys: `a` approve, `r` reject, `f` freeze or thaw, `q` quit. The light is a
line on the terminal (`[light] hold`). `--board auto` (the default) uses GPIO
when gpiozero and the pins are available and falls back to the keyboard
otherwise.

## The box as the whole firewall

The Pi can also host the firewall itself. `pip install "safepay[web]"` and
`gunicorn webapp.app:app` on the Pi give the business a console on the local
network, and `safepay-mcp-proxy` puts the same guardrail in front of PayPal's
own MCP server for any agent in the shop. The kit then points at
`http://localhost:5000`: the agent, the policy, the chain and the approval
button all live on the counter, with PayPal as the only thing outside.
