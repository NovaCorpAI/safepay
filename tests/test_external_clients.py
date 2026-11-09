"""The three thin wrappers around paid services (Tavily, Nebius, LangSmith)
with their SDKs replaced by fakes: what is sent, what comes back, and that
tracing stays a no-op until it is explicitly configured."""

from __future__ import annotations

import sys
import types
from types import SimpleNamespace

from safepay import tracing
from safepay.config import Config
from safepay.nebius_client import NebiusChat
from safepay.search import SearchHit, WebSearch
from safepay.tracing import traceable, tracing_enabled


# --- Tavily ----------------------------------------------------------------------

def test_search_without_a_key_is_disabled_and_empty():
    w = WebSearch(None)
    assert w.enabled is False and w.search("anything") == []


def test_search_sends_an_advanced_query_and_trims_content(monkeypatch):
    import tavily

    class FakeTavily:
        def __init__(self, api_key):
            self.api_key, self.calls = api_key, []

        def search(self, **kw):
            self.calls.append(kw)
            return {"results": [{"title": "Acme", "url": "https://acme.example", "content": "c" * 2000},
                                {"url": "https://x.example", "content": None}]}
    monkeypatch.setattr(tavily, "TavilyClient", FakeTavily)
    w = WebSearch("tv-key")
    assert w.enabled is True and w._client.api_key == "tv-key"
    hits = w.search("acme reviews", max_results=3)
    assert w._client.calls == [{"query": "acme reviews", "max_results": 3,
                                "search_depth": "advanced", "include_answer": False}]
    assert hits[0] == SearchHit("Acme", "https://acme.example", "c" * 1500)
    assert hits[1] == SearchHit("", "https://x.example", "")


# --- Nebius -------------------------------------------------------------------------

def _cfg():
    return Config(nebius_api_key="nb-key", nebius_base_url="https://nb.example/v1/",
                  nebius_model="nvidia/test", tavily_api_key=None, paypal_client_id=None,
                  paypal_secret=None, paypal_base_url="https://api-m.sandbox.paypal.com")


class FakeOpenAI:
    last = None

    def __init__(self, api_key, base_url, timeout, max_retries):
        FakeOpenAI.last = self
        self.init = dict(api_key=api_key, base_url=base_url, timeout=timeout, max_retries=max_retries)
        self.requests = []
        self.usage = SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15)
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kw):
        self.requests.append(kw)
        return SimpleNamespace(usage=self.usage,
                               choices=[SimpleNamespace(message=SimpleNamespace(content="  {\"ok\": true}\n"))])


def test_nebius_chat_sends_system_and_user_and_counts_tokens(monkeypatch):
    import openai
    monkeypatch.setattr(openai, "OpenAI", FakeOpenAI)
    chat = NebiusChat(_cfg())
    assert chat.model == "nvidia/test"
    assert FakeOpenAI.last.init == {"api_key": "nb-key", "base_url": "https://nb.example/v1/",
                                    "timeout": 180.0, "max_retries": 4}
    assert chat.complete("be strict", "judge this", temperature=0.3) == '{"ok": true}'
    req = FakeOpenAI.last.requests[0]
    assert req["model"] == "nvidia/test" and req["temperature"] == 0.3
    assert req["messages"] == [{"role": "system", "content": "be strict"},
                               {"role": "user", "content": "judge this"}]
    chat.complete("s", "u")
    assert chat.usage == {"calls": 2, "prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30}
    FakeOpenAI.last.usage = None                      # a provider that reports no usage
    chat.complete("s", "u")
    assert chat.usage["calls"] == 2


# --- LangSmith tracing --------------------------------------------------------------

def test_tracing_is_off_unless_flag_and_key_are_both_set(monkeypatch):
    for k in ("LIVEFIX_DISABLE_TRACING", "LANGSMITH_TRACING", "LANGCHAIN_TRACING_V2",
              "LANGSMITH_API_KEY", "LANGCHAIN_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    assert tracing_enabled() is False
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    assert tracing_enabled() is False                 # flag without a key
    monkeypatch.setenv("LANGCHAIN_API_KEY", "ls-key")
    assert tracing_enabled() is True
    monkeypatch.setenv("LIVEFIX_DISABLE_TRACING", "1")
    assert tracing_enabled() is False
    monkeypatch.delenv("LIVEFIX_DISABLE_TRACING")
    monkeypatch.delenv("LANGSMITH_TRACING")
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "yes")  # the legacy flag works too
    assert tracing_enabled() is True


def test_traceable_is_identity_when_off(monkeypatch):
    monkeypatch.setattr(tracing, "tracing_enabled", lambda: False)

    @traceable
    def bare(x): return x + 1

    @traceable(run_type="llm", name="n")
    def param(x): return x * 2
    assert bare(1) == 2 and param(2) == 4 and bare.__name__ == "bare"


def test_traceable_wraps_once_with_langsmith_and_falls_back_without(monkeypatch):
    monkeypatch.setattr(tracing, "tracing_enabled", lambda: True)
    monkeypatch.setattr(tracing, "_LS_TRACEABLE", None)
    monkeypatch.setitem(sys.modules, "langsmith", None)   # not installed: identity
    @traceable(name="x")
    def f(x): return x
    assert f(3) == 3 and tracing._LS_TRACEABLE is False

    built = []

    def fake_traceable(*dargs, **dkwargs):
        if dargs and callable(dargs[0]) and not dkwargs:
            built.append(("bare",))
            return lambda *a, **k: ("traced", dargs[0](*a, **k))

        def deco(func):
            built.append(dkwargs)
            return lambda *a, **k: ("traced", dkwargs.get("name"), func(*a, **k))
        return deco
    monkeypatch.setitem(sys.modules, "langsmith", types.SimpleNamespace(traceable=fake_traceable))
    monkeypatch.setattr(tracing, "_LS_TRACEABLE", None)

    @traceable(run_type="llm", name="model")
    def g(x): return x * 10

    @traceable
    def h(x): return -x
    assert g(1) == ("traced", "model", 10) and g(2) == ("traced", "model", 20)
    assert h(1) == ("traced", -1)
    assert built == [{"run_type": "llm", "name": "model"}, ("bare",)]   # built once each
