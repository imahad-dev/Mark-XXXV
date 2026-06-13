"""
os_layer/browser_bridge.py — Secure WebSocket Bridge to Browser Extension
===========================================================================
Provides a local WebSocket server that the JARVIS browser extension connects
to for real-time DOM manipulation, navigation, and page state queries.

Security Model:
    - Token-based authentication: connection URL must contain a valid session
      token matching the one written by SessionSecurityManager at boot.
    - Origin validation: connections are rejected unless the Origin header
      matches ALLOWED_EXTENSION_ID (fail-closed when configured).

Threading Model:
    The WebSocket server runs an asyncio event loop on a dedicated daemon
    thread. The ActuationManager's synchronous worker thread calls
    send_action() which uses asyncio.run_coroutine_threadsafe() to bridge
    the sync→async boundary, then .result(timeout) to block synchronously.

    If the extension disconnects mid-action, the async coroutine catches
    websockets.ConnectionClosed and returns None immediately (fast-fail)
    instead of waiting for the full timeout.
"""

from __future__ import annotations

import asyncio
import json
import logging
import socket
import threading
from typing import Any, Optional
from urllib.parse import parse_qs, urlparse

from core.config import config

logger = logging.getLogger(__name__)

_MAX_PORT_RETRIES = 5


class BrowserBridgeServer:
    """
    Secure local WebSocket server for browser extension communication.

    Usage:
        bridge = BrowserBridgeServer(security_manager)
        bridge.start()          # spawns daemon thread
        result = bridge.send_action({"action_type": "click", ...})
        bridge.stop()
    """

    def __init__(self, security_manager) -> None:
        self._security = security_manager
        self._client: Optional[Any] = None          # active websocket connection
        self._client_lock = threading.Lock()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._server: Optional[Any] = None
        self._stop_event = threading.Event()
        self._bound_port: Optional[int] = None

    # ── Public API ───────────────────────────────────────────────────────

    def start(self) -> None:
        """Start the WebSocket server on a background daemon thread."""
        if self._thread and self._thread.is_alive():
            logger.warning("[BrowserBridge] Already running")
            return

        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run_loop,
            daemon=True,
            name="browser-bridge-ws",
        )
        self._thread.start()
        logger.info("[BrowserBridge] ✅ Server thread started")

    def stop(self) -> None:
        """Shut down the WebSocket server and clean up."""
        self._stop_event.set()

        if self._loop and not self._loop.is_closed():
            self._loop.call_soon_threadsafe(self._loop.stop)

        if self._thread:
            self._thread.join(timeout=5.0)
            self._thread = None

        with self._client_lock:
            self._client = None

        logger.info("[BrowserBridge] 🛑 Server stopped")

    def is_connected(self) -> bool:
        """Return True if an authenticated extension client is active."""
        with self._client_lock:
            return self._client is not None

    def send_action(self, action: dict) -> Optional[dict]:
        """
        Send a command payload to the extension synchronously.

        Called from the ActuationManager worker thread. Bridges the
        sync→async boundary via asyncio.run_coroutine_threadsafe().

        Returns the extension response dict, or None if the extension
        is disconnected or the request times out.
        """
        if not self.is_connected() or not self._loop:
            return None

        try:
            future = asyncio.run_coroutine_threadsafe(
                self._send_and_wait(action),
                self._loop,
            )
            return future.result(timeout=config.BROWSER_ACTION_TIMEOUT_SEC)
        except TimeoutError:
            logger.warning(
                "[BrowserBridge] ⏱️ send_action timed out after "
                f"{config.BROWSER_ACTION_TIMEOUT_SEC}s"
            )
            return None
        except Exception as e:
            logger.error(f"[BrowserBridge] send_action error: {e}")
            return None

    @property
    def bound_port(self) -> Optional[int]:
        """The port the server is currently bound to (None if not started)."""
        return self._bound_port

    # ── Async Internals ──────────────────────────────────────────────────

    async def _send_and_wait(self, action: dict) -> Optional[dict]:
        """
        Async coroutine: send action to extension, await response.

        Fast-fails on ConnectionClosed instead of letting the timeout fire.
        """
        try:
            import websockets
        except ImportError:
            logger.error("[BrowserBridge] websockets not installed")
            return None

        with self._client_lock:
            client = self._client

        if client is None:
            return None

        try:
            await client.send(json.dumps(action))
            raw = await client.recv()
            return json.loads(raw)
        except websockets.ConnectionClosed:
            logger.warning("[BrowserBridge] ⚡ Extension disconnected mid-action (fast-fail)")
            with self._client_lock:
                self._client = None
            self._emit_disconnect_event()
            return None
        except Exception as e:
            logger.error(f"[BrowserBridge] send/recv error: {e}")
            return None

    # ── Server Loop ──────────────────────────────────────────────────────

    def _run_loop(self) -> None:
        """Entry point for the daemon thread — runs the asyncio event loop."""
        try:
            import websockets
        except ImportError:
            logger.error(
                "[BrowserBridge] ❌ 'websockets' package not installed. "
                "Browser bridge disabled."
            )
            return

        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)

        try:
            self._loop.run_until_complete(self._serve(websockets))
        except Exception as e:
            logger.error(f"[BrowserBridge] Server loop exited: {e}")
        finally:
            self._loop.close()
            self._loop = None

    async def _serve(self, websockets_module) -> None:
        """Bind to a port and serve WebSocket connections."""
        base_port = config.BROWSER_BRIDGE_PORT

        for attempt in range(_MAX_PORT_RETRIES):
            port = base_port + attempt
            if not self._is_port_available(port):
                logger.warning(f"[BrowserBridge] Port {port} occupied, trying next...")
                continue

            try:
                self._server = await websockets_module.serve(
                    self._handler,
                    "127.0.0.1",
                    port,
                )
                self._bound_port = port
                logger.info(f"[BrowserBridge] 🌐 Listening on ws://127.0.0.1:{port}")
                break
            except OSError as e:
                logger.warning(f"[BrowserBridge] Bind to port {port} failed: {e}")
                continue
        else:
            logger.error(
                f"[BrowserBridge] ❌ Failed to bind after {_MAX_PORT_RETRIES} attempts "
                f"(ports {base_port}–{base_port + _MAX_PORT_RETRIES - 1})"
            )
            return

        # Block until stop is signalled
        while not self._stop_event.is_set():
            await asyncio.sleep(0.5)

        self._server.close()
        await self._server.wait_closed()

    async def _handler(self, websocket) -> None:
        """
        Handle an incoming WebSocket connection.

        Validates token from query params and Origin header before
        accepting. Rejects with close code 4003 on auth failure.
        """
        # ── Token validation ─────────────────────────────────────────────
        path = websocket.request.path if hasattr(websocket, 'request') else ""
        parsed = urlparse(f"ws://localhost{path}")
        params = parse_qs(parsed.query)
        token = params.get("token", [""])[0]

        if not self._security.validate(token):
            logger.warning("[BrowserBridge] 🚫 Connection rejected: invalid token")
            await websocket.close(4003, "Invalid token")
            return

        # ── Origin validation (fail-closed) ──────────────────────────────
        origin = self._get_origin(websocket)
        allowed_id = config.ALLOWED_EXTENSION_ID

        if allowed_id and origin != allowed_id:
            logger.warning(
                f"[BrowserBridge] 🚫 Connection rejected: origin '{origin}' "
                f"does not match '{allowed_id}'"
            )
            await websocket.close(4003, "Origin not allowed")
            return

        # ── Connection accepted ──────────────────────────────────────────
        with self._client_lock:
            if self._client is not None:
                # Only one extension connection at a time
                logger.warning("[BrowserBridge] Replacing existing client connection")
                try:
                    await self._client.close(4001, "Replaced by new connection")
                except Exception:
                    pass
            self._client = websocket

        logger.info(f"[BrowserBridge] ✅ Extension connected (origin={origin})")
        self._emit_connect_event()

        try:
            # Keep connection alive — responses are handled via send_and_wait
            async for _message in websocket:
                pass  # Messages are consumed by _send_and_wait via recv()
        except Exception as e:
            logger.info(f"[BrowserBridge] Client disconnected: {e}")
        finally:
            with self._client_lock:
                if self._client is websocket:
                    self._client = None
            self._emit_disconnect_event()
            logger.info("[BrowserBridge] 🔌 Extension disconnected")

    # ── Helpers ───────────────────────────────────────────────────────────

    @staticmethod
    def _is_port_available(port: int) -> bool:
        """Check if a TCP port is available for binding."""
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(("127.0.0.1", port))
                return True
        except OSError:
            return False

    @staticmethod
    def _get_origin(websocket) -> str:
        """Extract the Origin header from the WebSocket handshake."""
        try:
            headers = websocket.request.headers if hasattr(websocket, 'request') else {}
            return headers.get("Origin", "")
        except Exception:
            return ""

    def _emit_connect_event(self) -> None:
        """Publish browser.extension_connected to EventBus."""
        try:
            from os_layer.event_bus import get_event_bus, EventType
            get_event_bus().emit(
                EventType.BROWSER_EXT_CONNECTED,
                source="browser_bridge",
                payload={"port": self._bound_port},
            )
        except Exception:
            pass  # EventBus may not be running

    def _emit_disconnect_event(self) -> None:
        """Publish browser.extension_disconnected to EventBus."""
        try:
            from os_layer.event_bus import get_event_bus, EventType
            get_event_bus().emit(
                EventType.BROWSER_EXT_DISCONNECTED,
                source="browser_bridge",
            )
        except Exception:
            pass


# ── Singleton ────────────────────────────────────────────────────────────────

_instance: Optional[BrowserBridgeServer] = None
_instance_lock = threading.Lock()


def get_browser_bridge() -> BrowserBridgeServer:
    """Thread-safe singleton accessor. Requires SecurityManager to be initialized."""
    global _instance
    if _instance is None:
        with _instance_lock:
            if _instance is None:
                from core.security import get_session_security
                _instance = BrowserBridgeServer(get_session_security())
    return _instance
