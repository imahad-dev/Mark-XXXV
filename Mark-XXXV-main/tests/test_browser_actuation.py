"""
tests/test_browser_actuation.py — Browser Actuation Integration Tests
=======================================================================
Validates the end-to-end flow of browser actions through ActuationManager:
rollback scoping for type/check/click/hover, navigate rollback,
and the extension→Playwright fallback hierarchy.
"""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch, PropertyMock

# Ensure project root is on sys.path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class TestBrowserInteractRollbackScoping(unittest.TestCase):
    """Verify that rollback scoping correctly differentiates action subtypes."""

    def _make_manager(self):
        from os_layer.actuation import ActuationManager
        mgr = ActuationManager()
        return mgr

    def _make_payload(self, action="click", selector="#btn", value=None):
        from os_layer.actuation import ActionPayload
        params = {"action": action}
        if value is not None:
            params["value"] = value
        return ActionPayload(
            action_type="BROWSER_INTERACT",
            target=selector,
            parameters=params,
            risk_level="MEDIUM",
        )

    @patch("os_layer.actuation.ActuationManager._dispatch_browser_action")
    @patch("os_layer.actuation.ActuationManager._get_element_value")
    def test_type_action_captures_original_value(self, mock_get_val, mock_dispatch):
        """type actions should capture the current input value for rollback."""
        mock_get_val.return_value = "original text"
        mock_dispatch.return_value = {"status": "success"}

        mgr = self._make_manager()
        payload = self._make_payload(action="type", selector="#input", value="new text")
        result = mgr._handle_browser_interact(payload)

        self.assertTrue(result.success)
        self.assertTrue(payload.undo_supported)
        self.assertIsNotNone(payload.undo_payload)
        self.assertEqual(payload.undo_payload["rollback_type"], "browser_type")
        self.assertEqual(
            payload.undo_payload["rollback_parameters"]["value"],
            "original text",
        )

    @patch("os_layer.actuation.ActuationManager._dispatch_browser_action")
    @patch("os_layer.actuation.ActuationManager._get_checked_state")
    def test_check_action_captures_boolean_state(self, mock_get_checked, mock_dispatch):
        """check actions should capture the current boolean checked state."""
        mock_get_checked.return_value = False
        mock_dispatch.return_value = {"status": "success"}

        mgr = self._make_manager()
        payload = self._make_payload(action="check", selector="#checkbox")
        result = mgr._handle_browser_interact(payload)

        self.assertTrue(result.success)
        self.assertTrue(payload.undo_supported)
        self.assertIsNotNone(payload.undo_payload)
        self.assertEqual(payload.undo_payload["rollback_type"], "browser_check")
        self.assertEqual(
            payload.undo_payload["rollback_parameters"]["checked"],
            False,
        )

    @patch("os_layer.actuation.ActuationManager._dispatch_browser_action")
    def test_click_action_is_irreversible(self, mock_dispatch):
        """click actions should set undo_supported=False."""
        mock_dispatch.return_value = {"status": "success"}

        mgr = self._make_manager()
        payload = self._make_payload(action="click", selector="#btn")
        result = mgr._handle_browser_interact(payload)

        self.assertTrue(result.success)
        self.assertFalse(payload.undo_supported)

    @patch("os_layer.actuation.ActuationManager._dispatch_browser_action")
    def test_hover_action_is_irreversible(self, mock_dispatch):
        """hover actions should set undo_supported=False."""
        mock_dispatch.return_value = {"status": "success"}

        mgr = self._make_manager()
        payload = self._make_payload(action="hover", selector="#menu")
        result = mgr._handle_browser_interact(payload)

        self.assertTrue(result.success)
        self.assertFalse(payload.undo_supported)

    @patch("os_layer.actuation.ActuationManager._dispatch_browser_action")
    @patch("os_layer.actuation.ActuationManager._get_element_value")
    def test_type_without_readable_value_disables_undo(self, mock_get_val, mock_dispatch):
        """If the input value can't be read, undo should be disabled."""
        mock_get_val.return_value = None
        mock_dispatch.return_value = {"status": "success"}

        mgr = self._make_manager()
        payload = self._make_payload(action="type", selector="#hidden", value="x")
        mgr._handle_browser_interact(payload)

        self.assertFalse(payload.undo_supported)


