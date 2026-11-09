# SafePay, Devpost submission copy

Paste-ready text for paypalaihackathon.devpost.com. Links at the bottom get
filled once the video is up. No em dashes used, per house style.

---

## Project name
SafePay

## Tagline (one line)
The firewall any AI agent asks before it pays: it judges the payment, pays
through PayPal or lets the agent's own checkout proceed, and leaves proof
anyone can verify without us. In production inside a real SaaS.

## Elevator pitch (shorter, if a short field is asked)
Agents are starting to pay the bills, and the biggest ones now check out on
their own: Meta's Muse buys through PayPal. An agent that can pay can be prompt
injected by an invoice email, handed a real vendor's name on a lookalike
domain, or sent to a merchant registered last week. Spending caps do not catch
that. SafePay does, and proves it:

- In production: its rules check every supplier purchase inside Impulsa, our
  SaaS for micro-businesses in Chile, and what owners do with each alert is
  measured.
- Any agent can ask before it pays: SafePay Check is a remote MCP server
  (/mcp) that judges a payment before the agent's own checkout and returns a
  signed attestation, moving no money, within the rules of Meta's Muse
  Connector Platform (submitted for review on November 7, 2026). Listed in the
  official MCP Registry.
- Judgment, not only limits: caps and allowlists are where most agent spending
  firewalls stop; SafePay also checks the payee on the live web and asks
  whether the payment serves the task, which is what catches injected invoices
  and goal mismatches inside every limit.
- PayPal and stablecoins under one guardrail: PYUSD and USDC payments are
  judged with the same rules (plus network and wallet-address checks, since a
  stablecoin sent to the wrong network is lost), and a settlement on Stellar is
  checked on the public ledger against SafePay's signed precheck. Verified
  against a real AgentPOS payment on Stellar mainnet. SafePay validates; it
  never moves the stablecoins.
- Evidence anyone can verify without us: a hash-chained log, Ed25519 receipts
  verifiable offline, operator-signed policy, and AP2 Intent, Cart and Payment
  mandates on PayPal authorize and capture.
- Measured: 16 of 17 attacks caught (17 with the new-payee hold), zero false
  positives, case by case at /security.

---

## Inspiration
Agentic commerce is arriving fast: agents that can browse, decide, and pay on
their own. The upside is obvious, and so is the failure mode. The moment an
agent can move money, a bad prompt, a spoofed merchant, or a subtle goal drift
turns into a real payment. A static allow list is too blunt for open ended
tasks, and letting the model approve its own spending is exactly what goes
wrong. Spending caps are where most tools stop. We wanted the layer that also
looks outward (is this payee what it claims to be?) and inward
(does this payment serve the task the agent was given?), that an operator sets
up in plain words once, and that proves how well it works instead of asserting
it.

The fraud is not hypothetical. Business email compromise cost USD 3.05 billion
in 2025 in losses reported to the FBI (IC3 2025 Internet Crime Report), and 79%
of organizations were hit by attempted or actual payments fraud in 2024, with
business email compromise the No. 1 avenue and vendor imposter fraud cited by
45% (AFP 2025 Payments Fraud and Control Survey). An agent that pays invoices
inherits all of it, at machine speed and without a clerk's doubt, so SafePay's
rules are those attacks by name.

