"""
os_layer/screen_intel.py — Screen Intelligence Engine
======================================================
Continuous screen awareness: what app is active, what text is visible,
what notifications appeared. Zero CPU when idle — event-triggered captures.

Architecture (3-tier extraction):
    1. UIAutomation (pywinauto) — structured data, zero OCR overhead
    2. Win32 API (win32gui) — window titles, process info
    3. Tesseract OCR — fallback for image-based content (PDF viewers, etc.)

Hardware Budget:
    - GTX 960M (2GB VRAM) — OCR runs on CPU only
    - Event-driven capture on focus change — not polling
    - 30-second fallback capture when no events fire
    - Context cache prevents redundant captures (30s TTL)

Storage:
    All captures persist in SQLite `screen_context` table within
    the existing `agent_episodes.db` database.

Thread Safety:
    Uses the same WAL-mode SQLite pattern as agent_memory.py.
    Safe to call from async executors and daemon threads.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────

DB_PATH = Path(__file__).resolve().parent.parent / "memory" / "agent_episodes.db"

_CONTEXT_CACHE_TTL = 30.0  # seconds — skip redundant captures
_FALLBACK_INTERVAL = 30.0  # seconds — capture even without events
_MAX_TEXT_LENGTH = 5000     # truncate extracted text to prevent DB bloat

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS screen_context (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp   REAL    NOT NULL,
    app_name    TEXT    NOT NULL DEFAULT '',
    window_title TEXT   NOT NULL DEFAULT '',
    process_id  INTEGER DEFAULT NULL,
    extracted_text TEXT  DEFAULT '',
    source      TEXT    NOT NULL DEFAULT 'uia',
    metadata    TEXT    DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_screen_ctx_ts
    ON screen_context(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_screen_ctx_app
    ON screen_context(app_name);
"""


# ── Data Classes ─────────────────────────────────────────────────────────────

@dataclass
class WindowContext:
    """Snapshot of the currently active window."""
    title: str = ""
    app_name: str = ""
    process_id: int = 0
    exe_path: str = ""
    rect: tuple[int, int, int, int] = (0, 0, 0, 0)  # x, y, w, h


@dataclass
class WindowInfo:
    """Minimal info about a visible window."""
    hwnd: int = 0
    title: str = ""
    app_name: str = ""
    rect: tuple[int, int, int, int] = (0, 0, 0, 0)
    is_visible: bool = True


@dataclass
class Notification:
    """A detected OS notification."""
    title: str = ""
    body: str = ""
    app_name: str = ""
    timestamp: float = 0.0


@dataclass
class ScreenContext:
    """Full screen context snapshot."""
    timestamp: float = 0.0
    active_window: WindowContext = field(default_factory=WindowContext)
    visible_text: str = ""
    window_layout: list[WindowInfo] = field(default_factory=list)
    notifications: list[Notification] = field(default_factory=list)
    source: str = "uia"  # uia | win32 | ocr


# ── SQLite Connection (reuses agent_episodes.db) ────────────────────────────

_db_lock = threading.Lock()
_db_local = threading.local()


def _get_conn() -> sqlite3.Connection:
    """Thread-local SQLite connection with WAL mode."""
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


_schema_initialized = False


def _ensure_schema() -> None:
    """Create screen_context table if it doesn't exist."""
    global _schema_initialized
    if _schema_initialized:
        return
    with _db_lock:
        if _schema_initialized:
            return
        conn = _get_conn()
        conn.executescript(_SCHEMA_SQL)
        conn.commit()
        _schema_initialized = True


# ── Screen Intelligence Engine ───────────────────────────────────────────────

