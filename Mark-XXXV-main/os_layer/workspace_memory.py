"""
os_layer/workspace_memory.py — Persistent Workspace Memory
============================================================
Saves and restores workspace state across JARVIS sessions:
window positions, active apps, last task context, and incomplete tasks.

Integration Point:
    Called from main.py `_trigger_boot_sequence()` —
    the boot briefing includes workspace restoration context.

Storage:
    SQLite table `workspace_snapshots` in existing `agent_episodes.db`.
    Snapshots are lightweight JSON blobs (~2KB each).

Thread Safety:
    Same WAL-mode SQLite pattern as agent_memory.py.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────

DB_PATH = Path(__file__).resolve().parent.parent / "memory" / "agent_episodes.db"
_MAX_SNAPSHOTS = 50  # keep last N snapshots, prune older ones

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS workspace_snapshots (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp   REAL    NOT NULL,
    snapshot    TEXT    NOT NULL DEFAULT '{}',
    task_context TEXT   DEFAULT '',
    session_id  TEXT    DEFAULT ''
);

CREATE TABLE IF NOT EXISTS incomplete_tasks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    description TEXT    NOT NULL,
    created_at  REAL    NOT NULL,
    completed   INTEGER NOT NULL DEFAULT 0,
    metadata    TEXT    DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_ws_snap_ts
    ON workspace_snapshots(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_incomplete_tasks_status
    ON incomplete_tasks(completed);
"""


# ── Data Classes ─────────────────────────────────────────────────────────────

@dataclass
class WorkspaceSnapshot:
    """Serializable workspace state."""
    timestamp: float = 0.0
    active_app: str = ""
    active_window_title: str = ""
    open_windows: list[dict] = field(default_factory=list)
    task_context: str = ""
    session_id: str = ""


@dataclass
class RestoreResult:
    """Result of a workspace restore attempt."""
    success: bool = False
    restored_count: int = 0
    total_windows: int = 0
    last_task_context: str = ""
    incomplete_tasks: list[str] = field(default_factory=list)
    briefing: str = ""


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


# ── Workspace Memory ─────────────────────────────────────────────────────────

