"""Search Provider abstraction supporting HTTP, MCP (GrokSearch), and offline fixtures."""
from __future__ import annotations

import json
from abc import ABC, abstractmethod
from typing import Any, Callable


class SearchProvider(ABC):
    @abstractmethod
    def search(self, query: str, rows: int = 10, sources: list[str] | None = None) -> list[dict[str, Any]]:
        pass


class HttpSearchProvider(SearchProvider):
    def __init__(self, search_func: Callable[..., list[dict[str, Any]]] | None = None):
        self._search_func = search_func

    def search(self, query: str, rows: int = 10, sources: list[str] | None = None) -> list[dict[str, Any]]:
        if self._search_func:
            return self._search_func(query, sources=sources, rows=rows)
        import workflow
        return workflow.search(query, sources=sources, rows=rows)


class FixtureSearchProvider(SearchProvider):
    def __init__(self, fixtures: list[dict[str, Any]] | None = None):
        self.fixtures = fixtures or []

    def set_fixtures(self, fixtures: list[dict[str, Any]]) -> None:
        self.fixtures = fixtures

    def search(self, query: str, rows: int = 10, sources: list[str] | None = None) -> list[dict[str, Any]]:
        q_lower = query.lower()
        matched = [
            f for f in self.fixtures
            if q_lower in f.get("title", "").lower() or q_lower in f.get("abstract", "").lower()
        ]
        return (matched or self.fixtures)[:rows]


class GrokSearchMcpProvider(SearchProvider):
    """Adapter for GrokSearch MCP server (web_search / web_fetch)."""
    def __init__(self, mcp_invoker: Callable[[str, dict[str, Any]], dict[str, Any]] | None = None):
        self.mcp_invoker = mcp_invoker

    def search(self, query: str, rows: int = 10, sources: list[str] | None = None) -> list[dict[str, Any]]:
        if not self.mcp_invoker:
            raise RuntimeError("GrokSearch MCP server is not configured or not available in the current environment")
        res = self.mcp_invoker("mcp_groksearch_web_search", {"query": query, "max_results": rows})
        results = res.get("results") or []
        out = []
        for r in results:
            out.append({
                "source": "groksearch",
                "title": r.get("title", ""),
                "url": r.get("url", ""),
                "doi": r.get("doi", ""),
                "abstract": r.get("content", r.get("snippet", "")),
                "authors": r.get("author", ""),
                "year": str(r.get("published_date", "")[:4]),
                "pdf_url": r.get("pdf_url", ""),
            })
        return out


def get_search_provider(kind: str = "http", **kwargs: Any) -> SearchProvider:
    if kind == "fixture":
        return FixtureSearchProvider(kwargs.get("fixtures"))
    if kind == "mcp":
        return GrokSearchMcpProvider(kwargs.get("mcp_invoker"))
    return HttpSearchProvider(kwargs.get("search_func"))
