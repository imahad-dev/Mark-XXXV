"""
os_layer/window_manager.py — Window & Process Orchestrator
===========================================================
OS-level window control: layouts, snap positions, process management,
and multi-monitor support.

Dependencies:
    - pywin32 (win32gui, win32con, win32process) — already in requirements
    - pywinauto — already in requirements
    - psutil — already in requirements

Thread Safety:
    All win32 calls are inherently thread-safe on Windows.
    Layout save/restore uses the same SQLite pattern as agent_memory.py.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────

DB_PATH = Path(__file__).resolve().parent.parent / "memory" / "agent_episodes.db"

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS window_layouts (
    name        TEXT PRIMARY KEY,
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL,
    windows     TEXT NOT NULL DEFAULT '[]',
    metadata    TEXT DEFAULT '{}'
);
"""


# ── Enums ────────────────────────────────────────────────────────────────────

class SnapPosition(str, Enum):
    """Pre-defined snap positions for window placement."""
    LEFT        = "left"
    RIGHT       = "right"
    TOP         = "top"
    BOTTOM      = "bottom"
    TOP_LEFT    = "top_left"
    TOP_RIGHT   = "top_right"
    BOTTOM_LEFT = "bottom_left"
    BOTTOM_RIGHT = "bottom_right"
    CENTER      = "center"
    MAXIMIZE    = "maximize"
    MINIMIZE    = "minimize"


# ── Data Classes ─────────────────────────────────────────────────────────────

@dataclass
class ProcessInfo:
    """Info about a running process."""
    pid: int = 0
    name: str = ""
    exe_path: str = ""
    cpu_percent: float = 0.0
    memory_mb: float = 0.0
    status: str = ""


@dataclass
class MonitorInfo:
    """Info about a connected display."""
    index: int = 0
    name: str = ""
    x: int = 0
    y: int = 0
    width: int = 0
    height: int = 0
    is_primary: bool = False


@dataclass
class LayoutConfig:
    """Saved window layout configuration."""
    name: str = ""
    windows: list[dict] = field(default_factory=list)
    created_at: float = 0.0
    updated_at: float = 0.0


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


# ── Window Manager ───────────────────────────────────────────────────────────

