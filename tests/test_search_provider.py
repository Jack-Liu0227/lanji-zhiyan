"""Tests for SearchProvider abstraction."""
from __future__ import annotations

import unittest

from search_provider import (
    FixtureSearchProvider,
    GrokSearchMcpProvider,
    HttpSearchProvider,
    get_search_provider,
)


class SearchProviderTest(unittest.TestCase):
    def test_fixture_search_provider(self):
        fixtures = [
            {"title": "Solid state battery interface", "abstract": "Li metal anode kinetics", "year": "2024"},
            {"title": "Perovskite solar cells", "abstract": "Efficiency and stability", "year": "2023"},
        ]
        provider = FixtureSearchProvider(fixtures)
        res = provider.search("solid state")
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["title"], "Solid state battery interface")

        res_all = provider.search("unknown keyword")
        self.assertEqual(len(res_all), 2)

    def test_get_search_provider_factory(self):
        p_fix = get_search_provider("fixture", fixtures=[{"title": "Test"}])
        self.assertIsInstance(p_fix, FixtureSearchProvider)

        p_http = get_search_provider("http")
        self.assertIsInstance(p_http, HttpSearchProvider)

    def test_groksearch_mcp_provider_without_mcp_raises(self):
        p_mcp = GrokSearchMcpProvider()
        with self.assertRaises(RuntimeError):
            p_mcp.search("battery")

    def test_groksearch_mcp_provider_with_invoker(self):
        def mock_invoker(tool_name, args):
            self.assertEqual(tool_name, "mcp_groksearch_web_search")
            return {
                "results": [
                    {"title": "MCP result", "url": "https://example.com/1", "snippet": "Test content", "published_date": "2024-01-01"}
                ]
            }

        provider = GrokSearchMcpProvider(mock_invoker)
        res = provider.search("query")
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["title"], "MCP result")
        self.assertEqual(res[0]["year"], "2024")


if __name__ == "__main__":
    unittest.main()
