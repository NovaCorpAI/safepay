# SafePay

**A safety guardrail between an autonomous AI agent and PayPal.** SafePay sits in
front of an agent's payment and decides, before any money moves, whether to
**allow**, **hold**, or **block** it. It then executes allowed payments through the
PayPal sandbox and signs an audit record of every decision.

Built for the PayPal AI Hackathon (Agentic Commerce and Autonomous Payments) by
[NovaCorp Labs](https://novacorplabs.com).

---

## The problem

Agentic commerce gives an AI agent a wallet. The upside is obvious; the risk is
the same thing. An agent that can pay can also be prompt-injected into paying
the wrong recipient, talked into an amount that does not fit its task, or sent
to a merchant that was registered last week and shows up on scam-report pages. A
static allow-list is too blunt, and letting the model decide alone is exactly
what goes wrong.

SafePay is the missing layer: a payments firewall that an operator configures
once and that every agent payment has to pass through.

## How it works

Each `PaymentIntent` runs three checks, cheapest and strictest first:

1. **Policy (deterministic).** Per-transaction and daily limits, allowed and
   blocked categories, recipient allow/block lists, currency. A breach here is a
   hard **block**, no model call spent. The daily cap is enforced across a burst
   of payments, not just one in isolation.
2. **Merchant verification (live web, Tavily).** Pulls fresh web evidence about
   the payee and flags fraud/scam signals. A soft signal: it raises risk and can
   trigger a **hold**, but never silently blocks on noisy web data.
3. **Intent and anomaly reasoning (NVIDIA Nemotron on Nebius Token Factory).**
   Reads the task the agent was given and judges whether the payment actually
   serves it. Catches goal/payment mismatches a rule set misses (a cloud-invoice
   agent paying a casino).

The verdict aggregates the three. On **allow**, SafePay releases the payment via
the PayPal **Payouts** API (agent sends money, no buyer redirect, fully
backend). On **hold** or **block**, the money never moves. Every decision is
written to a tamper-evident, optionally HMAC-signed audit record.

```
PaymentIntent ─▶ [policy] ─▶ [merchant web] ─▶ [reasoning] ─▶ verdict
                    │                                              │
                 hard block                            allow ─▶ PayPal sandbox payout
                                                        hold/block ─▶ stop + audit
```

## Run it

```bash
pip install -r requirements.txt

# Simulated mode (no keys needed): policy checks run, payouts are simulated.
python -m safepay --scenarios examples/scenarios.json

# Full mode: copy .env.example to .env, fill the keys, load it, and re-run.
set -a; . ./.env; set +a
python -m safepay --scenarios examples/scenarios.json
```

Single intent from the command line:

```bash
python -m safepay --recipient vendor@example.com --amount 42 \
  --category software --purpose "monthly API bill" \
  --goal "pay the team's approved SaaS invoices"
```

Add `--json` for the full decision and audit records.

### Keys

| Key | What it turns on | Without it |
| --- | --- | --- |
| `PAYPAL_CLIENT_ID` / `PAYPAL_SECRET` | Real sandbox payouts | Payouts are simulated |
| `NEBIUS_API_KEY` | Nemotron intent/anomaly reasoning | Reasoning check is skipped |
| `TAVILY_API_KEY` | Live-web merchant verification | Merchant check is skipped |
| `SAFEPAY_SIGNING_KEY` | HMAC-signed audit records | Records are integrity-only (unsigned) |

Get PayPal sandbox keys at `developer.paypal.com` > Apps & Credentials >
Sandbox > Create App. The host is pinned to the sandbox in `config.py`; SafePay
never defaults to the live host, so it cannot move real money by accident.

## The demo scenario

`examples/scenarios.json` runs one agent (`ops-agent-7`) whose task is to pay
the month's approved software and cloud invoices. SafePay:

- **allows** the Notion subscription (within limits, aligned, reputable),
- **blocks** a USD 900 charge (over the per-transaction limit),
- **holds** a casino payout (reasoning catches the goal mismatch; a rule set
  alone would let it through),
- **allows** two cloud invoices, then
- **blocks** the next (it would breach the daily cap).

## Tests

```bash
python -m pytest -q
```

Deterministic, offline: policy, aggregation, the daily cap across a burst,
simulated execution, and audit integrity.

## License

MIT. See `LICENSE`.