class TestBrowserNavigateRollback(unittest.TestCase):
    """Verify navigate rollback captures the current URL."""

    def _make_navigate_payload(self, url="https://example.com"):
        from os_layer.actuation import ActionPayload
        return ActionPayload(
            action_type="BROWSER_NAVIGATE",
            target=url,
            risk_level="MEDIUM",
        )

    @patch("os_layer.actuation.ActuationManager._dispatch_browser_action")
    @patch("os_layer.actuation.ActuationManager._get_current_url")
    def test_navigate_captures_current_url(self, mock_url, mock_dispatch):
        """Navigate should capture the current URL for rollback."""
        mock_url.return_value = "https://old-page.com"
        mock_dispatch.return_value = {"status": "success"}

        from os_layer.actuation import ActuationManager
        mgr = ActuationManager()
        payload = self._make_navigate_payload("https://new-page.com")
        result = mgr._handle_browser_navigate(payload)

        self.assertTrue(result.success)
        self.assertTrue(payload.undo_supported)
        self.assertEqual(
            payload.undo_payload["rollback_parameters"]["url"],
            "https://old-page.com",
        )

    @patch("os_layer.actuation.ActuationManager._dispatch_browser_action")
    @patch("os_layer.actuation.ActuationManager._get_current_url")
    def test_navigate_without_current_url_disables_undo(self, mock_url, mock_dispatch):
        """If no current URL is available, undo should be disabled."""
        mock_url.return_value = None
        mock_dispatch.return_value = {"status": "success"}

        from os_layer.actuation import ActuationManager
        mgr = ActuationManager()
        payload = self._make_navigate_payload()
        mgr._handle_browser_navigate(payload)

        self.assertFalse(payload.undo_supported)

    @patch("os_layer.actuation.ActuationManager._dispatch_browser_action")
    @patch("os_layer.actuation.ActuationManager._get_current_url")
    def test_navigate_failure_returns_error(self, mock_url, mock_dispatch):
        """Failed navigation should return an error result."""
        mock_url.return_value = "https://old.com"
        mock_dispatch.return_value = {"status": "error", "error": "timeout"}

        from os_layer.actuation import ActuationManager
        mgr = ActuationManager()
        payload = self._make_navigate_payload()
        result = mgr._handle_browser_navigate(payload)

        self.assertFalse(result.success)
        self.assertEqual(result.outcome, "failed")


