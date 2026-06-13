"""
core/intelligence_router.py
=============================
Master intelligence router for JARVIS.

Routes every text query through a priority chain where Gemini is the
LAST RESORT. 90-95% of queries should cost $0.00.

Priority Chain (optimized for GTX 960M / 2GB VRAM / 16GB RAM):
  1. Memory cache         -> instant, free
  2. Wolfram Alpha        -> math/science/facts
  3. Direct data APIs     -> weather, stocks, news
  4. Wikipedia            -> knowledge questions
  5. DuckDuckGo search    -> real-time web data
  6. Groq (free tier)     -> complex reasoning (14,400 req/day)
  7. Gemini Flash Lite    -> absolute last resort

Note: Ollama llama3.2:1b is used ONLY as a pre-router classifier,
NOT as a generation source. It helps decide which step to try first
but never produces the final answer.

Architecture (Graphify-informed):
  - Does NOT replace LLMOrchestrator (121 edges, betweenness 0.217)
  - Calls INTO LLMOrchestrator as step 7
  - Does NOT touch JarvisLive voice pipeline
  - Single integration point: agent/executor.py
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Optional

from core.credit_tracker import CreditTracker

logger = logging.getLogger(__name__)


@dataclass
class RouterResult:
    """Result from the intelligence router."""
    answer: str
    source: str                  # e.g., "wolfram", "groq", "gemini_lite"
    cost: float = 0.0           # estimated cost in USD
    duration_ms: int = 0        # how long the query took
    cached: bool = False        # whether answer came from cache
    confidence: float = 1.0     # how confident we are in the answer


class IntelligenceRouter:
    """
    Priority-chain query router.

    Tries each source in order. First successful non-empty response wins.
    Gemini is called ONLY when all free sources fail.
    """

    def __init__(self):
        # Lazy-init specialists on first use to avoid import-time side effects
        self._wolfram = None
        self._web = None
        self._local_llm = None
        self._tracker = CreditTracker()
        self._initialized = False

    def _ensure_init(self) -> None:
        """Lazy-init all specialists."""
        if self._initialized:
            return
        try:
            from core.specialists.wolfram import WolframBrain
            self._wolfram = WolframBrain()
        except Exception as exc:
            logger.warning(f"[Router] Wolfram init failed: {exc}")

        try:
            from core.specialists.web_intelligence import WebIntelligence
            self._web = WebIntelligence()
        except Exception as exc:
            logger.warning(f"[Router] WebIntelligence init failed: {exc}")

        try:
            from core.specialists.local_llm import LocalLLM
            self._local_llm = LocalLLM()
        except Exception as exc:
            logger.warning(f"[Router] LocalLLM init failed: {exc}")

        self._initialized = True

    # ── Main routing method ───────────────────────────────────────

    def route(self, query: str, context: Optional[dict] = None) -> Optional[RouterResult]:
        """
        Route a query through the priority chain.

        Returns RouterResult if a free/cheap source can handle it,
        or None if the query should fall through to the full
        ReAct agent loop (which uses LLMOrchestrator internally).

        Args:
            query:   The user's natural language query.
            context: Optional context dict (unused for now, future-proof).
        """
        self._ensure_init()

        if not query or len(query.strip()) < 3:
            return None

        query = query.strip()
        start = time.time()

        # ── STEP 1: Memory cache ──────────────────────────────────
        result = self._try_cache(query)
        if result:
            result.duration_ms = int((time.time() - start) * 1000)
            return result

        # ── STEP 2: Wolfram Alpha (math/science) ──────────────────
        result = self._try_wolfram(query)
        if result:
            result.duration_ms = int((time.time() - start) * 1000)
            self._cache_result(query, result.answer, result.source)
            return result

        # ── STEP 3: Direct data APIs ──────────────────────────────
        result = self._try_direct_apis(query)
        if result:
            result.duration_ms = int((time.time() - start) * 1000)
            return result

        # ── STEP 4: Wikipedia ─────────────────────────────────────
        result = self._try_wikipedia(query)
        if result:
            result.duration_ms = int((time.time() - start) * 1000)
            self._cache_result(query, result.answer, result.source)
            return result

        # ── STEP 5: DuckDuckGo search ─────────────────────────────
        # Only for explicitly search-oriented queries
        result = self._try_web_search(query)
        if result:
            result.duration_ms = int((time.time() - start) * 1000)
            return result

        # ── STEP 6: Groq (free tier) ─────────────────────────────
        result = self._try_groq(query)
        if result:
            result.duration_ms = int((time.time() - start) * 1000)
            return result

        # ── STEP 7: Fall through to Gemini ────────────────────────
        # Return None — let executor.py handle via LLMOrchestrator/ReAct
        logger.info(f"[Router] No free source handled query, falling through to Gemini")
        return None

    # ── Step implementations ──────────────────────────────────────

    def _try_cache(self, query: str) -> Optional[RouterResult]:
        """Step 1: Check vector store memory cache."""
        try:
            from memory.vector_store import semantic_search
            hits = semantic_search(query, k=1)
            if hits and hits[0].get("distance", 1.0) < 0.15:
                self._tracker.log("cache_hit")
                return RouterResult(
                    answer=hits[0]["document"],
                    source="cache_hit",
                    cost=0.0,
                    cached=True,
                    confidence=1.0 - hits[0]["distance"],
                )
        except Exception as exc:
            logger.debug(f"[Router] Cache check failed: {exc}")
        return None

    def _try_wolfram(self, query: str) -> Optional[RouterResult]:
        """Step 2: Wolfram Alpha for math/science/facts."""
        if not self._wolfram or not self._wolfram.is_configured:
            return None

        from core.specialists.wolfram import WolframBrain
        if not WolframBrain.can_handle(query):
            return None

        answer = self._wolfram.query(query)
        if answer:
            self._tracker.log("wolfram")
            return RouterResult(
                answer=answer,
                source="wolfram",
                cost=0.0,
            )
        return None

    def _try_direct_apis(self, query: str) -> Optional[RouterResult]:
        """Step 3: Weather, stocks, news via free APIs."""
        if not self._web:
            return None

        from core.specialists.web_intelligence import WebIntelligence

        # Weather
        if WebIntelligence.is_weather_query(query):
            city = self._extract_entity(query, "city")
            answer = self._web.get_weather(city)
            if answer:
                self._tracker.log("wttr")
                return RouterResult(answer=answer, source="wttr", cost=0.0)

        # Stocks
        if WebIntelligence.is_stock_query(query):
            ticker = self._extract_ticker(query)
            if ticker:
                data = self._web.get_price(ticker)
                if data:
                    self._tracker.log("yfinance")
                    return RouterResult(
                        answer=self._web.format_price(data),
                        source="yfinance",
                        cost=0.0,
                    )

        # News
        if WebIntelligence.is_news_query(query):
            category = self._detect_news_category(query)
            items = self._web.get_news(category=category)
            if items:
                self._tracker.log("rss")
                return RouterResult(
                    answer=self._web.format_news(items),
                    source="rss",
                    cost=0.0,
                )

        return None

    def _try_wikipedia(self, query: str) -> Optional[RouterResult]:
        """Step 4: Wikipedia for knowledge questions."""
        if not self._web:
            return None

        from core.specialists.web_intelligence import WebIntelligence
        if not WebIntelligence.is_knowledge_query(query):
            return None

        topic = self._extract_entity(query, "topic")
        answer = self._web.wiki_summary(topic)
        if answer:
            self._tracker.log("wikipedia")
            return RouterResult(answer=answer, source="wikipedia", cost=0.0)
        return None

    def _try_web_search(self, query: str) -> Optional[RouterResult]:
        """Step 5: DuckDuckGo for real-time web data."""
        if not self._web:
            return None

        q_lower = query.lower()
        search_triggers = ["search", "find", "look up", "current", "latest"]
        if not any(t in q_lower for t in search_triggers):
            return None

        results = self._web.search(query, max_results=5)
        if results:
            formatted = "\n".join(
                f"- {r.get('title', '')}: {r.get('body', '')[:150]}"
                for r in results[:5]
            )
            self._tracker.log("ddg_search")
            return RouterResult(
                answer=formatted,
                source="ddg_search",
                cost=0.0,
                confidence=0.7,  # search results need user judgment
            )
        return None

    def _try_groq(self, query: str) -> Optional[RouterResult]:
        """Step 6: Groq free tier (llama-3.3-70b, 14,400 req/day)."""
        try:
            from core.config import config
            if not config.GROQ_API_KEY:
                return None

            import requests as req
            headers = {
                "Authorization": f"Bearer {config.GROQ_API_KEY}",
                "Content-Type": "application/json",
            }
            payload = {
                "model": "llama-3.3-70b-versatile",
                "messages": [
                    {"role": "system", "content": "You are JARVIS, an intelligent assistant. Be concise and accurate."},
                    {"role": "user", "content": query},
                ],
                "temperature": 0.7,
                "max_tokens": 1024,
            }

            resp = req.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers=headers,
                json=payload,
                timeout=15,
            )
            resp.raise_for_status()
            text = resp.json()["choices"][0]["message"]["content"]

            if text and text.strip():
                self._tracker.log("groq")
                return RouterResult(
                    answer=text.strip(),
                    source="groq",
                    cost=0.0,
                )
        except Exception as exc:
            logger.warning(f"[Router] Groq failed: {exc}")
        return None

    # ── Entity extraction helpers ─────────────────────────────────

    @staticmethod
    def _extract_entity(query: str, entity_type: str) -> str:
        """
        Simple rule-based entity extraction.
        Falls back to returning the query itself as the entity.
        """
        q = query.lower()

        if entity_type == "city":
            # "weather in X" / "temperature in X"
            for prep in ["in ", "for ", "at "]:
                idx = q.rfind(prep)
                if idx != -1:
                    candidate = query[idx + len(prep):].strip().rstrip("?.,!")
                    if candidate:
                        return candidate
            return ""  # let weather_action auto-detect

        if entity_type == "topic":
            # "who is X" / "what is X" / "tell me about X" / "explain X"
            patterns = [
                "who is ", "what is ", "what are ", "tell me about ",
                "explain ", "history of ", "biography of ", "define ",
                "meaning of ",
            ]
            for p in patterns:
                idx = q.find(p)
                if idx != -1:
                    return query[idx + len(p):].strip().rstrip("?.,!")
            return query  # fallback: use full query

        return query

    @staticmethod
    def _extract_ticker(query: str) -> Optional[str]:
        """Extract stock ticker from query (company names + crypto)."""
        q = query.upper()

        # Common crypto shortcuts
        crypto_map = {
            "BITCOIN": "BTC-USD", "BTC": "BTC-USD",
            "ETHEREUM": "ETH-USD", "ETH": "ETH-USD",
            "DOGECOIN": "DOGE-USD", "DOGE": "DOGE-USD",
            "SOLANA": "SOL-USD", "SOL": "SOL-USD",
            "XRP": "XRP-USD", "RIPPLE": "XRP-USD",
        }
        for name, ticker in crypto_map.items():
            if name in q:
                return ticker

        # Company name → ticker (top 30 most queried)
        company_map = {
            "TESLA": "TSLA", "APPLE": "AAPL", "GOOGLE": "GOOGL",
            "ALPHABET": "GOOGL", "AMAZON": "AMZN", "MICROSOFT": "MSFT",
            "META": "META", "FACEBOOK": "META", "NVIDIA": "NVDA",
            "NETFLIX": "NFLX", "DISNEY": "DIS", "SPOTIFY": "SPOT",
            "UBER": "UBER", "PAYPAL": "PYPL", "AMD": "AMD",
            "INTEL": "INTC", "IBM": "IBM", "ORACLE": "ORCL",
            "SALESFORCE": "CRM", "SHOPIFY": "SHOP", "TWITTER": "X",
            "SNAP": "SNAP", "PINTEREST": "PINS", "ZOOM": "ZM",
            "AIRBNB": "ABNB", "COINBASE": "COIN", "ROBLOX": "RBLX",
            "PALANTIR": "PLTR", "SAMSUNG": "005930.KS", "SONY": "SONY",
            "TOYOTA": "TM", "NIKE": "NKE",
        }
        for name, ticker in company_map.items():
            if name in q:
                return ticker

        # "price of AAPL" / "TSLA stock" — raw ticker extraction
        words = query.split()
        for word in words:
            clean = word.strip(".,?!$").upper()
            if clean.isalpha() and 1 <= len(clean) <= 5 and clean not in {
                "THE", "OF", "IN", "IS", "A", "AN", "FOR", "WHAT",
                "HOW", "PRICE", "STOCK", "SHARE", "CRYPTO", "GET",
            }:
                return clean

        return None

    @staticmethod
    def _detect_news_category(query: str) -> str:
        """Detect news category from query keywords."""
        q = query.lower()
        if any(w in q for w in ["tech", "technology", "startup", "ai", "software"]):
            return "tech"
        if any(w in q for w in ["science", "research", "study", "space"]):
            return "science"
        if any(w in q for w in ["pakistan", "local", "lahore", "karachi"]):
            return "local"
        return "world"

    # ── Cache integration ─────────────────────────────────────────

    @staticmethod
    def _cache_result(query: str, answer: str, source: str) -> None:
        """Store a successful result in vector memory for future cache hits."""
        try:
            from memory.vector_store import store_dialogue
            store_dialogue(
                user_text=query,
                jarvis_text=f"[{source}] {answer}",
                has_tool_call=False,
            )
        except Exception:
            pass  # caching failure is non-critical

    # ── Status ────────────────────────────────────────────────────

    def get_stats(self) -> dict:
        """Return credit tracker stats."""
        return self._tracker.get_ui_data()

    def get_stats_text(self) -> str:
        """One-liner for logging."""
        return self._tracker.get_stats_text()


# ── Module-level singleton ────────────────────────────────────────

_router: Optional[IntelligenceRouter] = None


def get_router() -> IntelligenceRouter:
    """Get or create the module-level router singleton."""
    global _router
    if _router is None:
        _router = IntelligenceRouter()
    return _router