class WindowManager:
    """
    Window & Process Orchestrator.

    Usage:
        wm = WindowManager()

        # Move a window
        wm.move_window(hwnd, x=0, y=0, w=960, h=1080)

        # Snap to half screen
        wm.snap_window(hwnd, SnapPosition.LEFT)

        # Save/restore layouts
        wm.save_layout("coding_mode")
        wm.restore_layout("coding_mode")

        # Process management
        hogs = wm.get_resource_hogs(threshold_cpu=80)
    """

    def __init__(self):
        self._win32gui = None
        self._win32con = None
        self._win32process = None
        self._psutil = None
        _ensure_schema()

    # ── Lazy Imports ─────────────────────────────────────────────────────

    def _load_win32(self) -> bool:
        if self._win32gui is not None:
            return True
        try:
            import win32gui
            import win32con
            import win32process
            self._win32gui = win32gui
            self._win32con = win32con
            self._win32process = win32process
            return True
        except ImportError:
            logger.warning("[WindowMgr] pywin32 not installed")
            return False

    def _load_psutil(self) -> bool:
        if self._psutil is not None:
            return True
        try:
            import psutil
            self._psutil = psutil
            return True
        except ImportError:
            logger.warning("[WindowMgr] psutil not installed")
            return False

    # ── Window Control ───────────────────────────────────────────────────

    def move_window(
        self, hwnd: int, x: int, y: int, w: int, h: int,
    ) -> bool:
        """Move and resize a window by handle."""
        if not self._load_win32():
            return False
        try:
            # Restore if minimized/maximized before moving
            self._win32gui.ShowWindow(
                hwnd, self._win32con.SW_RESTORE
            )
            self._win32gui.MoveWindow(hwnd, x, y, w, h, True)
            return True
        except Exception as e:
            logger.warning(f"[WindowMgr] move_window failed: {e}")
            return False

    def snap_window(self, hwnd: int, position: SnapPosition) -> bool:
        """Snap a window to a predefined screen position."""
        if not self._load_win32():
            return False

        try:
            # Get monitor dimensions for the window's current monitor
            monitor = self._get_monitor_for_window(hwnd)
            mx, my, mw, mh = monitor.x, monitor.y, monitor.width, monitor.height

            # Taskbar compensation (approximate 40px)
            taskbar_h = 40
            usable_h = mh - taskbar_h

            snap_map = {
                SnapPosition.LEFT:         (mx, my, mw // 2, usable_h),
                SnapPosition.RIGHT:        (mx + mw // 2, my, mw // 2, usable_h),
                SnapPosition.TOP:          (mx, my, mw, usable_h // 2),
                SnapPosition.BOTTOM:       (mx, my + usable_h // 2, mw, usable_h // 2),
                SnapPosition.TOP_LEFT:     (mx, my, mw // 2, usable_h // 2),
                SnapPosition.TOP_RIGHT:    (mx + mw // 2, my, mw // 2, usable_h // 2),
                SnapPosition.BOTTOM_LEFT:  (mx, my + usable_h // 2, mw // 2, usable_h // 2),
                SnapPosition.BOTTOM_RIGHT: (mx + mw // 2, my + usable_h // 2, mw // 2, usable_h // 2),
                SnapPosition.CENTER:       (mx + mw // 4, my + usable_h // 4, mw // 2, usable_h // 2),
            }

            if position == SnapPosition.MAXIMIZE:
                self._win32gui.ShowWindow(hwnd, self._win32con.SW_MAXIMIZE)
                return True

            if position == SnapPosition.MINIMIZE:
                self._win32gui.ShowWindow(hwnd, self._win32con.SW_MINIMIZE)
                return True

            coords = snap_map.get(position)
            if coords:
                return self.move_window(hwnd, *coords)

            return False

        except Exception as e:
            logger.warning(f"[WindowMgr] snap_window failed: {e}")
            return False

    def minimize_window(self, hwnd: int) -> bool:
        return self.snap_window(hwnd, SnapPosition.MINIMIZE)

    def maximize_window(self, hwnd: int) -> bool:
        return self.snap_window(hwnd, SnapPosition.MAXIMIZE)

    def close_window(self, hwnd: int) -> bool:
        """Close a window by handle."""
        if not self._load_win32():
            return False
        try:
            self._win32gui.PostMessage(
                hwnd, self._win32con.WM_CLOSE, 0, 0
            )
            return True
        except Exception as e:
            logger.warning(f"[WindowMgr] close_window failed: {e}")
            return False

    def find_window(self, title_fragment: str) -> Optional[int]:
        """Find a window handle by partial title match."""
        if not self._load_win32():
            return None

        result = []

        def _callback(hwnd: int, results: list) -> None:
            if self._win32gui.IsWindowVisible(hwnd):
                title = self._win32gui.GetWindowText(hwnd)
                if title and title_fragment.lower() in title.lower():
                    results.append(hwnd)

        try:
            self._win32gui.EnumWindows(_callback, result)
        except Exception:
            pass

        return result[0] if result else None

    # ── Layout Management ────────────────────────────────────────────────

    def save_layout(self, name: str) -> LayoutConfig:
        """Save the current window arrangement as a named layout."""
        if not self._load_win32():
            return LayoutConfig(name=name)

        windows = []

        def _callback(hwnd: int, results: list) -> None:
            if not self._win32gui.IsWindowVisible(hwnd):
                return
            title = self._win32gui.GetWindowText(hwnd)
            if not title or title in ("Program Manager", ""):
                return

            rect = self._win32gui.GetWindowRect(hwnd)
            w, h = rect[2] - rect[0], rect[3] - rect[1]
            if w < 50 or h < 50:
                return

            # Get exe path for reliable re-identification
            exe_path = ""
            if self._load_psutil():
                try:
                    _, pid = self._win32process.GetWindowThreadProcessId(hwnd)
                    proc = self._psutil.Process(pid)
                    exe_path = proc.exe()
                except Exception:
                    pass

            results.append({
                "title": title,
                "exe_path": exe_path,
                "x": rect[0],
                "y": rect[1],
                "w": w,
                "h": h,
            })

        try:
            self._win32gui.EnumWindows(_callback, windows)
        except Exception as e:
            logger.warning(f"[WindowMgr] save_layout enum failed: {e}")

        now = time.time()
        layout = LayoutConfig(
            name=name,
            windows=windows,
            created_at=now,
            updated_at=now,
        )

        # Persist to SQLite (upsert)
        try:
            conn = _get_conn()
            conn.execute(
                """INSERT INTO window_layouts (name, created_at, updated_at, windows)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(name) DO UPDATE SET
                       updated_at = excluded.updated_at,
                       windows = excluded.windows""",
                (name, now, now, json.dumps(windows)),
            )
            conn.commit()
            logger.info(f"[WindowMgr] Layout '{name}' saved ({len(windows)} windows)")
        except Exception as e:
            logger.warning(f"[WindowMgr] save_layout persist failed: {e}")

        return layout

    def restore_layout(self, name: str) -> bool:
        """Restore a previously saved window layout."""
        if not self._load_win32():
            return False

        try:
            conn = _get_conn()
            row = conn.execute(
                "SELECT * FROM window_layouts WHERE name = ?", (name,)
            ).fetchone()

            if not row:
                logger.warning(f"[WindowMgr] Layout '{name}' not found")
                return False

            windows = json.loads(row["windows"])
            restored = 0

            for win_info in windows:
                # Find the window by title (partial match)
                hwnd = self.find_window(win_info.get("title", ""))
                if hwnd:
                    self.move_window(
                        hwnd,
                        win_info["x"], win_info["y"],
                        win_info["w"], win_info["h"],
                    )
                    restored += 1

            logger.info(
                f"[WindowMgr] Layout '{name}' restored "
                f"({restored}/{len(windows)} windows)"
            )
            return restored > 0

        except Exception as e:
            logger.warning(f"[WindowMgr] restore_layout failed: {e}")
            return False

    def list_layouts(self) -> list[str]:
        """List all saved layout names."""
        try:
            conn = _get_conn()
            rows = conn.execute(
                "SELECT name FROM window_layouts ORDER BY updated_at DESC"
            ).fetchall()
            return [row["name"] for row in rows]
        except Exception:
            return []

    def delete_layout(self, name: str) -> bool:
        """Delete a saved layout."""
        try:
            conn = _get_conn()
            conn.execute(
                "DELETE FROM window_layouts WHERE name = ?", (name,)
            )
            conn.commit()
            return True
        except Exception:
            return False

    # ── Process Management ───────────────────────────────────────────────

    def list_processes(self) -> list[ProcessInfo]:
        """List running processes with resource usage."""
        if not self._load_psutil():
            return []

        procs = []
        for proc in self._psutil.process_iter(
            ["pid", "name", "exe", "cpu_percent", "memory_info", "status"]
        ):
            try:
                info = proc.info
                mem_mb = (info.get("memory_info") or type("", (), {"rss": 0})).rss / (1024 * 1024)
                procs.append(ProcessInfo(
                    pid=info["pid"],
                    name=info.get("name", ""),
                    exe_path=info.get("exe", "") or "",
                    cpu_percent=info.get("cpu_percent", 0.0) or 0.0,
                    memory_mb=round(mem_mb, 1),
                    status=info.get("status", ""),
                ))
            except (self._psutil.NoSuchProcess, self._psutil.AccessDenied):
                continue

        return procs

    def kill_process(self, pid: int, force: bool = False) -> bool:
        """Terminate a process by PID."""
        if not self._load_psutil():
            return False
        try:
            proc = self._psutil.Process(pid)
            if force:
                proc.kill()
            else:
                proc.terminate()
            logger.info(f"[WindowMgr] Process {pid} ({proc.name()}) terminated")
            return True
        except Exception as e:
            logger.warning(f"[WindowMgr] kill_process failed: {e}")
            return False

    def get_resource_hogs(self, threshold_cpu: float = 80.0) -> list[ProcessInfo]:
        """Find processes consuming excessive CPU."""
        if not self._load_psutil():
            return []

        # First call returns 0.0, need two calls with interval
        for proc in self._psutil.process_iter(["cpu_percent"]):
            pass
        time.sleep(0.5)

        hogs = []
        for proc in self._psutil.process_iter(
            ["pid", "name", "exe", "cpu_percent", "memory_info"]
        ):
            try:
                info = proc.info
                cpu = info.get("cpu_percent", 0.0) or 0.0
                if cpu >= threshold_cpu:
                    mem_mb = (info.get("memory_info") or type("", (), {"rss": 0})).rss / (1024 * 1024)
                    hogs.append(ProcessInfo(
                        pid=info["pid"],
                        name=info.get("name", ""),
                        exe_path=info.get("exe", "") or "",
                        cpu_percent=cpu,
                        memory_mb=round(mem_mb, 1),
                    ))
            except Exception:
                continue

        return sorted(hogs, key=lambda p: p.cpu_percent, reverse=True)

    def launch_app(self, path: str, args: list[str] | None = None) -> int:
        """Launch an application and return its PID."""
        import subprocess
        try:
            cmd = [path] + (args or [])
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
            )
            logger.info(f"[WindowMgr] Launched {path} (PID: {proc.pid})")
            return proc.pid
        except Exception as e:
            logger.error(f"[WindowMgr] launch_app failed: {e}")
            return 0

    # ── Multi-Monitor ────────────────────────────────────────────────────

    def get_monitors(self) -> list[MonitorInfo]:
        """Get info about all connected monitors."""
        if not self._load_win32():
            return []

        monitors = []
        try:
            def _callback(hmonitor, hdc, rect_ptr, data):
                import ctypes
                from ctypes import wintypes

                class MONITORINFOEX(ctypes.Structure):
                    _fields_ = [
                        ("cbSize", wintypes.DWORD),
                        ("rcMonitor", wintypes.RECT),
                        ("rcWork", wintypes.RECT),
                        ("dwFlags", wintypes.DWORD),
                        ("szDevice", ctypes.c_wchar * 32),
                    ]

                info = MONITORINFOEX()
                info.cbSize = ctypes.sizeof(MONITORINFOEX)
                ctypes.windll.user32.GetMonitorInfoW(hmonitor, ctypes.byref(info))

                rc = info.rcMonitor
                monitors.append(MonitorInfo(
                    index=len(monitors),
                    name=info.szDevice.strip('\x00'),
                    x=rc.left,
                    y=rc.top,
                    width=rc.right - rc.left,
                    height=rc.bottom - rc.top,
                    is_primary=bool(info.dwFlags & 1),
                ))
                return True

            import ctypes
            ctypes.windll.user32.EnumDisplayMonitors(
                None, None,
                ctypes.WINFUNCTYPE(
                    ctypes.c_bool,
                    ctypes.c_ulong,
                    ctypes.c_ulong,
                    ctypes.POINTER(ctypes.wintypes.RECT),
                    ctypes.c_double,
                )(_callback),
                0,
            )
        except Exception as e:
            logger.warning(f"[WindowMgr] get_monitors failed: {e}")

        return monitors

    def move_to_monitor(self, hwnd: int, monitor_index: int) -> bool:
        """Move a window to a specific monitor."""
        monitors = self.get_monitors()
        if monitor_index >= len(monitors):
            logger.warning(
                f"[WindowMgr] Monitor {monitor_index} not found "
                f"(have {len(monitors)})"
            )
            return False

        target = monitors[monitor_index]

        # Get current window size
        if not self._load_win32():
            return False
        try:
            rect = self._win32gui.GetWindowRect(hwnd)
            w = rect[2] - rect[0]
            h = rect[3] - rect[1]
            return self.move_window(hwnd, target.x, target.y, w, h)
        except Exception as e:
            logger.warning(f"[WindowMgr] move_to_monitor failed: {e}")
            return False

    # ── Private Helpers ──────────────────────────────────────────────────

    def _get_monitor_for_window(self, hwnd: int) -> MonitorInfo:
        """Get the monitor that contains the given window."""
        monitors = self.get_monitors()
        if not monitors:
            # Fallback: assume single 1920x1080 monitor
            return MonitorInfo(
                index=0, width=1920, height=1080, is_primary=True,
            )

        if self._load_win32():
            try:
                rect = self._win32gui.GetWindowRect(hwnd)
                cx = (rect[0] + rect[2]) // 2
                cy = (rect[1] + rect[3]) // 2

                for mon in monitors:
                    if (mon.x <= cx < mon.x + mon.width
                            and mon.y <= cy < mon.y + mon.height):
                        return mon
            except Exception:
                pass

        # Fallback: primary monitor
        for mon in monitors:
            if mon.is_primary:
                return mon
        return monitors[0]


# ── Singleton ────────────────────────────────────────────────────────────────

_instance: Optional[WindowManager] = None
_instance_lock = threading.Lock()


def get_window_manager() -> WindowManager:
    """Thread-safe singleton accessor."""
    global _instance
    if _instance is None:
        with _instance_lock:
            if _instance is None:
                _instance = WindowManager()
    return _instance