## What it does
SafePay is a payment safety guardrail for agents that pay vendors. The
operator writes the policy in plain words ("USD 300 per payment, 1,000 a day,
ask me before anything over 100, no gambling or crypto, hold any new payee")
and Nemotron drafts the exact rules, which the operator reviews and applies.
Each payment the agent wants to make then runs three checks, cheapest and
strictest first:

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
or rejects it, in the console, through the MCP tools, with one tap on a
Telegram card, or with a physical button: `safepay-kit` turns a Raspberry Pi
with three buttons and three LEDs into a box on the shop counter (amber light
when a payment is held, green approves, red rejects, black freezes the agent),
with parts, wiring and setup in docs/kit.md. That decision is appended to the
chain as its own link naming who took it ("via Telegram by <name>", "via kit
counter-1"). Every agent the guardrail has seen is listed with a
Freeze switch, the way a bank freezes a card: a frozen agent's next request is
refused before any check runs, the kill switch for an incident. And the
firewall watches what goes around it: a payout PayPal reports that SafePay
never released, or a debit found by reconciling the account against
Transaction Search, is chained as an "outside" link and the operator is
alerted on Telegram. Every decision carries the hash of the policy it was
judged under, and policy changes are chained too, so an auditor can prove
which rules governed any payment. When PayPal later reports what
happened to a released payout (succeeded, failed, returned), SafePay appends
that settlement to the same chain and releases the budget of a payout that
never left, via a signature-verified webhook or on demand. Every decision is
appended to a hash-chained audit log: each record carries the hash of the one
before it, so editing or dropping any earlier decision breaks every hash that
follows, and a one-line verify walks the whole chain. SafePay is also a
drop-in front for PayPal's own MCP server: an agent changes one command in its
MCP config (safepay-mcp-proxy instead of npx @paypal/mcp) and keeps all 28
PayPal tools, while the five that move money (create_order, pay_order,
create_refund, accept_dispute_claim, create_subscription) are judged first,
with the amount read back from PayPal itself, and reach PayPal only on ALLOW.
SafePay also ships its
own payables agent: paste an invoice or a vendor email and it reads the payee,
amount and invoice number (Nemotron reads, but every value must appear in the
text, so a planted or invented payee is dropped), then pays through the same
guardrail with no privileges; a resent invoice is blocked as a duplicate, a
"we changed our bank details" email or an invoice that talks to the reviewer
is held. SafePay also works on both sides of agentic commerce we build: its shopping
agent buys from a real agent-ready store made with AgentPOS (our open-source
point of sale for agents), paying the store's own quote through a PayPal
mandate, and the store verifies the payment's seal with SafePay before it
delivers; and its purchase-check rules run inside Impulsa, our SaaS in
production for micro-businesses in Chile, before a supplier document is saved.
SafePay does not need to hold the money to protect it: SafePay Check is a
remote MCP server (streamable HTTP at /mcp, agent-key auth) that any agent,
Meta's Muse, Claude, Cursor, calls before its own checkout; it returns allow,
hold or block, the reasons and an Ed25519 precheck attestation valid 15
minutes, moves no money and keeps only hashes of the payee and the task. It
is listed in the official MCP Registry (com.agentposhq.safepay/check) and was
submitted to Meta's Muse Connector Platform on November 7, 2026. The same
guardrail judges stablecoin payments (PYUSD, USDC): the network must be named,
the token issued on it, the network allowed by policy and the payee a valid
address for that network (a stablecoin sent to the wrong one is lost for
good); after the payment, safepay_verify_settlement reads the transaction on
the public Stellar ledger and checks it against the signed precheck (payee,
Circle's USDC, amount, timing), verified against a real AgentPOS payment on
Stellar mainnet. SafePay validates stablecoins; it never moves them.
The policy itself is signed in the operator's browser (WebCrypto, ECDSA
P-256, a non-extractable key), pinned to the session, and re-verified on
every payment, so a leaked operator token or an edit in the database cannot
quietly loosen the rules. For tasks whose final price is not known up front (a hotel,
compute, a batch of purchases) an agent opens a mandate: the ceiling is judged
like any payment and authorized with PayPal (Orders v2, intent AUTHORIZE), each
capture is checked on its own (the agent that opened it, not frozen, within what
is left) before PayPal is called, and closing voids the rest and gives it back
to the daily cap. The agent card lives in PayPal's Vault (v3), so mandates are
authorized with a payment token, and PayPal's capture and void webhooks are
re-read and chained as settlements. Every released payout
is sealed: the note PayPal shows the recipient carries a seal id ("Sealed by
SafePay SP-3F9A0C21BE. Verify: safepay.agentposhq.com/v/SP-3F9A0C21BE"), and
that public page shows the payee, with the address masked, the signed record of
why the payment was allowed: the checks it passed, the policy fingerprint, the
hash and chain proofs and what PayPal reported. Each seal also carries a
receipt anyone can verify without SafePay: Ed25519, public key at
/.well-known/safepay-seal-keys.json, the same envelope as AgentPOS receipts,
checked in the visitor's browser or offline. Mandates speak AP2: the operator
signs an AP2 IntentMandate in the browser (ES256, the policy key) that
SafePay verifies and enforces (merchants, expiry) before PayPal is called;
an AgentPOS purchase adds a CartMandate from the store's quote; each capture
is a PaymentMandate signed with the published key. And the policy learns
from the operator: repeated, unanimous approvals and rejections become a
signed-draft proposal (a vendor at its usual amount, a recipient or category
blocked), each citing its chain links, reviewed by Nemotron, applied only
when the operator signs it. The web console shows this ledger as an
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
a toolkit gate demo that creates real sandbox orders, a local MCP server that exposes
the guardrail to any agent, a remote one (SafePay Check, stateless JSON-RPC
over streamable HTTP, tested with the official MCP Python SDK) that any agent
calls before it pays, a read-only settlement check that reads Stellar's
Horizon API, and a deterministic offline test suite (383 tests, 91% line
coverage, every PayPal REST call checked against a scripted HTTP layer).

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
to see it. The same discipline applies to the plain-words policy editor: the
model's output is data that is validated field by field, the model never
applies anything (a person does), and text that instructs the reviewer is
refused before any model reads it.

A retry is an attack too. PayPal de-duplicates a request that carries a
PayPal-Request-Id header; the PayPal Agent Toolkit's Python client does not
send one (we read the 1.1.0 and 1.11.0 wheels: the request_id its Context
accepts is never used), so an agent that retries create_order or
capture_order after a timeout creates a second order or charge. Crashpay,
another entry in this hackathon, measured that against the sandbox. Our gate
now sends a request id derived from the payment intent on every money-moving
toolkit call and records it in the chain link, so a retry of the same intent
is the same request to PayPal; SafePay's own PayPal client always did. The
header alone does not cover simultaneous duplicates (PayPal de-duplicates
against a request it has already finished), so the gate also runs one call
per request id at a time and replays the first result to the twin instead of
calling PayPal twice. We measured it against the sandbox, 20 simultaneous
duplicates of one create_order over 3 rounds: 20 orders every round with the
client as shipped, 2 to 7 with the header alone, 1 through the gate
(benchmark/retries/ in the repo, runner and raw results). We reported the gap
on the toolkit's issue tracker and sent the fix upstream as
paypal/agent-toolkit#124 (per-call request id for the Python client, open).

## Accomplishments that we are proud of
Real sandbox activity, not mocks: allowed payments produce actual PayPal payout
batches, and the Agent Toolkit gate creates actual sandbox orders, while blocked
intents never reach PayPal at all. The reasoning check genuinely catches the
goal mismatch attack that a pure rule set lets through: in the prompt-injection
demo it holds a payment to an attacker that sat inside every spending limit,
while allowing the real invoice. And every decision, allowed or blocked, leaves a
signed, verifiable record in a chain you can re-verify.

It runs inside a product with real users. Impulsa, our SaaS for
micro-businesses in Chile, checks every supplier document with SafePay before
saving it, and measures what each owner does with an alert (corrected or
recorded anyway), so precision and money not double-recorded are counted,
not estimated; the aggregates are public at /api/impact, test accounts
excluded, zeros shown as zeros.

We also measured it instead of asserting it. On a reproducible attack benchmark
in the repo (17 realistic attacks on a wallet-holding agent, 8 legitimate
invoices), rules alone catch 9 of 17; the full guardrail catches 16 of 17
(94%), and with the new-payee control on, 17 of 17, all with zero false
positives on real vendors. A judge can rerun it live from the console: one
button judges the same cases through the deployment, verdict by verdict, each
one chained into the ledger, with the tally next to the committed run. Three of those attacks are the ones accounts
payable teams lose real money to and no model is needed for: a duplicate
invoice (blocked), a "we changed our bank details" payee change, and an
amount far above a contractor's own history (both held), read
deterministically from the payment ledger. The first version of this benchmark missed two
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

## Business model (monetization)
Open core, priced on decisions. The guardrail, the MCP server and the console
are MIT-licensed and free to self-host with your own keys; that is how
developers adopt it. The hosted service charges for decisions, one payment
an agent asked SafePay to judge, because a decision is what costs money: a
live-web lookup and two model calls when it is not cached, nothing when
policy blocks it or the same payment was judged in the last day.

- Open source: free forever, self-hosted.
- Sandbox: free, 1,000 decisions a month, PayPal sandbox only.
- Team: USD 149 a month, 10,000 decisions, then USD 0.015 each. Payouts
  and Agent Toolkit orders through the gate, unlimited agents and keys,
  Telegram approvals, outside-the-firewall alerts. Email support within two
  business days.
- Business: USD 799 a month, 60,000 decisions, then USD 0.010 each. Priority
  support (four business hours, one hour for a severe incident), a shared
  Slack channel and quarterly policy reviews.

These are launch prices for the first teams. The pricing page marks every
feature that is not built yet as "Soon" (a live PayPal account, Slack
approvals, operator roles and SSO, daily automated reconciliation, long
retention, an uptime SLA) and says those are not billed until they ship;
everything else works today.
- Platforms and payment providers: embedded and white-label, per-tenant
  policies, the chain anchored to a public ledger, priced on volume.
- Services: onboarding (policy and integration, two weeks, USD 2,500,
  credited against a year of Business) and an agent payment audit (our
  attack benchmark extended to the customer's agent and PayPal flows,
  USD 4,000).

Unit economics, measured on 37 decisions (the demo scenarios and the
benchmark payments) with token counts from Nebius: a payment that passes
policy costs USD 0.0056 to 0.0086 (one Tavily search at USD 0.005 to 0.008,
which is 90% of it, and two Nemotron calls at USD 0.0006 together, about
770 input and 200 output tokens each); a policy block or a cached repeat
costs nothing; the measured blend is USD 0.0032 to 0.0048 per decision.
Team at full use costs us USD 32 to 86, a gross margin of about 70% at the
measured mix and 42% in the worst case. Card fraud screening sells at about
USD 0.05 per screened transaction, so a decision at USD 0.015 is cheap.
We charge for the judgment, never a percentage of the money moved. These
are launch prices built on measured cost; the first teams, onboarded by
hand, will shape them. Pricing is on the site: /pricing.

## What is next for SafePay
SafePay Check is in the official MCP Registry and under review for Meta's Muse
connector directory, so the next step is the first outside agents asking
before they pay, and what their checks teach the policy. Then: anchor the
audit chain head to a public ledger so the record of every agent payment
decision is independently verifiable, PYUSD settlement checks on Ethereum and
Solana next to the Stellar one, and the same gate for the Agent Toolkit's other
framework adapters. The hosted console
already takes early-access sign-ups (stored in the same Supabase project as the
audit chain, write-only), and we onboard the first teams by hand: their agents'
real payouts decide what SafePay builds next.

## What is real and what is simulated

Real: every PayPal call (payouts, orders, authorize, capture, void, vault,
webhooks, reconciliation) goes to PayPal's sandbox API, pinned there in the
config, so no real money has ever moved; the web check is a live Tavily
search and the reasoning check is Nemotron on Nebius, cached and budgeted;
the hash chain, the Ed25519 seals and the operator's P-256 signatures are
real keys, verifiable offline; the state lives in Supabase; the attack
benchmark and the duplicate-submission benchmark are real runs committed
with their raw results, and the console reruns the attack cases live;
Impulsa is a real product in production with the live counters at
/api/impact (test accounts excluded, zeros shown as zeros, and the
businesses our own team owns tallied apart under their names, so our own
use is never presented as external traction); the AgentPOS
catalog and quotes come from a real store; paypal/agent-toolkit#124 is a
real pull request, open and not merged.

Simulated or ours by design: the demo scenarios, the sample invoices and
the 25 benchmark cases were written by us, with fictional vendors on
.example addresses and no customer data; the human in the loop is whoever
clicks Approve; operator actions are open on the public demo so a judge
needs no setup; stablecoins are validated, never moved; AP2 mandates are
produced and verified on our PayPal mandates, we are not a participant in
an AP2 network; the tamper buttons edit your own session's policy and a
copy of the chain; the counter kit's client is tested end to end against
the real console with a keyboard standing in for the buttons, and its GPIO
board follows gpiozero and the documented pins but has not been run on a
Raspberry Pi yet; there are no paying customers yet and the pricing is a
proposal, the measured figures are costs.

## Built with
python, paypal, paypal-payouts, paypal-orders, paypal-vault, paypal-webhooks,
paypal-agent-toolkit, ap2, model-context-protocol, nvidia-nemotron, nebius,
tavily, telegram-bot-api, flask, gunicorn, ag-grid, ag-studio, ag-charts,
supabase, postgres, vercel, render, webcrypto, ed25519, ecdsa-p256, hmac,
sha-256, pypi, mcp-registry, stellar, horizon, pyusd, usdc, x402

## Category / tracks targeted
Best Use of Agentic Commerce; Best Use of PayPal + AI; Most Impactful.
Sponsor: Best Use of AG Grid (the audit ledger is an AG Grid table where each
link expands into a full-width detail row with its checks and the hash that ties
it to the link before; verdict chips, search and clicks on three AG Charts drive
one external filter that the pinned totals row and the CSV export follow;
a pinned Act column approves or rejects held payments and freezes agents from the
row; /studio is an AG Studio report over the whole chain, five finished pages
(overview, checks, a day-by-day timeline with sparklines and a gauge, the attack
benchmark as run live by the console, and mandates as a funnel and a pivot) over
four derived tables, with an AG Studio assistant run by NVIDIA Nemotron
through a key-holding proxy; the /proof page is a second grid of PayPal ids re-checked live; AG Grid
Community for the grids, AG Studio on a trial licence); Best Use of Render (a live instance
of the web console runs on Render at https://safepay-ygzp.onrender.com, deployed
from the included render.yaml blueprint with gunicorn).

## Image gallery (upload in this order)
1. `docs/img/gallery-flow.png` (thumbnail): the Flow, real chain links replayed as pulses through the layers.
2. `docs/img/gallery-connect.png`: SafePay Check, the remote MCP connector: a key, a live check through /mcp, ALLOW with its checks and a signed attestation, no money moved.
3. `docs/img/gallery-security.png`: the attack benchmark case by case, including the one case full mode lets through (in red) and strict mode holds.
4. `docs/img/gallery-impact.png`: the fraud an agent with a wallet walks into (FBI IC3, AFP), next to the live chain figures.
5. `docs/img/gallery-ledger.png`: the AG Grid ledger, a blocked Impulsa purchase opened to its checks and hashes, the Act column, totals and the AG Charts.
6. `docs/img/gallery-studio.png`: AG Studio over the whole chain; the Nemotron 3 Ultra analyst answers "how much did SafePay stop" with the real totals.
6b. `docs/img/gallery-studio-timeline.png`: AG Studio, Timeline page: released vs stopped per day, holds and blocks per day, value tiles with day sparklines, a gauge of average risk.
6c. `docs/img/gallery-studio-benchmark.png`: AG Studio, Benchmark page: every benchmark case the console ran against this deployment, caught vs missed by category and by mode.
6d. `docs/img/gallery-studio-mandates.png`: AG Studio, Mandates page: a funnel of USD authorized, captured and voided, amount by agent and link type.
6e. `docs/img/gallery-console-benchmark.png`: the console's "Run the attack benchmark" button, full mode, judged live by the deployment, each verdict a benchmark link in the chain.
6f. `docs/img/gallery-retries.png`: the duplicate-submission benchmark against the real sandbox: 60/60 orders as shipped, 13/60 with the header alone, 3/60 through the gate.
7. `docs/img/gallery-mandate-card.png`: a mandate authorized with a Vault payment token, captured, the rest released.
8. `docs/img/gallery-mandate-overdraw.png`: a capture over what is left, blocked before PayPal is called.
9. `docs/img/gallery-store-merchant-check.png`: buying from an AgentPOS store, and the store verifying the seal before it delivers.
10. `docs/img/gallery-proof.png`: every PayPal object in the chain, re-read live from PayPal's API.
11. `docs/img/impulsa-safepay-alert.png`: SafePay inside Impulsa (production), a supplier document recorded twice.
12. `docs/img/impulsa-safepay-price.png`: SafePay inside Impulsa, a unit cost 2.5x its history held.
13. `docs/img/gallery-seal-receipt.png`: the seal's Ed25519 receipt, verified in the browser against the published key.
14. `docs/img/gallery-tour.png`: the 3-minute judge tour, running the scenarios on the real console.

## Testing instructions (paste in Devpost's "testing instructions" field)
Live demo: https://safepay.agentposhq.com . No login, no keys. Every PayPal
call goes to the real PayPal sandbox; the web check (Tavily) and the
reasoning (NVIDIA Nemotron on Nebius) run live. Each browser gets its own
session.

Fastest path: https://safepay.agentposhq.com/?tour=1 , a 3-minute guided tour
across the real pages (it can run the scenarios and the chain check for you).

Connect your own agent: https://safepay.agentposhq.com/connect creates a key
and runs a live check through the MCP endpoint; the same endpoint works from
Claude (`claude mcp add --transport http ...`), Cursor or any MCP client.
Listed in the official MCP Registry as com.agentposhq.safepay/check:
https://registry.modelcontextprotocol.io/v0.1/servers?search=com.agentposhq.safepay
Every attack case, verdict by verdict: https://safepay.agentposhq.com/security
Stablecoins through the same endpoint: `python scripts/usdc_settlement.py
check --key spk_... --payee <Stellar G... address> --amount 1` judges a USDC
payment (a 0x address on Stellar is blocked before anyone pays), and `verify
--tx <hash>` checks a settled Stellar transaction against that precheck. The
session policy must list USDC under Currencies (console, Draft the policy).

1. Console: type a policy in plain words, e.g. "Up to $500 per payment and
   $1,000 a day. Ask me before anything over $300. No gambling or crypto."
   Press Draft the policy, review the fields, then Apply to this session.
   The policy is signed in your browser with an operator key WebCrypto made
   for you (the private half never leaves the browser); the session is now
   pinned to it. "Tamper with the stored policy (demo)" edits the stored rules
   without re-signing: the next payment is blocked until you Apply again.
2. Press Run demo scenarios. Seven payments resolve: a Notion invoice paid; the
   same invoice to a lookalike domain blocked; USD 900 blocked by the cap; a
   casino payout held (open the merchant and reasoning checks to see the web
   sources and the model verdict); two cloud invoices held over your "ask me"
   line (approve one in Pending approvals); the last blocked by the daily cap.
   Then press Create the order through the gate: a real PayPal Agent Toolkit
   order, created only because SafePay allowed it.
   Then, in "SafePay's own payables agent", click the four sample invoices in
   order and press Read and pay: paid and sealed, duplicate blocked,
   bank-details change blocked, injected invoice held.
   Then, in "Agentic commerce: buy from an agent-ready store", press Quote,
   judge and pay: a real AgentPOS store quotes the cart and SafePay pays that
   exact quote through a PayPal mandate.
   Then, in "Mandates", press Run the mandate story: USD 300 authorized with
   PayPal, USD 180 captured and sealed, a USD 200 draw blocked (only 120 left),
   and the rest voided when the mandate closes.
   On any paid card, the "sealed SP-..." link opens the public verification
   page the payee reaches from the PayPal note; its signed receipt is
   verified in your browser against /.well-known/safepay-seal-keys.json.
   The mandate legend links "AP2 mandates": the IntentMandate your browser
   signed, and a signed PaymentMandate per capture.
   After approving or rejecting a few held payments, press "Learn from my
   approvals" under the policy: a draft citing the chain links, applied only
   when you sign it.
3. Proof: every PayPal sandbox id in the chain; press Ask PayPal on any row
   and PayPal's own API answers. Ledger: approve a held payment from the
   grid's Act column, then press Tamper (demo) and watch the chain badge turn red, then Verify
   chain. The Flow page replays the chain as pulses through the layers.

Without the browser: pip install safepay, then safepay --demo (no keys).
Repository, tests (CI on every push) and the threat model:
https://github.com/NovaCorpAI/safepay (SECURITY.md).

## Links
- Looking for pilot teams and partners: security@novacorplabs.com (pilots are free and onboarded by hand)
- Repo: https://github.com/NovaCorpAI/safepay
- Demo video: (YouTube link, under 3 min, to add)
- Live demo: https://safepay.agentposhq.com (no login, no setup)
- Render instance: https://safepay-ygzp.onrender.com (same console, Render blueprint)
- Package: https://pypi.org/project/safepay/ (`pip install safepay`, then `safepay --demo`)
- Run the console yourself: `python webapp/app.py` per the README
