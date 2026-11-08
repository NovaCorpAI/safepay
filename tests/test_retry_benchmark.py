"""The duplicate-submission benchmark harness, offline, against a stand-in
PayPal that behaves like the sandbox: a POST without a request id always
creates an order; a POST with one is folded onto the original only once that
original has finished (de-duplication against completed requests)."""

from __future__ import annotations

import json
import os
import threading
import time
from types import SimpleNamespace

for _k in ("NEBIUS_API_KEY", "TAVILY_API_KEY"):
    os.environ[_k] = ""

import pytest  # noqa: E402

from benchmark.retries import run_retry_benchmark as bench  # noqa: E402
from safepay.agent_toolkit import IDEMPOTENCY_HEADER  # noqa: E402

N = 6


class FakePayPal:
    """The sandbox's de-duplication, in memory."""

    def __init__(self, latency: float = 0.05) -> None:
        self.latency = latency
        self.lock = threading.Lock()
        self.done: dict[str, dict] = {}
        self.created = 0

    def post(self, headers: dict, payload: dict) -> dict:
        rid = headers.get(IDEMPOTENCY_HEADER)
        with self.lock:
            if rid and rid in self.done:
                return self.done[rid]
        time.sleep(self.latency)
        with self.lock:
            self.created += 1
            order = {"id": f"ORDER{self.created:04d}", "status": "CREATED"}
            if rid:
                self.done[rid] = order
        return order


class FakeClient:
    """The two entry points `make_idempotent` and the toolkit handler use."""
    base_url = "https://fake.sandbox"

    def __init__(self, paypal: FakePayPal) -> None:
        self._paypal = paypal

    def get_access_token(self) -> str:
        return "tok"

    def post(self, uri: str, payload: dict) -> dict:
        return self._paypal.post({"Authorization": "Bearer tok"}, payload)


class FakeAPI:
    def __init__(self, paypal: FakePayPal) -> None:
        self._paypal_client = FakeClient(paypal)

    def run(self, method: str, params: dict) -> str:
        assert method == "create_order"
        return json.dumps(self._paypal_client.post(bench.ORDERS_URI, params))


@pytest.fixture
def factory(monkeypatch):
    paypal = FakePayPal()
    import requests

    def fake_requests_post(url, headers=None, json=None, timeout=None):
        body = paypal.post(headers or {}, json or {})
        return SimpleNamespace(status_code=201, raise_for_status=lambda: None, json=lambda: body)

    # `make_idempotent` posts with `requests` directly; route that to the fake too.
    monkeypatch.setattr(requests, "post", fake_requests_post)
    return lambda: FakeAPI(paypal)


def test_shipped_client_makes_an_order_per_duplicate(factory):
    run = bench.run_mode("toolkit", N, factory, "k-toolkit")
    assert run["burst"]["orders"] == N and run["burst"]["posts"] == N
    assert run["retry"]["orders"] == 2


def test_header_alone_folds_the_retry_but_not_the_burst(factory):
    run = bench.run_mode("header", N, factory, "k-header")
    # Twins all in flight together: the fake (like the sandbox) only folds
    # against a finished request, so every one of them lands.
    assert run["burst"]["posts"] == N and run["burst"]["orders"] == N
    # A retry after the first returned gets the original order back.
    assert run["retry"]["posts"] == 2 and run["retry"]["orders"] == 1


def test_gate_lets_one_call_through_and_replays_the_rest(factory):
    run = bench.run_mode("gate", N, factory, "k-gate")
    assert run["burst"]["posts"] == 1 and run["burst"]["orders"] == 1
    assert run["burst"]["replayed"] == N - 1 and run["burst"]["failed_submissions"] == 0
    assert run["retry"]["posts"] == 1 and run["retry"]["orders"] == 1 and run["retry"]["replayed"] == 1


def test_report_totals_and_markdown(factory):
    runs = bench.run_benchmark(3, 2, factory, modes=("toolkit", "gate"), key_prefix="k")
    assert runs["modes"]["toolkit"]["burst_orders_total"] == 6
    assert runs["modes"]["gate"]["burst_orders_total"] == 2
    md = bench.to_markdown(runs, "now", "1.1.0", "fake.sandbox")
    assert "| SafePay gate" in md and "2 of 6 submissions" in md
