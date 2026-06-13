"""
os_layer/playwright_engine.py — Playwright Fallback Engine
============================================================
Headless/headed Chromium browser automation via Playwright, used as
a fallback when the browser extension WebSocket is offline.

Design Constraints:
    - Uses Playwright's BUNDLED Chromium exclusively. Never attaches
      to the user's Chrome profile (avoids profile lock corruption).
    - Headed by default (PLAYWRIGHT_HEADLESS=False) for transparency.
    - Window title set to "JARVIS Browser Agent" via JavaScript on
      first page load (--window-name is not a valid Chromium flag).
    - Singleton pattern with thread-safe lazy initialization.
    - Liveness checks before every action — automatically restarts
      the browser if it was manually closed or crashed.

Usage:
    engine = PlaywrightEngine()
    engine.ensure_ready()
    result = engine.navigate("https://example.com")
    result = engine.interact("#search", "type", "hello world")
    engine.close()
"""

from __future__ import annotations

import logging
import threading
from typing import Optional

from core.config import config

logger = logging.getLogger(__name__)

_TITLE = "JARVIS Browser Agent"


class PlaywrightEngine:
    """
    Thread-safe Playwright wrapper for browser automation fallback.

    Lazily initializes a Chromium browser instance on first use.
    Automatically recovers from crashes and manual closures.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pw = None          # Playwright instance
        self._browser = None     # Browser instance
        self._context = None     # BrowserContext
        self._page = None        # Primary page
        self._initialized = False

    # ── Public API ───────────────────────────────────────────────────────

    def ensure_ready(self) -> bool:
        """
        Ensure the browser is running and responsive.

        Lazily starts Playwright on first call. Recovers from crashes
        on subsequent calls. Returns True if the browser is ready.
        """
        with self._lock:
            if self._is_alive():
                return True
            return self._start_browser()

    def navigate(self, url: str) -> dict:
        """
        Navigate the primary page to a URL.

        Returns:
            {"status": "success", "url": url, "title": title}
            or {"status": "error", "error": message}
        """
        if not self.ensure_ready():
            return {"status": "error", "error": "Playwright browser not available"}

        try:
            self._page.goto(url, wait_until="domcontentloaded", timeout=15000)
            self._set_title()
            return {
                "status": "success",
                "url": self._page.url,
                "title": self._page.title(),
            }
        except Exception as e:
            logger.error(f"[Playwright] Navigate failed: {e}")
            return {"status": "error", "error": str(e)}

    def interact(
        self,
        selector: str,
        action: str,
        value: Optional[str] = None,
    ) -> dict:
        """
        Interact with a DOM element.

        Args:
            selector: CSS selector for the target element.
            action: One of "click", "type", "hover", "check".
            value: Text input for "type" action.

        Returns:
            {"status": "success", "action": action, "selector": selector}
            or {"status": "error", "error": message}
        """
        if not self.ensure_ready():
            return {"status": "error", "error": "Playwright browser not available"}

        try:
            if action == "click":
                self._page.click(selector, timeout=5000)
            elif action == "type":
                self._page.fill(selector, value or "", timeout=5000)
            elif action == "hover":
                self._page.hover(selector, timeout=5000)
            elif action == "check":
                locator = self._page.locator(selector)
                if locator.is_checked():
                    locator.uncheck(timeout=5000)
                else:
                    locator.check(timeout=5000)
            else:
                return {"status": "error", "error": f"Unknown action: {action}"}

            return {"status": "success", "action": action, "selector": selector}
        except Exception as e:
            logger.error(f"[Playwright] Interact ({action}) failed: {e}")
            return {"status": "error", "error": str(e)}

    def get_current_url(self) -> Optional[str]:
        """Return the URL of the current page, or None if not ready."""
        if not self._is_alive():
            return None
        try:
            return self._page.url
        except Exception:
            return None

    def get_input_value(self, selector: str) -> Optional[str]:
        """Return the current value of an input field, or None on failure."""
        if not self._is_alive():
            return None
        try:
            return self._page.input_value(selector, timeout=3000)
        except Exception:
            return None

    def get_checked_state(self, selector: str) -> Optional[bool]:
        """Return the checked state of a checkbox/radio, or None on failure."""
        if not self._is_alive():
            return None
        try:
            return self._page.locator(selector).is_checked()
        except Exception:
            return None

    def close(self) -> None:
        """Tear down the browser and Playwright instances."""
        with self._lock:
            self._teardown()
            logger.info("[Playwright] 🛑 Engine closed")

    # ── Private Methods ──────────────────────────────────────────────────

    def _is_alive(self) -> bool:
        """Check if the browser process is still running and responsive."""
        if not self._initialized or not self._browser or not self._page:
            return False
        try:
            # A lightweight DOM eval to verify the page is responsive
            self._page.evaluate("1 + 1")
            return True
        except Exception:
            logger.warning("[Playwright] ⚠️ Browser not responsive — will restart")
            self._teardown()
            return False

    def _start_browser(self) -> bool:
        """Initialize Playwright and launch a Chromium instance."""
        try:
            from playwright.sync_api import sync_playwright

            self._pw = sync_playwright().start()

            launch_args = []
            if not config.PLAYWRIGHT_HEADLESS:
                launch_args.append(
                    f"--app=data:text/html,<title>{_TITLE}</title>"
                )

            self._browser = self._pw.chromium.launch(
                headless=config.PLAYWRIGHT_HEADLESS,
                args=launch_args,
            )
            self._context = self._browser.new_context()
            self._page = self._context.new_page()

            # Set title via JavaScript as a fallback
            self._set_title()

            self._initialized = True
            logger.info(
                f"[Playwright] ✅ Chromium launched "
                f"(headless={config.PLAYWRIGHT_HEADLESS})"
            )
            return True

        except ImportError:
            logger.error(
                "[Playwright] ❌ 'playwright' package not installed. "
                "Run: pip install playwright && playwright install chromium"
            )
            return False
        except Exception as e:
            logger.error(f"[Playwright] ❌ Browser launch failed: {e}")
            self._teardown()
            return False

    def _set_title(self) -> None:
        """Set the browser window title to identify it as JARVIS-controlled."""
        try:
            self._page.evaluate(f"document.title = '{_TITLE}'")
        except Exception:
            pass  # Non-critical — title is cosmetic

    def _teardown(self) -> None:
        """Clean up all Playwright resources."""
        for resource_name in ("_page", "_context", "_browser"):
            resource = getattr(self, resource_name, None)
            if resource:
                try:
                    resource.close()
                except Exception:
                    pass
            setattr(self, resource_name, None)

        if self._pw:
            try:
                self._pw.stop()
            except Exception:
                pass
            self._pw = None

        self._initialized = False


# ── Singleton ────────────────────────────────────────────────────────────────

_instance: Optional[PlaywrightEngine] = None
_instance_lock = threading.Lock()


def get_playwright_engine() -> PlaywrightEngine:
    """Thread-safe singleton accessor."""
    global _instance
    if _instance is None:
        with _instance_lock:
            if _instance is None:
                _instance = PlaywrightEngine()
    return _instance
