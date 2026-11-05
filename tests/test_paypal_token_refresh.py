"""A revoked or stale PayPal token is replaced once, not retried forever."""

from types import SimpleNamespace

import requests

from safepay.config import Config
from safepay.paypal_client import PayPalClient


def test_401_mints_a_new_token_and_retries_once(monkeypatch):
    c = PayPalClient(Config.from_env())
    tokens = iter(["old", "new"])
    monkeypatch.setattr(c, "_access_token", lambda: next(tokens))
    seen = []

    def fake(method, url, headers=None, **kw):
        seen.append(headers["Authorization"])
        return SimpleNamespace(status_code=401 if headers["Authorization"] == "Bearer old" else 200)
    monkeypatch.setattr(requests, "request", fake)
    assert c._send("get", "https://x.example/y").status_code == 200
    assert seen == ["Bearer old", "Bearer new"]


def test_second_401_is_returned(monkeypatch):
    c = PayPalClient(Config.from_env())
    monkeypatch.setattr(c, "_access_token", lambda: "t")
    calls = []
    monkeypatch.setattr(requests, "request", lambda *a, **k: calls.append(1) or SimpleNamespace(status_code=401))
    assert c._send("post", "https://x.example/y").status_code == 401 and len(calls) == 2
