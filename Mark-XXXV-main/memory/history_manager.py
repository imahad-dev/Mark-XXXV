"""
history_manager.py — Auto-Compacting Conversation History
==========================================================
Tracks all conversation turns locally and auto-compacts when
token count exceeds a configurable threshold. On reconnect,
the compacted summary is injected into the system instruction
so Gemini Live sessions retain continuity across drops.

Inspired by fury-sdk HistoryManager pattern — adapted for
Gemini Live bidirectional audio sessions where the server
maintains its own context but loses it on session reconnect.

Design:
  - Each turn is stored as {role, content, timestamp}
  - Estimated token count = len(content) / 4 (conservative)
  - When total tokens > COMPACT_THRESHOLD, oldest 80% of turns
    are summarized via Gemini into a single "context" message
  - Raw transcript always persisted as JSONL (never deleted)
  - Thread-safe via Lock for concurrent turn additions
"""

import json
import logging
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from threading import Lock
from typing import Optional

logger = logging.getLogger(__name__)

# ── Defaults ──────────────────────────────────────────────────────────────────
DEFAULT_COMPACT_THRESHOLD = 30_000   # estimated tokens before compaction
COMPACT_RATIO = 0.80                 # compact oldest 80% of turns
CHARS_PER_TOKEN = 4                  # conservative estimate


def _get_base_dir() -> Path:
    """Resolve project root — works for both dev and frozen builds."""
    import sys
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


