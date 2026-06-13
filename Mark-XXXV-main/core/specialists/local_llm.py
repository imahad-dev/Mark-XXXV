"""
core/specialists/local_llm.py
==============================
Ollama integration — LIMITED to classifier + embeddings ONLY.

Hardware constraint: GTX 960M with 2GB VRAM.
  - llama3.2:1b    → intent classification (1.2GB VRAM, 2-4s)
  - nomic-embed-text → local embeddings (270MB, instant)

NO general answering — Groq handles that (14,400 free req/day).
"""

from __future__ import annotations

import logging
from typing import Optional

import requests

from core.config import config

logger = logging.getLogger(__name__)


class LocalLLM:
    """
    Minimal Ollama wrapper — classifier and embeddings only.

    Uses raw HTTP to localhost:11434 to avoid the ``ollama`` Python
    package dependency (one fewer dep to maintain).
    """

    # Only models that fit in 2GB VRAM
    MODELS = {
        "classifier": "llama3.2:1b",       # 1.2GB VRAM — fast routing
        "embedding":  "nomic-embed-text",   # 270MB — local embeddings
    }

    def __init__(self, base_url: Optional[str] = None):
        self._base_url = (
            base_url
            or getattr(config, "OLLAMA_BASE_URL", None)
            or "http://localhost:11434"
        )

    # ── Classification (the ONLY generation task) ─────────────────

    def classify(self, text: str, categories: list[str]) -> Optional[str]:
        """
        Ultra-fast intent classification using llama3.2:1b.

        Returns the best-matching category name, or None on failure.
        This is the ONLY generation call — all real answering goes to Groq.
        """
        prompt = (
            f"Classify this into exactly one category.\n"
            f"Categories: {', '.join(categories)}\n"
            f"Text: {text}\n"
            f"Reply with ONLY the category name, nothing else."
        )
        result = self._generate(prompt, model="classifier")
        if result:
            # Clean: strip whitespace, lowercase, pick first word if multi-word
            cleaned = result.strip().lower()
            # Match against known categories (fuzzy: first match wins)
            for cat in categories:
                if cat.lower() in cleaned:
                    return cat
            # Fallback: return raw if it's short enough to be a category
            if len(cleaned) < 30:
                return cleaned
        return None

    # ── Embeddings (for ChromaDB) ─────────────────────────────────

    def embed(self, text: str) -> Optional[list[float]]:
        """Generate embeddings locally using nomic-embed-text."""
        try:
            resp = requests.post(
                f"{self._base_url}/api/embeddings",
                json={"model": self.MODELS["embedding"], "prompt": text},
                timeout=15,
            )
            resp.raise_for_status()
            return resp.json().get("embedding")
        except Exception as exc:
            logger.debug(f"[LocalLLM] Embed failed: {exc}")
            return None

    # ── Health check ──────────────────────────────────────────────

    def is_available(self) -> bool:
        """Check if Ollama is running and responsive."""
        try:
            resp = requests.get(
                f"{self._base_url}/api/tags",
                timeout=2,
            )
            return resp.status_code == 200
        except Exception:
            return False

    def list_models(self) -> list[str]:
        """Return list of locally available model names."""
        try:
            resp = requests.get(
                f"{self._base_url}/api/tags",
                timeout=3,
            )
            resp.raise_for_status()
            models = resp.json().get("models", [])
            return [m.get("name", "") for m in models]
        except Exception:
            return []

    # ── Internal ──────────────────────────────────────────────────

    def _generate(self, prompt: str, model: str = "classifier") -> Optional[str]:
        """Low-level generation call. Used ONLY by classify()."""
        model_name = self.MODELS.get(model, model)
        try:
            resp = requests.post(
                f"{self._base_url}/api/generate",
                json={
                    "model":   model_name,
                    "prompt":  prompt,
                    "stream":  False,
                    "options": {"temperature": 0.1, "num_predict": 20},
                },
                timeout=30,
            )
            resp.raise_for_status()
            return resp.json().get("response", "")
        except requests.ConnectionError:
            logger.debug("[LocalLLM] Ollama not running — silent skip")
            return None
        except Exception as exc:
            logger.debug(f"[LocalLLM] Generate failed: {exc}")
            return None
