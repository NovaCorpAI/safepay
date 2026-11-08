# SafePay duplicate-submission benchmark

Run: 2026-11-08 16:08 UTC. Sandbox: api-m.sandbox.paypal.com. paypal-agent-toolkit 1.1.0. 20 simultaneous duplicate `create_order` submissions per round, 3 round(s) per set-up, plus one sequential retry pair per round. Same parameters everywhere (12.00 USD, one line item). Orders are left CREATED and never captured.

## Summary

| Set-up | Burst: orders created | Burst: POSTs reaching PayPal | Retry pair: orders created |
| --- | --- | --- | --- |
| Toolkit as shipped (no PayPal-Request-Id) | 60 of 60 submissions (20 per round) | 60 | 6 of 6 submissions |
| PayPal-Request-Id only (SafePay header patch) | 13 of 60 submissions (2 to 7 per round) | 60 | 3 of 6 submissions |
| SafePay gate (header + one call per id + replay) | 3 of 60 submissions (1 per round) | 3 | 3 of 6 submissions |

## Per round

| Set-up | Round | Burst orders | Burst POSTs | Errors | Reconnected | Slowest POST (ms) | Retry orders |
| --- | --- | --- | --- | --- | --- | --- | --- |
| toolkit | 1 | 20 | 20 | 0 | 2 | 12474 | 2 |
| toolkit | 2 | 20 | 20 | 0 | 4 | 12601 | 2 |
| toolkit | 3 | 20 | 20 | 0 | 4 | 12646 | 2 |
| header | 1 | 4 | 20 | 0 | 1 | 6949 | 1 |
| header | 2 | 2 | 20 | 0 | 3 | 6838 | 1 |
| header | 3 | 7 | 20 | 0 | 5 | 12687 | 1 |
| gate | 1 | 1 | 1 | 0 | 1 | 6567 | 1 |
| gate | 2 | 1 | 1 | 0 | 0 | 1547 | 1 |
| gate | 3 | 1 | 1 | 0 | 0 | 1811 | 1 |

Reading it: the shipped client turns every duplicate into an order. The header alone folds the sequential retry (PayPal returns the original order) but PayPal only de-duplicates against a request it has already finished, so simultaneous twins can still both create an order; how many depends on timing, which is why it swings between rounds. The gate lets one call per request id reach PayPal and hands the first result to the others, so the burst is one order and one POST. "Reconnected" counts submissions whose TCP connection failed to open and was retried (the request never reached PayPal); a reconnected duplicate leaves after the burst, so in the header set-up it is more likely to be folded, which makes that number conservative. Rerun with `python benchmark/retries/run_retry_benchmark.py --n 20 --rounds 3`.