class HistoryManager:
    """
    Auto-compacting conversation history for Gemini Live sessions.

    Usage:
        hm = HistoryManager()
        hm.add("user", "What's the weather?")
        hm.add("model", "Currently 28°C and sunny in Lahore.")

        # On session reconnect, inject context:
        context = hm.get_context_summary()
        # → append to system_instruction in _build_config()
    """

    def __init__(
        self,
        compact_threshold: int = DEFAULT_COMPACT_THRESHOLD,
        transcript_dir: Optional[Path] = None,
    ):
        self._threshold = compact_threshold
        self._turns: deque[dict] = deque()
        self._compacted_summary: str = ""
        self._total_estimated_tokens: int = 0
        self._lock = Lock()
        self._turn_count: int = 0

        # Transcript persistence
        base = _get_base_dir()
        self._transcript_dir = transcript_dir or (base / "memory" / "transcripts")
        self._transcript_dir.mkdir(parents=True, exist_ok=True)

        # Session-specific JSONL file
        session_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        self._transcript_path = self._transcript_dir / f"session_{session_id}.jsonl"

        # Load any persisted compacted summary from previous sessions
        self._summary_path = base / "memory" / "history_summary.json"
        self._load_persisted_summary()

        logger.info(
            f"[HistoryManager] Initialized — threshold={compact_threshold} tokens, "
            f"transcript={self._transcript_path.name}"
        )

    # ── Public API ────────────────────────────────────────────────────────────

    def add(self, role: str, content: str) -> None:
        """
        Add a conversation turn. Triggers compaction if threshold exceeded.

        Args:
            role: "user" or "model"
            content: The text content of the turn
        """
        if not content or not content.strip():
            return

        content = content.strip()
        estimated_tokens = len(content) // CHARS_PER_TOKEN

        turn = {
            "role": role,
            "content": content,
            "timestamp": datetime.now().isoformat(),
            "tokens_est": estimated_tokens,
        }

        with self._lock:
            self._turns.append(turn)
            self._total_estimated_tokens += estimated_tokens
            self._turn_count += 1

        # Persist to JSONL immediately (append-only, never lost)
        self._append_transcript(turn)

        # Check if compaction is needed
        if self._total_estimated_tokens > self._threshold:
            self._compact()

    def get_context_summary(self) -> str:
        """
        Return the conversation context to inject into system_instruction.
        Includes the compacted summary + recent uncompacted turns.
        """
        with self._lock:
            parts = []

            if self._compacted_summary:
                parts.append(
                    "[PREVIOUS CONVERSATION CONTEXT]\n"
                    f"{self._compacted_summary}\n"
                )

            # Include recent turns (the ones not yet compacted)
            if self._turns:
                recent_lines = []
                for turn in self._turns:
                    prefix = "User" if turn["role"] == "user" else "JARVIS"
                    recent_lines.append(f"{prefix}: {turn['content']}")

                if recent_lines:
                    parts.append(
                        "[RECENT CONVERSATION]\n" +
                        "\n".join(recent_lines[-20:])  # cap at 20 most recent
                    )

            if not parts:
                return ""

            return "\n\n".join(parts) + "\n"

    def get_stats(self) -> dict:
        """Return current history statistics for HUD display."""
        with self._lock:
            return {
                "total_turns": self._turn_count,
                "active_turns": len(self._turns),
                "estimated_tokens": self._total_estimated_tokens,
                "has_summary": bool(self._compacted_summary),
                "threshold": self._threshold,
                "compaction_pct": round(
                    self._total_estimated_tokens / self._threshold * 100
                ) if self._threshold else 0,
            }

    def clear(self) -> None:
        """Clear all history (for persona switch / hard reset)."""
        with self._lock:
            self._turns.clear()
            self._compacted_summary = ""
            self._total_estimated_tokens = 0
            self._turn_count = 0
        self._persist_summary("")
        logger.info("[HistoryManager] History cleared.")

    # ── Compaction ────────────────────────────────────────────────────────────

    def _compact(self) -> None:
        """
        Compact oldest COMPACT_RATIO of turns into a summary via Gemini.
        Falls back to a naive truncation if the LLM call fails.
        """
        with self._lock:
            total = len(self._turns)
            if total < 4:
                # Not enough turns to compact meaningfully
                return

            compact_count = max(2, int(total * COMPACT_RATIO))
            turns_to_compact = []
            for _ in range(compact_count):
                turns_to_compact.append(self._turns.popleft())

            # Recalculate token count for remaining turns
            self._total_estimated_tokens = sum(
                t["tokens_est"] for t in self._turns
            )

        # Build the text to summarize
        text_block = "\n".join(
            f"{'User' if t['role'] == 'user' else 'JARVIS'}: {t['content']}"
            for t in turns_to_compact
        )

        print(
            f"[HistoryManager] 📦 Compacting {compact_count}/{total} turns "
            f"(~{sum(t['tokens_est'] for t in turns_to_compact)} tokens)..."
        )

        summary = self._summarize_via_llm(text_block)

        if not summary:
            # Fallback: naive truncation — keep last 500 chars of each turn
            summary = self._naive_compact(turns_to_compact)

        with self._lock:
            # Merge with any existing compacted summary
            if self._compacted_summary:
                self._compacted_summary = (
                    f"{self._compacted_summary}\n\n"
                    f"[Later in conversation]\n{summary}"
                )
            else:
                self._compacted_summary = summary

            # Re-estimate the summary's token cost
            summary_tokens = len(self._compacted_summary) // CHARS_PER_TOKEN
            self._total_estimated_tokens += summary_tokens

        self._persist_summary(self._compacted_summary)
        print(f"[HistoryManager] ✅ Compacted → {len(summary)} chars summary")

    def _summarize_via_llm(self, text_block: str) -> str:
        """Use Gemini (via LLMOrchestrator) to summarize the conversation block."""
        try:
            from core.llm_orchestrator import LLMOrchestrator, TaskTier
            orchestrator = LLMOrchestrator()

            prompt = (
                "You are a conversation summarizer for an AI assistant named JARVIS.\n"
                "Summarize the following conversation block into a concise paragraph.\n"
                "PRESERVE:\n"
                "  - All factual information discussed\n"
                "  - Any decisions made or actions taken\n"
                "  - User preferences or requests mentioned\n"
                "  - Tool calls and their results\n"
                "  - Emotional tone and context\n"
                "SKIP:\n"
                "  - Filler words, greetings, and pleasantries\n"
                "  - Repeated information\n"
                "Keep the summary under 300 words.\n\n"
                f"CONVERSATION:\n{text_block}\n\n"
                "SUMMARY:"
            )

            result = orchestrator.generate_content_with_retry(
                TaskTier.ROUTING, prompt
            )
            summary = result.text.strip()

            if len(summary) < 20:
                return ""

            return summary

        except Exception as e:
            print(f"[HistoryManager] ⚠️ LLM summarization failed: {e}")
            return ""

    @staticmethod
    def _naive_compact(turns: list[dict]) -> str:
        """Fallback compaction: keep first + last N turns as plain text."""
        if not turns:
            return ""

        # Keep first 2 and last 2 turns
        keep = turns[:2] + turns[-2:] if len(turns) > 4 else turns
        lines = []
        for t in keep:
            prefix = "User" if t["role"] == "user" else "JARVIS"
            # Truncate individual turns to 200 chars
            content = t["content"][:200]
            lines.append(f"{prefix}: {content}")

        return (
            f"[Summarized {len(turns)} earlier turns — key points:]\n" +
            "\n".join(lines)
        )

    # ── Persistence ───────────────────────────────────────────────────────────

    def _append_transcript(self, turn: dict) -> None:
        """Append a single turn to the JSONL transcript file."""
        try:
            with open(self._transcript_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(turn, ensure_ascii=False) + "\n")
        except Exception as e:
            logger.debug(f"[HistoryManager] Transcript write failed: {e}")

    def _persist_summary(self, summary: str) -> None:
        """Save the compacted summary to disk for cross-session continuity."""
        try:
            data = {
                "summary": summary,
                "updated": datetime.now().isoformat(),
                "turn_count": self._turn_count,
            }
            self._summary_path.write_text(
                json.dumps(data, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception as e:
            logger.debug(f"[HistoryManager] Summary persist failed: {e}")

    def _load_persisted_summary(self) -> None:
        """Load a previously saved compacted summary if it exists."""
        try:
            if self._summary_path.exists():
                data = json.loads(
                    self._summary_path.read_text(encoding="utf-8")
                )
                saved_summary = data.get("summary", "")
                if saved_summary:
                    self._compacted_summary = saved_summary
                    self._total_estimated_tokens += (
                        len(saved_summary) // CHARS_PER_TOKEN
                    )
                    print(
                        f"[HistoryManager] 📂 Loaded persisted summary "
                        f"({len(saved_summary)} chars)"
                    )
        except Exception as e:
            logger.debug(f"[HistoryManager] Summary load failed: {e}")


# ── Module-level singleton ────────────────────────────────────────────────────
_instance: Optional[HistoryManager] = None
_instance_lock = Lock()


def get_history_manager() -> HistoryManager:
    """
    Get or create the singleton HistoryManager instance.
    Thread-safe — safe to call from any thread.
    """
    global _instance
    if _instance is None:
        with _instance_lock:
            if _instance is None:
                _instance = HistoryManager()
    return _instance