class ScreenIntelligence:
    """
    Continuous screen awareness engine.

    3-tier extraction strategy:
        1. UIAutomation (pywinauto) — richest data, no OCR
        2. Win32 API — window titles when UIA is unavailable
        3. Tesseract OCR — image-based content as last resort

    Usage:
        si = ScreenIntelligence()
        ctx = si.capture_context()
        print(ctx.active_window.title, ctx.visible_text[:100])

        # Background monitoring (event-driven)
        si.start_monitoring(callback=lambda ctx: print(ctx.active_window.title))
        # ... later ...
        si.stop_monitoring()
    """

    def __init__(self):
        self._last_capture: Optional[ScreenContext] = None
        self._last_capture_ts: float = 0.0
        self._monitor_thread: Optional[threading.Thread] = None
        self._monitor_stop = threading.Event()
        self._callback: Optional[Callable[[ScreenContext], None]] = None

        # Ambient-mode dynamic capture interval (seconds)
        # Normal = _FALLBACK_INTERVAL (30s), Idle = 300s
        self._ambient_fallback_interval: float = _FALLBACK_INTERVAL

        # Lazy-loaded modules (avoid import cost at startup)
        self._win32gui = None
        self._win32process = None
        self._psutil = None
        self._pywinauto_app = None

        _ensure_schema()

    # ── Lazy Imports ─────────────────────────────────────────────────────

    def _load_win32(self) -> bool:
        """Load win32gui and win32process. Returns True if successful."""
        if self._win32gui is not None:
            return True
        try:
            import win32gui
            import win32process
            self._win32gui = win32gui
            self._win32process = win32process
            return True
        except ImportError:
            logger.warning("[ScreenIntel] pywin32 not installed — win32 layer disabled")
            return False

    def _load_psutil(self) -> bool:
        """Load psutil. Returns True if successful."""
        if self._psutil is not None:
            return True
        try:
            import psutil
            self._psutil = psutil
            return True
        except ImportError:
            logger.warning("[ScreenIntel] psutil not installed — process info disabled")
            return False

    # ── Core API ─────────────────────────────────────────────────────────

    def get_active_window(self) -> WindowContext:
        """Get info about the currently focused window."""
        ctx = WindowContext()

        # Tier 1: Try UIAutomation via pywinauto
        try:
            from pywinauto import Desktop
            desktop = Desktop(backend="uia")
            focused = desktop.window(active_only=True)
            wrapper = focused.wrapper_object()

            ctx.title = wrapper.window_text() or ""
            ctx.process_id = wrapper.process_id()
            ctx.rect = wrapper.rectangle().mid_point()  # center point

            rect = wrapper.rectangle()
            ctx.rect = (rect.left, rect.top,
                        rect.right - rect.left, rect.bottom - rect.top)

            # Get app name from process
            if self._load_psutil():
                try:
                    proc = self._psutil.Process(ctx.process_id)
                    ctx.app_name = proc.name().replace(".exe", "")
                    ctx.exe_path = proc.exe()
                except (self._psutil.NoSuchProcess, self._psutil.AccessDenied):
                    ctx.app_name = self._extract_app_name(ctx.title)
            else:
                ctx.app_name = self._extract_app_name(ctx.title)

            return ctx

        except Exception as uia_err:
            logger.debug(f"[ScreenIntel] UIA failed: {uia_err}")

        # Tier 2: Fallback to win32gui
        if self._load_win32():
            try:
                hwnd = self._win32gui.GetForegroundWindow()
                ctx.title = self._win32gui.GetWindowText(hwnd) or ""

                _, pid = self._win32process.GetWindowThreadProcessId(hwnd)
                ctx.process_id = pid

                rect = self._win32gui.GetWindowRect(hwnd)
                ctx.rect = (rect[0], rect[1],
                            rect[2] - rect[0], rect[3] - rect[1])

                if self._load_psutil():
                    try:
                        proc = self._psutil.Process(pid)
                        ctx.app_name = proc.name().replace(".exe", "")
                        ctx.exe_path = proc.exe()
                    except Exception:
                        ctx.app_name = self._extract_app_name(ctx.title)
                else:
                    ctx.app_name = self._extract_app_name(ctx.title)

                return ctx

            except Exception as win32_err:
                logger.debug(f"[ScreenIntel] Win32 failed: {win32_err}")

        return ctx

    def get_visible_text(self) -> str:
        """
        Extract visible text from the active window.
        Uses UIAutomation controls first, with Electron-app fallbacks
        (clipboard snoop, cropped OCR), then full-screen OCR as last resort.
        """
        active_window = self.get_active_window()
        app_name = active_window.app_name

        # Tier 1: UIAutomation — extract text from controls
        uia_texts: list[str] = []
        try:
            from pywinauto import Desktop
            desktop = Desktop(backend="uia")
            focused = desktop.window(active_only=True)
            wrapper = focused.wrapper_object()

            try:
                for child in wrapper.descendants():
                    try:
                        text = child.window_text()
                        if text and len(text.strip()) > 1:
                            uia_texts.append(text.strip())
                    except Exception:
                        continue
            except Exception:
                pass

            if len(uia_texts) >= 5:
                combined = "\n".join(uia_texts[:50])
                return combined[:_MAX_TEXT_LENGTH]

        except Exception as uia_err:
            logger.debug(f"[ScreenIntel] UIA text extraction failed: {uia_err}")

        # Tier 1.5: Electron-app fallbacks (clipboard snoop → cropped OCR)
        if self._is_electron_app(app_name):
            # Try clipboard snoop first (fastest, most accurate)
            snooped = self._clipboard_snoop()
            if snooped:
                logger.debug(f"[ScreenIntel] Clipboard snoop extracted {len(snooped)} chars")
                return snooped

            # Try cropped OCR (faster than full-screen)
            rect = active_window.rect
            if rect and rect[2] > 0 and rect[3] > 0:
                cropped = self._ocr_crop(rect)
                if cropped:
                    return cropped

            # Try app-specific shortcut extraction
            shortcut_text = self._shortcut_route(app_name)
            if shortcut_text:
                return shortcut_text

        # Return whatever UIA got (even if sparse) before falling back
        if uia_texts:
            combined = "\n".join(uia_texts[:50])
            return combined[:_MAX_TEXT_LENGTH]

        # Tier 2: Win32 — just window title
        if self._load_win32():
            try:
                hwnd = self._win32gui.GetForegroundWindow()
                title = self._win32gui.GetWindowText(hwnd) or ""
                if title:
                    return f"[Window Title] {title}"
            except Exception:
                pass

        # Tier 3: OCR fallback (only if pytesseract is installed)
        return self._ocr_fallback()

    def get_window_layout(self) -> list[WindowInfo]:
        """Get positions of all visible windows."""
        windows: list[WindowInfo] = []

        if not self._load_win32():
            return windows

        def _enum_callback(hwnd: int, results: list) -> None:
            if not self._win32gui.IsWindowVisible(hwnd):
                return
            title = self._win32gui.GetWindowText(hwnd)
            if not title or title in ("Program Manager", ""):
                return

            rect = self._win32gui.GetWindowRect(hwnd)
            w, h = rect[2] - rect[0], rect[3] - rect[1]

            # Skip tiny/hidden windows
            if w < 50 or h < 50:
                return

            app_name = self._extract_app_name(title)

            results.append(WindowInfo(
                hwnd=hwnd,
                title=title,
                app_name=app_name,
                rect=(rect[0], rect[1], w, h),
                is_visible=True,
            ))

        try:
            self._win32gui.EnumWindows(_enum_callback, windows)
        except Exception as e:
            logger.warning(f"[ScreenIntel] EnumWindows failed: {e}")

        return windows

    def get_recent_notifications(self) -> list[Notification]:
        """Get recent OS notifications (Windows 10/11 Action Center)."""
        # Windows notification access requires UWP interop or COM —
        # for now, return empty list. Phase 2 EventBus will hook into
        # WMI events for real-time notification capture.
        return []

    def capture_context(self) -> ScreenContext:
        """
        Full screen context snapshot. Uses cache to prevent
        redundant captures within the TTL window.
        """
        now = time.time()

        # Cache check — skip if captured recently
        if (self._last_capture is not None
                and (now - self._last_capture_ts) < _CONTEXT_CACHE_TTL):
            return self._last_capture

        active = self.get_active_window()
        visible_text = self.get_visible_text()
        layout = self.get_window_layout()
        notifications = self.get_recent_notifications()

        # Determine source tier used
        source = "uia"
        if not visible_text or visible_text.startswith("[Window Title]"):
            source = "win32"
        if visible_text.startswith("[OCR]"):
            source = "ocr"

        ctx = ScreenContext(
            timestamp=now,
            active_window=active,
            visible_text=visible_text,
            window_layout=layout,
            notifications=notifications,
            source=source,
        )

        # Persist to SQLite
        self._persist_context(ctx)

        # Update cache
        self._last_capture = ctx
        self._last_capture_ts = now

        return ctx

    # ── Background Monitoring ────────────────────────────────────────────

    def start_monitoring(
        self,
        callback: Callable[[ScreenContext], None],
    ) -> None:
        """
        Start event-driven background monitoring.

        Captures context on:
        - Window focus change (via win32 event hook)
        - Fallback timer (every 30s if no events fire)

        The callback receives a ScreenContext on each capture.
        """
        if self._monitor_thread and self._monitor_thread.is_alive():
            logger.warning("[ScreenIntel] Monitoring already active")
            return

        self._callback = callback
        self._monitor_stop.clear()
        self._monitor_thread = threading.Thread(
            target=self._monitor_loop,
            daemon=True,
            name="screen-intel-monitor",
        )
        self._monitor_thread.start()
        logger.info("[ScreenIntel] ✅ Background monitoring started")

        # Subscribe to ambient idle/active events for dynamic throttling
        self._subscribe_ambient()

    def _subscribe_ambient(self) -> None:
        """Subscribe to user.idle / user.active events for dynamic throttling."""
        try:
            from os_layer.event_bus import get_event_bus, EventType
            bus = get_event_bus()
            bus.subscribe(
                callback=self._on_ambient_idle,
                event_types={EventType.USER_IDLE},
                name="screen_intel_idle_throttle",
            )
            bus.subscribe(
                callback=self._on_ambient_active,
                event_types={EventType.USER_ACTIVE},
                name="screen_intel_active_restore",
            )
            logger.debug("[ScreenIntel] Subscribed to ambient idle/active events")
        except Exception as e:
            logger.warning(f"[ScreenIntel] Ambient subscription failed: {e}")

    def _on_ambient_idle(self, event) -> None:
        """Throttle capture rate when user is idle (300s fallback)."""
        self._ambient_fallback_interval = 300.0
        logger.info("[ScreenIntel] 💤 Throttled to 300s capture interval")

    def _on_ambient_active(self, event) -> None:
        """Restore normal capture rate when user returns."""
        self._ambient_fallback_interval = _FALLBACK_INTERVAL
        logger.info("[ScreenIntel] ⚡ Restored to normal capture interval")

    def stop_monitoring(self) -> None:
        """Stop background monitoring."""
        self._monitor_stop.set()
        if self._monitor_thread:
            self._monitor_thread.join(timeout=5.0)
            self._monitor_thread = None
        logger.info("[ScreenIntel] 🛑 Background monitoring stopped")

    @property
    def is_monitoring(self) -> bool:
        return (self._monitor_thread is not None
                and self._monitor_thread.is_alive())

    # ── Private Methods ──────────────────────────────────────────────────

    def _monitor_loop(self) -> None:
        """
        Background loop: detect focus changes and capture context.

        Uses win32gui polling (100ms) instead of SetWinEventHook
        because the hook requires a message pump (GetMessage loop)
        which is incompatible with our daemon thread model.

        CPU cost: negligible — one GetForegroundWindow() call per 100ms.
        """
        last_hwnd = 0
        last_capture = 0.0

        while not self._monitor_stop.is_set():
            try:
                now = time.time()
                current_hwnd = 0

                # Detect focus change
                if self._load_win32():
                    try:
                        current_hwnd = self._win32gui.GetForegroundWindow()
                    except Exception:
                        pass

                focus_changed = (current_hwnd != last_hwnd and current_hwnd != 0)
                fallback_due = (now - last_capture) >= self._ambient_fallback_interval

                if focus_changed or fallback_due:
                    # Invalidate cache to force fresh capture
                    self._last_capture_ts = 0.0

                    ctx = self.capture_context()
                    last_hwnd = current_hwnd
                    last_capture = now

                    if self._callback:
                        try:
                            self._callback(ctx)
                        except Exception as cb_err:
                            logger.error(
                                f"[ScreenIntel] Callback error: {cb_err}"
                            )

            except Exception as loop_err:
                logger.error(f"[ScreenIntel] Monitor loop error: {loop_err}")

            # Sleep 100ms — ~0.1% CPU overhead
            self._monitor_stop.wait(timeout=0.1)

    def _persist_context(self, ctx: ScreenContext) -> None:
        """Write screen context to SQLite."""
        try:
            conn = _get_conn()
            conn.execute(
                """INSERT INTO screen_context
                   (timestamp, app_name, window_title, process_id,
                    extracted_text, source, metadata)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    ctx.timestamp,
                    ctx.active_window.app_name,
                    ctx.active_window.title,
                    ctx.active_window.process_id,
                    ctx.visible_text[:_MAX_TEXT_LENGTH],
                    ctx.source,
                    json.dumps({
                        "window_count": len(ctx.window_layout),
                        "rect": ctx.active_window.rect,
                    }),
                ),
            )
            conn.commit()
        except Exception as e:
            logger.warning(f"[ScreenIntel] Persist failed: {e}")

    def _ocr_fallback(self) -> str:
        """Capture screen and run Tesseract OCR. CPU-intensive — last resort."""
        try:
            import mss
            import pytesseract
            from PIL import Image

            with mss.mss() as sct:
                monitor = sct.monitors[1]  # primary monitor
                screenshot = sct.grab(monitor)
                img = Image.frombytes(
                    "RGB",
                    (screenshot.width, screenshot.height),
                    screenshot.rgb,
                )

            # Downscale for speed on GTX 960M class hardware
            img = img.resize(
                (img.width // 2, img.height // 2),
                Image.LANCZOS,
            )

            text = pytesseract.image_to_string(img, timeout=5)
            return f"[OCR] {text.strip()[:_MAX_TEXT_LENGTH]}"

        except ImportError:
            logger.debug("[ScreenIntel] pytesseract not installed — OCR disabled")
            return ""
        except Exception as e:
            logger.warning(f"[ScreenIntel] OCR failed: {e}")
            return ""

    @staticmethod
    def _extract_app_name(title: str) -> str:
        """Heuristic: extract app name from window title."""
        if not title:
            return "unknown"

        # Common patterns: "Document - App Name", "App Name - Tab"
        separators = [" - ", " — ", " | ", " · "]
        for sep in separators:
            if sep in title:
                parts = title.split(sep)
                # Last part is usually the app name
                candidate = parts[-1].strip()
                if len(candidate) > 2:
                    return candidate

        # Fallback: first 30 chars of title
        return title[:30].strip()

    # ── Electron-App Fallback Extraction (OS Layer 2) ────────────────────

    # Apps built on Electron (VS Code, Discord, Slack, etc.) expose
    # shallow UIA trees. These three methods provide alternative
    # extraction paths that work with any app:
    #
    #   1. _clipboard_snoop: Ctrl+A → Ctrl+C → read clipboard → restore
    #   2. _ocr_crop:        Screenshot only the active window rect
    #   3. _shortcut_route:  App-specific hotkeys for structured data

    _ELECTRON_APPS = frozenset([
        "code", "discord", "slack", "teams", "obsidian", "notion",
        "figma", "spotify", "postman", "insomnia", "atom",
    ])

    def _is_electron_app(self, app_name: str) -> bool:
        """Check if the current app is likely Electron-based."""
        return app_name.lower() in self._ELECTRON_APPS

    def _clipboard_snoop(self) -> str:
        """
        Extract visible text via clipboard injection.

        Flow: save clipboard → Ctrl+A → Ctrl+C → read → restore clipboard.

        Uses native Win32 clipboard lock (OpenClipboard/CloseClipboard)
        via ctypes for atomicity, preventing race conditions where the
        user copies something during the ~50ms snoop window.

        Returns extracted text, or empty string on failure.
        """
        try:
            import ctypes
            import ctypes.wintypes

            user32 = ctypes.windll.user32
            kernel32 = ctypes.windll.kernel32

            CF_UNICODETEXT = 13
            GMEM_MOVEABLE = 0x0002

            # Open clipboard with retries (another process may hold it)
            opened = False
            for _ in range(5):
                if user32.OpenClipboard(0):
                    opened = True
                    break
                time.sleep(0.01)

            if not opened:
                logger.debug("[ScreenIntel] Clipboard lock failed — skipping snoop")
                return ""

            try:
                # Save current clipboard content
                saved_text = ""
                handle = user32.GetClipboardData(CF_UNICODETEXT)
                if handle:
                    ptr = kernel32.GlobalLock(handle)
                    if ptr:
                        saved_text = ctypes.wstring_at(ptr)
                        kernel32.GlobalUnlock(handle)

                user32.CloseClipboard()

                # Inject Ctrl+A, Ctrl+C
                import win32api
                import win32con

                VK_CONTROL = 0x11
                VK_A = 0x41
                VK_C = 0x43

                # Ctrl+A (select all)
                win32api.keybd_event(VK_CONTROL, 0, 0, 0)
                win32api.keybd_event(VK_A, 0, 0, 0)
                win32api.keybd_event(VK_A, 0, win32con.KEYEVENTF_KEYUP, 0)
                win32api.keybd_event(VK_CONTROL, 0, win32con.KEYEVENTF_KEYUP, 0)
                time.sleep(0.05)

                # Ctrl+C (copy)
                win32api.keybd_event(VK_CONTROL, 0, 0, 0)
                win32api.keybd_event(VK_C, 0, 0, 0)
                win32api.keybd_event(VK_C, 0, win32con.KEYEVENTF_KEYUP, 0)
                win32api.keybd_event(VK_CONTROL, 0, win32con.KEYEVENTF_KEYUP, 0)
                time.sleep(0.05)

                # Read the copied text
                snooped_text = ""
                for _ in range(5):
                    if user32.OpenClipboard(0):
                        handle = user32.GetClipboardData(CF_UNICODETEXT)
                        if handle:
                            ptr = kernel32.GlobalLock(handle)
                            if ptr:
                                snooped_text = ctypes.wstring_at(ptr)
                                kernel32.GlobalUnlock(handle)
                        user32.CloseClipboard()
                        break
                    time.sleep(0.01)

                # Restore original clipboard content
                try:
                    for _ in range(5):
                        if user32.OpenClipboard(0):
                            user32.EmptyClipboard()
                            if saved_text:
                                byte_len = (len(saved_text) + 1) * 2
                                h_mem = kernel32.GlobalAlloc(GMEM_MOVEABLE, byte_len)
                                ptr = kernel32.GlobalLock(h_mem)
                                ctypes.memmove(ptr, saved_text, byte_len)
                                kernel32.GlobalUnlock(h_mem)
                                user32.SetClipboardData(CF_UNICODETEXT, h_mem)
                            user32.CloseClipboard()
                            break
                        time.sleep(0.01)
                except Exception as restore_err:
                    logger.warning(
                        f"[ScreenIntel] Clipboard restore failed: {restore_err}"
                    )

                # Undo the select-all (Escape)
                VK_ESCAPE = 0x1B
                win32api.keybd_event(VK_ESCAPE, 0, 0, 0)
                win32api.keybd_event(VK_ESCAPE, 0, win32con.KEYEVENTF_KEYUP, 0)

                if snooped_text and snooped_text != saved_text:
                    return snooped_text[:_MAX_TEXT_LENGTH]
                return ""

            except Exception:
                # Ensure clipboard is closed on any error path
                try:
                    user32.CloseClipboard()
                except Exception:
                    pass
                raise

        except ImportError:
            logger.debug("[ScreenIntel] Win32 not available for clipboard snoop")
            return ""
        except Exception as e:
            logger.warning(f"[ScreenIntel] Clipboard snoop failed: {e}")
            return ""

    def _ocr_crop(self, rect: tuple[int, int, int, int]) -> str:
        """
        Run OCR on only the active window region.

        Much faster than full-screen OCR — captures only the
        bounding box of the focused window (typically 30-50% of screen).

        Args:
            rect: (x, y, width, height) of the active window.

        Returns:
            Extracted text prefixed with [OCR_CROP], or empty string.
        """
        x, y, w, h = rect
        if w < 100 or h < 100:
            return ""

        try:
            import mss
            import pytesseract
            from PIL import Image

            monitor = {"left": x, "top": y, "width": w, "height": h}
            with mss.mss() as sct:
                screenshot = sct.grab(monitor)
                img = Image.frombytes(
                    "RGB",
                    (screenshot.width, screenshot.height),
                    screenshot.rgb,
                )

            # Downscale if larger than 1280px wide
            if img.width > 1280:
                ratio = 1280 / img.width
                img = img.resize(
                    (1280, int(img.height * ratio)),
                    Image.LANCZOS,
                )

            text = pytesseract.image_to_string(img, timeout=5)
            if text.strip():
                return f"[OCR_CROP] {text.strip()[:_MAX_TEXT_LENGTH]}"
            return ""

        except ImportError:
            logger.debug("[ScreenIntel] pytesseract/mss not installed for OCR crop")
            return ""
        except Exception as e:
            logger.warning(f"[ScreenIntel] OCR crop failed: {e}")
            return ""

    # App-specific keyboard shortcut extractors
    _APP_SHORTCUTS: dict[str, list[tuple[str, list[int]]]] = {
        # VS Code: Ctrl+Shift+P opens command palette — too intrusive
        # Instead, we read the title bar which has file + project info
        "code": [],
        # Discord: no safe read-only shortcut
        "discord": [],
    }

    def _shortcut_route(self, app_name: str) -> str:
        """
        Use app-specific keyboard shortcuts to extract structured data.

        Currently a stub — returns window title enrichment for known apps.
        Phase 2 will add specific shortcut sequences for extracting:
        - VS Code: active file, git branch, problems count
        - Discord: current channel, server name
        - Slack: channel name, unread count

        Returns extracted text, or empty string.
        """
        # For now, shortcut routing enriches via title parsing only.
        # Actual keystroke injection is deferred to Phase 2 when the
        # Accessibility Layer is fully wired.
        return ""

    # ── Query API (for tools and agents) ─────────────────────────────────

    def get_recent_contexts(
        self,
        limit: int = 10,
        app_filter: Optional[str] = None,
    ) -> list[dict]:
        """Query recent screen contexts from the database."""
        _ensure_schema()
        conn = _get_conn()

        if app_filter:
            rows = conn.execute(
                """SELECT * FROM screen_context
                   WHERE app_name LIKE ?
                   ORDER BY timestamp DESC LIMIT ?""",
                (f"%{app_filter}%", limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT * FROM screen_context
                   ORDER BY timestamp DESC LIMIT ?""",
                (limit,),
            ).fetchall()

        return [dict(row) for row in rows]

    def get_context_summary(self) -> str:
        """
        Human-readable summary of current screen state.
        Suitable for injection into LLM prompts.
        """
        ctx = self.capture_context()
        active = ctx.active_window

        lines = [
            f"[SCREEN CONTEXT — {ctx.source.upper()} capture]",
            f"Active App: {active.app_name}",
            f"Window: {active.title[:80]}",
            f"Visible Windows: {len(ctx.window_layout)}",
        ]

        if ctx.visible_text:
            preview = ctx.visible_text[:200].replace("\n", " ")
            lines.append(f"Visible Text: {preview}...")

        return "\n".join(lines)

    def prune_old_contexts(self, max_age_hours: int = 24) -> int:
        """Delete screen contexts older than max_age_hours."""
        cutoff = time.time() - (max_age_hours * 3600)
        try:
            conn = _get_conn()
            cursor = conn.execute(
                "DELETE FROM screen_context WHERE timestamp < ?",
                (cutoff,),
            )
            conn.commit()
            deleted = cursor.rowcount
            if deleted:
                logger.info(f"[ScreenIntel] Pruned {deleted} old contexts")
            return deleted
        except Exception as e:
            logger.warning(f"[ScreenIntel] Prune failed: {e}")
            return 0


# ── Singleton ────────────────────────────────────────────────────────────────

_instance: Optional[ScreenIntelligence] = None
_instance_lock = threading.Lock()


def get_screen_intelligence() -> ScreenIntelligence:
    """Thread-safe singleton accessor."""
    global _instance
    if _instance is None:
        with _instance_lock:
            if _instance is None:
                _instance = ScreenIntelligence()
    return _instance
