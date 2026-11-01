"""Live web search via Tavily, with a graceful no-op when no key is present."""

from __future__ import annotations

from dataclasses import dataclass

from .tracing import traceable


@dataclass
class SearchHit:
    title: str
    url: str
    content: str


class WebSearch:
    """Wraps the Tavily client. If no key is configured it returns no hits, so
    the agent still runs (just without web grounding) during offline testing."""

    def __init__(self, api_key: str | None):
        self._client = None
        if api_key:
            from tavily import TavilyClient

            self._client = TavilyClient(api_key=api_key)

    @property
    def enabled(self) -> bool:
        return self._client is not None

    @traceable(run_type="retriever", name="tavily.search")
    def search(self, query: str, max_results: int = 5) -> list[SearchHit]:
        if self._client is None:
            return []
        res = self._client.search(
            query=query,
            max_results=max_results,
            search_depth="advanced",
            include_answer=False,
        )
        hits: list[SearchHit] = []
        for item in res.get("results", []):
            hits.append(
                SearchHit(
                    title=item.get("title", ""),
                    url=item.get("url", ""),
                    content=(item.get("content", "") or "")[:1500],
                )
            )
        return hits
