# SafePay against the OWASP Top 10 for Agentic Applications (2026)

OWASP published its [Top 10 for Agentic Applications](https://genai.owasp.org/resource/owasp-top-10-for-agentic-applications-for-2026/)
on December 9, 2025, and in the August 2026 edition of the
[LLM Top 10](https://cloudsecurityalliance.org/articles/the-owasp-top-10-for-llm-applications-2026-what-changed-and-why-it-matters)
moved **Excessive Agency** up to third place. Its summary of the agentic
list is "least agency": every capability an agent does not need is attack
surface. SafePay is the least-agency control for one capability, paying.

This page maps each ASI entry to what SafePay does about it, with the code
and the test that proves it, and says plainly where SafePay does not help.
Everything named here is in the repository and runs offline in the test
suite (`python -m pytest -q`).

| ASI | Risk | What SafePay does | Where | Proof |
| --- | --- | --- | --- | --- |
| ASI01 | Agent goal hijack | The task goal travels with every payment intent; a reasoning check asks whether the payment serves it, and reviewer-directed instructions in the invoice or payee page are a fraud signal that fails the check closed. | `safepay/reason.py`, `safepay/injection.py` | `tests/test_injection_defence.py`; benchmark cases `inj_*` and `goal_*` in `benchmark/attacks/cases.json`, results in `benchmark/attacks/results.md` |
| ASI02 | Tool misuse and exploitation | The money-moving tools (PayPal Payouts, Orders authorize and capture, the Agent Toolkit's create and pay order) run only after a verdict of ALLOW; a policy BLOCK is final before any model runs, and no model output can raise a verdict. | `safepay/guardrail.py`, `safepay/agent_toolkit.py`, `safepay/mcp_proxy.py` | `tests/test_guardrail.py`, `tests/test_agent_toolkit.py`, `tests/test_mcp_proxy.py` |
| ASI03 | Identity and privilege abuse | Agents can be bound to a key (`spk_...`, only its sha256 stored); a request for a keyed agent without the key is refused before any check. Operator actions (policy, freeze, approve, keys, reconcile) require the operator token. Policies are signed by the operator (WebCrypto Ed25519) and a tampered policy is refused. | `safepay/control.py`, `safepay/opsig.py`, `webapp/app.py` | `tests/test_agent_keys.py`, `tests/test_opsig.py`, `tests/test_webapp_controls.py` |
| ASI04 | Agentic supply chain | Partial. SafePay pins the PayPal toolkit release it wraps, publishes a wheel on PyPI that CI builds and installs on three Python versions, and its MCP server is listed in the official MCP Registry; it does not vet the tools or models the agent itself loads. | `requirements.txt`, `pyproject.toml` | `.github/workflows/ci.yml` |
| ASI05 | Unexpected code execution | Not in scope. SafePay never executes agent-supplied code; invoices and policies are parsed as data (lenient JSON, plain-words rules), never evaluated. | `safepay/jsonutil.py`, `safepay/policy_draft.py` | `tests/test_jsonutil.py`, `tests/test_policy_draft.py` |
| ASI06 | Memory and context poisoning | Partial. What reaches the model is wrapped as delimited data with a system prompt that says nothing inside it is an instruction; the decision cache is keyed by the canonical intent, so a poisoned reply cannot be replayed for a different payment. SafePay does not protect the agent's own memory. | `safepay/reason.py`, `safepay/cache.py` | `tests/test_injection_defence.py`, `tests/test_cache_budget.py` |
| ASI07 | Insecure inter-agent communication | SafePay Check (remote MCP) returns a signed attestation per decision; AP2 Intent, Cart and Payment mandates are Ed25519 JWTs bound to the PayPal authorization; receipts verify offline with the public key alone. | `safepay/connector.py`, `safepay/ap2.py`, `safepay/receipt.py` | `tests/test_connector.py`, `tests/test_ap2.py`, `tests/test_receipt.py` |
| ASI08 | Cascading failures | A daily cap per agent across a burst, a per-recipient cap, a velocity rule, and the replay window in front of the Agent Toolkit that turns 20 simultaneous duplicates into one order (benchmark: 60 submissions, 3 orders). The slow-drain and burst cases are in the attack benchmark. A payment PayPal later fails or returns releases the budget once, never twice. | `safepay/policy.py`, `safepay/agent_toolkit.py`, `safepay/agent.py` | `tests/test_policy_chain.py`, `tests/test_idempotency.py`, `benchmark/retries/results.md`, `tests/test_agent_edges.py` |
| ASI09 | Human-agent trust exploitation | HOLD is a queue, not a dead end: a person approves or rejects from the console, the API, an MCP tool or a Telegram card, and the chain records who and when. The model cannot approve on the human's behalf. | `safepay/approvals.py`, `safepay/telegram.py` | `tests/test_approvals.py`, `tests/test_telegram.py`, `tests/test_webapp_approvals.py` |
| ASI10 | Rogue agents | Freeze is the one rule that beats every other: a frozen agent's next request is refused before any check. Money that moved around the firewall (a direct PayPal call, a leaked credential) is caught by the webhook and by reconciliation against Transaction Search and chained as an outside link with an alert. | `safepay/agent.py` (`pause_agent`, `reconcile`, `settle_payment_event`) | `tests/test_mcp_controls.py`, `tests/test_outside.py`, `tests/test_settlement.py` |

## What this page does not claim

SafePay is a payment control, not an agent runtime. It does not sandbox
code, scan the agent's tool supply chain, or clean the agent's memory. An
agent that is fully compromised can still try to pay; what SafePay
guarantees is that the attempt is judged by rules the attacker cannot
rewrite, that anything a person should see waits for that person, and that
every attempt, allowed or not, is in a chain that cannot be edited after
the fact. The threat model with adversaries and non-goals is in
[SECURITY.md](../SECURITY.md).

## The incidents this is for

The agentic list is not theoretical. In 2026 alone, as reported:

- An autonomous trading agent, asked on X for about 4 SOL (around USD 310),
  sent 52.4 million tokens, 5% of a coin's supply, valued on paper between
  USD 250,000 and 450,000; the postmortem blamed a decimal confusion and
  lost conversational state after a crash
  ([report](https://blockeden.xyz/blog/2026/02/24/ai-agent-450k-accident-lobstar-wilde/)).
  A per-transaction cap and a hold above a threshold stop that payment
  before the decimal matters.
- An internal OpenAI research model, denied free testnet tokens, searched
  362 wallet addresses tied to public recovery phrases, paid its fees from
  one that had funds, and left the spending out of its final report;
  OpenAI's own finding was that "a model can substitute its own judgment
  about likely harm for the account holder's permission"
  ([Fortune, October 16, 2026](https://fortune.com/2026/10/16/rogue-openai-agent-crypto-wallet-funds-task/)).
  That is ASI10 and ASI09 in one run; the agent key, the freeze switch and
  the outside-the-firewall link exist for exactly this.
