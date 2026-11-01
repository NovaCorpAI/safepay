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


_LS_TRACEABLE = None
if tracing_enabled():
    try:  # pragma: no cover - exercised only with LangSmith configured
        from langsmith import traceable as _LS_TRACEABLE
    except Exception:
        _LS_TRACEABLE = None


def traceable(*dargs, **dkwargs):
    """``langsmith.traceable`` when tracing is on and importable; else a no-op.

    Supports both bare ``@traceable`` and parameterized
    ``@traceable(run_type="llm", name="...")`` usage, so call sites read the same
    whether or not LangSmith is present.
    """
    # Bare usage: @traceable with no parentheses.
    if len(dargs) == 1 and callable(dargs[0]) and not dkwargs:
        func = dargs[0]
        if _LS_TRACEABLE is None:
            return func
        return _LS_TRACEABLE(func)

    def decorator(func):
        if _LS_TRACEABLE is None:
            return func
        return _LS_TRACEABLE(*dargs, **dkwargs)(func)

    return decorator
