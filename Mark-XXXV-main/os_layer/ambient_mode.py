"""
os_layer/ambient_mode.py — Low-Power Ambient State Manager
===========================================================
Manages user idle/active state transitions using Windows
GetLastInputInfo. Publishes ``user.idle`` and ``user.active``
events via the central event bus so that other modules
(screen_intel, doc_intelligence) can independently adapt their
behaviour.

Single Responsibility:
    Detect idle state → publish events. Nothing else.
    Each subscriber module decides how to react to idle/active.

Hardware Budget:
    One ctypes call every ``AMBIENT_MONITOR_INTERVAL_SEC`` (default 5s).
    CPU cost: effectively zero.

Rollover Safety:
    GetLastInputInfo returns a DWORD tick count that wraps at
    ~49.7 days of uptime. The idle calculation applies an
    unsigned 32-bit mask to prevent negative-equivalent results.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes
import logging
import threading
import time
from typing import Optional

from core.config import config

logger = logging.getLogger(__name__)


# ── Win32 Structures ────────────────────────────────────────────────────────

class _LASTINPUTINFO(ctypes.Structure):
    """Win32 LASTINPUTINFO structure for GetLastInputInfo."""
    _fields_ = [
        ("cbSize", ctypes.wintypes.UINT),
        ("dwTime", ctypes.wintypes.DWORD),
    ]


# ── Ambient Mode Manager ────────────────────────────────────────────────────

class AmbientModeManager:
    """
    Monitors user input activity via Windows API and publishes
    ``user.idle`` / ``user.active`` events on the central event bus.

    State Machine:
        ACTIVE  →  idle_time > threshold  →  IDLE   (publish user.idle)
        IDLE    →  input detected          →  ACTIVE (publish user.active)
    """

    def __init__(self) -> None:
        self._running = False
        self._is_idle = False
        self._monitor_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

        # Win32 API handles — loaded lazily
        self._user32 = None
        self._kernel32 = None

    # ── Public API ───────────────────────────────────────────────────────

    def start(self) -> None:
        """Start the idle-detection daemon thread."""
        if self._running:
            logger.warning("[AmbientMode] Already running")
            return

        if not self._load_win32():
            logger.error("[AmbientMode] Win32 API unavailable — cannot start")
            return

        self._running = True
        self._is_idle = False
        self._stop_event.clear()

        self._monitor_thread = threading.Thread(
            target=self._monitor_loop,
            daemon=True,
            name="ambient-mode-monitor",
        )
        self._monitor_thread.start()
        logger.info("[AmbientMode] ✅ Idle detection monitor started")

    def stop(self) -> None:
        """Stop the daemon thread."""
        if not self._running:
            return
        self._running = False
        self._stop_event.set()
        if self._monitor_thread:
            self._monitor_thread.join(timeout=10.0)
            self._monitor_thread = None
        logger.info("[AmbientMode] 🛑 Monitor stopped")

    @property
    def is_idle(self) -> bool:
        """Whether the user is currently idle."""
        return self._is_idle

    @property
    def is_running(self) -> bool:
        return self._running

    # ── Win32 Loader ─────────────────────────────────────────────────────

    def _load_win32(self) -> bool:
        """Load user32.dll and kernel32.dll via ctypes."""
        if self._user32 is not None:
            return True
        try:
            self._user32 = ctypes.windll.user32
            self._kernel32 = ctypes.windll.kernel32
            return True
        except (OSError, AttributeError) as e:
            logger.warning(f"[AmbientMode] ctypes Win32 load failed: {e}")
            return False

    # ── Idle Time Measurement ────────────────────────────────────────────

    def _get_idle_seconds(self) -> float:
        """
        Return seconds since last keyboard/mouse input.

        Uses GetLastInputInfo for input timestamp and
        GetTickCount for current tick. Both return DWORD
        (unsigned 32-bit) milliseconds since boot.

        The bitwise AND with 0xFFFFFFFF ensures correct
        unsigned subtraction even after the ~49.7 day rollover.
        """
        lii = _LASTINPUTINFO()
        lii.cbSize = ctypes.sizeof(_LASTINPUTINFO)

        if not self._user32.GetLastInputInfo(ctypes.byref(lii)):
            return 0.0

        current_tick = self._kernel32.GetTickCount()
        idle_ms = (current_tick - lii.dwTime) & 0xFFFFFFFF
        return idle_ms / 1000.0

    # ── Monitor Loop ─────────────────────────────────────────────────────

    def _monitor_loop(self) -> None:
        """
        Daemon loop: poll idle time at configured interval.

        Transitions:
            ACTIVE → IDLE:   publish user.idle when idle_seconds > threshold
            IDLE   → ACTIVE: publish user.active on first input detection
        """
        from os_layer.event_bus import get_event_bus, EventType, Event

        bus = get_event_bus()
        threshold = config.AMBIENT_IDLE_THRESHOLD_SEC
        interval = config.AMBIENT_MONITOR_INTERVAL_SEC

        logger.info(
            f"[AmbientMode] Monitoring with threshold={threshold}s, "
            f"interval={interval}s"
        )

        while not self._stop_event.is_set():
            try:
                idle_secs = self._get_idle_seconds()

                if not self._is_idle and idle_secs >= threshold:
                    # Transition: ACTIVE → IDLE
                    self._is_idle = True
                    bus.publish(Event(
                        event_type=EventType.USER_IDLE,
                        source="ambient_mode",
                        payload={"idle_seconds": round(idle_secs, 1)},
                        priority=3,
                    ))
                    logger.info(
                        f"[AmbientMode] 💤 User idle "
                        f"({idle_secs:.0f}s > {threshold}s threshold)"
                    )

                elif self._is_idle and idle_secs < threshold:
                    # Transition: IDLE → ACTIVE
                    self._is_idle = False
                    bus.publish(Event(
                        event_type=EventType.USER_ACTIVE,
                        source="ambient_mode",
                        payload={"resumed_after_seconds": round(idle_secs, 1)},
                        priority=2,
                    ))
                    logger.info("[AmbientMode] ⚡ User active again")

            except Exception as e:
                logger.error(f"[AmbientMode] Monitor loop error: {e}")

            self._stop_event.wait(timeout=interval)


# ── Singleton ────────────────────────────────────────────────────────────────

_instance: Optional[AmbientModeManager] = None
_instance_lock = threading.Lock()


def get_ambient_mode() -> AmbientModeManager:
    """Thread-safe singleton accessor."""
    global _instance
    if _instance is None:
        with _instance_lock:
            if _instance is None:
                _instance = AmbientModeManager()
    return _instance
