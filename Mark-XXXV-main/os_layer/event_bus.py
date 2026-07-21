"""
os_layer/event_bus.py — Event-Driven Reactive System
======================================================
Central event bus for JARVIS OS-level reactivity. Components publish
events, subscribers react asynchronously. Decouples producers from
consumers — screen_intel, window_manager, and workflow_recorder all
communicate through this bus.

Architecture:
    - Typed events via EventType enum — no stringly-typed dispatch.
    - Sync and async subscriber support.
    - Priority queue for event ordering.
    - File system monitoring via watchdog (Phase 2).
    - Bounded event history for replay/debugging.
    - Daemon dispatch thread — zero overhead when idle.

Thread Safety:
    All public methods are thread-safe. The dispatch loop runs
    on a dedicated daemon thread. Subscribers may be called from
    the dispatch thread — keep handlers fast or offload to async.

Integration:
    The event bus is initialized once in main.py runner() and
    shared via the singleton accessor `get_event_bus()`.
"""

from __future__ import annotations

import asyncio
import json
import logging
import queue
import sqlite3
import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from enum import Enum, auto
from pathlib import Path
from typing import Any, Callable, Optional, Union

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────

DB_PATH = Path(__file__).resolve().parent.parent / "memory" / "agent_episodes.db"
_MAX_HISTORY = 200          # bounded event history for replay
_DISPATCH_TIMEOUT = 0.25    # seconds — dispatcher wakes every 250ms at most

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS event_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp   REAL    NOT NULL,
    event_type  TEXT    NOT NULL,
    source      TEXT    NOT NULL DEFAULT '',
    payload     TEXT    DEFAULT '{}',
    handled     INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_event_log_ts
    ON event_log(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_event_log_type
    ON event_log(event_type);
"""


# ── Event Types ──────────────────────────────────────────────────────────────

class EventType(str, Enum):
    """All event types flowing through the bus."""

    # Screen events (from screen_intel)
    WINDOW_FOCUS_CHANGED    = "window.focus_changed"
    WINDOW_OPENED           = "window.opened"
    WINDOW_CLOSED           = "window.closed"
    SCREEN_TEXT_CHANGED      = "screen.text_changed"

    # File system events (from watchdog)
    FILE_CREATED            = "file.created"
    FILE_MODIFIED           = "file.modified"
    FILE_DELETED            = "file.deleted"
    FILE_MOVED              = "file.moved"

    # Workspace events
    WORKSPACE_SAVED         = "workspace.saved"
    WORKSPACE_RESTORED      = "workspace.restored"
    TASK_ADDED              = "task.added"
    TASK_COMPLETED          = "task.completed"

    # User activity events
    USER_IDLE               = "user.idle"
    USER_ACTIVE             = "user.active"
    CLIPBOARD_CHANGED       = "clipboard.changed"

    # System events
    SYSTEM_BOOT             = "system.boot"
    SYSTEM_SHUTDOWN         = "system.shutdown"
    SYSTEM_ERROR            = "system.error"
    SCHEDULE_TRIGGER        = "schedule.trigger"

    # Workflow events (from workflow_recorder)
    WORKFLOW_STARTED        = "workflow.started"
    WORKFLOW_STEP_RECORDED  = "workflow.step_recorded"
    WORKFLOW_COMPLETED      = "workflow.completed"
    WORKFLOW_REPLAY_STARTED = "workflow.replay_started"

    # Document Intelligence events (Phase 3)
    DOC_INDEXED             = "doc.indexed"

    # Communication events (Phase 3)
    EMAIL_RECEIVED          = "email.received"
    CALENDAR_EVENT_REMINDER = "calendar.event_reminder"

    # Knowledge Graph events (Phase 4)
    KG_SUGGEST              = "kg.suggest"

    # Browser Bridge events (OS Layer 2 — Phase 2)
    BROWSER_EXT_CONNECTED       = "browser.extension_connected"
    BROWSER_EXT_DISCONNECTED    = "browser.extension_disconnected"

    # Agentic Shell events (OS Layer 2 — Phase 3)
    SHELL_TIMEOUT_RECOVERY      = "shell.timeout_recovery"
    SHELL_COMMAND_EXECUTED       = "shell.command_executed"

    # Goal Orchestration events (OS Layer 2 — Phase 4)
    GOAL_STARTED            = "goal.started"
    GOAL_PROGRESS           = "goal.progress"
    GOAL_SUSPENDED          = "goal.suspended"
    GOAL_APPROVAL           = "goal.approval"
    GOAL_COMPLETED          = "goal.completed"
    GOAL_FAILED             = "goal.failed"

    # Custom / extension events
    CUSTOM                  = "custom"


# ── Data Classes ─────────────────────────────────────────────────────────────

@dataclass
class Event:
    """Immutable event flowing through the bus."""
    event_type: EventType
    source: str = ""
    payload: dict = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)
    priority: int = 5          # 1=highest, 10=lowest (for queue ordering)

    def __lt__(self, other: Event) -> bool:
        """Priority queue ordering: lower priority number = higher priority."""
        return self.priority < other.priority


@dataclass
class Subscription:
    """A registered event subscriber."""
    callback: Callable
    event_types: set[EventType]
    source_filter: Optional[str] = None
    is_async: bool = False
    name: str = ""


# ── SQLite Helpers ───────────────────────────────────────────────────────────

_db_local = threading.local()
_schema_initialized = False
_schema_lock = threading.Lock()


def _get_conn() -> sqlite3.Connection:
    conn = getattr(_db_local, "conn", None)
    if conn is None:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(
            str(DB_PATH),
            check_same_thread=False,
            timeout=10.0,
        )
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.row_factory = sqlite3.Row
        _db_local.conn = conn
    return conn


def _ensure_schema() -> None:
    global _schema_initialized
    if _schema_initialized:
        return
    with _schema_lock:
        if _schema_initialized:
            return
        conn = _get_conn()
        conn.executescript(_SCHEMA_SQL)
        conn.commit()
        _schema_initialized = True


# ── Event Bus ────────────────────────────────────────────────────────────────

class EventBus:
    """
    Central event bus with typed dispatch, priority ordering,
    and persistent event logging.

    Usage:
        bus = EventBus()
        bus.start()

        # Subscribe to specific events
        bus.subscribe(
            callback=lambda e: print(f"Focus → {e.payload}"),
            event_types={EventType.WINDOW_FOCUS_CHANGED},
            name="focus_logger",
        )

        # Publish an event
        bus.publish(Event(
            event_type=EventType.WINDOW_FOCUS_CHANGED,
            source="screen_intel",
            payload={"app": "VS Code", "title": "main.py"},
        ))

        # Start file system watcher
        bus.watch_directory("C:/Projects/my_app")

        bus.stop()
    """

    def __init__(self):
        self._queue: queue.PriorityQueue[Event] = queue.PriorityQueue()
        self._subscribers: list[Subscription] = []
        self._sub_lock = threading.Lock()
        self._dispatch_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._history: list[Event] = []
        self._history_lock = threading.Lock()
        self._watchers: list[Any] = []  # watchdog observers

        _ensure_schema()

    # ── Publish ──────────────────────────────────────────────────────────

    def publish(self, event: Event) -> None:
        """
        Publish an event to the bus. Thread-safe, non-blocking.
        Events are queued and dispatched by the background thread.
        """
        self._queue.put(event)

    def emit(
        self,
        event_type: EventType,
        source: str = "",
        payload: dict | None = None,
        priority: int = 5,
    ) -> None:
        """Convenience method: create and publish an event in one call."""
        self.publish(Event(
            event_type=event_type,
            source=source,
            payload=payload or {},
            priority=priority,
        ))

    # ── Subscribe ────────────────────────────────────────────────────────

    def subscribe(
        self,
        callback: Callable,
        event_types: set[EventType] | None = None,
        source_filter: Optional[str] = None,
        name: str = "",
    ) -> Subscription:
        """
        Subscribe to events. If event_types is None, receives ALL events.

        Args:
            callback: Function(Event) or async coroutine(Event).
            event_types: Set of EventType to filter on (None = all).
            source_filter: Only receive events from this source.
            name: Human-readable subscriber name for debugging.

        Returns:
            The Subscription object (use for unsubscribe).
        """
        is_async = asyncio.iscoroutinefunction(callback)
        sub = Subscription(
            callback=callback,
            event_types=event_types or set(),
            source_filter=source_filter,
            is_async=is_async,
            name=name or callback.__name__,
        )
        with self._sub_lock:
            self._subscribers.append(sub)
        logger.debug(f"[EventBus] Subscriber added: {sub.name}")
        return sub

    def unsubscribe(self, subscription: Subscription) -> bool:
        """Remove a subscriber."""
        with self._sub_lock:
            try:
                self._subscribers.remove(subscription)
                logger.debug(f"[EventBus] Subscriber removed: {subscription.name}")
                return True
            except ValueError:
                return False

    def on(self, *event_types: EventType, source: str = ""):
        """
        Decorator for subscribing to events.

        @bus.on(EventType.WINDOW_FOCUS_CHANGED)
        def handle_focus(event: Event):
            print(event.payload)
        """
        def decorator(func: Callable) -> Callable:
            self.subscribe(
                callback=func,
                event_types=set(event_types),
                source_filter=source or None,
                name=func.__name__,
            )
            return func
        return decorator

    # ── Lifecycle ────────────────────────────────────────────────────────

    def start(self) -> None:
        """Start the dispatch thread."""
        if self._dispatch_thread and self._dispatch_thread.is_alive():
            logger.warning("[EventBus] Already running")
            return

        self._stop_event.clear()
        self._dispatch_thread = threading.Thread(
            target=self._dispatch_loop,
            daemon=True,
            name="event-bus-dispatch",
        )
        self._dispatch_thread.start()
        logger.info("[EventBus] ✅ Dispatch thread started")

        # Emit boot event
        self.emit(EventType.SYSTEM_BOOT, source="event_bus")

    def stop(self) -> None:
        """Stop the dispatch thread and all watchers."""
        # Emit shutdown event (dispatched synchronously)
        self.emit(EventType.SYSTEM_SHUTDOWN, source="event_bus", priority=1)

        # Wait briefly for shutdown event to be dispatched
        time.sleep(0.3)

        self._stop_event.set()

        # Stop file watchers
        for watcher in self._watchers:
            try:
                watcher.stop()
                watcher.join(timeout=2.0)
            except Exception:
                pass
        self._watchers.clear()

        if self._dispatch_thread:
            self._dispatch_thread.join(timeout=3.0)
            self._dispatch_thread = None

        logger.info("[EventBus] 🛑 Stopped")

    @property
    def is_running(self) -> bool:
        return (self._dispatch_thread is not None
                and self._dispatch_thread.is_alive())

    # ── File System Watching ─────────────────────────────────────────────

    def watch_directory(
        self,
        path: str,
        recursive: bool = True,
        patterns: list[str] | None = None,
    ) -> bool:
        """
        Watch a directory for file system changes.
        Emits FILE_CREATED, FILE_MODIFIED, FILE_DELETED, FILE_MOVED events.

        Args:
            path: Directory path to watch.
            recursive: Watch subdirectories.
            patterns: Glob patterns to filter (e.g., ["*.py", "*.json"]).

        Returns:
            True if watcher started successfully.
        """
        try:
            # type: ignore comments suppress IDE false-positives for optional dynamic imports
            from watchdog.observers import Observer  # type: ignore
            from watchdog.events import FileSystemEventHandler, FileSystemEvent  # type: ignore

            class EventBusFileWatcher(FileSystemEventHandler):
                def __init__(self, event_bus, glob_patterns):
                    self.bus = event_bus
                    self.patterns = glob_patterns

                def _emit(self, event_type: EventType, fs_event: FileSystemEvent):
                    self.bus.emit(
                        event_type=event_type,
                        source="file_watcher",
                        payload={
                            "path": fs_event.src_path,
                            "is_directory": fs_event.is_directory,
                            "dest_path": getattr(fs_event, "dest_path", ""),
                        },
                    )

                def on_created(self, event):
                    if self.bus._matches_pattern(event.src_path, self.patterns):
                        self._emit(EventType.FILE_CREATED, event)

                def on_modified(self, event):
                    if self.bus._matches_pattern(event.src_path, self.patterns):
                        self._emit(EventType.FILE_MODIFIED, event)

                def on_deleted(self, event):
                    if self.bus._matches_pattern(event.src_path, self.patterns):
                        self._emit(EventType.FILE_DELETED, event)

                def on_moved(self, event):
                    if self.bus._matches_pattern(event.src_path, self.patterns):
                        self._emit(EventType.FILE_MOVED, event)

            observer = Observer()
            observer.schedule(EventBusFileWatcher(self, patterns), path, recursive=recursive)
            observer.daemon = True
            observer.start()
            self._watchers.append(observer)

            logger.info(f"[EventBus] 👁️ Watching: {path}")
            return True

        except ImportError:
            logger.warning("[EventBus] watchdog not installed — file watching disabled")
            return False
        except Exception as e:
            logger.warning(f"[EventBus] watch_directory failed: {e}")
            return False

    @staticmethod
    def _matches_pattern(filepath: str, patterns: list[str] | None) -> bool:
        """Check if a filepath matches any of the glob patterns."""
        if not patterns:
            return True
        from fnmatch import fnmatch
        name = Path(filepath).name
        return any(fnmatch(name, p) for p in patterns)

    # ── Query / History ──────────────────────────────────────────────────

    def get_history(
        self,
        event_type: EventType | None = None,
        limit: int = 20,
    ) -> list[Event]:
        """Get recent events from in-memory history."""
        with self._history_lock:
            if event_type:
                filtered = [e for e in self._history if e.event_type == event_type]
            else:
                filtered = list(self._history)
            return filtered[-limit:]

    def get_persisted_events(
        self,
        event_type: str | None = None,
        limit: int = 20,
    ) -> list[dict]:
        """Query events from SQLite log."""
        conn = _get_conn()
        if event_type:
            rows = conn.execute(
                """SELECT * FROM event_log
                   WHERE event_type = ?
                   ORDER BY timestamp DESC LIMIT ?""",
                (event_type, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT * FROM event_log
                   ORDER BY timestamp DESC LIMIT ?""",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_subscriber_count(self) -> int:
        with self._sub_lock:
            return len(self._subscribers)

    def prune_event_log(self, max_age_hours: int = 48) -> int:
        """Delete old events from the persistent log."""
        cutoff = time.time() - (max_age_hours * 3600)
        try:
            conn = _get_conn()
            cursor = conn.execute(
                "DELETE FROM event_log WHERE timestamp < ?", (cutoff,)
            )
            conn.commit()
            deleted = cursor.rowcount
            if deleted:
                logger.info(f"[EventBus] Pruned {deleted} old events")
            return deleted
        except Exception as e:
            logger.warning(f"[EventBus] Prune failed: {e}")
            return 0

    # ── Private ──────────────────────────────────────────────────────────

    def _dispatch_loop(self) -> None:
        """
        Background dispatch loop. Pulls events from the priority queue
        and delivers to matching subscribers.
        """
        while not self._stop_event.is_set():
            try:
                event = self._queue.get(timeout=_DISPATCH_TIMEOUT)
            except queue.Empty:
                continue

            # Add to bounded history
            with self._history_lock:
                self._history.append(event)
                if len(self._history) > _MAX_HISTORY:
                    self._history = self._history[-_MAX_HISTORY:]

            # Persist to SQLite
            self._persist_event(event)

            # Deliver to matching subscribers
            with self._sub_lock:
                subs = list(self._subscribers)

            for sub in subs:
                if not self._matches_subscription(event, sub):
                    continue

                try:
                    if sub.is_async:
                        # Schedule async callback on the event loop
                        try:
                            loop = asyncio.get_event_loop()
                            if loop.is_running():
                                asyncio.run_coroutine_threadsafe(
                                    sub.callback(event), loop
                                )
                            else:
                                asyncio.run(sub.callback(event))
                        except RuntimeError:
                            # No event loop — run synchronously in new loop
                            asyncio.run(sub.callback(event))
                    else:
                        sub.callback(event)

                except Exception as cb_err:
                    logger.error(
                        f"[EventBus] Subscriber '{sub.name}' error "
                        f"on {event.event_type}: {cb_err}"
                    )

    @staticmethod
    def _matches_subscription(event: Event, sub: Subscription) -> bool:
        """Check if an event matches a subscription's filters."""
        # Type filter (empty set = match all)
        if sub.event_types and event.event_type not in sub.event_types:
            return False

        # Source filter
        if sub.source_filter and event.source != sub.source_filter:
            return False

        return True

    def _persist_event(self, event: Event) -> None:
        """Write event to SQLite log."""
        try:
            conn = _get_conn()
            conn.execute(
                """INSERT INTO event_log
                   (timestamp, event_type, source, payload, handled)
                   VALUES (?, ?, ?, ?, ?)""",
                (
                    event.timestamp,
                    event.event_type.value,
                    event.source,
                    json.dumps(event.payload),
                    1,
                ),
            )
            conn.commit()
        except Exception as e:
            logger.debug(f"[EventBus] Persist failed: {e}")


# ── Singleton ────────────────────────────────────────────────────────────────

_instance: Optional[EventBus] = None
_instance_lock = threading.Lock()


def get_event_bus() -> EventBus:
    """Thread-safe singleton accessor."""
    global _instance
    if _instance is None:
        with _instance_lock:
            if _instance is None:
                _instance = EventBus()
    return _instance
