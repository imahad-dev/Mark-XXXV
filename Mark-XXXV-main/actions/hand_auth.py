"""
actions/hand_auth.py
====================
Jarvis tool wrapper around the hand scanner.

Gemini can invoke this mid-conversation — for example, before deleting
files, sending messages, or executing any sensitive system command.

Registration
------------
Registered via @tool decorator in core/tool_defs.py (tool #20).
Safety tier: CONFIRM — the registry enforces user confirmation before execution.
The adapter _hand_auth() normalizes the non-standard signature.
"""

from __future__ import annotations
import asyncio
import time
from typing import Any

# Lazy import so Jarvis starts even if mediapipe isn't installed yet
def _run_scan(reason: str, timeout: float, theme: str) -> dict[str, Any]:
    from hand_scanner.scanner import authenticate
    print(f"[HandAuth] Scan requested — reason: '{reason}'")
    t0 = time.time()
    success = authenticate(timeout=timeout, theme=theme)
    elapsed = round(time.time() - t0, 1)
    if success:
        return {
            "status":  "authenticated",
            "elapsed": elapsed,
            "message": "Hand verified. Proceeding with the action.",
        }
    return {
        "status":  "denied",
        "elapsed": elapsed,
        "message": "Hand verification failed or timed out. Action cancelled.",
    }


# ---------------------------------------------------------------------------
# Synchronous entry point (called by _execute_tool via asyncio.to_thread)
# ---------------------------------------------------------------------------

def hand_auth(reason: str = "",
              timeout: float = 15.0,
              theme: str = "jarvis") -> dict[str, Any]:
    """
    Display the hand-scanner window and block until verified or timed out.

    Parameters
    ----------
    reason  : Why auth is needed (spoken/logged by Jarvis before scanning)
    timeout : Max seconds to wait
    theme   : "jarvis" | "friday" | "ultron" — matches active UI theme

    Returns
    -------
    dict with keys:
        status  : "authenticated" | "denied"
        elapsed : seconds taken
        message : human-readable result for Jarvis to speak
    """
    return _run_scan(reason, timeout, theme)


# ---------------------------------------------------------------------------
# Registration note
# ---------------------------------------------------------------------------
# This tool is registered via @tool decorator in core/tool_defs.py.
# The adapter function _hand_auth() normalizes the signature for the
# registry's execute() pipeline. No manual wiring needed.
# ---------------------------------------------------------------------------

