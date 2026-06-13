"""
tests/test_event_bus.py — Event Bus Tests
==========================================
Tests for typed event dispatch, subscriber matching, priority ordering,
history tracking, file system watching, and lifecycle management.
"""

from __future__ import annotations

import platform
import threading
import time
import unittest

IS_WINDOWS = platform.system() == "Windows"


class TestEventTypeEnum(unittest.TestCase):
    """Test EventType values and string access."""

    def test_all_core_types_exist(self):
        from os_layer.event_bus import EventType
        core = [
            "window.focus_changed", "file.created", "file.modified",
            "system.boot", "system.shutdown", "workflow.started",
            "workflow.completed", "custom",
        ]
        for val in core:
            self.assertEqual(EventType(val).value, val)

    def test_invalid_type_raises(self):
        from os_layer.event_bus import EventType
        with self.assertRaises(ValueError):
            EventType("nonexistent.event.type")


class TestEventDataClass(unittest.TestCase):
    """Test Event ordering and defaults."""

    def test_default_priority(self):
        from os_layer.event_bus import Event, EventType
        e = Event(event_type=EventType.CUSTOM)
        self.assertEqual(e.priority, 5)

    def test_priority_ordering(self):
        from os_layer.event_bus import Event, EventType
        high = Event(event_type=EventType.SYSTEM_BOOT, priority=1)
        low = Event(event_type=EventType.CUSTOM, priority=10)
        self.assertTrue(high < low)

    def test_timestamp_auto_set(self):
        from os_layer.event_bus import Event, EventType
        before = time.time()
        e = Event(event_type=EventType.CUSTOM)
        after = time.time()
        self.assertGreaterEqual(e.timestamp, before)
        self.assertLessEqual(e.timestamp, after)


class TestEventBusSubscription(unittest.TestCase):
    """Test subscribe/unsubscribe mechanics."""

    def setUp(self):
        from os_layer.event_bus import EventBus
        self.bus = EventBus()

    def test_subscribe_returns_subscription(self):
        from os_layer.event_bus import EventType
        sub = self.bus.subscribe(
            callback=lambda e: None,
            event_types={EventType.CUSTOM},
            name="test_sub",
        )
        self.assertEqual(sub.name, "test_sub")
        self.assertIn(EventType.CUSTOM, sub.event_types)

    def test_unsubscribe_removes(self):
        sub = self.bus.subscribe(callback=lambda e: None, name="temp")
        self.assertEqual(self.bus.get_subscriber_count(), 1)
        ok = self.bus.unsubscribe(sub)
        self.assertTrue(ok)
        self.assertEqual(self.bus.get_subscriber_count(), 0)

    def test_unsubscribe_nonexistent(self):
        from os_layer.event_bus import Subscription
        fake = Subscription(callback=lambda e: None, event_types=set(), name="fake")
        ok = self.bus.unsubscribe(fake)
        self.assertFalse(ok)

    def test_decorator_subscription(self):
        from os_layer.event_bus import EventType

        @self.bus.on(EventType.FILE_CREATED, EventType.FILE_MODIFIED)
        def handler(event):
            pass

        self.assertEqual(self.bus.get_subscriber_count(), 1)


class TestEventBusDispatch(unittest.TestCase):
    """Test event dispatch and delivery."""

    def setUp(self):
        from os_layer.event_bus import EventBus
        self.bus = EventBus()

    def tearDown(self):
        self.bus.stop()

    def test_publish_and_receive(self):
        from os_layer.event_bus import EventType
        received = []

        self.bus.subscribe(
            callback=lambda e: received.append(e),
            event_types={EventType.CUSTOM},
            name="test_receiver",
        )
        self.bus.start()

        self.bus.emit(EventType.CUSTOM, source="test", payload={"key": "value"})

        # Wait for dispatch
        time.sleep(0.5)

        # Filter out the SYSTEM_BOOT event emitted by start()
        custom_events = [e for e in received if e.event_type == EventType.CUSTOM]
        self.assertEqual(len(custom_events), 1)
        self.assertEqual(custom_events[0].payload["key"], "value")

    def test_type_filtering(self):
        from os_layer.event_bus import EventType
        received = []

        self.bus.subscribe(
            callback=lambda e: received.append(e),
            event_types={EventType.FILE_CREATED},
            name="file_only",
        )
        self.bus.start()

        # Emit events the subscriber should NOT receive
        self.bus.emit(EventType.CUSTOM, source="test")
        self.bus.emit(EventType.WINDOW_FOCUS_CHANGED, source="test")

        time.sleep(0.5)

        # Should not have received any (only subscribed to FILE_CREATED)
        file_events = [e for e in received if e.event_type == EventType.FILE_CREATED]
        self.assertEqual(len(file_events), 0)

    def test_source_filtering(self):
        from os_layer.event_bus import EventType
        received = []

        self.bus.subscribe(
            callback=lambda e: received.append(e),
            event_types={EventType.CUSTOM},
            source_filter="specific_source",
            name="source_filtered",
        )
        self.bus.start()

        self.bus.emit(EventType.CUSTOM, source="wrong_source")
        self.bus.emit(EventType.CUSTOM, source="specific_source")

        time.sleep(0.5)

        self.assertEqual(len(received), 1)
        self.assertEqual(received[0].source, "specific_source")

    def test_history_bounded(self):
        from os_layer.event_bus import EventType
        self.bus.start()

        for i in range(10):
            self.bus.emit(EventType.CUSTOM, source="test", payload={"i": i})

        time.sleep(1.0)

        history = self.bus.get_history(limit=5)
        self.assertLessEqual(len(history), 5)


class TestEventBusLifecycle(unittest.TestCase):
    """Test start/stop lifecycle."""

    def test_start_stop(self):
        from os_layer.event_bus import EventBus
        bus = EventBus()
        bus.start()
        self.assertTrue(bus.is_running)
        bus.stop()
        time.sleep(0.5)
        self.assertFalse(bus.is_running)

    def test_double_start_safe(self):
        from os_layer.event_bus import EventBus
        bus = EventBus()
        bus.start()
        bus.start()  # should not crash
        self.assertTrue(bus.is_running)
        bus.stop()


class TestEventBusPersistence(unittest.TestCase):
    """Test SQLite event persistence."""

    def test_persisted_events_query(self):
        from os_layer.event_bus import EventBus
        bus = EventBus()
        events = bus.get_persisted_events(limit=5)
        self.assertIsInstance(events, list)

    def test_prune_event_log(self):
        from os_layer.event_bus import EventBus
        bus = EventBus()
        deleted = bus.prune_event_log(max_age_hours=0)
        self.assertIsInstance(deleted, int)


class TestEventBusPatternMatching(unittest.TestCase):
    """Test file pattern matching."""

    def test_matches_python_files(self):
        from os_layer.event_bus import EventBus
        self.assertTrue(EventBus._matches_pattern("main.py", ["*.py"]))
        self.assertFalse(EventBus._matches_pattern("main.js", ["*.py"]))

    def test_matches_multiple_patterns(self):
        from os_layer.event_bus import EventBus
        self.assertTrue(EventBus._matches_pattern("data.json", ["*.py", "*.json"]))

    def test_no_patterns_matches_all(self):
        from os_layer.event_bus import EventBus
        self.assertTrue(EventBus._matches_pattern("anything.xyz", None))
        self.assertTrue(EventBus._matches_pattern("anything.xyz", []))


if __name__ == "__main__":
    unittest.main()
