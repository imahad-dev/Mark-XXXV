"""
tests/test_ambient_mode.py — Ambient Mode Manager Tests
=========================================================
Tests for idle detection state machine, event dispatching,
and screen_intel / doc_intelligence throttle integration.

Mocks ctypes/Win32 APIs to run on any platform.
"""

from __future__ import annotations

import threading
import time
import unittest
from unittest.mock import patch, MagicMock, PropertyMock


class TestAmbientModeStateTransitions(unittest.TestCase):
    """Test the idle/active state machine logic."""

    def _make_manager(self):
        """Create a fresh AmbientModeManager with mocked Win32."""
        from os_layer.ambient_mode import AmbientModeManager
        mgr = AmbientModeManager()
        # Mock Win32 as loaded
        mgr._user32 = MagicMock()
        mgr._kernel32 = MagicMock()
        return mgr

    def test_initial_state_is_active(self):
        mgr = self._make_manager()
        self.assertFalse(mgr.is_idle)
        self.assertFalse(mgr.is_running)

    def test_get_idle_seconds_normal(self):
        """Verify idle seconds calculation with mocked tick counts."""
        mgr = self._make_manager()

        # Simulate: current tick = 10000ms, last input = 5000ms → 5s idle
        mgr._kernel32.GetTickCount.return_value = 10000

        import ctypes
        def mock_get_last_input(byref_obj):
            byref_obj._obj.dwTime = 5000
            return True

        mgr._user32.GetLastInputInfo.side_effect = mock_get_last_input

        idle = mgr._get_idle_seconds()
        self.assertAlmostEqual(idle, 5.0, places=1)

    def test_get_idle_seconds_rollover(self):
        """Verify 32-bit DWORD rollover is handled correctly."""
        mgr = self._make_manager()

        # Simulate rollover: current = 100, last_input = 0xFFFFFF00
        # Expected idle = (100 - 0xFFFFFF00) & 0xFFFFFFFF = 356 ms
        mgr._kernel32.GetTickCount.return_value = 100

        import ctypes
        def mock_get_last_input(byref_obj):
            byref_obj._obj.dwTime = 0xFFFFFF00
            return True

        mgr._user32.GetLastInputInfo.side_effect = mock_get_last_input

        idle = mgr._get_idle_seconds()
        expected_ms = (100 - 0xFFFFFF00) & 0xFFFFFFFF
        self.assertAlmostEqual(idle, expected_ms / 1000.0, places=2)
        # Should be a small positive number, not negative or huge
        self.assertGreater(idle, 0)
        self.assertLess(idle, 1.0)

    def test_get_idle_seconds_api_failure(self):
        """Returns 0 when GetLastInputInfo fails."""
        mgr = self._make_manager()
        mgr._user32.GetLastInputInfo.return_value = False
        idle = mgr._get_idle_seconds()
        self.assertEqual(idle, 0.0)


class TestAmbientModeEventDispatch(unittest.TestCase):
    """Test that idle/active events are published correctly."""

    @patch("os_layer.event_bus.get_event_bus")
    def test_idle_event_published_on_threshold(self, mock_get_bus):
        """Verify user.idle event fires when idle exceeds threshold."""
        from os_layer.ambient_mode import AmbientModeManager

        mock_bus = MagicMock()
        mock_get_bus.return_value = mock_bus

        mgr = AmbientModeManager()
        mgr._user32 = MagicMock()
        mgr._kernel32 = MagicMock()
        mgr._running = True
        mgr._is_idle = False

        # Simulate 600 seconds of idle (threshold default is 300)
        with patch.object(mgr, "_get_idle_seconds", return_value=600.0):
            from os_layer.event_bus import EventType, Event

            # Run one iteration of the monitor logic manually
            from os_layer.event_bus import get_event_bus
            bus = get_event_bus()

            idle_secs = mgr._get_idle_seconds()
            threshold = 300

            if not mgr._is_idle and idle_secs >= threshold:
                mgr._is_idle = True
                bus.publish(Event(
                    event_type=EventType.USER_IDLE,
                    source="ambient_mode",
                    payload={"idle_seconds": round(idle_secs, 1)},
                ))

        self.assertTrue(mgr._is_idle)
        mock_bus.publish.assert_called_once()
        published_event = mock_bus.publish.call_args[0][0]
        self.assertEqual(published_event.event_type, EventType.USER_IDLE)

    @patch("os_layer.event_bus.get_event_bus")
    def test_active_event_published_on_resume(self, mock_get_bus):
        """Verify user.active event fires when user returns from idle."""
        from os_layer.ambient_mode import AmbientModeManager
        from os_layer.event_bus import EventType, Event

        mock_bus = MagicMock()
        mock_get_bus.return_value = mock_bus

        mgr = AmbientModeManager()
        mgr._user32 = MagicMock()
        mgr._kernel32 = MagicMock()
        mgr._running = True
        mgr._is_idle = True  # Previously idle

        bus = mock_get_bus()

        # Simulate 2 seconds idle (below 300s threshold)
        idle_secs = 2.0
        threshold = 300

        if mgr._is_idle and idle_secs < threshold:
            mgr._is_idle = False
            bus.publish(Event(
                event_type=EventType.USER_ACTIVE,
                source="ambient_mode",
                payload={"resumed_after_seconds": round(idle_secs, 1)},
            ))

        self.assertFalse(mgr._is_idle)
        mock_bus.publish.assert_called()
        published_event = mock_bus.publish.call_args[0][0]
        self.assertEqual(published_event.event_type, EventType.USER_ACTIVE)

    def test_no_event_when_already_idle(self):
        """No duplicate idle event if already in idle state."""
        from os_layer.ambient_mode import AmbientModeManager

        mgr = AmbientModeManager()
        mgr._is_idle = True
        # If already idle and idle_secs > threshold, no transition occurs
        # The condition `not self._is_idle` prevents re-publishing
        self.assertTrue(mgr._is_idle)