class TestBrowserDispatchFallback(unittest.TestCase):
    """Verify the extension→Playwright fallback hierarchy."""

    @patch("os_layer.playwright_engine.get_playwright_engine")
    @patch("os_layer.browser_bridge.get_browser_bridge")
    def test_uses_extension_when_connected(self, mock_bridge_fn, mock_pw_fn):
        """Should use extension bridge when connected."""
        mock_bridge = MagicMock()
        mock_bridge.is_connected.return_value = True
        mock_bridge.send_action.return_value = {"status": "success", "action_id": "123"}
        mock_bridge_fn.return_value = mock_bridge

        from os_layer.actuation import ActuationManager
        result = ActuationManager._dispatch_browser_action({
            "command": "navigate",
            "url": "https://example.com",
        })

        self.assertEqual(result["status"], "success")
        mock_bridge.send_action.assert_called_once()
        mock_pw_fn.assert_not_called()

    @patch("os_layer.playwright_engine.get_playwright_engine")
    @patch("os_layer.browser_bridge.get_browser_bridge")
    def test_falls_through_to_playwright_when_disconnected(self, mock_bridge_fn, mock_pw_fn):
        """Should fall through to Playwright when extension is offline."""
        mock_bridge = MagicMock()
        mock_bridge.is_connected.return_value = False
        mock_bridge_fn.return_value = mock_bridge

        mock_engine = MagicMock()
        mock_engine.navigate.return_value = {"status": "success", "url": "https://example.com"}
        mock_pw_fn.return_value = mock_engine

        from os_layer.actuation import ActuationManager
        result = ActuationManager._dispatch_browser_action({
            "command": "navigate",
            "url": "https://example.com",
        })

        self.assertEqual(result["status"], "success")
        mock_engine.navigate.assert_called_once_with("https://example.com")

    @patch("os_layer.playwright_engine.get_playwright_engine")
    @patch("os_layer.browser_bridge.get_browser_bridge")
    def test_falls_through_when_extension_returns_none(self, mock_bridge_fn, mock_pw_fn):
        """Should fall through to Playwright when extension fast-fails (returns None)."""
        mock_bridge = MagicMock()
        mock_bridge.is_connected.return_value = True
        mock_bridge.send_action.return_value = None  # fast-fail
        mock_bridge_fn.return_value = mock_bridge

        mock_engine = MagicMock()
        mock_engine.navigate.return_value = {"status": "success"}
        mock_pw_fn.return_value = mock_engine

        from os_layer.actuation import ActuationManager
        result = ActuationManager._dispatch_browser_action({
            "command": "navigate",
            "url": "https://example.com",
        })

        self.assertEqual(result["status"], "success")
        mock_engine.navigate.assert_called_once()


class TestPlaywrightEngine(unittest.TestCase):
    """Unit tests for PlaywrightEngine."""

    def test_import(self):
        from os_layer.playwright_engine import PlaywrightEngine
        self.assertIsNotNone(PlaywrightEngine)

    def test_initial_state(self):
        """Engine should not be initialized until ensure_ready is called."""
        from os_layer.playwright_engine import PlaywrightEngine
        engine = PlaywrightEngine()
        self.assertFalse(engine._initialized)
        self.assertIsNone(engine.get_current_url())

    def test_get_input_value_returns_none_when_not_ready(self):
        from os_layer.playwright_engine import PlaywrightEngine
        engine = PlaywrightEngine()
        self.assertIsNone(engine.get_input_value("#any"))

    def test_get_checked_state_returns_none_when_not_ready(self):
        from os_layer.playwright_engine import PlaywrightEngine
        engine = PlaywrightEngine()
        self.assertIsNone(engine.get_checked_state("#any"))

    def test_navigate_returns_error_when_not_ready(self):
        """Navigate should return an error dict if browser can't start."""
        from os_layer.playwright_engine import PlaywrightEngine
        engine = PlaywrightEngine()

        with patch.object(engine, '_start_browser', return_value=False):
            result = engine.navigate("https://example.com")
            self.assertEqual(result["status"], "error")

    def test_interact_returns_error_for_unknown_action(self):
        """Unknown action types should return an error."""
        from os_layer.playwright_engine import PlaywrightEngine
        engine = PlaywrightEngine()

        with patch.object(engine, 'ensure_ready', return_value=True):
            engine._page = MagicMock()
            result = engine.interact("#btn", "swipe")
            self.assertEqual(result["status"], "error")
            self.assertIn("Unknown action", result["error"])


class TestConfigBrowserVariables(unittest.TestCase):
    """Verify browser config variables exist and have correct defaults."""

    def test_browser_bridge_port_default(self):
        from core.config import Config
        self.assertEqual(Config.BROWSER_BRIDGE_PORT, 8765)

    def test_playwright_headless_default(self):
        from core.config import Config
        self.assertFalse(Config.PLAYWRIGHT_HEADLESS)

    def test_browser_action_timeout_default(self):
        from core.config import Config
        self.assertEqual(Config.BROWSER_ACTION_TIMEOUT_SEC, 10.0)


if __name__ == "__main__":
    unittest.main()
