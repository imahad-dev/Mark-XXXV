"""
tests/test_screen_intel.py — Screen Intelligence Engine Tests
==============================================================
Tests for UIAutomation text extraction, win32 fallback, context
caching, SQLite persistence, and background monitoring lifecycle.

Runs WITHOUT mocking win32/UIA — exercises real OS APIs on Windows.
Gracefully skips on non-Windows or missing dependencies.
"""

from __future__ import annotations

import json
import platform
import sqlite3
import time
import threading
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

# Skip entire module on non-Windows
IS_WINDOWS = platform.system() == "Windows"


class TestScreenIntelDataClasses(unittest.TestCase):
    """Test data class defaults and serialization."""

    def test_window_context_defaults(self):
        from os_layer.screen_intel import WindowContext
        ctx = WindowContext()
        self.assertEqual(ctx.title, "")
        self.assertEqual(ctx.app_name, "")
        self.assertEqual(ctx.process_id, 0)
        self.assertEqual(ctx.rect, (0, 0, 0, 0))

    def test_screen_context_defaults(self):
        from os_layer.screen_intel import ScreenContext
        ctx = ScreenContext()
        self.assertEqual(ctx.timestamp, 0.0)
        self.assertEqual(ctx.visible_text, "")
        self.assertEqual(ctx.source, "uia")
        self.assertIsInstance(ctx.window_layout, list)

    def test_notification_defaults(self):
        from os_layer.screen_intel import Notification
        n = Notification(title="Test", body="Hello", app_name="Slack")
        self.assertEqual(n.title, "Test")
        self.assertEqual(n.app_name, "Slack")


class TestScreenIntelExtractAppName(unittest.TestCase):
    """Test the heuristic app-name extractor."""

    def test_dash_separator(self):
        from os_layer.screen_intel import ScreenIntelligence
        result = ScreenIntelligence._extract_app_name("document.py - Visual Studio Code")
        self.assertEqual(result, "Visual Studio Code")

    def test_pipe_separator(self):
        from os_layer.screen_intel import ScreenIntelligence
        result = ScreenIntelligence._extract_app_name("Gmail | Google Chrome")
        self.assertEqual(result, "Google Chrome")

    def test_no_separator(self):
        from os_layer.screen_intel import ScreenIntelligence
        result = ScreenIntelligence._extract_app_name("Calculator")
        self.assertEqual(result, "Calculator")

    def test_empty_title(self):
        from os_layer.screen_intel import ScreenIntelligence
        result = ScreenIntelligence._extract_app_name("")
        self.assertEqual(result, "unknown")

    def test_em_dash_separator(self):
        from os_layer.screen_intel import ScreenIntelligence
        result = ScreenIntelligence._extract_app_name("Inbox — Outlook")
        self.assertEqual(result, "Outlook")


@unittest.skipUnless(IS_WINDOWS, "Windows-only: requires win32gui")
class TestScreenIntelLive(unittest.TestCase):
    """Live OS integration tests — run on Windows with real APIs."""

    def setUp(self):
        from os_layer.screen_intel import ScreenIntelligence
        self.si = ScreenIntelligence()

    def test_get_active_window_returns_context(self):
        ctx = self.si.get_active_window()
        # Should return a WindowContext even if title is empty (e.g., in CI)
        self.assertIsNotNone(ctx)
        self.assertIsInstance(ctx.title, str)

    def test_get_window_layout_returns_list(self):
        layout = self.si.get_window_layout()
        self.assertIsInstance(layout, list)
        # At least the test runner window should be visible
        if layout:
            self.assertTrue(hasattr(layout[0], "hwnd"))

    def test_capture_context_caching(self):
        """Second capture within TTL should return cached result."""
        from os_layer import screen_intel
        original_ttl = screen_intel._CONTEXT_CACHE_TTL
        screen_intel._CONTEXT_CACHE_TTL = 300.0  # Temporarily increase to prevent UIA latency expiration
        try:
            ctx1 = self.si.capture_context()
            ctx2 = self.si.capture_context()
            # Same object (cached)
            self.assertIs(ctx1, ctx2)
        finally:
            screen_intel._CONTEXT_CACHE_TTL = original_ttl

    def test_capture_context_invalidation(self):
        """Force cache invalidation and verify fresh capture."""
        ctx1 = self.si.capture_context()
        self.si._last_capture_ts = 0.0  # invalidate cache
        ctx2 = self.si.capture_context()
        # Different object (fresh capture)
        self.assertIsNot(ctx1, ctx2)

    def test_get_context_summary_returns_string(self):
        summary = self.si.get_context_summary()
        self.assertIsInstance(summary, str)
        self.assertIn("[SCREEN CONTEXT", summary)

    def test_get_visible_text_returns_string(self):
        text = self.si.get_visible_text()
        self.assertIsInstance(text, str)


@unittest.skipUnless(IS_WINDOWS, "Windows-only: requires win32gui")
class TestScreenIntelMonitoring(unittest.TestCase):
    """Test background monitoring lifecycle."""

    def setUp(self):
        from os_layer.screen_intel import ScreenIntelligence
        self.si = ScreenIntelligence()

    def tearDown(self):
        self.si.stop_monitoring()

    def test_start_stop_monitoring(self):
        captured = []
        self.si.start_monitoring(callback=lambda ctx: captured.append(ctx))
        self.assertTrue(self.si.is_monitoring)

        # Wait for at least one capture
        time.sleep(0.5)

        self.si.stop_monitoring()
        self.assertFalse(self.si.is_monitoring)

    def test_double_start_ignored(self):
        self.si.start_monitoring(callback=lambda ctx: None)
        self.si.start_monitoring(callback=lambda ctx: None)
        # Should not crash — just logs a warning
        self.assertTrue(self.si.is_monitoring)


class TestScreenIntelPersistence(unittest.TestCase):
    """Test SQLite persistence (uses real DB)."""

    def test_recent_contexts_query(self):
        from os_layer.screen_intel import ScreenIntelligence
        si = ScreenIntelligence()
        # Query should not crash even with empty DB
        results = si.get_recent_contexts(limit=5)
        self.assertIsInstance(results, list)

    def test_prune_old_contexts(self):
        from os_layer.screen_intel import ScreenIntelligence
        si = ScreenIntelligence()
        # Should return 0 if nothing to prune
        deleted = si.prune_old_contexts(max_age_hours=0)
        self.assertIsInstance(deleted, int)


if __name__ == "__main__":
    unittest.main()