class WorkspaceMemory:
    """
    Persistent workspace state across JARVIS sessions.

    Usage:
        wm = WorkspaceMemory()

        # On shutdown — save current state
        snapshot = wm.save_workspace_state()

        # On boot — restore and get briefing
        result = wm.restore_workspace()
        print(result.briefing)  # "You were working on X..."

        # Track incomplete tasks
        wm.mark_task_incomplete("Finish the API refactor")
        tasks = wm.get_incomplete_tasks()
    """

    def __init__(self):
        _ensure_schema()
        self._session_id = self._generate_session_id()

    # ── Save State ───────────────────────────────────────────────────────

    def save_workspace_state(self) -> WorkspaceSnapshot:
        """
        Capture and persist current workspace state.
        Called on shutdown and periodically during operation.
        """
        from os_layer.screen_intel import get_screen_intelligence
        from os_layer.window_manager import get_window_manager

        si = get_screen_intelligence()
        wm = get_window_manager()

        # Capture current state
        active = si.get_active_window()
        layout = si.get_window_layout()

        windows = []
        for win in layout:
            windows.append({
                "title": win.title,
                "app_name": win.app_name,
                "hwnd": win.hwnd,
                "rect": win.rect,
            })

        snapshot = WorkspaceSnapshot(
            timestamp=time.time(),
            active_app=active.app_name,
            active_window_title=active.title,
            open_windows=windows,
            session_id=self._session_id,
        )

        # Persist
        try:
            conn = _get_conn()
            conn.execute(
                """INSERT INTO workspace_snapshots
                   (timestamp, snapshot, task_context, session_id)
                   VALUES (?, ?, ?, ?)""",
                (
                    snapshot.timestamp,
                    json.dumps({
                        "active_app": snapshot.active_app,
                        "active_window_title": snapshot.active_window_title,
                        "open_windows": snapshot.open_windows,
                    }),
                    snapshot.task_context,
                    snapshot.session_id,
                ),
            )
            conn.commit()
            self._prune_old_snapshots(conn)
            logger.info(
                f"[WorkspaceMem] Saved snapshot: {snapshot.active_app} "
                f"({len(windows)} windows)"
            )
        except Exception as e:
            logger.warning(f"[WorkspaceMem] Save failed: {e}")

        return snapshot

    def save_active_task_context(self, task_description: str) -> None:
        """Save a description of what the user is currently working on."""
        try:
            conn = _get_conn()
            # Update the most recent snapshot's task_context
            conn.execute(
                """UPDATE workspace_snapshots
                   SET task_context = ?
                   WHERE id = (
                       SELECT id FROM workspace_snapshots
                       ORDER BY timestamp DESC LIMIT 1
                   )""",
                (task_description,),
            )
            conn.commit()
            logger.info(
                f"[WorkspaceMem] Task context updated: {task_description[:60]}"
            )
        except Exception as e:
            logger.warning(f"[WorkspaceMem] Task context save failed: {e}")

    # ── Restore State ────────────────────────────────────────────────────

    def restore_workspace(self) -> RestoreResult:
        """
        Attempt to restore the last saved workspace state.
        Returns a RestoreResult with briefing text for the boot sequence.
        """
        result = RestoreResult()

        try:
            conn = _get_conn()

            # Get the most recent snapshot
            row = conn.execute(
                """SELECT * FROM workspace_snapshots
                   ORDER BY timestamp DESC LIMIT 1"""
            ).fetchone()

            if not row:
                result.briefing = "No previous workspace state found."
                return result

            snapshot_data = json.loads(row["snapshot"] or "{}")
            task_context = row["task_context"] or ""
            timestamp = row["timestamp"]

            # Calculate session gap
            gap_hours = (time.time() - timestamp) / 3600
            gap_str = self._format_time_gap(timestamp)

            result.total_windows = len(snapshot_data.get("open_windows", []))
            result.last_task_context = task_context

            # Attempt to restore window positions
            from os_layer.window_manager import get_window_manager
            wm = get_window_manager()

            restored = 0
            for win_info in snapshot_data.get("open_windows", []):
                hwnd = wm.find_window(win_info.get("title", ""))
                if hwnd:
                    rect = win_info.get("rect", (0, 0, 960, 540))
                    wm.move_window(hwnd, *rect)
                    restored += 1

            result.restored_count = restored
            result.success = restored > 0

            # Get incomplete tasks
            result.incomplete_tasks = self.get_incomplete_tasks()

            # Build briefing
            result.briefing = self._build_briefing(
                active_app=snapshot_data.get("active_app", ""),
                task_context=task_context,
                gap_str=gap_str,
                restored=restored,
                total=result.total_windows,
                incomplete_tasks=result.incomplete_tasks,
            )

            logger.info(
                f"[WorkspaceMem] Restored {restored}/{result.total_windows} "
                f"windows (gap: {gap_str})"
            )

        except Exception as e:
            logger.warning(f"[WorkspaceMem] Restore failed: {e}")
            result.briefing = "Could not restore previous workspace."

        return result

    def get_last_session_brief(self) -> str:
        """
        Quick summary of the last session.
        Used by boot briefing to remind user what they were doing.
        """
        try:
            conn = _get_conn()
            row = conn.execute(
                """SELECT * FROM workspace_snapshots
                   ORDER BY timestamp DESC LIMIT 1"""
            ).fetchone()

            if not row:
                return ""

            snapshot_data = json.loads(row["snapshot"] or "{}")
            task_context = row["task_context"] or ""
            timestamp = row["timestamp"]
            gap_str = self._format_time_gap(timestamp)

            active_app = snapshot_data.get("active_app", "")
            window_count = len(snapshot_data.get("open_windows", []))

            parts = [f"Last session was {gap_str}."]
            if active_app:
                parts.append(f"You were using {active_app}.")
            if task_context:
                parts.append(f"Working on: {task_context}")
            if window_count > 0:
                parts.append(f"{window_count} windows were open.")

            # Add incomplete tasks
            tasks = self.get_incomplete_tasks()
            if tasks:
                parts.append(
                    f"Pending tasks: {', '.join(tasks[:3])}"
                    + (f" (+{len(tasks)-3} more)" if len(tasks) > 3 else "")
                )

            return " ".join(parts)

        except Exception as e:
            logger.debug(f"[WorkspaceMem] get_last_session_brief failed: {e}")
            return ""

    # ── Task Tracking ────────────────────────────────────────────────────

    def mark_task_incomplete(self, description: str) -> None:
        """Mark a task as incomplete for future follow-up."""
        try:
            conn = _get_conn()
            # Avoid duplicates
            existing = conn.execute(
                """SELECT id FROM incomplete_tasks
                   WHERE description = ? AND completed = 0""",
                (description,),
            ).fetchone()

            if existing:
                return

            conn.execute(
                """INSERT INTO incomplete_tasks (description, created_at)
                   VALUES (?, ?)""",
                (description, time.time()),
            )
            conn.commit()
            logger.info(f"[WorkspaceMem] Task marked incomplete: {description[:60]}")
        except Exception as e:
            logger.warning(f"[WorkspaceMem] mark_task_incomplete failed: {e}")

    def complete_task(self, description: str) -> None:
        """Mark an incomplete task as completed."""
        try:
            conn = _get_conn()
            conn.execute(
                """UPDATE incomplete_tasks SET completed = 1
                   WHERE description = ? AND completed = 0""",
                (description,),
            )
            conn.commit()
        except Exception as e:
            logger.warning(f"[WorkspaceMem] complete_task failed: {e}")

    def get_incomplete_tasks(self) -> list[str]:
        """Get all incomplete task descriptions."""
        try:
            conn = _get_conn()
            rows = conn.execute(
                """SELECT description FROM incomplete_tasks
                   WHERE completed = 0
                   ORDER BY created_at DESC""",
            ).fetchall()
            return [row["description"] for row in rows]
        except Exception:
            return []

    # ── Private Helpers ──────────────────────────────────────────────────

    def _prune_old_snapshots(self, conn: sqlite3.Connection) -> None:
        """Keep only the most recent N snapshots."""
        try:
            conn.execute(
                """DELETE FROM workspace_snapshots
                   WHERE id NOT IN (
                       SELECT id FROM workspace_snapshots
                       ORDER BY timestamp DESC
                       LIMIT ?
                   )""",
                (_MAX_SNAPSHOTS,),
            )
        except Exception:
            pass

    @staticmethod
    def _format_time_gap(timestamp: float) -> str:
        """Human-readable time gap from timestamp to now."""
        delta = time.time() - timestamp
        if delta < 60:
            return "moments ago"
        if delta < 3600:
            mins = int(delta / 60)
            return f"{mins} minute{'s' if mins != 1 else ''} ago"
        if delta < 86400:
            hours = int(delta / 3600)
            return f"{hours} hour{'s' if hours != 1 else ''} ago"
        days = int(delta / 86400)
        return f"{days} day{'s' if days != 1 else ''} ago"

    @staticmethod
    def _generate_session_id() -> str:
        """Generate a unique session identifier."""
        import uuid
        return str(uuid.uuid4())[:8]

    @staticmethod
    def _build_briefing(
        active_app: str,
        task_context: str,
        gap_str: str,
        restored: int,
        total: int,
        incomplete_tasks: list[str],
    ) -> str:
        """Build a natural-language briefing for boot sequence."""
        parts = []

        if gap_str:
            parts.append(f"Your last session was {gap_str}.")

        if active_app:
            parts.append(f"You were using {active_app}.")

        if task_context:
            parts.append(f"You were working on: {task_context}")

        if restored > 0:
            parts.append(
                f"I restored {restored} of {total} window positions."
            )

        if incomplete_tasks:
            task_list = ", ".join(incomplete_tasks[:3])
            suffix = f" and {len(incomplete_tasks)-3} more" if len(incomplete_tasks) > 3 else ""
            parts.append(f"Pending tasks: {task_list}{suffix}.")

        return " ".join(parts) if parts else "Welcome back. No previous session data found."


# ── Singleton ────────────────────────────────────────────────────────────────

_instance: Optional[WorkspaceMemory] = None
_instance_lock = threading.Lock()


def get_workspace_memory() -> WorkspaceMemory:
    """Thread-safe singleton accessor."""
    global _instance
    if _instance is None:
        with _instance_lock:
            if _instance is None:
                _instance = WorkspaceMemory()
    return _instance
