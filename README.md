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

1. **Policy (deterministic).** Per-transaction, daily, per-recipient and
   velocity limits, allowed and blocked categories, recipient allow/block lists,
   currency. A breach here is a hard **block**, no model call spent. The daily,
   per-recipient and velocity caps are enforced across a burst of payments, not
   just one in isolation, so a slow-drain or high-frequency attack is caught.
   The deterministic policy is the floor: the model layers below can tighten a
   verdict to HOLD, never loosen a policy block.
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

### Web console (what a judge runs)

```bash
set -a; . ./.env; set +a
python webapp/app.py
# open http://127.0.0.1:5000
```

Load the demo scenarios or enter a custom payment and watch each intent resolve:
the three checks, the verdict, the PayPal sandbox batch, and the signed stamp.
"Reset ledger" starts the daily-cap scenario fresh. Below the results, the
**audit ledger** (an AG Grid table over `/api/audit`) shows the hash-chained log
with a live verification badge; "Tamper (demo)" edits a row in place to show the
chain break turn the badge red, and "Verify chain" re-checks it.

### Hosted demo

**Live: https://safepay.agentposhq.com** (no login, no local setup). It runs on
Vercel from this repo (`api/index.py` + `vercel.json`); every push to `master`
redeploys. The same console also deploys to Render: `render.yaml` is a Render
blueprint and `wsgi.py` is the gunicorn entry point (New > Blueprint, point it at
this repo). Set the secret values (PayPal, Nebius, Tavily, signing key) as
environment variables on the host; without the PayPal keys it runs in simulated
mode. Locally the same server runs with `gunicorn -w 1 -b 0.0.0.0:8000 wsgi:app`.

### Single intent from the command line

```bash
python -m safepay --recipient vendor@example.com --amount 42 \
  --category software --purpose "monthly API bill" \
  --goal "pay the team's approved SaaS invoices"
```

Add `--json` for the full decision and audit records.

### Gate the PayPal Agent Toolkit (agentic commerce)

SafePay also drops in front of the [PayPal Agent Toolkit](https://pypi.org/project/paypal-agent-toolkit/).
The toolkit gives an agent real PayPal tools (`create_order`, `capture_order`,
and more); PayPal's own guidance is to "invoke pay_order only after the user
approves." SafePay is that approver, made automatic: it judges the intent, and
the toolkit's money-moving tool is called only on ALLOW.

```bash
python -m venv .venv-agent
.venv-agent/Scripts/pip install -r requirements.txt -r requirements-agent.txt
set -a; . ./.env; set +a
.venv-agent/Scripts/python examples/agent_toolkit_demo.py
```

Allowed intents create a real sandbox order (with its buyer approval link);
over-limit and daily-cap breaches are stopped before the toolkit is ever
called. The gate lives in `safepay/agent_toolkit.py` as `GuardedToolkit`:

```python
from safepay.agent_toolkit import GuardedToolkit
gate = GuardedToolkit(policy=policy)          # reasoning/web optional
out = gate.create_order(intent)               # evaluate, then create on ALLOW
# out["decision"], out["result"] (the order), out["audit"] (signed)
```

The toolkit is an optional dependency; the core package and tests never need it.

### Use it as an MCP server (any agent)

SafePay also runs as a Model Context Protocol server, so any MCP client (Claude
Desktop, Cursor, an agent framework) can route its payments through the guardrail
without importing any SafePay code. It exposes four tools: `safepay_evaluate`
(judge without paying), `safepay_pay` (evaluate, then pay via Payouts only on
ALLOW), `safepay_create_order` (gate the Agent Toolkit order flow), and
`safepay_audit_status` (re-verify the tamper-evident chain).

```bash
pip install -r requirements.txt -r requirements-mcp.txt
python -m safepay.mcp_server          # stdio transport
```

Point your MCP client at that command. For Claude Desktop or Cursor
(`examples/mcp_config.json`):

```json
{
  "mcpServers": {
    "safepay": {
      "command": "python",
      "args": ["-m", "safepay.mcp_server"],
      "env": { "PAYPAL_CLIENT_ID": "...", "PAYPAL_SECRET": "...",
               "NEBIUS_API_KEY": "...", "TAVILY_API_KEY": "...",
               "SAFEPAY_SIGNING_KEY": "..." }
    }
  }
}
```

Run it from the repo directory (so `safepay` is importable), or `pip install -e .`
first. The `mcp` package is optional; the core package and tests never need it.

### Keys

| Key | What it turns on | Without it |
| --- | --- | --- |
| `PAYPAL_CLIENT_ID` / `PAYPAL_SECRET` | Real sandbox payouts | Payouts are simulated |
| `NEBIUS_API_KEY` | Nemotron intent/anomaly reasoning | Reasoning check is skipped |
| `TAVILY_API_KEY` | Live-web merchant verification | Merchant check is skipped |
| `SAFEPAY_SIGNING_KEY` | HMAC-signed audit records | Records are integrity-only (unsigned) |
| `SAFEPAY_AUDIT_LOG` | Persist the hash-chained log to this JSONL file | Log is in-memory only |
| `SAFEPAY_EXECUTE` | `simulate`/`0`/`false` evaluates without moving money (budget still charged) | ALLOW executes the payout |
| `SAFEPAY_RATE_LIMIT` | Max web-console evaluations per IP per minute | 30 |

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

## Catching prompt injection

```bash
set -a; . ./.env; set +a
python examples/prompt_injection_demo.py
```

An agent reads an invoice email carrying a hidden instruction to pay an attacker.
The injected payment is deliberately inside every spending limit, so policy alone
passes it, but the reasoning layer catches that a "processing fee to release
invoices" does not serve the task, and holds it for a human, while the real
invoice is allowed. This is the agentic-commerce attack SafePay exists to stop.

## Tamper-evident audit chain

Every decision is appended to a hash-chained log: each record carries `prev`,
the SHA-256 of the record before it, so editing or dropping any earlier decision
breaks every hash that follows. Point a run at a file and verify it later:

```bash
python -m safepay --scenarios examples/scenarios.json --audit-log audit.jsonl
python -m safepay --verify-log audit.jsonl      # "audit chain OK: N records, intact"
```

Tamper with any record and the verifier names the first broken link and exits
non-zero. The web console serves the live chain and its status at `/api/audit`.
With `SAFEPAY_SIGNING_KEY` set, each link is also HMAC signed. This chain is the
seam the Concordium Trusted Agents build extends, signing each link with a
verified-human identity instead of a shared secret.

## Tests

```bash
python -m pytest -q
```

Deterministic, offline: policy, aggregation, the daily cap across a burst,
simulated execution, and audit integrity.

## License

MIT. See `LICENSE`.
