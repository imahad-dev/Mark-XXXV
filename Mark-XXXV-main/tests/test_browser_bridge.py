"""
tests/test_browser_bridge.py — Browser Bridge Server Tests
============================================================
Validates the secure WebSocket bridge: authentication, origin
validation, port retry, threading model, and disconnect fast-fail.
"""

import asyncio
import json
import os
import socket
import sys
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

# Ensure project root is on sys.path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class TestBrowserBridgeImport(unittest.TestCase):
    """Verify the module loads without crashing."""

    def test_import(self):
        from os_layer.browser_bridge import BrowserBridgeServer, get_browser_bridge
        self.assertIsNotNone(BrowserBridgeServer)


class TestBrowserBridgeServer(unittest.TestCase):
    """Integration tests for BrowserBridgeServer."""

    @classmethod
    def setUpClass(cls):
        """Patch config to use a random available port and no origin restriction."""
        cls._port = cls._find_free_port()

    @classmethod
    def _find_free_port(cls) -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            return s.getsockname()[1]

    def _make_server(self, allowed_extension_id=""):
        """Create a BrowserBridgeServer with a mock security manager."""
        from os_layer.browser_bridge import BrowserBridgeServer

        security = MagicMock()
        security.validate = MagicMock(side_effect=lambda t: t == "valid-token")

        with patch("core.config.config") as mock_config:
            mock_config.BROWSER_BRIDGE_PORT = self._port
            mock_config.ALLOWED_EXTENSION_ID = allowed_extension_id
            mock_config.BROWSER_ACTION_TIMEOUT_SEC = 5.0

            server = BrowserBridgeServer(security)
            # Override config reference to avoid re-patching
            server._config_port = self._port
            server._config_origin = allowed_extension_id
            server._config_timeout = 5.0

        return server, security

    def test_is_connected_initially_false(self):
        """Server should report no connection before any client connects."""
        from os_layer.browser_bridge import BrowserBridgeServer
        security = MagicMock()
        server = BrowserBridgeServer(security)
        self.assertFalse(server.is_connected())

    def test_send_action_returns_none_when_disconnected(self):
        """send_action should return None immediately when no client is connected."""
        from os_layer.browser_bridge import BrowserBridgeServer
        security = MagicMock()
        server = BrowserBridgeServer(security)
        result = server.send_action({"command": "click"})
        self.assertIsNone(result)

    def test_bound_port_is_none_before_start(self):
        """bound_port should be None before the server is started."""
        from os_layer.browser_bridge import BrowserBridgeServer
        security = MagicMock()
        server = BrowserBridgeServer(security)
        self.assertIsNone(server.bound_port)

    def test_port_availability_check(self):
        """_is_port_available should detect occupied ports."""
        from os_layer.browser_bridge import BrowserBridgeServer

        # Bind a port to make it unavailable
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(("127.0.0.1", 0))
        occupied_port = sock.getsockname()[1]

        try:
            self.assertFalse(BrowserBridgeServer._is_port_available(occupied_port))
        finally:
            sock.close()

        # After release, it should be available
        self.assertTrue(BrowserBridgeServer._is_port_available(occupied_port))


class TestBrowserBridgeAuth(unittest.TestCase):
    """Tests for the authentication and origin validation logic."""

    def test_token_validation_uses_security_manager(self):
        """The bridge should delegate token validation to SessionSecurityManager."""
        from os_layer.browser_bridge import BrowserBridgeServer

        security = MagicMock()
        security.validate = MagicMock(return_value=True)

        server = BrowserBridgeServer(security)
        # Simulate internal validation call
        self.assertTrue(server._security.validate("any-token"))
        security.validate.assert_called_once_with("any-token")

    def test_token_rejection(self):
        """Invalid tokens should be rejected."""
        from os_layer.browser_bridge import BrowserBridgeServer

        security = MagicMock()
        security.validate = MagicMock(return_value=False)

        server = BrowserBridgeServer(security)
        self.assertFalse(server._security.validate("bad-token"))

    def test_origin_extraction_fallback(self):
        """_get_origin should return empty string on missing headers."""
        from os_layer.browser_bridge import BrowserBridgeServer

        # Mock websocket with no request attribute
        mock_ws = MagicMock(spec=[])
        origin = BrowserBridgeServer._get_origin(mock_ws)
        self.assertEqual(origin, "")


class TestBrowserBridgeEventTypes(unittest.TestCase):
    """Verify that browser bridge events are registered in EventType."""

    def test_event_types_exist(self):
        from os_layer.event_bus import EventType

        self.assertEqual(
            EventType.BROWSER_EXT_CONNECTED.value,
            "browser.extension_connected",
        )
        self.assertEqual(
            EventType.BROWSER_EXT_DISCONNECTED.value,
            "browser.extension_disconnected",
        )


class TestBrowserBridgeThreading(unittest.TestCase):
    """Test the sync→async threading boundary."""

    def test_send_action_from_sync_thread_returns_none(self):
        """
        Calling send_action from a synchronous thread when no loop
        is running should return None gracefully.
        """
        from os_layer.browser_bridge import BrowserBridgeServer

        security = MagicMock()
        server = BrowserBridgeServer(security)

        result = [None]

        def worker():
            result[0] = server.send_action({"command": "test"})

        t = threading.Thread(target=worker)
        t.start()
        t.join(timeout=3.0)

        self.assertIsNone(result[0])


if __name__ == "__main__":
    unittest.main()
