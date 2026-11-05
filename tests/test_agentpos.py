"""Buying from an AgentPOS store: the amount comes from the store's quote."""

import pytest

from safepay import agentpos


class Resp:
    def __init__(self, code, body):
        self.status_code, self._b = code, body

    def json(self):
        return self._b


CAT = {"site": {"name": "AgentPOS Demo Store", "url": "https://demo.agentposhq.com"},
       "payment": {"protocol": "x402"},
       "items": [{"id": "guide-1", "title": "Field guide", "price": {"amount": "2", "asset": "USDC"}}]}


@pytest.fixture(autouse=True)
def fake_store(monkeypatch):
    import requests
    agentpos._CACHE.clear()
    calls = []
    monkeypatch.setattr(requests, "get", lambda url, **k: calls.append(url) or Resp(200, CAT))

    def post(url, json=None, **k):
        calls.append((url, json))
        return Resp(201, {"cartId": "cart_X", "quote": {"total": "4", "asset": "USDC",
                                                       "lines": [{"quantity": 2, "title": "Field guide"}]},
                          "policy": {"decision": "allowed"}})
    monkeypatch.setattr(requests, "post", post)
    return calls


def test_quote_builds_the_intent_from_the_store(fake_store):
    q = agentpos.quote([{"itemId": "guide-1", "quantity": 2}, {"itemId": "../evil", "quantity": 1}])
    assert q["total"] == 4 and q["intent"]["amount"] == 4
    assert q["intent"]["merchant_url"] == "https://demo.agentposhq.com"
    assert "cart_X" in q["intent"]["purpose"] and "2 x Field guide" in q["intent"]["purpose"]
    sent = [c for c in fake_store if isinstance(c, tuple)][0]
    assert sent[1] == {"items": [{"itemId": "guide-1", "quantity": 2}]}     # bad id dropped


def test_only_the_configured_store(monkeypatch, fake_store):
    monkeypatch.setenv("AGENTPOS_STORE", "http://169.254.169.254")
    with pytest.raises(agentpos.StoreError):
        agentpos.catalog()
    monkeypatch.delenv("AGENTPOS_STORE")
    agentpos.catalog()
    assert all(str(c if isinstance(c, str) else c[0]).startswith("https://demo.agentposhq.com") for c in fake_store)


def test_empty_cart_refused():
    with pytest.raises(agentpos.StoreError):
        agentpos.quote([{"itemId": "guide-1", "quantity": 0}])
