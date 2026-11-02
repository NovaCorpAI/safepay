"""Optional LangSmith tracing, with a transparent no-op when it is not configured.

LiveFix is a loop of two external calls (a Tavily search and a Nemotron
completion) per iteration. Those are exactly the steps you want to see when a
repair goes wrong, so the loop, the model call, and the search call are each
wrapped with ``@traceable``.

Tracing turns on only when BOTH are true, so the library never needs LangSmith
to run and never phones home by accident:

  - ``LANGSMITH_TRACING`` (or the legacy ``LANGCHAIN_TRACING_V2``) is a truthy
    value, and
  - ``LANGSMITH_API_KEY`` (or ``LANGCHAIN_API_KEY``) is set.

When either is missing, or the ``langsmith`` package is not installed, every
decorator below is the identity function and adds nothing at runtime. Set
``LIVEFIX_DISABLE_TRACING=1`` to force it off regardless.
"""

from __future__ import annotations

import functools
import os

_TRUTHY = {"1", "true", "yes", "on"}


def tracing_enabled() -> bool:
    if os.environ.get("LIVEFIX_DISABLE_TRACING", "").lower() in _TRUTHY:
        return False
    flag = (
        os.environ.get("LANGSMITH_TRACING")
        or os.environ.get("LANGCHAIN_TRACING_V2")
        or ""
    ).lower()
    has_key = bool(
        os.environ.get("LANGSMITH_API_KEY") or os.environ.get("LANGCHAIN_API_KEY")
    )
    return flag in _TRUTHY and has_key


_LS_TRACEABLE: object | None = None  # langsmith.traceable, resolved on first use


def _ls_traceable():
    """Lazily import langsmith.traceable; cache the result (False if absent)."""
    global _LS_TRACEABLE
    if _LS_TRACEABLE is None:
        try:  # pragma: no cover - exercised only with LangSmith installed
            from langsmith import traceable as _t
            _LS_TRACEABLE = _t
        except Exception:
            _LS_TRACEABLE = False
    return _LS_TRACEABLE or None


def traceable(*dargs, **dkwargs):
    """``langsmith.traceable`` when tracing is on and importable; else a no-op.

    The decision is made per call, not at import, so LANGSMITH_* variables set
    after ``import safepay`` still take effect. The traced wrapper is built once
    and cached. Supports both bare ``@traceable`` and parameterized
    ``@traceable(run_type="llm", name="...")`` usage.
    """
    bare = len(dargs) == 1 and callable(dargs[0]) and not dkwargs

    def build(func):
        cache: dict[str, object] = {}

        @functools.wraps(func)
        def inner(*a, **k):
            if not tracing_enabled():
                return func(*a, **k)
            ls = _ls_traceable()
            if ls is None:
                return func(*a, **k)
            traced = cache.get("f")
            if traced is None:
                traced = ls(func) if bare else ls(*dargs, **dkwargs)(func)
                cache["f"] = traced
            return traced(*a, **k)

        return inner

    return build(dargs[0]) if bare else build
