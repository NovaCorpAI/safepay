# SafePay Check: Muse Connector Platform submission

Submission copy for <https://platform.muse.ai> ("Submit a connector", Meta login
required). Sections follow the platform docs (5.1 to 5.6). Fill the items marked
**[Anael]** before submitting; everything else is ready to paste.

## 5.1 Connector overview

**Name:** SafePay Check

**One line:** Checks a payment before Muse makes it, and verifies SafePay seals.

**What it does:** Muse already checks out with PayPal, Shop Pay and Stripe Link.
SafePay Check gives the person a second, independent judgment before money moves:
Muse sends the merchant, the total and the person's request, and SafePay returns
allow, hold or block with the reasons, judged against the person's own payment
rules, the payee's payment history, a live web check of the merchant and an
intent check (does this payment serve what was asked). Each check is recorded in
a tamper-evident log and comes back with a signed attestation the merchant or the
person can verify later. The connector moves no money, never sees card or bank
credentials, and stores no payee address or task text (only hashes).

**Intended users:** people and small businesses who let Muse shop or pay bills
and want rules and a record they control (a spending cap, categories they never
buy, merchants they trust, "ask me above X").

**Supported tasks:**
1. "Before you buy this, check it with SafePay." (safepay_check_payment)
2. "Is this SafePay seal / receipt real?" (safepay_verify)
3. "What are my payment rules?" (safepay_policy)
4. "What did SafePay check this week?" (safepay_recent_checks)

**Why it is better than the browser:** the browser can show a merchant's site;
it cannot apply the person's own payment rules and history, run an independent
fraud and intent check, or produce a signed, verifiable record of the decision.

**Restrictions:** requires a SafePay agent key (free, created at
<https://safepay.agentposhq.com/connect>). Payments SafePay releases itself run
on the PayPal sandbox during the launch period; the connector itself works for
any merchant and currency. No age restriction beyond Muse's own. Available in all
regions where Muse is available.

## 5.2 Business verification and terms

- Business: NovaCorp Labs, Santiago, Chile. **[Anael: legal entity name, RUT, address, registration document]**
- Website: <https://novacorplabs.com> · product: <https://safepay.agentposhq.com>
- Privacy notice: <https://safepay.agentposhq.com/privacy>
- Product terms: <https://safepay.agentposhq.com/terms>
- Security page: <https://safepay.agentposhq.com/security>
- Support contact and security contact: security@novacorplabs.com **[Anael: confirm the mailbox receives mail]**
- Maintenance developer: **[Anael: name and email]**
- Brand assets: the seal mark `webapp/static/seal.svg` (export a 512 px PNG).
- Muse developer terms: accepted at submission.

## 5.3 Data processing questionnaire

| Question | Answer |
| --- | --- |
| Data accessed | For each check: merchant name and URL, amount, currency, payee identifier (optional), the person's request (task), purpose, category, order reference (optional). The agent key. |
| Why | To judge the payment against the person's rules and history, check the merchant on the public web, and judge whether the payment serves the request. |
| Stored | Merchant name and domain, amount, currency, category, verdict, risk, rule names, time, and sha256 hashes of the payee, the task, the purpose and the reference. Payee address and task text are not stored. |
| Who receives it | Tavily (merchant name or domain, for the web check); Nebius AI Studio running NVIDIA Nemotron (payment details and task, for the intent check, not stored by SafePay); Supabase (storage); Vercel (hosting). |
| Protection | HTTPS only; keys stored as hashes; each key sees only its own account's rules and checks; the log is hash-chained and HMAC-signed. |
| Retention | The log is append-only; connector records contain no personal data, so nothing personal is retained. |
| Deletion | Revoking the key ends access immediately. Requests: security@novacorplabs.com. |
| Incident notice | To Meta and affected users within 48 hours of discovery. |

## 5.4 Integration credentials and tool documentation

- **Endpoint:** `https://safepay.agentposhq.com/mcp`
- **Transport:** MCP streamable HTTP, stateless, JSON responses (protocol 2025-06-18; 2025-03-26 and 2024-11-05 accepted).
- **Auth:** API key in `Authorization: Bearer spk_...`. One key per account. Test key: see 5.5. Production keys are the same kind.
- **Scopes:** none beyond the account the key names.
- **Rate limit:** per IP, shared with the web console; over the limit the server answers JSON-RPC error -32000 with HTTP 429.
- **Errors:** protocol errors as JSON-RPC errors (-32700 parse, -32600 invalid request, -32601 unknown method, -32602 unknown tool); a call the caller can fix (missing key, bad amount) returns a tool result with `isError: true` and an `error` message.

| Tool | Inputs | Output | Classification | Side effects |
| --- | --- | --- | --- | --- |
| safepay_check_payment | merchant_name, amount, task (required); currency, merchant_url, payee, purpose, category, invoice_ref | verdict (allow, hold, block), recommendation, reasons, checks, check_id, valid_until, chain_seq, attestation (signed), moved_money: false | Write, not sensitive: records a check in the account's log; moves no money; not idempotent (each call is a new check) | One log record |
| safepay_verify | seal or attestation | genuine, code, kind, what it states, expiry | Read | None |
| safepay_policy | none | rules in plain words, fingerprint | Read | None |
| safepay_recent_checks | limit (1 to 20) | latest checks: verdict, merchant, amount, time | Read | None |

Status handling: a check states `verdict` and `valid_until` (15 minutes); an
attestation can be re-verified at any time with `safepay_verify` or offline
against <https://safepay.agentposhq.com/.well-known/safepay-seal-keys.json>.

## 5.5 Test user credentials

- Create the reviewer key: open <https://safepay.agentposhq.com/connect>, press
  "Create a connector key for this session", copy the key. **[Anael: create it
  the day you submit and paste it in the form; it is shown once]**
- Representative test data: any merchant. Examples that show each verdict with
  the default rules: Notion, USD 48, "Pay our monthly software invoices" →
  allow; any merchant, USD 900 → block (over the per-payment limit); a casino
  payout for a software task → hold or block.
- No real charges are possible: the connector moves no money.
- Limits: the default rules allow up to USD 500 per payment and USD 1,000 a day
  per account.

## 5.6 Tool annotation

- safepay_check_payment: **Write** (records a check). Not a sensitive write: no
  money, no third-party account changes, no messages sent.
- safepay_verify, safepay_policy, safepay_recent_checks: **Read**.
- Tool descriptions are factual and do not instruct Muse to act on its own
  (guideline 3.2). Approvals: Muse's normal approval for writes applies.

## Before submitting (checklist)

- [ ] Legal entity and verification documents for NovaCorp Labs
- [ ] security@novacorplabs.com receives mail
- [ ] Privacy and terms reviewed by a lawyer (Chile, Ley 19.628 and 21.719)
- [ ] Reviewer key created the day of submission
- [ ] 512 px PNG of the seal mark
- [ ] Do not describe SafePay as "partnered with" or "endorsed by" Meta (connector terms)
