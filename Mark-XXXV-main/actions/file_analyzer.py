"""
file_analyzer.py – Multimodal file analysis via Gemini.

Accepts base64-encoded files from the webview UI, sends them to Gemini
for analysis/summarization, falls back to local text extraction if
the API is unavailable.
"""

import base64
import os
import sys
import mimetypes
from pathlib import Path


def get_base_dir():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


# Supported MIME prefixes for Gemini multimodal
_GEMINI_MIME_PREFIXES = ("image/", "application/pdf", "text/", "video/", "audio/")
_MAX_SIZE_BYTES = 25 * 1024 * 1024  # 25 MB


def _get_gemini_client():
    """Lazy-load a Gemini client using the project's API key pool."""
    try:
        from core.config import config as cfg
        from google import genai
        key = cfg.get_gemini_key()
        if not key:
            return None
        return genai.Client(api_key=key)
    except Exception as exc:
        print(f"[FileAnalyzer] Gemini client init failed: {exc}")
        return None


def _guess_mime(filename: str) -> str:
    """Return best-guess MIME type for a filename."""
    mt, _ = mimetypes.guess_type(filename)
    if mt:
        return mt
    ext = Path(filename).suffix.lower()
    overrides = {
        ".py": "text/x-python",
        ".js": "text/javascript",
        ".ts": "text/typescript",
        ".jsx": "text/javascript",
        ".tsx": "text/typescript",
        ".rs": "text/x-rust",
        ".go": "text/x-go",
        ".java": "text/x-java",
        ".cpp": "text/x-c++",
        ".c": "text/x-c",
        ".h": "text/x-c",
        ".md": "text/markdown",
        ".json": "application/json",
        ".yaml": "text/yaml",
        ".yml": "text/yaml",
        ".csv": "text/csv",
        ".log": "text/plain",
    }
    return overrides.get(ext, "application/octet-stream")


def _local_fallback(filename: str, raw_bytes: bytes) -> str:
    """Best-effort local text extraction when Gemini is unavailable."""
    mime = _guess_mime(filename)

    if mime.startswith("text/") or mime in ("application/json", "text/yaml"):
        try:
            text = raw_bytes.decode("utf-8", errors="replace")
            lines = text.splitlines()
            preview = "\n".join(lines[:100])
            suffix = f"\n... ({len(lines)} total lines)" if len(lines) > 100 else ""
            return f"[Local] {filename} — {len(lines)} lines of {mime}:\n```\n{preview}{suffix}\n```"
        except Exception:
            pass

    size_kb = len(raw_bytes) / 1024
    return f"[Local] {filename} ({size_kb:.1f} KB, {mime}) — Gemini unavailable, cannot analyze binary files locally."


def analyze_file(filename: str, b64_data: str) -> str:
    """
    Analyze a file using Gemini multimodal or local fallback.

    Args:
        filename: Original filename (used for MIME detection).
        b64_data: Base64-encoded file contents.

    Returns:
        Analysis result string.
    """
    try:
        raw_bytes = base64.b64decode(b64_data)
    except Exception:
        return f"Error: Could not decode file data for {filename}."

    if len(raw_bytes) > _MAX_SIZE_BYTES:
        return f"Error: {filename} is {len(raw_bytes)/1024/1024:.1f}MB — exceeds 25MB limit."

    mime = _guess_mime(filename)

    # Try Gemini first
    client = _get_gemini_client()
    if client:
        try:
            from google.genai import types

            prompt = (
                f"Analyze this file: {filename}\n"
                "Provide a concise summary of its contents, purpose, and key findings. "
                "If it's code, identify the language, main functions, and any issues. "
                "If it's a document, summarize the key points. "
                "If it's an image, describe what you see. "
                "Keep your response under 200 words."
            )

            parts = [types.Part.from_text(text=prompt)]

            # For text-like files, inline the content as text
            if mime.startswith("text/") or mime in ("application/json",):
                text_content = raw_bytes.decode("utf-8", errors="replace")
                if len(text_content) > 50000:
                    text_content = text_content[:50000] + "\n... (truncated)"
                parts.append(types.Part.from_text(text=f"```\n{text_content}\n```"))
            else:
                # Binary files — send as inline data
                parts.append(types.Part.from_bytes(data=raw_bytes, mime_type=mime))

            response = client.models.generate_content(
                model="gemini-2.0-flash",
                contents=[types.Content(role="user", parts=parts)],
            )
            return response.text.strip() if response.text else "No analysis returned."

        except Exception as exc:
            print(f"[FileAnalyzer] Gemini error: {exc}")
            return _local_fallback(filename, raw_bytes)

    # No Gemini client — local fallback
    return _local_fallback(filename, raw_bytes)


def analyze_screen_capture(b64_png: str) -> str:
    """Analyze a screenshot via Gemini vision."""
    client = _get_gemini_client()
    if not client:
        return "Screen analysis unavailable — no API key configured."

    try:
        from google.genai import types
        raw = base64.b64decode(b64_png)

        response = client.models.generate_content(
            model="gemini-2.0-flash",
            contents=[types.Content(role="user", parts=[
                types.Part.from_text(
                    text="Describe what's on this screen. Be concise — 2-3 sentences max."
                ),
                types.Part.from_bytes(data=raw, mime_type="image/png"),
            ])],
        )
        return response.text.strip() if response.text else "Could not analyze screen."
    except Exception as exc:
        return f"Screen analysis error: {exc}"


def analyze_webcam_frame(b64_jpg: str) -> str:
    """Analyze a webcam frame via Gemini vision."""
    client = _get_gemini_client()
    if not client:
        return "Webcam analysis unavailable — no API key configured."

    try:
        from google.genai import types
        raw = base64.b64decode(b64_jpg)

        response = client.models.generate_content(
            model="gemini-2.0-flash",
            contents=[types.Content(role="user", parts=[
                types.Part.from_text(
                    text="Describe what you see in this webcam image. Be concise — 2 sentences max."
                ),
                types.Part.from_bytes(data=raw, mime_type="image/jpeg"),
            ])],
        )
        return response.text.strip() if response.text else "Could not analyze webcam feed."
    except Exception as exc:
        return f"Webcam analysis error: {exc}"
