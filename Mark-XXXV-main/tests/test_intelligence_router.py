"""
tests/test_intelligence_router.py
===================================
Smoke tests for the Intelligence Router system.

Tests are offline (no API calls) — validates:
  - CreditTracker mechanics (reset, log, stats)
  - Ticker extraction with company-name mapping
  - News HTML stripping
  - Router singleton initialization
  - HUD data shape
"""

import sys
import os
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class TestCreditTracker(unittest.TestCase):
    """CreditTracker — singleton reset, logging, and stats output."""

    def setUp(self):
        from core.credit_tracker import CreditTracker
        self.tracker = CreditTracker()
        self.tracker.reset()

    def test_logging_and_count(self):
        self.tracker.log("wolfram")
        self.tracker.log("wikipedia")
        self.tracker.log("wolfram")
        data = self.tracker.get_ui_data()
        self.assertEqual(data["total_queries"], 3)
        self.assertEqual(data["breakdown"]["wolfram"], 2)
        self.assertEqual(data["breakdown"]["wikipedia"], 1)

    def test_local_percentage(self):
        self.tracker.log("wttr")
        self.tracker.log("yfinance")
        self.tracker.log("groq")
        data = self.tracker.get_ui_data()
        # 2 local, 1 cloud → 67%
        self.assertEqual(data["local_pct"], "67%")

    def test_session_cost_zero_for_free_sources(self):
        for src in ["wolfram", "wikipedia", "wttr", "yfinance", "rss", "ddg_search"]:
            self.tracker.log(src)
        data = self.tracker.get_ui_data()
        self.assertEqual(data["session_cost"], "$0.0000")

    def test_stats_text_is_string(self):
        self.tracker.log("wttr")
        text = self.tracker.get_stats_text()
        self.assertIsInstance(text, str)
        self.assertIn("Cost:", text)


class TestTickerExtraction(unittest.TestCase):
    """_extract_ticker — company names, crypto, and raw tickers."""

    @classmethod
    def setUpClass(cls):
        from core.intelligence_router import IntelligenceRouter
        # Store the static method as a plain function reference
        cls.extract = staticmethod(IntelligenceRouter._extract_ticker).__func__

    def _extract(self, query):
        from core.intelligence_router import IntelligenceRouter
        return IntelligenceRouter._extract_ticker(query)

    def test_company_names(self):
        cases = {
            "price of Tesla": "TSLA",
            "Apple stock": "AAPL",
            "Nvidia share price": "NVDA",
            "How is Google doing": "GOOGL",
            "Microsoft price": "MSFT",
        }
        for query, expected in cases.items():
            with self.subTest(query=query):
                self.assertEqual(self._extract(query), expected)

    def test_crypto_shortcuts(self):
        self.assertEqual(self._extract("Bitcoin price"), "BTC-USD")
        self.assertEqual(self._extract("Ethereum today"), "ETH-USD")
        self.assertEqual(self._extract("Dogecoin to the moon"), "DOGE-USD")

    def test_raw_ticker(self):
        self.assertEqual(self._extract("price of AAPL"), "AAPL")
        self.assertEqual(self._extract("TSLA stock"), "TSLA")

    def test_graceful_handling(self):
        # Should not crash on ambiguous input
        result = self._extract("what is the price")
        # May return a stopword fallback or None — just ensure no crash
        self.assertIsNotNone(result or "")


class TestNewsHTMLStripping(unittest.TestCase):
    """format_news — ensures HTML tags are stripped from summaries."""

    def test_strips_html_tags(self):
        from core.specialists.web_intelligence import WebIntelligence
        items = [
            {"title": "Test Headline", "summary": '<p style="text-align: left;">Some content here</p>'},
            {"title": "Another One", "summary": '<div><a href="#">Link text</a> and more</div>'},
        ]
        result = WebIntelligence.format_news(items)
        self.assertNotIn("<p", result)
        self.assertNotIn("<div", result)
        self.assertNotIn("<a", result)
        self.assertIn("Some content here", result)
        self.assertIn("Link text", result)

    def test_empty_items(self):
        from core.specialists.web_intelligence import WebIntelligence
        result = WebIntelligence.format_news([])
        self.assertEqual(result, "No news available.")


class TestRouterSingleton(unittest.TestCase):
    """Router singleton and stats shape."""

    def test_singleton_identity(self):
        import core.intelligence_router as mod
        mod._router = None  # reset
        r1 = mod.get_router()
        r2 = mod.get_router()
        self.assertIs(r1, r2)

    def test_stats_shape(self):
        import core.intelligence_router as mod
        mod._router = None
        stats = mod.get_router().get_stats()
        required_keys = {"session_cost", "saved_this_session", "local_pct", "total_queries", "top_source", "breakdown"}
        self.assertTrue(required_keys.issubset(stats.keys()), f"Missing keys: {required_keys - stats.keys()}")


class TestHUDDataShape(unittest.TestCase):
    """Verify the intelligence data matches the shape JS expects."""

    def test_breakdown_is_dict(self):
        from core.intelligence_router import get_router
        data = get_router().get_stats()
        self.assertIn("breakdown", data)
        self.assertIsInstance(data["breakdown"], dict)

    def test_all_hud_fields_present(self):
        from core.intelligence_router import get_router
        data = get_router().get_stats()
        for key in ["session_cost", "saved_this_session", "local_pct", "total_queries", "top_source"]:
            self.assertIn(key, data, f"Missing HUD field: {key}")


if __name__ == "__main__":
    unittest.main()
