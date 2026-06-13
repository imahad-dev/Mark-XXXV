"""
core/specialists/web_intelligence.py
=====================================
Free web data APIs for JARVIS.

Design principle: WRAP existing JARVIS tools where they exist,
add new APIs only where there's no overlap.

Existing (wrapped):
  - DuckDuckGo search  → actions/web_search.py _ddg_search()
  - Weather             → actions/weather_report.py weather_action()

New:
  - Stock/crypto prices → yfinance (free, no key)
  - News RSS feeds      → feedparser (free, no key)
  - Page reader         → httpx + BeautifulSoup (free)
  - Wikipedia summary   → wikipedia-api (free, no key)
"""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)


class WebIntelligence:
    """
    Aggregates free web data sources.
    All methods are synchronous (matches JARVIS tool executor threading model).
    """

    # ── DuckDuckGo Search (wraps existing) ────────────────────────

    @staticmethod
    def search(query: str, max_results: int = 5) -> list[dict]:
        """
        Web search via DuckDuckGo — wraps existing _ddg_search.
        Returns list of {title, url, body}.
        """
        try:
            from actions.web_search import _ddg_search
            raw = _ddg_search(query, max_results=max_results)
            return raw if isinstance(raw, list) else []
        except Exception as exc:
            logger.warning(f"[WebIntel] DDG search failed: {exc}")
            return []

    # ── Weather (wraps existing) ──────────────────────────────────

    @staticmethod
    def get_weather(city: str = "") -> Optional[str]:
        """
        Weather report — delegates to existing weather_action.
        Handles auto-detection of city via IP if not provided.
        """
        try:
            from actions.weather_report import weather_action
            return weather_action(parameters={"city": city})
        except Exception as exc:
            logger.warning(f"[WebIntel] Weather failed: {exc}")
            return None

    # ── Stock/Crypto Prices (NEW — yfinance) ──────────────────────

    @staticmethod
    def get_price(ticker: str) -> Optional[dict]:
        """
        Live stock/crypto prices via yfinance (free, no API key).

        Args:
            ticker: Stock symbol (e.g., "AAPL", "BTC-USD", "TSLA")

        Returns:
            Dict with name, price, change, high, low — or None on failure.
        """
        try:
            import yfinance as yf
            stock = yf.Ticker(ticker.upper())
            info = stock.info

            name = info.get("longName") or info.get("shortName") or ticker
            price = info.get("currentPrice") or info.get("regularMarketPrice")

            if price is None:
                return None

            return {
                "name":   name,
                "price":  price,
                "change": info.get("regularMarketChangePercent"),
                "high":   info.get("dayHigh"),
                "low":    info.get("dayLow"),
                "volume": info.get("volume"),
            }
        except Exception as exc:
            logger.warning(f"[WebIntel] Price lookup failed for {ticker}: {exc}")
            return None

    @staticmethod
    def format_price(data: dict) -> str:
        """Format price dict into readable string."""
        if not data:
            return "Price data unavailable."
        parts = [f"{data['name']}: ${data['price']:.2f}"]
        if data.get("change") is not None:
            sign = "+" if data["change"] >= 0 else ""
            parts.append(f"({sign}{data['change']:.2f}%)")
        if data.get("high") and data.get("low"):
            parts.append(f"Range: ${data['low']:.2f} - ${data['high']:.2f}")
        return " | ".join(parts)

    # ── News RSS Feeds (NEW — feedparser) ─────────────────────────

    NEWS_FEEDS = {
        "world":   "http://feeds.bbci.co.uk/news/world/rss.xml",
        "tech":    "https://feeds.feedburner.com/TechCrunch",
        "science": "https://rss.nytimes.com/services/xml/rss/nyt/Science.xml",
        "local":   "https://geo.tv/rss",
    }

    @staticmethod
    def get_news(category: str = "world", limit: int = 5) -> list[dict]:
        """
        Fetch headlines from RSS feeds (zero API cost).

        Args:
            category: One of "world", "tech", "science", "local"
            limit: Max headlines to return.
        """
        try:
            import feedparser
            url = WebIntelligence.NEWS_FEEDS.get(
                category.lower(),
                WebIntelligence.NEWS_FEEDS["world"],
            )
            feed = feedparser.parse(url)
            results = []
            for entry in feed.entries[:limit]:
                results.append({
                    "title":   entry.get("title", ""),
                    "summary": entry.get("summary", "")[:200],
                    "link":    entry.get("link", ""),
                    "date":    entry.get("published", ""),
                })
            return results
        except Exception as exc:
            logger.warning(f"[WebIntel] News fetch failed: {exc}")
            return []

    @staticmethod
    def format_news(items: list[dict]) -> str:
        """Format news list into readable string (HTML stripped)."""
        import re
        if not items:
            return "No news available."
        lines = []
        for i, item in enumerate(items, 1):
            lines.append(f"{i}. {item['title']}")
            if item.get("summary"):
                clean = re.sub(r"<[^>]+>", "", item["summary"])[:120]
                if clean.strip():
                    lines.append(f"   {clean.strip()}")
        return "\n".join(lines)

    # ── Page Reader (NEW — httpx + BeautifulSoup) ─────────────────

    @staticmethod
    def read_page(url: str, max_chars: int = 6000) -> Optional[str]:
        """
        Scrape and extract text content from a URL.

        Returns cleaned text (scripts/styles/nav removed), or None on failure.
        """
        try:
            import httpx
            from bs4 import BeautifulSoup

            resp = httpx.get(
                url,
                headers={"User-Agent": "Mozilla/5.0 (JARVIS)"},
                timeout=10,
                follow_redirects=True,
            )
            resp.raise_for_status()

            soup = BeautifulSoup(resp.text, "html.parser")
            for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
                tag.decompose()

            text = soup.get_text(separator="\n", strip=True)
            return text[:max_chars] if text else None
        except Exception as exc:
            logger.warning(f"[WebIntel] Page read failed for {url}: {exc}")
            return None

    # ── Wikipedia Summary (NEW — wikipedia-api) ───────────────────

    @staticmethod
    def wiki_summary(topic: str, sentences: int = 5) -> Optional[str]:
        """
        Fetch Wikipedia summary for a topic (free, no key).

        Uses the maintained wikipedia-api package (not the buggy original).
        """
        try:
            import wikipediaapi
            wiki = wikipediaapi.Wikipedia(
                user_agent="JARVIS/1.0 (personal assistant)",
                language="en",
            )
            page = wiki.page(topic)
            if not page.exists():
                return None

            summary = page.summary
            # Truncate to ~N sentences
            parts = summary.split(". ")
            if len(parts) > sentences:
                summary = ". ".join(parts[:sentences]) + "."

            return summary
        except Exception as exc:
            logger.warning(f"[WebIntel] Wikipedia failed for {topic}: {exc}")
            return None

    # ── Routing helpers ───────────────────────────────────────────

    @staticmethod
    def is_weather_query(query: str) -> bool:
        q = query.lower()
        return any(w in q for w in ["weather", "temperature", "forecast", "humid"])

    @staticmethod
    def is_stock_query(query: str) -> bool:
        q = query.lower()
        return any(w in q for w in [
            "stock", "price of", "bitcoin", "crypto", "share price",
            "market cap", "ticker", "nasdaq", "s&p",
        ])

    @staticmethod
    def is_news_query(query: str) -> bool:
        q = query.lower()
        return any(w in q for w in [
            "news", "latest", "headlines", "today in", "what happened",
        ])

    @staticmethod
    def is_knowledge_query(query: str) -> bool:
        q = query.lower()
        return any(w in q for w in [
            "who is", "what is", "tell me about", "explain",
            "history of", "biography", "define", "meaning of",
        ])
