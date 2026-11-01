"""Thin wrapper over the OpenAI-compatible Nebius Token Factory chat endpoint."""

from __future__ import annotations

from openai import OpenAI

from .config import Config
from .tracing import traceable


class NebiusChat:
    def __init__(self, config: Config):
        self._model = config.nebius_model
        # A 120B model regenerating a whole file can take a while, and Token
        # Factory occasionally returns a transient 5xx or drops the connection.
        # Give each request a generous timeout and let the SDK retry transient
        # failures with backoff, so one slow or flaky call does not sink a run.
        self._client = OpenAI(
            api_key=config.nebius_api_key,
            base_url=config.nebius_base_url,
            timeout=180.0,
            max_retries=4,
        )
        # Cumulative token accounting across a run, so a remediation can report
        # what it actually cost (model calls are the dominant cost).
        self.usage = {"calls": 0, "prompt_tokens": 0,
                      "completion_tokens": 0, "total_tokens": 0}

    @property
    def model(self) -> str:
        return self._model

    @traceable(run_type="llm", name="nemotron.complete")
    def complete(self, system: str, user: str, temperature: float = 0.1) -> str:
        """Return the assistant message text for a single-shot prompt."""
        resp = self._client.chat.completions.create(
            model=self._model,
            temperature=temperature,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        u = getattr(resp, "usage", None)
        if u is not None:
            self.usage["calls"] += 1
            self.usage["prompt_tokens"] += getattr(u, "prompt_tokens", 0) or 0
            self.usage["completion_tokens"] += getattr(u, "completion_tokens", 0) or 0
            self.usage["total_tokens"] += getattr(u, "total_tokens", 0) or 0
        return (resp.choices[0].message.content or "").strip()
