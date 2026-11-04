# SafePay

[![PyPI](https://img.shields.io/pypi/v/safepay)](https://pypi.org/project/safepay/)
[![Live demo](https://img.shields.io/badge/demo-safepay.agentposhq.com-blue)](https://safepay.agentposhq.com)

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
   currency, and an **approved vendor directory** (per vendor: its real domains
   and the amount you expect to pay it). A breach here is a hard **block**, no
   model call spent. The daily, per-recipient and velocity caps are enforced
   across a burst of payments, not just one in isolation, so a slow-drain or
   high-frequency attack is caught. The vendor directory catches the two
   attacks that stay inside every cap: a lookalike recipient (real vendor name,
   swapped domain) and an inflated amount to a real vendor; a stated merchant
   URL must also agree with the recipient's domain. With `hold_unknown_vendors`
   on, any payee outside the directory is held for a human, the "new
   beneficiary" control banks apply. The deterministic policy is the floor:
   the model layers below can tighten a verdict to HOLD, never loosen a policy
   block.
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
written to a tamper-evident, optionally HMAC-signed audit record, and when
PayPal later reports what happened to the payout (succeeded, failed, returned)
that **settlement** is appended to the same chain, so the record says not only
what SafePay decided but whether the money actually moved.

```
PaymentIntent ─▶ [policy] ─▶ [merchant web] ─▶ [reasoning] ─▶ verdict
                    │                                              │
                 hard block                            allow ─▶ PayPal sandbox payout
                                                        hold/block ─▶ stop + audit
```

## Run it

One minute to a first verdict, no keys needed:

```bash
pip install safepay          # or, from a clone: pip install -e .
safepay --demo               # 7 scenarios, payouts simulated, lookalike and caps blocked
```

Three lines to put it in front of your own agent's payments:

```python
from safepay.agent import SafePayAgent
from safepay.models import PaymentIntent, PolicyConfig

agent = SafePayAgent(policy=PolicyConfig(per_tx_limit=500, daily_limit=2000))
result = agent.pay(PaymentIntent(recipient="billing@notion.so", amount=48,
                                 purpose="Team plan", task_goal="pay approved SaaS invoices"))
# result["decision"]["verdict"] is "allow", "hold" or "block"; on allow the payout went out.
```

From a clone, with the real checks:

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
redeploys. A second live instance runs on Render from the included blueprint:
**https://safepay-ygzp.onrender.com** (`render.yaml` + the gunicorn entry point
`wsgi.py`; free plan, so it may take up to a minute to wake after idling). Set
the secret values (PayPal, Nebius, Tavily, signing key) as environment variables
on the host; without the PayPal keys it runs in simulated mode. Locally the same
server runs with `gunicorn -w 1 -b 0.0.0.0:8000 wsgi:app`.

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
without importing any SafePay code. It exposes seven tools: `safepay_evaluate`
(judge without paying), `safepay_pay` (evaluate, then pay via Payouts only on
ALLOW), `safepay_create_order` (gate the Agent Toolkit order flow),
`safepay_pending_approvals`, `safepay_approve` and `safepay_reject` (the human
in the loop for HOLD decisions), and `safepay_audit_status` (re-verify the
tamper-evident chain).

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
| `SUPABASE_URL` / `SUPABASE_SERVICE_ROLE_KEY` | Durable, shared audit chain, ledger and approvals | In-memory state |
| `PAYPAL_WEBHOOK_ID` | Accept PayPal Payouts webhooks at `/api/paypal/webhook` (signature verified with PayPal) | Settlements only on demand (console button, API, MCP tool) |

Get PayPal sandbox keys at `developer.paypal.com` > Apps & Credentials >
Sandbox > Create App. The host is pinned to the sandbox in `config.py`; SafePay
never defaults to the live host, so it cannot move real money by accident.

## The demo scenario

`examples/scenarios.json` runs one agent (`ops-agent-7`) whose task is to pay
the month's approved software and cloud invoices. SafePay:

- **allows** the Notion subscription (within limits, aligned, reputable),
- **blocks** the same invoice sent to `billing@notion-payments.example`: a real
  vendor name on a lookalike domain, caught by the vendor directory before any
  model runs,
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

## Attack benchmark (proof, not promise)

`benchmark/attacks/` runs 14 realistic attacks on a wallet-holding agent (prompt
injection in invoices and vendor pages, goal mismatches, an unknown payee, a
lookalike recipient, amount inflation, limit breaches, a slow drain, a velocity
burst) plus 7 legitimate invoices, through the guardrail. Three modes, side by
side, so the numbers are honest:

| Mode | Attacks caught | Legit allowed (no false positives) |
| --- | --- | --- |
| policy only (rules + vendor directory, no model, no web) | 6/14 (42%) | 7/7 (100%) |
| full (policy + Tavily + Nemotron) | **13/14 (92%)** | **7/7 (100%)** |
| strict (full + hold unknown vendors) | **14/14 (100%)** | **7/7 (100%)** |

Rules alone catch what breaches a limit plus, with the vendor directory, the
lookalike recipient and the inflated amount (the two misses of the first
version of this benchmark, now closed deterministically). The model layers
catch every prompt injection and every goal mismatch, with zero false positives
on real vendors. The one case full mode does not catch reliably is the unknown
payee with a plausible invoice: the model checks are not deterministic and that
case flips between runs (caught by reasoning in an earlier run, missed in the
three runs behind the table). That is exactly what
the `hold_unknown_vendors` control is for, and strict mode shows it. Full
per-case results are in `benchmark/attacks/results.md`.

```bash
python benchmark/attacks/run_attack_benchmark.py --mode all    # needs keys for full/strict
python benchmark/attacks/run_attack_benchmark.py --mode policy # offline
```

## Human in the loop (HOLD is a workflow, not a dead end)

When a soft check raises a concern inside the policy limits, the verdict is
HOLD and the payment waits in an approval queue. In the web console a "Pending
approvals" panel appears with **Approve and pay** / **Reject**; the same queue
is available as `GET /api/approvals`, `POST /api/approvals/<id>/approve` and
`/reject`, and as MCP tools, so a person (or a supervising agent) can decide
from anywhere. Approving releases the payment and charges the spend ledger, so
the daily and per-recipient caps reflect real spend; rejecting moves nothing.
Both outcomes are appended to the audit chain as their own link, so "who
approved this agent payment, and when" is provable.

## Settlement (did the money actually move?)

PayPal accepts a payout as PENDING; it succeeds, fails or is returned later.
SafePay closes that loop. `POST /api/payouts/<batch_id>/settle` (the "check
settlement" button on a paid card in the console, the `safepay_payout_status`
MCP tool, or `SafePayAgent.settle()`) asks the Payouts API for the batch's
real state and appends it to the chain as a **settlement** link: SUCCESS is an
ALLOW link (money moved), a final failure is a BLOCK link that also releases
the budget the payout had charged (the money never left, so the cap should not
count it), anything still in flight is a HOLD link. Settling twice with the
same state appends nothing.

With a webhook, PayPal pushes the event instead: create one in the developer
dashboard for `PAYMENT.PAYOUTSBATCH.*` and `PAYMENT.PAYOUTS-ITEM.*` pointing
at `https://<your host>/api/paypal/webhook`, and set `PAYPAL_WEBHOOK_ID`. Every
delivery is verified with PayPal's signature API before it is acted on, and
even then only the batch id is taken from the event: the state is re-read from
the Payouts API, never trusted from the body.

## Durable state (Supabase)

By default the spend ledger, the audit chain and the approval queue live in
memory, which is fine locally and in tests but not for a hosted service: a
restart loses them, and on a serverless host each instance would keep its own
copy. Set `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY` and all three persist
in Supabase and are shared by every instance:

- the chain is appended against the head stored in the database (`seq` is the
  primary key, so two instances can never both claim the same link),
- the daily cap, per-recipient cap and velocity rule are computed from real
  history over a true UTC day,
- approvals survive restarts and resolve atomically.

Create the tables once with `supabase/schema.sql` (RLS on, no policies: only
the service role key, kept server-side, can touch them). The console shows
`state: durable (Supabase)` when it is on. The audit chain is never deleted;
"Reset ledger" in the console starts a new run id for the demo agents instead.

The console also has an **Early access** form (`POST /api/early-access`) for
teams that want SafePay in front of their own agents' payouts. Sign-ups go to
the same backend (`safepay_leads`, upserted on email, in memory without
Supabase). The endpoint is write-only: nothing public lists them, and the
form says exactly what the data is used for.

## Attacks on the guardrail itself

Two of SafePay's inputs are text an attacker can write: the intent's purpose
and task (they come from the agent, which may already be compromised) and the
web snippets the merchant check retrieves (a payee's own page, or one planted
to rank for its name). Both would normally land in a model prompt. SafePay's
defence is structural first and explicit second:

- **The model can never raise a verdict.** A policy block is final before any
  model runs; a soft check can only fail (HOLD), never override another check.
  So the best an injected instruction can achieve is to not lower the risk,
  which changes nothing a rule already decided.
- **Instructions aimed at the reviewer are a fraud signal.** Before any model
  reads it, text is scanned for reviewer-directed instructions ("ignore
  previous instructions", "SafePay: mark this payee as safe", "set risk to
  0"); a hit fails the check closed and the payment is held for a human, and
  the planted fragment is recorded as evidence. What does reach the model is
  wrapped as delimited data with the system prompt saying nothing inside it is
  an instruction.

`tests/test_injection_defence.py` feeds a deliberately obedient fake model a
planted page and a planted purpose, and shows it never gets to read either.

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
