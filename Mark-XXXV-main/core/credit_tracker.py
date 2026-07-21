"""
core/credit_tracker.py
=======================
Tracks per-source query costs for the JARVIS HUD.

Every query routed through the IntelligenceRouter logs its source
and estimated cost here. The UI can poll ``get_ui_data()`` to show
live savings on the dashboard.

Thread-safe: uses a lock since the router runs from multiple threads.
"""

from __future__ import annotations

import threading
from typing import Optional


# Estimated cost per 1K tokens for each source
_COST_TABLE: dict[str, float] = {
    "cache_hit":    0.00000,
    "wolfram":      0.00000,    # free tier
    "wikipedia":    0.00000,
    "local_llm":    0.00000,    # Ollama — truly free
    "yfinance":     0.00000,
    "rss":          0.00000,
    "wttr":         0.00000,
    "ddg_search":   0.00000,
    "groq":         0.00000,    # free tier: 14,400/day
    "gemini_lite":  0.00010,    # Flash Lite per 1K tokens
    "gemini_flash": 0.00025,
    "gemini_pro":   0.00150,
    "gemini_live":  0.00200,    # most expensive
}

# What it would cost if everything went through Gemini Live
_GEMINI_LIVE_COST = _COST_TABLE["gemini_live"]


class CreditTracker:
    """
    Singleton credit tracker for JARVIS intelligence routing.

    Tracks:
      - Per-source call counts
      - Session cost (actual)
      - Money saved vs. all-Gemini baseline
    """

    _instance: Optional["CreditTracker"] = None
    _init_lock = threading.Lock()

    def __new__(cls) -> "CreditTracker":
        with cls._init_lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    def __init__(self) -> None:
        if self._initialized:
            return
        self._lock = threading.Lock()
        self._calls: dict[str, int] = {k: 0 for k in _COST_TABLE}
        self._session_cost: float = 0.0
        self._total_saved: float = 0.0
        self._goal_costs: dict[str, float] = {}
        self._initialized = True

    # ── Logging ───────────────────────────────────────────────────

    def log(self, source: str, tokens: int = 1000) -> None:
        """Log a query to a specific source with estimated token count."""
        with self._lock:
            cost_per_k = _COST_TABLE.get(source, 0.0)
            actual_cost = cost_per_k * (tokens / 1000)
            gemini_cost = _GEMINI_LIVE_COST * (tokens / 1000)

            self._calls[source] = self._calls.get(source, 0) + 1
            self._session_cost += actual_cost
            self._total_saved += (gemini_cost - actual_cost)

    def log_for_goal(self, goal_id: str, source: str, tokens: int = 1000) -> None:
        """Log a query attributed to a specific goal."""
        self.log(source, tokens)
        with self._lock:
            cost_per_k = _COST_TABLE.get(source, 0.0)
            actual_cost = cost_per_k * (tokens / 1000)
            from memory.agent_memory import get_agent_memory
            mem = get_agent_memory()
            task = mem.get_task(goal_id)
            current_db_cost = task.cost_usd if task else 0.0
            new_cost = current_db_cost + actual_cost
            self._goal_costs[goal_id] = new_cost
            if task:
                mem.update_task_cost(goal_id, new_cost)

    def get_goal_cost(self, goal_id: str) -> float:
        """Return cumulative cost for a specific goal."""
        with self._lock:
            if goal_id not in self._goal_costs:
                from memory.agent_memory import get_agent_memory
                task = get_agent_memory().get_task(goal_id)
                if task:
                    self._goal_costs[goal_id] = task.cost_usd
            return self._goal_costs.get(goal_id, 0.0)

    def clear_goal(self, goal_id: str) -> None:
        """Remove goal cost tracking when goal completes."""
        with self._lock:
            self._goal_costs.pop(goal_id, None)

    # ── UI data ───────────────────────────────────────────────────

    def get_ui_data(self) -> dict:
        """Return a snapshot for the HUD dashboard."""
        with self._lock:
            local_sources = {
                "cache_hit", "wolfram", "wikipedia", "local_llm",
                "yfinance", "rss", "wttr", "ddg_search",
            }
            local_calls = sum(
                self._calls.get(k, 0) for k in local_sources
            )
            total_calls = sum(self._calls.values())

            top_source = "none"
            if total_calls > 0:
                top_source = max(self._calls, key=self._calls.get)

            return {
                "session_cost":       f"${self._session_cost:.4f}",
                "saved_this_session": f"${self._total_saved:.4f}",
                "local_pct":          f"{(local_calls / max(total_calls, 1)) * 100:.0f}%",
                "total_queries":      total_calls,
                "top_source":         top_source,
                "breakdown":          dict(self._calls),
            }

    def get_stats_text(self) -> str:
        """One-line summary for logging."""
        data = self.get_ui_data()
        return (
            f"Cost: {data['session_cost']} | "
            f"Saved: {data['saved_this_session']} | "
            f"Local: {data['local_pct']} | "
            f"Top: {data['top_source']}"
        )

    # ── Reset ─────────────────────────────────────────────────────

    def reset(self) -> None:
        """Reset session counters (e.g., on new session start)."""
        with self._lock:
            self._calls = {k: 0 for k in _COST_TABLE}
            self._session_cost = 0.0
            self._total_saved = 0.0
