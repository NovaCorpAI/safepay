# SafePay, Devpost submission copy

Paste-ready text for paypalaihackathon.devpost.com. Links at the bottom get
filled once the video is up. No em dashes used, per house style.

---

## Project name
SafePay

## Tagline (one line)
A payments firewall that sits between an autonomous AI agent and PayPal, and
decides allow, hold, or block before any money moves.

## Elevator pitch (shorter, if a short field is asked)
Give an AI agent a wallet and it can be prompt injected into paying the wrong
payee, the wrong amount, or a merchant registered last week. SafePay is the
guardrail in between: policy, live web verification, and intent reasoning on
every payment, with a signed record of each decision.

---

## Inspiration
Agentic commerce is arriving fast: agents that can browse, decide, and pay on
their own. The upside is obvious, and so is the failure mode. The moment an
agent can move money, a bad prompt, a spoofed merchant, or a subtle goal drift
turns into a real payment. A static allow list is too blunt for open ended
tasks, and letting the model approve its own spending is exactly what goes
wrong. We wanted the missing layer: something an operator sets up once, that
every agent payment has to pass through.

## What it does
SafePay is a payment safety guardrail. Each payment the agent wants to make runs
three checks, cheapest and strictest first:

1. Policy, deterministic. Per transaction and daily limits, allowed and blocked
   categories, recipient allow and block lists, currency, and an approved
   vendor directory (each vendor's real domains and expected amount), which
   catches a lookalike recipient and an inflated amount even when they sit
   inside every cap. A breach is a hard block and no model call is spent. The
   daily cap is enforced across a burst of payments, not one in isolation.
2. Merchant verification, live web via Tavily. Pulls fresh, neutral evidence
   about the payee and judges whether there are credible fraud reports. It is a
   soft signal: it can raise a hold, never a silent block on noisy web data.
3. Intent and anomaly reasoning, NVIDIA Nemotron on Nebius Token Factory. Reads
   the task the agent was given and judges whether the payment actually serves
   it. This catches goal and payment mismatches a rule set misses, for example a
   cloud invoice agent being steered into a casino payout.

The verdict is allow, hold, or block. On allow, SafePay releases the payment
through the PayPal Payouts API (the agent sends money, no buyer redirect, fully
backend). On hold or block the money never moves. A hold is not a dead end: it
waits for a person, who approves it (SafePay then pays and charges the ledger)
or rejects it, in the console or through the MCP tools, and that decision is
appended to the chain as its own link. When PayPal later reports what
happened to a released payout (succeeded, failed, returned), SafePay appends
that settlement to the same chain and releases the budget of a payout that
never left, via a signature-verified webhook or on demand. Every decision is
appended to a hash-chained audit log: each record carries the hash of the one
before it, so editing or dropping any earlier decision breaks every hash that
follows, and a one-line verify walks the whole chain. The web console shows this ledger as an
interactive table with a live verification badge, and a demo button that tampers
with a row so you can watch the badge turn red.

## How we built it
Python core with a clean separation between the deterministic policy engine and
the two model backed checks, orchestrated by a guardrail that aggregates the
three into one decision. Payments go out through the PayPal sandbox Payouts API
(OAuth client credentials, host pinned to the sandbox so it cannot touch real
money by accident). SafePay also drops in front of the PayPal Agent Toolkit: it
wraps the toolkit's money-moving tools so the agent's create order or pay order
call runs only after the guardrail returns ALLOW, which is exactly the "invoke
pay_order only after the user approves" step the toolkit asks for, made
automatic. Reasoning runs on NVIDIA Nemotron served by Nebius Token Factory
through an OpenAI compatible endpoint. Merchant evidence comes from Tavily. The
audit layer canonicalizes each decision to sorted key JSON and signs it with
HMAC SHA256. There is a command line runner, a Flask web console a judge can
click through (with the audit chain shown as a live, verifiable AG Grid table),
a toolkit gate demo that creates real sandbox orders, an MCP server that exposes
the guardrail to any agent, and a deterministic offline test suite.

## Challenges we ran into
The merchant check was the hard part. A naive keyword search flags every large
brand, because established companies all have "is X a scam" and phishing
awareness pages. We fixed it by using a neutral query and letting the model
judge the evidence, rather than matching risk words. We also hardened model JSON
parsing, since reasoning models sometimes wrap answers in thinking traces or
split values across lines, with a tolerant parser that recovers the verdict.

The guardrail is itself a target: the payee's web page and the agent's own
purpose text both end up in a model prompt. We made the defence structural (the
model can only tighten a verdict, never loosen a policy block) and explicit
(text with instructions aimed at the reviewer fails the check closed before any
model reads it, and what the model does read is delimited as data). A test
feeds a deliberately obedient fake model a planted page and shows it never gets
to see it.

## Accomplishments that we are proud of
Real sandbox activity, not mocks: allowed payments produce actual PayPal payout
batches, and the Agent Toolkit gate creates actual sandbox orders, while blocked
intents never reach PayPal at all. The reasoning check genuinely catches the
goal mismatch attack that a pure rule set lets through: in the prompt-injection
demo it holds a payment to an attacker that sat inside every spending limit,
while allowing the real invoice. And every decision, allowed or blocked, leaves a
signed, verifiable record in a chain you can re-verify.

We also measured it instead of asserting it. On a reproducible attack benchmark
in the repo (14 realistic attacks on a wallet-holding agent, 7 legitimate
invoices), rules alone catch 6 of 14; the full guardrail catches 13 of 14
(92%), and with the new-payee control on, 14 of 14, all with zero false
positives on real vendors. The first version of this benchmark missed two
attacks, a lookalike recipient domain behind a real merchant name and a 10x
amount on a real vendor; we published them as misses, then closed both with
the approved vendor directory, a deterministic rule that needs no model. The
one remaining soft spot, an unknown payee with a plausible invoice, flips
between runs because model checks are not deterministic, which is why the
strict mode exists and is reported separately.

## What we learned
Safety for agentic payments is a layering problem, not a single model call. The
cheap deterministic rules should run first and stop the obvious cases for free;
the model should be spent only on the judgment calls; and the live web belongs
as a soft signal in front of a human, never as a silent gate.

## What is next for SafePay
SafePay already runs as an MCP server, so any MCP agent (Claude Desktop, Cursor,
an agent framework) can install it as a spending gate with no code. Next: anchor
the audit chain head to a public ledger so the record of every agent payment
decision is independently verifiable, and ship the same gate for the PayPal MCP
server and the Agent Toolkit's other framework adapters. The hosted console
already takes early-access sign-ups (stored in the same Supabase project as the
audit chain, write-only), and we onboard the first teams by hand: their agents'
real payouts decide what SafePay builds next.

## Built with
python, paypal, paypal-payouts, paypal-webhooks, paypal-agent-toolkit,
model-context-protocol, nvidia-nemotron, nebius, tavily, flask, gunicorn,
ag-grid, supabase, postgres, vercel, render, hmac, sha-256

## Category / tracks targeted
Best Use of Agentic Commerce; Best Use of PayPal + AI; Most Impactful.
Sponsor: Best Use of AG Grid (the audit ledger is an interactive AG Grid table
with a live hash-chain verification badge); Best Use of Render (a live instance
of the web console runs on Render at https://safepay-ygzp.onrender.com, deployed
from the included render.yaml blueprint with gunicorn).

## Links
- Repo: https://github.com/NovaCorpAI/safepay
- Demo video: (YouTube link, under 3 min, to add)
- Live demo: https://safepay.agentposhq.com (no login, no setup)
- Render instance: https://safepay-ygzp.onrender.com (same console, Render blueprint)
- Package: https://pypi.org/project/safepay/ (`pip install safepay`, then `safepay --demo`)
- Run the console yourself: `python webapp/app.py` per the README
