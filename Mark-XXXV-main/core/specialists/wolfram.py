"""
core/specialists/wolfram.py
============================
Wolfram Alpha integration for factual computation.

Handles queries that LLMs hallucinate on: math, physics, chemistry,
unit conversions, geography, nutrition, astronomy, dates.

Cost: FREE tier = 2000 queries/month.
"""

from __future__ import annotations

import logging
from typing import Optional

import requests

from core.config import config

logger = logging.getLogger(__name__)

_RESULT_PODS = frozenset({
    "Result", "Solution", "Exact result", "Decimal approximation",
    "Definition", "Basic information", "Unit conversions",
    "Nutritional information", "Chemical data", "Geographic coordinates",
})

_QUERY_TRIGGERS = frozenset({
    "calculate", "solve", "integrate", "derivative", "equation",
    "convert", "how many", "distance from", "population of",
    "molecular weight", "what is the speed", "how far",
    "temperature in", "square root", "factorial", "prime",
    "plot", "graph of", "born", "capital of",
    "calories in", "nutrition", "atomic number", "boiling point",
    "melting point", "density of", "speed of light", "gravitational",
    "circumference", "area of", "volume of", "mass of",
})


class WolframBrain:
    """
    Handles math, science, conversions, and factual computation.

    Uses the Wolfram Alpha Full Results API v2 (JSON output).
    Static ``can_handle`` performs keyword matching to decide routing.
    """

    BASE_URL = "https://api.wolframalpha.com/v2/query"

    def __init__(self, app_id: Optional[str] = None):
        self._app_id = app_id or getattr(config, "WOLFRAM_APP_ID", "")

    # ── Public API ────────────────────────────────────────────────

    def query(self, question: str) -> Optional[str]:
        """
        Send a query to Wolfram Alpha and return plain-text results.

        Returns None if no useful answer is found or the API is unavailable.
        """
        if not self._app_id:
            logger.debug("[Wolfram] No APP_ID configured — skipping")
            return None

        params = {
            "input":    question,
            "appid":    self._app_id,
            "output":   "json",
            "format":   "plaintext",
            "podstate": "Result__Step-by-step solution",
        }

        try:
            resp = requests.get(
                self.BASE_URL,
                params=params,
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as exc:
            logger.warning(f"[Wolfram] API error: {exc}")
            return None

        pods = data.get("queryresult", {}).get("pods", [])
        if not pods:
            return None

        results: list[str] = []
        for pod in pods:
            title = pod.get("title", "")
            if title in _RESULT_PODS or title.startswith("Result"):
                for sub in pod.get("subpods", []):
                    text = (sub.get("plaintext") or "").strip()
                    if text:
                        results.append(f"{title}: {text}")

        return "\n".join(results) if results else None

    # ── Static routing check ──────────────────────────────────────

    @staticmethod
    def can_handle(query: str) -> bool:
        """Fast keyword check — does this query suit Wolfram?"""
        q = query.lower()
        return any(trigger in q for trigger in _QUERY_TRIGGERS)

    @property
    def is_configured(self) -> bool:
        return bool(self._app_id)
