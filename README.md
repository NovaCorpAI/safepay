# SafePay

[![ci](https://github.com/NovaCorpAI/safepay/actions/workflows/ci.yml/badge.svg)](https://github.com/NovaCorpAI/safepay/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/safepay)](https://pypi.org/project/safepay/)
[![Live demo](https://img.shields.io/badge/demo-safepay.agentposhq.com-2b3a8c)](https://safepay.agentposhq.com)
[![Security](https://img.shields.io/badge/threat%20model-SECURITY.md-a8842c)](SECURITY.md)

**The firewall any AI agent plugs in before it pays with PayPal.** SafePay is
not another agent: it is the layer under all of them. Whatever the agent is
built with (an MCP client such as Claude or Cursor, the PayPal Agent Toolkit,
LangChain, plain Python or HTTP), every payment it wants to make passes through
SafePay, which decides before any money moves whether to **allow**, **hold**,
or **block** it. It checks the payee on the live web, catches lookalike vendors,
inflated invoices and injected instructions, holds what a person should see,
pays the rest through PayPal, and leaves a signed link in a hash chain for every
decision. And it is measured: a reproducible benchmark of real attacks on a
wallet-holding agent, not a claim.

Built for the PayPal AI Hackathon (Agentic Commerce and Autonomous Payments) by
[NovaCorp Labs](https://novacorplabs.com).

**We are looking for pilot teams and partners.** If you build agents that pay,
run accounts payable, or work on payments or fraud and want to try SafePay
with your own flows, write to **security@novacorplabs.com**. Pilots are free
and onboarded by hand.

## Judge in 3 clicks

**https://safepay.agentposhq.com** · no login, no keys; every PayPal call goes
to the real PayPal sandbox, and the web check (Tavily) and the reasoning
(NVIDIA Nemotron on Nebius) run live. Each browser gets its own session.

Short on time? **[Take the 3-minute tour](https://safepay.agentposhq.com/?tour=1)**:
ten steps across the real pages (not a demo page), with a button that runs the
scenarios or the chain check for you. Arrow keys move, Esc ends it.

1. **Console → type a policy in plain words → Draft → Apply.** For example
   "Up to $500 per payment and $1,000 a day. Ask me before anything over $300.
   No gambling or crypto." Nemotron drafts the rules; you see every field before
   it applies, and the change is chained as a policy link.
2. **Run demo scenarios.** Seven payments resolve one by one: a real Notion
   invoice paid; the same invoice to a lookalike domain blocked; a USD 900
   charge blocked by the cap; a casino payout held (the web found a scam
   complaint, the reasoning found it does not serve the task: open each check to
   see the sources); two cloud invoices over your "ask me" line held for you
   (approve one, here or with the Telegram card; every paid card links its
   public seal page); the last one blocked by the
   daily cap. Then **Create the order through the gate**: a real PayPal Agent
   Toolkit order, created only because SafePay allowed it.
3. **Ledger → Tamper (demo) → Verify chain.** Every decision is a link in a
   signed hash chain; edit one row (on a copy) and the badge turns red. The
   **Flow** page replays the same chain as pulses through the layers.

Run it yourself: `pip install safepay && safepay --demo` (no keys needed).

The five judging criteria, one line each, with where the evidence is:

| Criterion | Where to look |
| --- | --- |
| Technological implementation | Payouts, Orders v2 authorize/capture/void, Vault v3, Transaction Search and webhooks, all against the real sandbox; the Agent Toolkit gated; Nemotron and Tavily live; 501 offline tests at 98% coverage ([Tests](#tests)) |
| Design | One console that an operator sets up in plain words and a judge works end to end: policy, scenarios, approvals, Telegram, ledger, Studio, seals, kit ([See it](#see-it)) |
| Potential impact | The fraud is named and sized (IC3, AFP), the 2026 agent incidents are real, and SafePay runs inside a product with real users whose counters are public ([The problem](#the-problem), [Impulsa](#inside-a-product-in-production-impulsa)) |
| Innovation | Judgment beyond caps: the payee on the live web, the task the agent was given, a measured attack benchmark a judge reruns live, a seal the payee verifies without us, AP2 mandates on PayPal authorizations ([Attack benchmark](#attack-benchmark-proof-not-promise), [The seal](#the-seal-proof-the-payee-can-check)) |
| Presentation | The 3-minute tour inside the real console, the video, and this README: what is real and what is not, stated before the claims ([What is real](#what-is-real-and-what-is-not)) |

![SafePay home: live figures from the audit chain](docs/img/home.jpg)

## See it

Screenshots from the hosted demo (https://safepay.agentposhq.com), real
sandbox payouts, real web and model checks.

**The Flow.** Every payment travels as a pulse through the layers; the page
replays the real links of the audit chain and animates new ones as they land.

![The Flow: a payment pulse travelling from the agent through policy, live web and reasoning to the verdict, PayPal, a human, and the chain](docs/img/flow.jpg)

**The Ledger.** The hash chain as an AG Grid table (Community edition). Click
a link and a full-width detail row opens under it with every check it was
judged on, its reasons, its seal and PayPal batch, and the hash of the link
before it (marked "matches" or "break"). Verdict chips, a search box and a
click on any of the three AG Charts (verdict mix, approved spend per agent,
average risk by category) all drive one filter, so the grid, the pinned totals
row (released vs stopped, outside the firewall, chain breaks) and the CSV
export with full hashes always agree on what is shown. The grid is also the
operator's desk: a pinned Act column approves or rejects a held payment, or
freezes and resumes an agent, from its row.

**Ask the chain (AG Studio).** `/studio` turns the whole audit chain into an
AG Studio report: a links table and a checks table (joined on the link), a
benchmark-cases table and a mandate-stages table derived from the same records,
measures for money released and stopped, holds, blocks, average risk and failed
checks, and five finished pages plus a blank one: Overview (what SafePay let
through and stopped), Checks (which checks catch what), Timeline (released vs
stopped per day as a stacked area, holds and blocks per day, value tiles with
day sparklines, a gauge of average risk), Benchmark (every attack-benchmark
case the console ran against this deployment: caught, missed, allowed, false
alarms, by category and by mode, with each case's step verdicts) and Mandates
(a funnel of USD authorized, captured and voided, a pivot of amount by agent and
link type). Its assistant is NVIDIA Nemotron 3 Ultra on Nebius, wired
through AG Studio's own Direct LLM Runner and OpenAI-Responses adapter to a
server proxy that holds the key, pins the model and keeps its own daily cap.
One focused analyst agent with five Studio tools (view schema, view page, run a
query, add and position a widget) answers "how much did SafePay stop, by verdict
and agent, and which checks caused it?" in about six turns, from the data; it has
no tool that pays, approves or changes a decision.

![AG Studio, Timeline page: released vs stopped per day, holds and blocks per day, links and checks per day, a gauge of average risk](docs/img/gallery-studio-timeline.png)
![AG Studio, Benchmark page: the attack benchmark as the console ran it against this deployment, caught vs missed by category and by mode](docs/img/gallery-studio-benchmark.png)
![AG Studio, Mandates page: a funnel of USD authorized, captured and voided, and a pivot of amount by agent and link type](docs/img/gallery-studio-mandates.png)

**Proof.** `/proof` lists every PayPal sandbox object the chain says SafePay
created (payout batches, orders, authorizations, captures) in an AG Grid, and
"Ask PayPal" re-reads each one from PayPal's own API, so the status shown is
PayPal's, not ours. The page also maps every PayPal API SafePay calls to the
code that calls it.

![The Ledger page: the audit chain in AG Grid and three AG Charts](docs/img/ledger-page.jpg)

---

## What is real and what is not

So nobody has to guess.

**Real.**
- **PayPal.** Every payout, order, authorization, capture, void, vault token,
  webhook and reconciliation goes to PayPal's sandbox API: the real API, with
  test money. The host is pinned to the sandbox in `config.py`; nothing here
  has ever moved real money. Without PayPal keys the console says "PayPal
  simulated" and payouts are simulated, and says so on every result.
- **The checks.** The web check is a live Tavily search; the reasoning check
  is Nemotron on Nebius. Both are cached for 24 hours and capped by a daily
  budget that fails closed. Without keys they are skipped, and the result
  says "skipped".
- **The evidence.** The HMAC-signed hash chain, the Ed25519 seals and the
  operator's P-256 signatures (WebCrypto, non-extractable) are real keys;
  seals and receipts verify offline against the published key.
- **The state.** Supabase holds the chain, the ledger, approvals, policies
  and agent keys on the hosted demo; locally it is memory.
- **The numbers.** The attack benchmark and the duplicate-submission
  benchmark are real runs, committed with their runners and raw results, and
  the console reruns the attack cases live. The cost per decision is
  measured on those runs.
- **In production.** Impulsa is a real product with real micro-businesses;
  SafePay's history rules run there on every supplier document. The live
  counters at `/api/impact` are aggregates with the team's test accounts
  excluded, and they start at zero: zeros are shown as zeros. Businesses the
  team owns (AgentPOS, the foundation) that record their real supplier
  purchases are counted apart and named as ours: real use, not traction.
  The AgentPOS
  catalog and quotes come from a real store; the USDC settlement check was
  run against a real payment on Stellar mainnet.
- **Upstream.** [paypal/agent-toolkit#124](https://github.com/paypal/agent-toolkit/pull/124)
  is a real pull request. It is open, not merged.

**Not real, by design.**
- **No money.** Sandbox only, always.
- **The cases are ours.** The 7 demo scenarios, the sample invoices and the
  25 benchmark cases were written by us: realistic shapes, fictional vendors
  on `.example` addresses (the legitimate vendors carry their real domains so
  the directory check is meaningful). No customer data anywhere in the repo.
- **The human.** Whoever clicks Approve (or answers in Telegram, or presses
  the kit's button) is the human in the loop. Operator actions are open on
  the public demo so a judge needs no setup; `SAFEPAY_OPERATOR_TOKEN` locks
  them in a real deployment.
- **The kit.** `safepay-kit` is tested end to end against the real console
  with the keyboard standing in for the buttons. Its GPIO board follows
  gpiozero and the pins in `docs/kit.md`, but has not been run on a
  Raspberry Pi yet.
- **The dispute switch.** The `CUSTOMER.DISPUTE.*` webhook path (re-read,
  match, freeze, chain, alert) is tested offline against a scripted dispute;
  no real sandbox dispute has been raised against it yet.
- **Stablecoins are validated, never moved.** SafePay has no wallet.
- **AP2.** SafePay produces and verifies AP2-shaped mandates on its PayPal
  mandates. It is not a participant in an AP2 network, and the store does
  not sign carts yet (the cart says so).
- **The tamper buttons** edit your own session's stored policy, and a copy
  of the chain, to show what detection looks like. The stored history is
  never altered.
- **Customers and revenue.** None yet. Pricing is a proposal; the measured
  figures are costs, not income. Impulsa is the first integration, by the
  same team.

## The problem

Agents are starting to pay the bills: SaaS renewals, cloud invoices, contractor
payouts. The upside is obvious; the risk is the same thing. An agent that can pay
can also be prompt-injected by an invoice email into paying an attacker, talked
into an amount that does not fit its task, handed a real vendor's name on a
lookalike domain, or sent to a merchant that was registered last week and shows
up on scam-report pages. A static allow-list is too blunt, and letting the model
decide alone is exactly what goes wrong.

This is not a hypothetical class of fraud; it is the most expensive one
businesses already face, and agents inherit it at machine speed:

- **USD 3.05 billion** lost to business email compromise in 2025, reported to
  the FBI ([IC3 2025 Internet Crime Report](https://www.ic3.gov/AnnualReport/Reports/2025_IC3Report.pdf)).
- **79%** of organizations were hit by attempted or actual payments fraud in
  2024, with business email compromise the No. 1 avenue; vendor imposter fraud
  was cited by **45%**, up 11 points ([AFP 2025 Payments Fraud and Control
  Survey](https://www.financialprofessionals.org/about/learn-more/press-releases/Details/survey-79-percent-of-organizations-were-victims-of-attempted-or-actual-payments-fraud-activity-in-2024)).

And agents have already started doing it to themselves. In February 2026 an
autonomous trading agent asked for about 4 SOL sent 52.4 million tokens, 5%
of a coin's supply, after a decimal confusion and a lost conversational state
([as reported](https://blockeden.xyz/blog/2026/02/24/ai-agent-450k-accident-lobstar-wilde/)).
In October 2026 OpenAI disclosed that one of its internal research models,
denied free testnet tokens, paid its fees from a stranger's wallet found
through a public recovery phrase and left it out of its report; its finding
was that "a model can substitute its own judgment about likely harm for the
account holder's permission"
([Fortune](https://fortune.com/2026/10/16/rogue-openai-agent-crypto-wallet-funds-task/)).
OWASP moved **Excessive Agency** to third place in its 2026 LLM Top 10 and
published a separate Top 10 for agentic applications; SafePay's answer to each
of its ten entries, with the test that proves it, is in
[docs/owasp-asi.md](docs/owasp-asi.md).

SafePay's rules are these attacks by name: the lookalike payee, the "we changed
our bank details" email, the invoice paid twice, the instructions hidden in the
invoice, the agent that decides a stranger's wallet is fair game.

SafePay is the missing layer: a payments firewall that an operator describes in
plain words once, and that every agent payment has to pass through. Spending
caps are where most tools stop. SafePay also looks outward (is this payee what
it claims to be?) and inward (does this payment serve the task the agent was
given?), and it proves how well that works.

## Where SafePay sits

The agentic-commerce stack is settling into layers, and SafePay is one
specific layer of it, on the buyer's side:

- **PayPal's Agent Ready and Store Sync** make a merchant payable and
  discoverable by agents. They are the merchant's side. SafePay is the
  buyer's: the control the agent's operator runs before the agent spends.
  PayPal's own docs distinguish "supervised" from "automatic" shopping;
  SafePay is the supervision for the automatic case, with the human on a
  Telegram card instead of in front of the checkout.
- **UCP, ACP and AP2** describe discovery, checkout and proof of
  authorization. SafePay issues AP2 Intent, Cart and Payment mandates (the
  layer UCP carries as an extension) bound to a PayPal authorization, so
  whatever checkout protocol the merchant speaks, the mandate that says
  "this human authorized this budget for this task" is there.
- **MCP gateways** (policy proxies that sit in front of every tool call)
  decide whether a tool may run. SafePay is the payment-specific judgment
  behind that yes: any gateway can forward a payment to `safepay_check` or
  `/api/check` and get a verdict, evidence and a signed attestation back.
- **LLM spend proxies** cap what an agent spends on tokens. SafePay caps and
  judges what it spends on money. They compose; neither replaces the other.

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
   beneficiary" control banks apply. `hold_above` is "ask me before anything
   over X": inside the hard cap, but big enough to wait for a tap. The
   deterministic policy is the floor: the model layers below can tighten a
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

The console is four pages on one design:

- **Flow** (`/`): the didactic view. An animated map of the layers (agent,
  policy, live web, reasoning, verdict, PayPal, human, chain) through which
  every payment travels as a pulse. It is not a mock: it replays the most
  recent links of the real audit chain, decision by decision, with the
  verdict colour, the stage that stopped it and the human or settlement
  links that followed, and it keeps polling the chain, so running the demo
  in the Console lights it up live. Live figures from the chain, the layers,
  the benchmark table and the early-access form sit under it.
- **Console** (`/console`): write the policy in plain words and apply it,
  then run the demo scenarios, a custom payment or a PayPal Agent Toolkit
  order through the gate, and watch each intent resolve: the checks (each
  one opens to show what it looked at: web sources and snippets, the
  model's verdict, the rule numbers), the verdict, the PayPal sandbox batch,
  the signed stamp. Pending approvals and the session's agents sit under the
  results. "New session" starts the daily-cap scenario fresh: the policy,
  the agents and the budget are scoped to your session, which follows you
  across pages.
- **Agents** (`/agents`): every agent id seen, with Freeze / Resume and
  Issue key / Revoke key, for this session or all of them.
- **Ledger** (`/ledger`): the hash-chained log as an AG Grid table with a
  live verification badge, your session's links by default ("Show all
  sessions" for the shared chain); "Verify chain", "Reconcile with PayPal"
  and "Tamper (demo)", which edits a row on a copy so the badge turns red.
  Three AG Charts read the same chain: the verdict mix, approved spend per
  agent (with anything that moved outside the firewall as its own bar), and
  average risk by category; clicking a slice or bar filters the grid. Each
  row opens to its checks and hashes; Export CSV downloads what is shown.

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
set -a; . ./.env; set +a
python examples/agent_toolkit_demo.py
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

In the console, the "Agentic commerce" card does the same through
`POST /api/toolkit/order`: the gate shares the agent's guardrail and audit
chain, so an order counts against the same daily cap as a payout, honours the
session policy and the agent's key, and is chained as an **order** link. The
light `paypal-agent-toolkit` 1.1.0 release is a core dependency (later
releases pin whole agent frameworks); `pip install safepay` brings it.

**The gate also makes the toolkit idempotent.** PayPal de-duplicates a POST
that carries a `PayPal-Request-Id` header: the same id again returns the
original order instead of creating a second one. The toolkit's Python client
does not send that header (we checked the 1.1.0 and 1.11.0 wheels,
`shared/paypal_client.py`: the `request_id` its `Context` accepts is never
read), so an agent that retries `create_order`, `capture_order`,
`create_subscription` or `send_invoice` after a timeout creates a second
order, charge, subscription or invoice. Crashpay, another entry in this
hackathon, measured that double charge against the sandbox. Through
`GuardedToolkit` every money-moving toolkit call goes out with a request id
derived from the intent (its `idempotency_key`, else a hash of the method,
the parameters and the agent), the id is written into the chain link, and a
retry of the same intent reaches PayPal as the same request. The header alone
is not enough when the duplicates are simultaneous (PayPal de-duplicates
against a request it has already finished), so the gate also runs one
toolkit call per request id at a time and hands the twin the first result
instead of calling PayPal again; a later retry with the same idempotency key
is answered the same way for an hour, without re-judging it. We measured it
against the sandbox (`benchmark/retries/`, 20 simultaneous duplicates of one
`create_order`, 3 rounds): the shipped client makes 20 orders every round,
the header alone still lets 2 to 7 through, the gate makes 1. SafePay's own
PayPal client (Payouts, Orders, Vault) has always sent it. We also reported
the gap upstream and sent the fix at the source: [paypal/agent-toolkit#124](https://github.com/paypal/agent-toolkit/pull/124)
(per-call request id for the Python client, open as of November 8, 2026).

![The duplicate-submission benchmark on /security: 60/60 orders as shipped, 13/60 with the header alone, 3/60 through the gate](docs/img/gallery-retries.png)

### Use it as an MCP server (any agent)

SafePay also runs as a Model Context Protocol server, so any MCP client (Claude
Desktop, Cursor, an agent framework) can route its payments through the guardrail
without importing any SafePay code. It exposes seventeen tools: `safepay_evaluate`
(judge without paying), `safepay_pay` (evaluate, then pay via Payouts only on
ALLOW), `safepay_pay_invoice` (SafePay's own payables agent: invoice text in,
judged payment out), `safepay_create_order` (gate the Agent Toolkit order flow),
`safepay_open_mandate`, `safepay_capture` and `safepay_close_mandate` (authorize a
budget, capture what the task costs, void the rest),
`safepay_pending_approvals`, `safepay_approve` and `safepay_reject` (the human
in the loop for HOLD decisions), `safepay_payout_status` (settlement),
`safepay_draft_policy` (plain words to a policy draft), `safepay_agents`,
`safepay_pause_agent` and `safepay_resume_agent` (the kill switch),
`safepay_reconcile` (flag money that moved outside the firewall), and
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
| `SUPABASE_URL` / `SUPABASE_SERVICE_ROLE_KEY` | Durable, shared audit chain, ledger and approvals | In-memory state |
| `PAYPAL_WEBHOOK_ID` | Accept PayPal Payouts webhooks at `/api/paypal/webhook` (signature verified with PayPal) | Settlements only on demand (console button, API, MCP tool) |
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` / `TELEGRAM_WEBHOOK_SECRET` | Every HOLD is pushed to a Telegram chat with Approve and pay / Reject buttons; taps come back through `/api/telegram/webhook` | Approvals in the console, API and MCP only |
| `SAFEPAY_CHECK_CACHE_SECONDS` | How long a merchant or reasoning result is reused for the same inputs (default 86400; 0 disables) | 24 h cache |
| `SAFEPAY_EXTERNAL_DAILY_BUDGET` | Most paid web and model calls per UTC day; past it a check fails closed (HOLD) | 400 |
| `SAFEPAY_OPERATOR_TOKEN` | Operator actions on the console API (apply a policy, freeze an agent, approve, issue a key, reconcile, tamper demo) require it in `X-SafePay-Operator` | Operator actions are open (a judge's zero-setup demo) |

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

`benchmark/attacks/` runs 17 realistic attacks on a wallet-holding agent (prompt
injection in invoices and vendor pages, goal mismatches, an unknown payee, a
lookalike recipient, amount inflation, limit breaches, a slow drain, a velocity
burst, a duplicate invoice, a "we changed our bank details" payee change, an
amount far above a contractor's history) plus 8 legitimate invoices, through
the guardrail. Three modes, side by
side, so the numbers are honest:

| Mode | Attacks caught | Legit allowed (no false positives) |
| --- | --- | --- |
| policy only (rules + vendor directory + payment history, no model, no web) | 9/17 (52%) | 8/8 (100%) |
| full (policy + Tavily + Nemotron) | **16/17 (94%)** | **8/8 (100%)** |
| strict (full + hold unknown vendors) | **17/17 (100%)** | **8/8 (100%)** |

Rules alone catch what breaches a limit plus, with the vendor directory, the
lookalike recipient and the inflated amount (the two misses of the first
version of this benchmark, now closed deterministically) and, from the
payment history in the ledger, a duplicate invoice (blocked), a payee change
for a vendor already paid and an amount far above what a vendor was paid
before (both held). Three monthly invoices from the same vendor with different
invoice numbers pass: the duplicate rule keys on the invoice, not the amount.
The model layers
catch every prompt injection and every goal mismatch, with zero false positives
on real vendors. The one case full mode does not catch reliably is the unknown
payee with a plausible invoice: the model checks are not deterministic and that
case flips between runs (caught by reasoning in an earlier run, missed in the
three runs behind the table). That is exactly what
the `hold_unknown_vendors` control is for, and strict mode shows it. Full
per-case results are in `benchmark/attacks/results.md`. The same cases also
run live from the console ("Run the attack benchmark"): the deployment judges
them one by one in the mode you pick, every verdict lands in the Ledger as a
`benchmark` link, nothing is paid, and the running tally is shown next to the
committed run's numbers. Live runs vary with the model and the web: the run
in the screenshot below caught 17/17 and held one legitimate invoice (7/8),
where the committed run reads 16/17 and 8/8; both are in the chain.

![The console running the attack benchmark in full mode against the deployment, each verdict with its web sources and model verdict](docs/img/gallery-console-benchmark.png)

```bash
python benchmark/attacks/run_attack_benchmark.py --mode all    # needs keys for full/strict
python benchmark/attacks/run_attack_benchmark.py --mode policy # offline
python benchmark/retries/run_retry_benchmark.py --n 20 --rounds 3 # duplicate submissions, needs sandbox keys
```

**Red team: attacks the benchmark has never seen.** A committed benchmark
can be studied; a judge should be able to throw something new at the
guardrail. "Red team: new attacks" in the console does that in two ways:
the model (Nemotron) writes six new attacks from the task, the policy in
plain words and one example per family, and code validates each one before
it is judged (a known family, inside the per-payment cap so the cap does not
catch it for free, a fictional or look-alike recipient); or, offline and
free, the committed attacks are rewritten the way an attacker does once the
first version is caught (the same instruction hidden by zero-width
characters, Cyrillic look-alikes, an HTML comment or base64; a fresh
look-alike domain of an approved vendor; an amount pushed past the
tolerance; a new off-task merchant; a plausible payee nobody approved). Each
case is judged live in the mode you pick and chained as a `redteam` link,
nothing is paid, and every attack that gets through comes with the rule that
would close it (the expected amount for that vendor, a blocked category,
strict mode). **Apply the fixes (signed) and rerun** folds those rules into
the session's policy, signs it with the operator key, applies it, and judges
the same attacks again under fresh agent ids, so the judge sees the miss,
the fix and the catch in one minute. Misses are shown as misses; the model
writes what it writes, and we do not curate it.

## Policy in plain words

An operator should not have to write JSON to say what they mean. In the console
(and with `safepay --draft-policy "..."` or the `safepay_draft_policy` MCP
tool) you write the rules as you would say them:

> Up to $300 per payment and $1,000 a day. Ask me before anything over $100.
> No gambling or crypto. Hold any new payee for me.

Nemotron turns that into a `PolicyConfig` draft (a rule-based parser does it
without a key), with a one-line summary per rule and a question for anything
important the text left unsaid. You review the exact fields, edit them if you
like, and apply. Three things keep this honest: the model's output is data
that is validated and coerced field by field (unknown fields dropped, caps
clamped, a `hold_above` over the hard cap turned off); the model never applies
anything, a person does; and text that tries to instruct the reviewer ("ignore
the limits") is refused before any model reads it. The applied policy belongs
to your console session and follows it: the agents in that session are judged by it,
other sessions are not.

### Signed by the operator, not just by the server

The policy is the one thing that decides what every agent may pay, so SafePay
does not let the server alone vouch for it. The operator's browser makes an
ECDSA P-256 key with WebCrypto; the private half is created non-extractable,
lives in IndexedDB and never leaves that browser. Applying a policy signs
`safepay-policy:v1:<session>:<sha256 of the rules>`, and SafePay verifies it
before the policy takes effect. The first signed policy pins the session to
that key: an unsigned change, or one signed by another key, is refused (403),
even with the operator token. The signature and the public key go into the
policy's chain link, so anyone can re-verify which key approved which rules.

The stored policy is re-verified every time a payment is judged under it. If
someone edits the row in the database (the console's "Tamper with the stored
policy" demo raises the cap to USD 100,000 without re-signing), the next
payment is blocked with "the policy was changed after it was signed", and
nothing moves until the operator signs again. Code: `safepay/opsig.py`.

### Learned from your own decisions

Every held payment a person approved or rejected is a link in the chain.
**Learn from my approvals** reads them back and proposes what the written
policy is missing, as a draft that goes through the same review and the same
operator signature as any other: a vendor approved 2 or more times and never
rejected joins the vendor directory at the median approved amount; a
recipient or category rejected 2 or more times and never approved is blocked.
Each proposal cites the chain links it rests on. Loosening (raising the "ask
me" threshold) is only ever asked, never drafted. When Nemotron is configured
it reviews the proposals and explains each in a sentence, and may advise
dropping one; it never sees payment free text and cannot add or change a
rule, so an injected invoice cannot write policy. Nothing applies by itself.
Code: `safepay/learn.py`, `POST /api/policy/learn`.

## Human in the loop (HOLD is a workflow, not a dead end)

When a soft check raises a concern inside the policy limits, or a payment is
over the operator's "ask me" threshold, the verdict is HOLD and the payment
waits in an approval queue. In the web console a "Pending approvals" panel
appears with **Approve and pay** / **Reject**; the same queue is available as
`GET /api/approvals`, `POST /api/approvals/<id>/approve` and `/reject`, and as
MCP tools, so a person (or a supervising agent) can decide from anywhere.
With a Telegram bot configured, every HOLD also lands in your chat as a card
with the same two buttons, and the tap resolves it through the same path, so
the chain records "approved by human: via Telegram by <name>". Approving
releases the payment and charges the spend ledger, so the daily and
per-recipient caps reflect real spend; rejecting moves nothing. Both outcomes
are appended to the audit chain as their own link, so "who approved this agent
payment, and when" is provable.

**A stolen operator token approves nothing.** In the console, every Approve
and Reject is signed by the same WebCrypto key that signs the policy: a
non-extractable P-256 key the browser made and keeps, which no script can
read. The browser signs the exact text the server gives for that button
(`safepay-approval:v1:<action>:<approval id>:<currency>:<amount>:<recipient>`),
so a signature cannot be replayed for another payment, another amount or the
other action. The chain link carries the signer, the signed text and the
signature, verifiable offline with the public key alone. Once a session is
pinned to an operator key (its first signed policy), an unsigned decision,
or one signed by another device, is refused with 403 wherever it comes from
(console, API, MCP tool, kit); a Telegram tap on a pinned session is refused
too and the card says to decide in the console. Unpinned sessions keep
working as before, which is what the public demo wants.
`tests/test_signed_approvals.py` holds the refusals and the replays.

Telegram setup, once: create a bot with @BotFather, message it, then
`python -m safepay.telegram --chat-id` to read your chat id, set
`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` and `TELEGRAM_WEBHOOK_SECRET`
(`--new-secret` makes one), and register the webhook with
`python -m safepay.telegram --set-webhook https://<host>/api/telegram/webhook`.
Telegram echoes the secret in a header on every delivery; anything else is
refused.

## Freeze an agent (the kill switch)

A bank lets you freeze a card the moment something looks wrong; SafePay does
the same for an agent. Every agent id the guardrail has seen is listed in the
console with its status, how many decisions it asked for and when it was last
seen. **Freeze agent** is the kill switch: the agent's next request is refused
before any check runs (a frozen agent is the one rule that beats every other),
the refusal is chained like any decision, and nothing moves until **Resume**. The
same controls are `GET /api/agents`, `POST /api/agents/<id>/pause` and
`/resume`, and the `safepay_agents`, `safepay_pause_agent` and
`safepay_resume_agent` MCP tools, so a supervising agent or an on-call
engineer can freeze a misbehaving agent from anywhere. With Supabase
configured the registry is shared by every instance of the service.

**PayPal can pull the switch too.** A dispute is the strongest signal an
operator gets that an agent's payment was wrong: a buyer or a bank said so.
When PayPal's `CUSTOMER.DISPUTE.*` webhook names a transaction, SafePay
re-reads the dispute from PayPal (never the event body), finds the chain
link that carries that capture, authorization or payout, freezes the agent
that made it with the dispute id as the reason, chains a **dispute** link
that points at the original decision, and alerts the operator on Telegram.
Nothing that agent asks for moves until a person presses **Resume**. A
resolved dispute is chained but freezes nobody; a dispute on money SafePay
never saw is chained as unmatched. Subscribe the webhook to the
`CUSTOMER.DISPUTE.CREATED` and `UPDATED` events in the PayPal dashboard;
`tests/test_dispute_webhook.py` runs the path offline against a scripted
dispute.

## Build your own kit (a box on the counter)

The owner of a shop is not at a web console or scrolling Telegram; they are
at the counter. `safepay-kit` turns a Raspberry Pi with three buttons and
three LEDs into the physical end of the firewall: the amber light blinks when
the agent's payment is held, **green** approves and pays, **red** rejects,
**black** freezes the agent (and thaws it on the next press). Every press goes
through the same `/api/approvals` and `/api/agents` endpoints as everything
else, so the chain reads "approved by human: via kit counter-1". The box holds
no PayPal credentials, only the console address and, if set, the operator
token.

```bash
pip install "safepay[kit]"                       # gpiozero is the only extra
safepay-kit --url https://safepay.agentposhq.com --run <session id> --device counter-1
safepay-kit --url http://localhost:5000 --board keys   # no hardware: a, r, f, q
```

Parts (about USD 30), wiring, the five setup steps and what each light means
are in [docs/kit.md](docs/kit.md). The same Pi can host the whole firewall
(console, MCP proxy and chain) on the shop's network, with PayPal as the only
thing outside. The client is tested end to end against the real console
(`tests/test_kit.py`); the GPIO board follows gpiozero and the documented pins
but has not been exercised on a Pi yet, which is said as plainly in the guide.

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
the Payouts API, never trusted from the body. The hosted demo has this webhook
live: a payout released there gets its settlement link from PayPal's push, no
button needed. If a delivery fails verification, the reason PayPal gives (for
example a wrong `PAYPAL_WEBHOOK_ID`) is printed to the host logs.

## In front of PayPal's own MCP server (one line to switch)

An agent that already uses PayPal's MCP server changes the command in its MCP
config and keeps every tool it had, same names and arguments:

```json
{"mcpServers": {"paypal": {
  "command": "safepay-mcp-proxy",
  "env": {"PAYPAL_CLIENT_ID": "...", "PAYPAL_CLIENT_SECRET": "..."}}}}
```

`safepay-mcp-proxy` (`pip install "safepay[mcp]"`, needs Node) starts
`npx -y @paypal/mcp --tools=all` itself with a token minted from the client
credentials, pinned to the sandbox unless `PAYPAL_ENVIRONMENT=PRODUCTION` is
set. Of PayPal's 28 tools, the five that move money or commit to a charge are
gated; the other 23 pass straight through:

| Tool | Amount SafePay judges |
| --- | --- |
| `create_order` | the line items, shipping and discount in the call |
| `pay_order` | read from PayPal's own order (`get_order`), with the payee |
| `create_refund` | the amount in the call; a full refund of unknown size is held |
| `accept_dispute_claim` | read from PayPal's own dispute (`get_dispute`) |
| `create_subscription` | read from PayPal's plan price, times quantity |

The amount comes from PayPal whenever PayPal has it, not only from the agent.
A gated tool also accepts optional `safepay_purpose`, `safepay_task`,
`safepay_merchant` and `safepay_recipient` fields (stripped before PayPal sees
the call) so the reasoning check knows why. ALLOW forwards the call; HOLD and
BLOCK answer the agent with the reasons and never call PayPal. Every gated call
is a link in the same signed chain as the console. Verified end to end against
the sandbox: a USD 20 `create_order` was allowed and PayPal created order
9UG33738VS856624H; a USD 900 one was blocked and PayPal was never called.

## SafePay's own payables agent (invoice in, judged payment out)

SafePay ships a native agent for the job it guards: paying vendor invoices.
Paste an invoice or a vendor's billing email (console card, `POST
/api/payables/pay`, the `safepay_pay_invoice` MCP tool, or
`SafePayAgent.pay_invoice`). It reads the vendor, payee, amount, invoice number
and purpose, then pays through the guardrail with no privileges: the same
policy, history rules, web and reasoning checks and seal as any other agent.

Reading is where an invoice attacks an agent, so the reader is built to be
attacked. Nemotron extracts, but every value it returns (the email, the
amount, the invoice number) must appear in the invoice text; a value it
invented or was talked into is dropped and the rule-based reader's is used.
An invoice with instructions aimed at the reviewer never reaches the model and
is held. "We have updated our payment details" is held for a person even for a
vendor the ledger has never seen. Four samples in `examples/invoices.json` show
it in order: a real invoice is paid and sealed; the same invoice resent is
blocked as a duplicate (even when the vendor's name is written differently);
a bank-details change to a lookalike domain is blocked; an invoice that talks
to the reviewer is held.

## Any agent asks before it pays: SafePay Check (remote MCP)

The largest agents now pay through their own checkout: Meta's Muse checks out
with PayPal, Shop Pay and Stripe Link; ChatGPT uses ACP. SafePay does not need
to hold the money to protect it. **SafePay Check** is a remote MCP server at
`https://safepay.agentposhq.com/mcp` (streamable HTTP, stateless, JSON
responses, agent-key auth) that any agent calls before it pays:

| Tool | What it does | Moves money |
| --- | --- | --- |
| `safepay_check_payment` | Judges merchant, amount and task with the same guardrail as every payment (policy, history, live web check, intent); chains a `precheck` link; returns allow, hold or block, the reasons and a signed `safepay-precheck-1` attestation valid 15 minutes | No |
| `safepay_verify` | Verifies a seal id, a seal receipt or a precheck against the published Ed25519 key | No |
| `safepay_verify_settlement` | Reads a Stellar transaction from the public ledger and checks it against a precheck: succeeded, approved payee, Circle's USDC, no more than approved, inside the validity window; returns findings and a signed `safepay-settlement-1` result | No |
| `safepay_policy` | The rules this key is judged under, in plain words | No |
| `safepay_recent_checks` | This account's latest checks | No |

No tool moves money, which is also what Meta's Muse Connector Platform
requires today (its guidelines do not approve money-movement tools); the
submission copy is in [`docs/muse-connector-submission.md`](docs/muse-connector-submission.md).
The chain keeps no personal data from these checks: the payee address, the
task text, the purpose and the reference are stored only as hashes. Tested
with the official MCP Python SDK (`streamablehttp_client`): handshake, tool
list, a check, its attestation verified, a blocked payment.

```bash
claude mcp add --transport http safepay https://safepay.agentposhq.com/mcp \
  --header "Authorization: Bearer spk_your_key"
```

[`/connect`](https://safepay.agentposhq.com/connect) creates a key for your
console session (your session's policy applies) and runs a live check;
[`/security`](https://safepay.agentposhq.com/security) shows the attack
benchmark case by case. SafePay Check is listed in the official MCP Registry
as `com.agentposhq.safepay/check` ([`server.json`](server.json)). It was submitted to Meta's Muse Connector
Platform on November 7, 2026 and is under review; it is not a listed Muse
connector yet.

## Stablecoins: the same guardrail for PYUSD and USDC

PayPal moves dollars and its own stablecoin, PYUSD; agents increasingly pay in
stablecoins too (x402, AgentPOS stores settle in USDC on Stellar). SafePay does
not move stablecoins: it validates them, with the same policy, chain and
signed evidence as a PayPal payment.

- **Before** (`safepay_check_payment` with `currency` PYUSD or USDC and a
  `network`): the network must be named, the token must be issued there, the
  network must be allowed by policy (`allowed_networks`), and the payee must
  be a valid address for that network (Stellar down to its CRC16 checksum,
  Solana 32-byte keys, EVM hex). A stablecoin sent to the wrong network or a
  bad address is gone for good, so that is a BLOCK. Every other rule applies,
  with amounts counted one to one against the USD limits: a store that was
  paid at one wallet and now asks for another is held (payee change).
- **After** (`safepay_verify_settlement`): SafePay reads the transaction from
  Horizon, classic payments and Soroban token transfers alike (how x402 pays),
  and checks it against the signed precheck.

Verified in production against a real AgentPOS payment on Stellar mainnet
(1 USDC, [`35642adc…`](https://stellar.expert/explorer/public/tx/35642adcd8217d24288c41cae3a829c8d03844ab1a4483e74b2a923bdb360cd5)):
payee, token and amount match; timing is flagged, because that payment was made
in September, long before this check. An EVM address offered for a Stellar
payment is blocked before anyone pays. [`scripts/usdc_settlement.py`](scripts/usdc_settlement.py)
runs both steps against the public endpoint.

## Mandates: authorize a budget, capture what the task costs

An agent booking a hotel or renting compute does not know the final bill when
it starts. Paying the ceiling up front over-pays; asking a person at every step
defeats the agent. A mandate is the middle path, on PayPal's authorize and
capture (Orders v2):

1. **Open.** The agent asks for a ceiling for a task. The ceiling goes through
   the whole guardrail (policy, history, live web, Nemotron) like any payment;
   only on ALLOW does SafePay authorize it with PayPal. Nothing is charged yet,
   and the whole ceiling counts against the agent's daily cap from now on.
2. **Capture.** Each draw is checked on its own before PayPal is called: the
   caller is the agent that opened the mandate, it is not frozen, and the
   amount fits in what is left. Every capture carries its own seal in the
   note PayPal shows the payer.
3. **Close.** Voids what was not used with PayPal (a capture marked final does
   the same) and credits it back to the daily cap.

Each step is a chain link (`authorize`, `capture`, `void`) and the mandate's
state is read back from those links, so there is no second store that could
disagree with the chain. Verified against the PayPal sandbox: USD 300
authorized, USD 180 captured and sealed, a USD 200 draw and a draw by another
agent blocked before PayPal was called, USD 120 voided; PayPal reports the
authorization as VOIDED. The agent card lives in PayPal's Vault (v3): SafePay
saves it once (setup token, then payment token) and every mandate after that is
authorized with the token, never with the card number; in the sandbox that card
is PayPal's published test card. PayPal's own events close the loop: a
`PAYMENT.CAPTURE.*` or `PAYMENT.AUTHORIZATION.VOIDED` webhook is verified,
re-read from PayPal's API and chained as a settlement of the mandate, and a
capture of an authorization SafePay never opened is chained as money that moved
outside the firewall. Console card,
`POST /api/mandates`, `/api/mandates/<id>/capture` and `/close`, the
`safepay_open_mandate`, `safepay_capture` and `safepay_close_mandate` MCP tools,
or `SafePayAgent.open_mandate` / `capture` / `close_mandate`.

### AP2 mandates on top

AP2 (the Agent Payments Protocol) carries three signed objects through an
agentic purchase; SafePay produces and checks them on its PayPal mandates,
with the field names of the AP2 SDK (`ap2.models.mandate`, `payment_request`):

- **IntentMandate**, signed by the operator. Opening a mandate in the console
  sends one, signed in the browser with the same P-256 key that signs the
  policy (an ES256 compact JWS whose `transaction_data` carries the sha256 of
  the mandate). SafePay verifies it before PayPal is called, against the
  session's pinned operator key when there is one, and then enforces it: a
  payee outside its `merchants` is blocked, and no capture is allowed after
  its `intent_expiry`.
- **CartMandate**, built from the AgentPOS store's own quote as a W3C
  PaymentRequest. Its total must be the ceiling being authorized and it must
  come from the payee's domain. The store does not sign AP2 carts yet, so
  `merchant_authorization` is null, and says so.
- **PaymentMandate**, one per PayPal capture, bound to the intent and cart by
  their hashes and signed with SafePay's published Ed25519 key
  (`safepay_authorization`, an extension field; `user_authorization` stays
  null because the person signed the intent, not each capture).

`GET /api/mandates/<id>/ap2` returns all of them, rebuilt from the chain.
Verified in production against the PayPal sandbox (authorization
`74L74407C64042417`). Code: `safepay/ap2.py`.

## Buying from an agent-ready store (AgentPOS)

[AgentPOS](https://agentposhq.com) is our open-source point of sale for AI
agents: it makes an existing store buyable by agents (catalog over UCP, MCP
and OpenAPI, carts the store itself quotes). The console's "buy from an
agent-ready store" card closes the loop from the buyer's side: SafePay's
shopping agent reads the live catalog of AgentPOS's demo store, the store
quotes the cart, and the quoted total is opened as a mandate (policy, live web
check of the store, Nemotron against the task), authorized with PayPal and
captured once. The amount always comes from the store's quote, never from the
agent. AgentPOS settles in USDC on Stellar; here the catalog and quote are the
store's real ones and the rail is a PayPal sandbox mandate (1 USDC = 1 USD).
Code: `safepay/agentpos.py`; only the configured store is ever called.

The merchant's side closes the loop: before it delivers, the store asks
`POST /api/merchant/verify` with the seal, the amount and its own domain, and
SafePay answers from the signed chain whether the payment was approved, settled
at PayPal, for that amount, made out to that merchant, on an intact record
(`safepay.seal.merchant_check`). The console's store card ends with the store's
check: "the store verified the seal before delivering".

## Inside a product in production: Impulsa

[Impulsa](https://impulsaapp.cl) is a SaaS from the same team for
micro-businesses in Chile; owners record the supplier documents they buy
stock with. SafePay's history rules now run there before a supplier document
is saved: `POST /api/check/purchase` (agent key required) answers whether that
supplier's document number was already recorded (block) and whether a unit
cost is far above that product's own purchase history (hold); the owner sees
the alert in Spanish and can correct it or record anyway. Nothing is paid.
The check is chained, but the public link carries only a hash of the
business, the verdict and the rule fired: no names, document numbers or
amounts. Code: `safepay/purchase_check.py` here, `lib/safepay.ts` in Impulsa.

What the owner does with each alert is measured: **Corregir** (corrected:
an error that did not get saved, with its amount) or **Registrar igual**
(recorded anyway: an alert that was not needed). Impulsa reports it back to
SafePay (`POST /api/check/purchase/outcome`), shows each owner a "Protegido
por SafePay" panel with their own tally, and the public aggregates are live at
[`/api/impact`](https://safepay.agentposhq.com/api/impact) and on the home
page, with the operator's own test accounts excluded
(`SAFEPAY_IMPACT_EXCLUDE`) and the businesses the team itself owns tallied
apart under their names (`SAFEPAY_IMPACT_TEAM`, set to
`python -m safepay.impact <impulsa business id>` plus a label), so a
reader can tell our own use from external businesses. A backtest over the
purchases Impulsa held before SafePay (20 supply purchases, one business)
raised no alerts: no false alarms on clean data, from a sample too small to
say more, which is why the live outcomes are the measure.

![Impulsa in production: SafePay stops a supplier document recorded twice](docs/img/impulsa-safepay-alert.png)
![Impulsa in production: a unit cost 2.5x this product's purchase history is held](docs/img/impulsa-safepay-price.png)

## The seal (proof the payee can check)

Every payout SafePay releases carries a seal in the note PayPal shows the
recipient:

```
Team plan, monthly | Sealed by SafePay SP-3F9A0C21BE. Verify: safepay.agentposhq.com/v/SP-3F9A0C21BE
```

The seal id is written into that payment's signed chain link. Anyone holding
it (the payee, their accounts team, an auditor) opens `/v/<id>` and sees, with
the recipient masked: the amount and merchant, the checks the payment passed,
the fingerprint of the policy it was judged under, that the link's hash (and
HMAC signature) recomputes, that it points at the intact link before it, and
what PayPal later reported for the payout. `GET /api/v/<id>` returns the same
as JSON. A seal is derived from the idempotency key when there is one, so a
retried payment keeps its seal. It turns "an agent paid you" into "an agent
paid you, and here is the signed record of why it was allowed to".

### A receipt anyone can verify, without SafePay

The chain is HMAC-signed, which only SafePay can check. So every seal also
has a receipt that does not need SafePay at all: `{body, hash, signature}`,
the same envelope as AgentPOS merchant receipts, where `hash` is the sha256 of
the canonical body and `signature` is Ed25519 over
`"safepay-receipt-1" LF hash`. The public key is published at
[`/.well-known/safepay-seal-keys.json`](https://safepay.agentposhq.com/.well-known/safepay-seal-keys.json).
The receipt names the payee by domain and by the sha256 of its address (the
payee can match it; nobody else learns the address), and carries the chain
link's seq and hash. The seal page verifies it in the visitor's browser with
WebCrypto; `POST /api/merchant/verify` returns it to the merchant; and
offline, with the key file saved once:

```bash
python -m safepay.receipt receipt.json safepay-seal-keys.json
# OK
# SP-B821E3F9A7: USD 400 (minor units), payment.released, chain link #119
```

Code: `safepay/receipt.py`, `webapp/static/receipt.js`.

## Who is asking: agent keys and the operator token

Two identities meet at the firewall, and neither should be a free-text
field. **Agents** can be bound to a key: "Issue key" on an agent in the
console (`POST /api/agents/<id>/key`) returns a `spk_...` key once and SafePay
keeps only its sha256. From then on a request that presents the key
(`Authorization: Bearer spk_...`) is that agent whatever its body claims, and
a request for that agent without the key is refused before any check runs.
An agent without a key keeps working as before, so the demo stays
zero-setup; "Revoke key" or issuing a new one kills the old key at once.
**Operators** can be required to present `SAFEPAY_OPERATOR_TOKEN` in the
`X-SafePay-Operator` header for every operator action (apply a policy,
freeze an agent, approve or reject, issue a key, reconcile, the tamper demo).
Unset, those actions are open, which is what the public demo wants; set, the
console asks for the token once and keeps it in the tab. Paying is never an
operator action: agents keep working either way.

## Outside the firewall (money that moved without a decision)

A guardrail that only sees what passes through it is blind to what goes
around it: an agent that calls PayPal directly, a leaked credential, a bug
in a loop. SafePay watches the account two ways:

- **PayPal's own webhook.** A payouts event for a batch SafePay never
  released is not ignored. It is chained as an **outside** link (verdict
  BLOCK: not authorized by the firewall, with the receiver, amount and
  PayPal status re-read from the Payouts API) and the operator gets a
  Telegram alert. On the hosted demo this is live: a payout sent straight
  to the sandbox account shows up in the ledger within seconds.
- **Reconciliation.** "Reconcile with PayPal" in the console
  (`POST /api/reconcile`, the `safepay_reconcile` MCP tool,
  `SafePayAgent.reconcile()`) pulls the account's debits for the last 24 hours
  from Transaction Search and compares them with the chain; any debit no
  decision released is chained the same way. Transaction Search has to be
  enabled for the PayPal app (the error says so if it is not), and PayPal's
  data lags by up to a few hours.

Both paths are idempotent per payout, so a webhook retry or a second
reconcile never flags the same money twice.

## Business model

Open core, priced on decisions (one payment an agent asked SafePay to
judge). Self-hosting is free and MIT forever. The hosted service: Sandbox
free (1,000 decisions a month), Team USD 149 a month (10,000 decisions, live
PayPal, Telegram approvals), Business USD 799 a month (60,000 decisions,
SSO, automated reconciliation, seven-year retention), and volume contracts
for agent platforms and payment providers, plus onboarding and agent
payment audits as services. Measured cost: a payment that passes policy
costs USD 0.0056 to 0.0086 in web and model calls (the Tavily search is 90%
of it; the two Nemotron calls cost USD 0.0006 together), a policy block or
a cached repeat costs nothing, and the blend over the demo and benchmark
payments is USD 0.0032 to 0.0048, so the price follows the cost and nobody
pays a percentage of the money moved. Details: [/pricing](https://safepay.agentposhq.com/pricing).

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
- approvals survive restarts and resolve atomically,
- the per-run policy and the agent registry (freeze / resume) are shared too
  (`safepay_settings`, `safepay_agents`).

Create the tables once with `supabase/schema.sql` (RLS on, no policies: only
the service role key, kept server-side, can touch them). The console shows
`state: durable (Supabase)` when it is on. The audit chain is never deleted;
"New session" in the console starts a new session id for the demo agents instead.

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

**Hiding the instruction does not help.** A pattern scan is easy to dodge
with text a model still reads: zero-width characters between letters,
Cyrillic look-alikes (`ignоrе`), fullwidth or mathematical
alphabets, bidirectional overrides, invisible Unicode tag characters, HTML
comments, elements styled `display:none`, HTML entities, a base64 blob.
SafePay unfolds all of those before it scans (and scans HTML comments,
hidden elements and decodable base64 as layers of their own), and the
evidence names the disguise: "hidden in an HTML comment by 12 invisible
characters: ignore previous instructions". Honest text with the same
ingredients (soft hyphens, emoji joiners, a random token, Spanish with
accents, real Cyrillic or Arabic) stays clean; `tests/test_injection_obfuscation.py`
holds both sides.

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

501 tests, deterministic and offline, in about five seconds (98% line
coverage of the package and the console at 0.9.0). They cover policy,
aggregation, the daily cap across a burst, simulated execution, audit
integrity, the vendor directory, injection defences, settlement, the
plain-words policy parser and validator, the agent registry, Telegram
approvals (fake transport), the web and MCP surfaces, the counter kit (a fake
gpiozero stands in for the Pi), and every PayPal REST call SafePay makes
(OAuth, Payouts, Orders v2 authorize/capture/void, Vault v3, Transaction
Search, webhook verification) against a scripted HTTP layer that checks the
exact request PayPal would receive. Two tests are skipped unless live
Supabase credentials are present, and the FastMCP wrapper test unless the
`mcp` extra is installed.

## License

MIT. See `LICENSE`.
