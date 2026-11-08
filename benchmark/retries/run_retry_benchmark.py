"""Duplicate-submission benchmark for SafePay's toolkit gate, against the real
PayPal sandbox.

The question: when an agent (or its framework) submits the same `create_order`
more than once, how many orders does PayPal end up with? Three set-ups are
measured side by side, with the same parameters and the same sandbox:

- toolkit: the PayPal Agent Toolkit's Python client as shipped (1.1.0), which
  sends no `PayPal-Request-Id`. Every duplicate is a new order.
- header:  the same client with SafePay's header patch only (`make_idempotent`):
  every duplicate carries the same `PayPal-Request-Id`, nothing else changes.
  PayPal de-duplicates against a request it has already finished, so a
  sequential retry is folded, but simultaneous twins can still both land.
- gate:    the full `GuardedToolkit`: header, one call per request id at a time,
  and the first result replayed to every twin.

Two shapes per set-up:

- burst: N threads release on a barrier and submit the same order at once
  (what a framework does when a tool call is fanned out, or a retry policy
  fires while the first call is still in flight).
- retry: one submission, then the same submission again after the first has
  returned (the timeout-then-retry case).

The metric is the number of distinct order ids PayPal returned, and the number
of POSTs that reached it. Nothing is captured: the orders stay CREATED in the
sandbox and expire on their own. The OAuth token is fetched once per run and
shared, so the duplicates are really simultaneous (the shipped client fetches a
fresh token before every POST, which would spread them out).

    python benchmark/retries/run_retry_benchmark.py --n 20 --rounds 3

Needs PAYPAL_CLIENT_ID / PAYPAL_SECRET (sandbox) and paypal-agent-toolkit.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, v = s.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


from safepay import agent_toolkit as gate_mod  # noqa: E402
from safepay.agent_toolkit import (  # noqa: E402
    GuardedToolkit, make_idempotent, order_params_from_intent, request_id_for, reset_replays,
)
from safepay.config import Config  # noqa: E402
from safepay.models import PaymentIntent, PolicyConfig  # noqa: E402

MODES = ("toolkit", "header", "gate")
LABELS = {
    "toolkit": "Toolkit as shipped (no PayPal-Request-Id)",
    "header": "PayPal-Request-Id only (SafePay header patch)",
    "gate": "SafePay gate (header + one call per id + replay)",
}
ORDERS_URI = "/v2/checkout/orders"


def benchmark_intent(key: str) -> PaymentIntent:
    """The one logical payment every duplicate repeats."""
    return PaymentIntent(
        agent_id="bench-retries",
        amount=12.0,
        currency="USD",
        recipient="billing@notion.so",
        merchant_name="Notion Labs",
        purpose="Notion Plus, one seat, November",
        task_goal="Pay the team's Notion subscription for November",
        category="software",
        idempotency_key=key,
    )


class Counter:
    """Counts the POSTs that reach PayPal through one toolkit client and the
    order ids that came back. Installed on `client.post`, after any header
    patch, so it sees exactly what goes out."""

    # A connection that never opened never reached PayPal, so it is retried
    # (the request is the same, so the count of what PayPal saw is unchanged)
    # and counted separately. A read timeout or an HTTP error is not retried:
    # the request may have landed.
    CONNECT_RETRIES = 3

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.posts: list[dict[str, Any]] = []
        self.connect_failures = 0

    def install(self, api: Any) -> None:
        client = api._paypal_client
        original = client.post

        def counted(uri: str, payload: Any):
            started = time.perf_counter()
            for attempt in range(self.CONNECT_RETRIES + 1):
                try:
                    result = original(uri, payload)
                    break
                except Exception as exc:
                    if _never_reached(exc) and attempt < self.CONNECT_RETRIES:
                        with self.lock:
                            self.connect_failures += 1
                        time.sleep(0.5)
                        continue
                    with self.lock:
                        self.posts.append({"uri": uri, "error": str(exc), "attempts": attempt + 1,
                                           "ms": round((time.perf_counter() - started) * 1000)})
                    raise
            with self.lock:
                self.posts.append({"uri": uri, "id": (result or {}).get("id"), "attempts": attempt + 1,
                                   "ms": round((time.perf_counter() - started) * 1000)})
            return result

        client.post = counted

    def reset(self) -> None:
        with self.lock:
            self.posts.clear()
            self.connect_failures = 0

    def summary(self) -> dict[str, Any]:
        with self.lock:
            orders = [p for p in self.posts if p["uri"] == ORDERS_URI]
            connect_failures = self.connect_failures
        ids = sorted({p["id"] for p in orders if p.get("id")})
        return {
            "posts": len(orders),
            "errors": sum(1 for p in orders if "error" in p),
            "connect_retries": connect_failures,
            "late": sum(1 for p in orders if p.get("attempts", 1) > 1 and "error" not in p),
            "orders": len(ids),
            "ids": ids,
            "ms_max": max((p["ms"] for p in orders), default=0),
        }


def _never_reached(exc: Exception) -> bool:
    """True for a failure before any byte was sent (TCP/TLS connect)."""
    try:
        import requests
    except ImportError:  # pragma: no cover
        return False
    return isinstance(exc, requests.exceptions.ConnectionError) and not isinstance(
        exc, requests.exceptions.ReadTimeout)


def _burst(n: int, call: Callable[[int], Any]) -> list[Any]:
    """Run `call(i)` on n threads released together; returns their results
    (an exception is returned in place of a result)."""
    barrier = threading.Barrier(n)
    results: list[Any] = [None] * n

    def worker(i: int) -> None:
        barrier.wait()
        try:
            results[i] = call(i)
        except Exception as exc:  # measured, not raised
            results[i] = exc

    threads = [threading.Thread(target=worker, args=(i,), daemon=True) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return results


def _caller(mode: str, api: Any, gate: GuardedToolkit | None, intent: PaymentIntent,
            params: dict[str, Any]) -> Callable[[int], Any]:
    """One duplicate submission, the way each set-up makes it."""
    if mode == "toolkit":
        return lambda i: api.run("create_order", params)
    if mode == "header":
        rid = request_id_for("create_order", params, intent)

        def with_header(i: int):
            token = gate_mod._REQUEST_ID.set(rid)
            try:
                return api.run("create_order", params)
            finally:
                gate_mod._REQUEST_ID.reset(token)

        return with_header
    if mode == "gate":
        assert gate is not None
        return lambda i: gate.create_order(intent, params)
    raise ValueError(mode)


def run_mode(mode: str, n: int, build_api: Callable[[], Any], key: str,
             policy: PolicyConfig | None = None) -> dict[str, Any]:
    """Burst and retry for one set-up. `build_api` returns a fresh toolkit
    `PayPalAPI` (or a stand-in with the same `_paypal_client.post` / `run`)."""
    reset_replays()
    intent = benchmark_intent(key)
    params = order_params_from_intent(intent)
    api = build_api()
    gate = None
    if mode in ("header", "gate"):
        api = make_idempotent(api)
    if mode == "gate":
        gate = GuardedToolkit(Config.from_env(), policy or PolicyConfig())
        gate._api = api
    counter = Counter()
    counter.install(api)
    call = _caller(mode, api, gate, intent, params)

    out: dict[str, Any] = {"label": LABELS[mode]}

    # Burst: n duplicates at once.
    started = time.perf_counter()
    results = _burst(n, call)
    burst = counter.summary()
    burst["submissions"] = n
    burst["failed_submissions"] = sum(1 for r in results if isinstance(r, Exception))
    burst["wall_ms"] = round((time.perf_counter() - started) * 1000)
    if mode == "gate":
        burst["replayed"] = sum(1 for r in results if isinstance(r, dict) and r.get("replayed"))
    out["burst"] = burst

    # Retry: the same submission again, after the first has returned. A new
    # key, so the burst's order is not what gets replayed.
    reset_replays()
    counter.reset()
    retry_intent = benchmark_intent(key + "-retry")
    retry_params = order_params_from_intent(retry_intent)
    retry_call = _caller(mode, api, gate, retry_intent, retry_params)
    retry_results = []
    for i in range(2):
        try:
            retry_results.append(retry_call(i))
        except Exception as exc:
            retry_results.append(exc)
    retry = counter.summary()
    retry["submissions"] = 2
    retry["failed_submissions"] = sum(1 for r in retry_results if isinstance(r, Exception))
    if mode == "gate":
        retry["replayed"] = sum(1 for r in retry_results if isinstance(r, dict) and r.get("replayed"))
    out["retry"] = retry
    return out


def run_benchmark(n: int, rounds: int, build_api: Callable[[], Any], modes=MODES,
                  key_prefix: str | None = None) -> dict[str, Any]:
    key_prefix = key_prefix or f"bench-{int(time.time())}"
    runs: dict[str, Any] = {"n": n, "rounds": rounds, "modes": {}}
    for mode in modes:
        per_round = []
        for r in range(rounds):
            per_round.append(run_mode(mode, n, build_api, f"{key_prefix}-{mode}-{r}"))
            print(f"  {mode:8s} round {r + 1}: burst {per_round[-1]['burst']['orders']} order(s) "
                  f"from {n} submissions, {per_round[-1]['burst']['posts']} POST(s); "
                  f"retry {per_round[-1]['retry']['orders']} order(s)", flush=True)
        runs["modes"][mode] = {
            "label": LABELS[mode],
            "rounds": per_round,
            "burst_orders_total": sum(x["burst"]["orders"] for x in per_round),
            "burst_orders_max": max(x["burst"]["orders"] for x in per_round),
            "burst_orders_min": min(x["burst"]["orders"] for x in per_round),
            "burst_posts_total": sum(x["burst"]["posts"] for x in per_round),
            "retry_orders_total": sum(x["retry"]["orders"] for x in per_round),
            "submissions_total": n * rounds,
        }
    return runs


def to_markdown(runs: dict[str, Any], when: str, toolkit_version: str, host: str) -> str:
    n, rounds = runs["n"], runs["rounds"]
    lines = [
        "# SafePay duplicate-submission benchmark",
        "",
        f"Run: {when}. Sandbox: {host}. paypal-agent-toolkit {toolkit_version}. "
        f"{n} simultaneous duplicate `create_order` submissions per round, {rounds} round(s) per set-up, "
        "plus one sequential retry pair per round. Same parameters everywhere (12.00 USD, one line item). "
        "Orders are left CREATED and never captured.",
        "",
        "## Summary",
        "",
        "| Set-up | Burst: orders created | Burst: POSTs reaching PayPal | Retry pair: orders created |",
        "| --- | --- | --- | --- |",
    ]
    for mode, m in runs["modes"].items():
        rng = (f"{m['burst_orders_min']} to {m['burst_orders_max']} per round"
               if m["burst_orders_min"] != m["burst_orders_max"] else f"{m['burst_orders_max']} per round")
        lines.append(
            f"| {m['label']} | {m['burst_orders_total']} of {m['submissions_total']} submissions ({rng}) "
            f"| {m['burst_posts_total']} | {m['retry_orders_total']} of {2 * rounds} submissions |"
        )
    lines += ["", "## Per round", "",
              "| Set-up | Round | Burst orders | Burst POSTs | Errors | Reconnected | Slowest POST (ms) | Retry orders |",
              "| --- | --- | --- | --- | --- | --- | --- | --- |"]
    for mode, m in runs["modes"].items():
        for i, r in enumerate(m["rounds"], 1):
            b, t = r["burst"], r["retry"]
            lines.append(f"| {mode} | {i} | {b['orders']} | {b['posts']} | {b['errors']} | {b.get('late', 0)} "
                         f"| {b['ms_max']} | {t['orders']} |")
    lines += [
        "",
        "Reading it: the shipped client turns every duplicate into an order. The header alone folds the "
        "sequential retry (PayPal returns the original order) but PayPal only de-duplicates against a "
        "request it has already finished, so simultaneous twins can still both create an order; how many "
        "depends on timing, which is why it swings between rounds. The gate lets one call per request id "
        "reach PayPal and hands the first result to the others, so the burst is one order and one POST. "
        "\"Reconnected\" counts submissions whose TCP connection failed to open and was retried (the "
        "request never reached PayPal); a reconnected duplicate leaves after the burst, so in the header "
        "set-up it is more likely to be folded, which makes that number conservative. Rerun with "
        "`python benchmark/retries/run_retry_benchmark.py --n 20 --rounds 3`.",
    ]
    return "\n".join(lines) + "\n"


def _sandbox_api_factory(config: Config) -> Callable[[], Any]:
    """A fresh toolkit client per set-up, sharing one OAuth token so the
    duplicates leave together."""
    import requests
    from paypal_agent_toolkit.shared.api import PayPalAPI
    from paypal_agent_toolkit.shared.configuration import Context

    # The shipped client posts with no timeout, so a TCP connect that never
    # answers waits for the OS (21 s on Windows). A short connect timeout
    # keeps a retried connection close to the burst; the read timeout is
    # left long so a slow answer is never mistaken for a lost request.
    if not getattr(requests.post, "_safepay_bench", False):
        _post = requests.post

        def post_with_timeout(url, **kwargs):
            kwargs["timeout"] = (5, 60)
            return _post(url, **kwargs)

        post_with_timeout._safepay_bench = True  # type: ignore[attr-defined]
        requests.post = post_with_timeout

    probe = PayPalAPI(client_id=config.paypal_client_id, secret=config.paypal_secret,
                      context=Context(sandbox=True))
    token = probe._paypal_client.get_access_token()

    def build() -> Any:
        api = PayPalAPI(client_id=config.paypal_client_id, secret=config.paypal_secret,
                        context=Context(sandbox=True))
        api._paypal_client.get_access_token = lambda: token
        return api

    return build


def main(argv: list[str] | None = None) -> int:
    _load_dotenv(ROOT / ".env")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--n", type=int, default=20, help="simultaneous duplicates per burst")
    ap.add_argument("--rounds", type=int, default=1)
    ap.add_argument("--modes", default=",".join(MODES))
    ap.add_argument("--out", default=str(Path(__file__).resolve().parent))
    args = ap.parse_args(argv)

    config = Config.from_env()
    if not config.paypal_live:
        print("PAYPAL_CLIENT_ID / PAYPAL_SECRET are not set; nothing to measure.", file=sys.stderr)
        return 2
    if "sandbox" not in config.paypal_base_url:
        print(f"refusing to run against {config.paypal_base_url}: sandbox only.", file=sys.stderr)
        return 2
    try:
        import importlib.metadata as md
        toolkit_version = md.version("paypal-agent-toolkit")
    except Exception:  # pragma: no cover
        toolkit_version = "unknown"

    modes = tuple(m.strip() for m in args.modes.split(",") if m.strip())
    when = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    host = config.paypal_base_url.replace("https://", "")
    print(f"{args.n} duplicates x {args.rounds} round(s) against {host}, toolkit {toolkit_version}")
    runs = run_benchmark(args.n, args.rounds, _sandbox_api_factory(config), modes)
    runs.update({"when": when, "toolkit_version": toolkit_version, "host": host})

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "results.json").write_text(json.dumps(runs, indent=2), encoding="utf-8")
    (out / "results.md").write_text(to_markdown(runs, when, toolkit_version, host), encoding="utf-8")
    print(f"\nwrote {out / 'results.md'} and results.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