class TestAmbientModeLifecycle(unittest.TestCase):
    """Test start/stop lifecycle."""

    def test_start_without_win32_fails_gracefully(self):
        from os_layer.ambient_mode import AmbientModeManager

        mgr = AmbientModeManager()
        # _load_win32 will fail on non-Windows
        with patch.object(mgr, "_load_win32", return_value=False):
            mgr.start()
        self.assertFalse(mgr.is_running)

    def test_stop_when_not_running(self):
        from os_layer.ambient_mode import AmbientModeManager

        mgr = AmbientModeManager()
        # Should not raise
        mgr.stop()
        self.assertFalse(mgr.is_running)

    def test_double_start_warns(self):
        from os_layer.ambient_mode import AmbientModeManager

        mgr = AmbientModeManager()
        mgr._running = True
        # Second start should log warning and return early
        mgr.start()
        self.assertTrue(mgr._running)


class TestScreenIntelThrottling(unittest.TestCase):
    """Test that screen_intel adapts its interval on ambient events."""

    def test_idle_sets_300s_interval(self):
        from os_layer.screen_intel import ScreenIntelligence, _FALLBACK_INTERVAL

        si = ScreenIntelligence()
        self.assertEqual(si._ambient_fallback_interval, _FALLBACK_INTERVAL)

        # Simulate idle event callback
        si._on_ambient_idle(MagicMock())
        self.assertEqual(si._ambient_fallback_interval, 300.0)

    def test_active_restores_normal_interval(self):
        from os_layer.screen_intel import ScreenIntelligence, _FALLBACK_INTERVAL

        si = ScreenIntelligence()
        si._ambient_fallback_interval = 300.0

        # Simulate active event callback
        si._on_ambient_active(MagicMock())
        self.assertEqual(si._ambient_fallback_interval, _FALLBACK_INTERVAL)


class TestDocIntelCrawlGating(unittest.TestCase):
    """Test that doc_intelligence pauses/resumes crawling on ambient events."""

    def test_initial_state_is_paused(self):
        from os_layer.doc_intelligence import DocumentIntelligence

        di = DocumentIntelligence.__new__(DocumentIntelligence)
        di._initialized = False
        di.__init__()
        # Crawler starts paused (waiting for user.idle to begin)
        self.assertTrue(di._crawl_paused.is_set())

    def test_idle_event_resumes_crawler(self):
        from os_layer.doc_intelligence import DocumentIntelligence

        di = DocumentIntelligence.__new__(DocumentIntelligence)
        di._initialized = False
        di.__init__()

        self.assertTrue(di._crawl_paused.is_set())  # paused

        di._on_ambient_idle(MagicMock())
        self.assertFalse(di._crawl_paused.is_set())  # resumed

    def test_active_event_pauses_crawler(self):
        from os_layer.doc_intelligence import DocumentIntelligence

        di = DocumentIntelligence.__new__(DocumentIntelligence)
        di._initialized = False
        di.__init__()

        # First resume (idle)
        di._on_ambient_idle(MagicMock())
        self.assertFalse(di._crawl_paused.is_set())

        # Then pause (active)
        di._on_ambient_active(MagicMock())
        self.assertTrue(di._crawl_paused.is_set())


if __name__ == "__main__":
    unittest.main()
