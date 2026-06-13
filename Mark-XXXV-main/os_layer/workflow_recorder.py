"""
os_layer/workflow_recorder.py — Workflow Record & Replay Engine
================================================================
Captures sequences of user actions (window switches, file edits,
app launches) as replayable workflows. Think OS-level macros on
steroids — JARVIS can learn repetitive patterns and offer to
automate them.

Architecture:
    - Records events from the EventBus into named workflows.
    - Each workflow is a timestamped sequence of WorkflowSteps.
    - Stored in SQLite `workflows` + `workflow_steps` tables.
    - Replay uses WindowManager to restore positions and launch apps.

Integration:
    Subscribes to EventBus events during recording. Uses
    WindowManager for the replay phase.

Thread Safety:
    Recording state is guarded by a lock. SQLite uses WAL mode.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────

DB_PATH = Path(__file__).resolve().parent.parent / "memory" / "agent_episodes.db"
_MAX_STEPS_PER_WORKFLOW = 200   # prevent runaway recordings
_MAX_STORED_WORKFLOWS = 50      # prune oldest when exceeded

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS workflows (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL UNIQUE,
    description TEXT    DEFAULT '',
    created_at  REAL    NOT NULL,
    updated_at  REAL    NOT NULL,
    step_count  INTEGER NOT NULL DEFAULT 0,
    total_duration REAL DEFAULT 0.0,
    tags        TEXT    DEFAULT '[]',
    metadata    TEXT    DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS workflow_steps (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    workflow_id INTEGER NOT NULL,
    step_index  INTEGER NOT NULL,
    timestamp   REAL    NOT NULL,
    action      TEXT    NOT NULL,
    target      TEXT    DEFAULT '',
    parameters  TEXT    DEFAULT '{}',
    delay_ms    INTEGER DEFAULT 0,
    FOREIGN KEY (workflow_id) REFERENCES workflows(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_wf_steps_wfid
    ON workflow_steps(workflow_id, step_index);
CREATE INDEX IF NOT EXISTS idx_workflows_name
    ON workflows(name);
"""


# ── Enums ────────────────────────────────────────────────────────────────────

class StepAction(str, Enum):
    """Types of actions that can be recorded in a workflow step."""
    FOCUS_WINDOW    = "focus_window"
    OPEN_APP        = "open_app"
    CLOSE_WINDOW    = "close_window"
    SNAP_WINDOW     = "snap_window"
    MOVE_WINDOW     = "move_window"
    FILE_OPEN       = "file_open"
    FILE_SAVE       = "file_save"
    NAVIGATE_URL    = "navigate_url"
    TYPE_TEXT       = "type_text"
    WAIT            = "wait"
    CUSTOM          = "custom"


class RecordingState(str, Enum):
    IDLE        = "idle"
    RECORDING   = "recording"
    PAUSED      = "paused"


# ── Data Classes ─────────────────────────────────────────────────────────────

@dataclass
class WorkflowStep:
    """A single action in a workflow."""
    step_index: int = 0
    timestamp: float = 0.0
    action: StepAction = StepAction.CUSTOM
    target: str = ""           # window title, app path, URL, etc.
    parameters: dict = field(default_factory=dict)
    delay_ms: int = 0          # delay before this step (for replay pacing)


@dataclass
class Workflow:
    """A recorded workflow (sequence of steps)."""
    id: int = 0
    name: str = ""
    description: str = ""
    created_at: float = 0.0
    updated_at: float = 0.0
    steps: list[WorkflowStep] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)

    @property
    def step_count(self) -> int:
        return len(self.steps)

    @property
    def total_duration_ms(self) -> int:
        return sum(s.delay_ms for s in self.steps)


@dataclass
class ReplayResult:
    """Result of a workflow replay attempt."""
    success: bool = False
    steps_executed: int = 0
    steps_total: int = 0
    errors: list[str] = field(default_factory=list)
    duration_ms: int = 0


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
        conn.execute("PRAGMA foreign_keys=ON")
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


# ── Workflow Recorder ────────────────────────────────────────────────────────

class WorkflowRecorder:
    """
    Record, store, and replay user workflows.

    Usage:
        rec = WorkflowRecorder()

        # Start recording
        rec.start_recording("morning_setup", description="My daily app layout")

        # Record steps (manually or via EventBus integration)
        rec.record_step(StepAction.OPEN_APP, target="code.exe")
        rec.record_step(StepAction.SNAP_WINDOW, target="VS Code", parameters={"position": "left"})
        rec.record_step(StepAction.OPEN_APP, target="chrome.exe")
        rec.record_step(StepAction.SNAP_WINDOW, target="Chrome", parameters={"position": "right"})

        # Stop and save
        workflow = rec.stop_recording()

        # Later — replay
        result = rec.replay_workflow("morning_setup")
        print(f"Replayed {result.steps_executed}/{result.steps_total} steps")
    """

    def __init__(self):
        self._state = RecordingState.IDLE
        self._lock = threading.Lock()
        self._current_name = ""
        self._current_desc = ""
        self._current_steps: list[WorkflowStep] = []
        self._recording_start: float = 0.0
        self._last_step_ts: float = 0.0
        self._event_sub = None  # EventBus subscription

        _ensure_schema()

    @property
    def state(self) -> RecordingState:
        return self._state

    @property
    def is_recording(self) -> bool:
        return self._state == RecordingState.RECORDING

    # ── Recording ────────────────────────────────────────────────────────

    def start_recording(
        self,
        name: str,
        description: str = "",
        auto_capture_events: bool = True,
    ) -> bool:
        """
        Start recording a new workflow.

        Args:
            name: Unique workflow name.
            description: Human-readable description.
            auto_capture_events: Subscribe to EventBus for automatic capture.

        Returns:
            True if recording started.
        """
        with self._lock:
            if self._state == RecordingState.RECORDING:
                logger.warning("[WorkflowRec] Already recording — stop first")
                return False

            self._state = RecordingState.RECORDING
            self._current_name = name
            self._current_desc = description
            self._current_steps = []
            self._recording_start = time.time()
            self._last_step_ts = self._recording_start

        # Auto-capture from EventBus
        if auto_capture_events:
            self._subscribe_to_events()

        # Emit recording started event
        self._emit_event("workflow.started", {
            "name": name,
            "description": description,
        })

        logger.info(f"[WorkflowRec] 🔴 Recording started: {name}")
        return True

    def record_step(
        self,
        action: StepAction,
        target: str = "",
        parameters: dict | None = None,
    ) -> bool:
        """Record a single step in the current workflow."""
        with self._lock:
            if self._state != RecordingState.RECORDING:
                return False

            if len(self._current_steps) >= _MAX_STEPS_PER_WORKFLOW:
                logger.warning("[WorkflowRec] Max steps reached — auto-stopping")
                # Release lock before stopping
                self._state = RecordingState.IDLE
                return False

            now = time.time()
            delay_ms = int((now - self._last_step_ts) * 1000)

            step = WorkflowStep(
                step_index=len(self._current_steps),
                timestamp=now,
                action=action,
                target=target,
                parameters=parameters or {},
                delay_ms=delay_ms,
            )
            self._current_steps.append(step)
            self._last_step_ts = now

        # Emit step recorded event
        self._emit_event("workflow.step_recorded", {
            "action": action.value,
            "target": target,
            "step_index": step.step_index,
        })

        return True

    def stop_recording(self) -> Optional[Workflow]:
        """Stop recording and save the workflow."""
        with self._lock:
            if self._state != RecordingState.RECORDING:
                logger.warning("[WorkflowRec] Not recording")
                return None

            self._state = RecordingState.IDLE
            name = self._current_name
            desc = self._current_desc
            steps = list(self._current_steps)

        # Unsubscribe from EventBus
        self._unsubscribe_from_events()

        if not steps:
            logger.warning("[WorkflowRec] No steps recorded — discarding")
            return None

        # Save to database
        workflow = self._save_workflow(name, desc, steps)

        # Emit completed event
        self._emit_event("workflow.completed", {
            "name": name,
            "step_count": len(steps),
        })

        logger.info(
            f"[WorkflowRec] ⏹️ Recording stopped: {name} "
            f"({len(steps)} steps)"
        )
        return workflow

    def pause_recording(self) -> None:
        """Pause recording (delays won't accumulate)."""
        with self._lock:
            if self._state == RecordingState.RECORDING:
                self._state = RecordingState.PAUSED
                logger.info("[WorkflowRec] ⏸️ Recording paused")

    def resume_recording(self) -> None:
        """Resume recording."""
        with self._lock:
            if self._state == RecordingState.PAUSED:
                self._state = RecordingState.RECORDING
                self._last_step_ts = time.time()  # reset delay counter
                logger.info("[WorkflowRec] ▶️ Recording resumed")

    def discard_recording(self) -> None:
        """Discard the current recording without saving."""
        with self._lock:
            self._state = RecordingState.IDLE
            self._current_steps = []
        self._unsubscribe_from_events()
        logger.info("[WorkflowRec] 🗑️ Recording discarded")

    # ── Replay ───────────────────────────────────────────────────────────

    def replay_workflow(
        self,
        name: str,
        speed_factor: float = 1.0,
        dry_run: bool = False,
    ) -> ReplayResult:
        """
        Replay a saved workflow.

        Args:
            name: Workflow name to replay.
            speed_factor: 1.0 = original speed, 2.0 = 2x fast, 0.5 = half speed.
            dry_run: If True, log steps without executing.

        Returns:
            ReplayResult with execution stats.
        """
        workflow = self.get_workflow(name)
        if not workflow:
            return ReplayResult(
                errors=[f"Workflow '{name}' not found"],
            )

        result = ReplayResult(steps_total=workflow.step_count)
        start_time = time.time()

        # Emit replay started event
        self._emit_event("workflow.replay_started", {
            "name": name,
            "step_count": workflow.step_count,
            "dry_run": dry_run,
        })

        for step in workflow.steps:
            # Apply delay (scaled by speed_factor)
            if step.delay_ms > 0 and speed_factor > 0:
                adjusted_delay = step.delay_ms / 1000.0 / speed_factor
                # Cap at 5 seconds per step to prevent long waits
                adjusted_delay = min(adjusted_delay, 5.0)
                time.sleep(adjusted_delay)

            if dry_run:
                logger.info(
                    f"[WorkflowRec] [DRY RUN] Step {step.step_index}: "
                    f"{step.action.value} → {step.target}"
                )
                result.steps_executed += 1
                continue

            # Execute the step
            try:
                ok = self._execute_step(step)
                if ok:
                    result.steps_executed += 1
                else:
                    result.errors.append(
                        f"Step {step.step_index} ({step.action.value}) failed"
                    )
            except Exception as e:
                result.errors.append(
                    f"Step {step.step_index} error: {str(e)[:100]}"
                )

        result.duration_ms = int((time.time() - start_time) * 1000)
        result.success = result.steps_executed == result.steps_total

        logger.info(
            f"[WorkflowRec] ▶️ Replay complete: {name} "
            f"({result.steps_executed}/{result.steps_total} steps, "
            f"{result.duration_ms}ms)"
        )
        return result

    def _execute_step(self, step: WorkflowStep) -> bool:
        """Execute a single workflow step."""
        from os_layer.window_manager import get_window_manager, SnapPosition
        wm = get_window_manager()

        if step.action == StepAction.OPEN_APP:
            pid = wm.launch_app(step.target)
            return pid > 0

        if step.action == StepAction.FOCUS_WINDOW:
            hwnd = wm.find_window(step.target)
            if hwnd:
                wm._load_win32()
                try:
                    wm._win32gui.SetForegroundWindow(hwnd)
                    return True
                except Exception:
                    return False
            return False

        if step.action == StepAction.SNAP_WINDOW:
            hwnd = wm.find_window(step.target)
            if not hwnd:
                return False
            pos_str = step.parameters.get("position", "left")
            try:
                pos = SnapPosition(pos_str)
                return wm.snap_window(hwnd, pos)
            except ValueError:
                return False

        if step.action == StepAction.MOVE_WINDOW:
            hwnd = wm.find_window(step.target)
            if not hwnd:
                return False
            return wm.move_window(
                hwnd,
                step.parameters.get("x", 0),
                step.parameters.get("y", 0),
                step.parameters.get("w", 960),
                step.parameters.get("h", 540),
            )

        if step.action == StepAction.CLOSE_WINDOW:
            hwnd = wm.find_window(step.target)
            if hwnd:
                return wm.close_window(hwnd)
            return False

        if step.action == StepAction.WAIT:
            wait_ms = step.parameters.get("duration_ms", 1000)
            time.sleep(wait_ms / 1000.0)
            return True

        # Custom steps — just log them
        logger.debug(
            f"[WorkflowRec] Custom step: {step.action.value} → {step.target}"
        )
        return True

    # ── CRUD ─────────────────────────────────────────────────────────────

    def get_workflow(self, name: str) -> Optional[Workflow]:
        """Load a workflow by name."""
        try:
            conn = _get_conn()
            row = conn.execute(
                "SELECT * FROM workflows WHERE name = ?", (name,)
            ).fetchone()

            if not row:
                return None

            steps_rows = conn.execute(
                """SELECT * FROM workflow_steps
                   WHERE workflow_id = ?
                   ORDER BY step_index""",
                (row["id"],),
            ).fetchall()

            steps = [
                WorkflowStep(
                    step_index=s["step_index"],
                    timestamp=s["timestamp"],
                    action=StepAction(s["action"]),
                    target=s["target"],
                    parameters=json.loads(s["parameters"] or "{}"),
                    delay_ms=s["delay_ms"],
                )
                for s in steps_rows
            ]

            return Workflow(
                id=row["id"],
                name=row["name"],
                description=row["description"] or "",
                created_at=row["created_at"],
                updated_at=row["updated_at"],
                steps=steps,
                tags=json.loads(row["tags"] or "[]"),
            )

        except Exception as e:
            logger.warning(f"[WorkflowRec] get_workflow failed: {e}")
            return None

    def list_workflows(self) -> list[dict]:
        """List all saved workflows (name, step_count, created_at)."""
        try:
            conn = _get_conn()
            rows = conn.execute(
                """SELECT name, description, step_count, created_at, updated_at
                   FROM workflows
                   ORDER BY updated_at DESC"""
            ).fetchall()
            return [dict(r) for r in rows]
        except Exception:
            return []

    def delete_workflow(self, name: str) -> bool:
        """Delete a workflow and its steps."""
        try:
            conn = _get_conn()
            row = conn.execute(
                "SELECT id FROM workflows WHERE name = ?", (name,)
            ).fetchone()
            if not row:
                return False

            conn.execute(
                "DELETE FROM workflow_steps WHERE workflow_id = ?", (row["id"],)
            )
            conn.execute(
                "DELETE FROM workflows WHERE id = ?", (row["id"],)
            )
            conn.commit()
            logger.info(f"[WorkflowRec] Deleted workflow: {name}")
            return True
        except Exception as e:
            logger.warning(f"[WorkflowRec] delete_workflow failed: {e}")
            return False

    # ── Private ──────────────────────────────────────────────────────────

    def _save_workflow(
        self,
        name: str,
        description: str,
        steps: list[WorkflowStep],
    ) -> Workflow:
        """Save a workflow and its steps to the database."""
        now = time.time()
        total_duration = sum(s.delay_ms for s in steps) / 1000.0

        try:
            conn = _get_conn()

            # Upsert workflow
            conn.execute(
                """INSERT INTO workflows
                   (name, description, created_at, updated_at, step_count, total_duration)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(name) DO UPDATE SET
                       description = excluded.description,
                       updated_at = excluded.updated_at,
                       step_count = excluded.step_count,
                       total_duration = excluded.total_duration""",
                (name, description, now, now, len(steps), total_duration),
            )

            # Get the workflow ID
            row = conn.execute(
                "SELECT id FROM workflows WHERE name = ?", (name,)
            ).fetchone()
            wf_id = row["id"]

            # Delete old steps (for re-recording)
            conn.execute(
                "DELETE FROM workflow_steps WHERE workflow_id = ?", (wf_id,)
            )

            # Insert new steps
            for step in steps:
                conn.execute(
                    """INSERT INTO workflow_steps
                       (workflow_id, step_index, timestamp, action, target,
                        parameters, delay_ms)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        wf_id,
                        step.step_index,
                        step.timestamp,
                        step.action.value,
                        step.target,
                        json.dumps(step.parameters),
                        step.delay_ms,
                    ),
                )

            conn.commit()
            self._prune_old_workflows(conn)

        except Exception as e:
            logger.warning(f"[WorkflowRec] save failed: {e}")

        return Workflow(
            name=name,
            description=description,
            created_at=now,
            updated_at=now,
            steps=steps,
        )

    def _prune_old_workflows(self, conn: sqlite3.Connection) -> None:
        """Keep only the most recent N workflows."""
        try:
            conn.execute(
                """DELETE FROM workflows
                   WHERE id NOT IN (
                       SELECT id FROM workflows
                       ORDER BY updated_at DESC
                       LIMIT ?
                   )""",
                (_MAX_STORED_WORKFLOWS,),
            )
        except Exception:
            pass

    def _subscribe_to_events(self) -> None:
        """Subscribe to EventBus for automatic step capture."""
        try:
            from os_layer.event_bus import get_event_bus, EventType

            bus = get_event_bus()

            def _on_event(event):
                if not self.is_recording:
                    return

                # Map EventBus events to workflow steps
                mapping = {
                    EventType.WINDOW_FOCUS_CHANGED: StepAction.FOCUS_WINDOW,
                    EventType.WINDOW_OPENED:        StepAction.OPEN_APP,
                    EventType.WINDOW_CLOSED:         StepAction.CLOSE_WINDOW,
                    EventType.FILE_CREATED:          StepAction.FILE_OPEN,
                    EventType.FILE_MODIFIED:          StepAction.FILE_SAVE,
                }

                action = mapping.get(event.event_type)
                if action:
                    target = (
                        event.payload.get("title")
                        or event.payload.get("app")
                        or event.payload.get("path")
                        or ""
                    )
                    self.record_step(action, target=target, parameters=event.payload)

            self._event_sub = bus.subscribe(
                callback=_on_event,
                event_types={
                    EventType.WINDOW_FOCUS_CHANGED,
                    EventType.WINDOW_OPENED,
                    EventType.WINDOW_CLOSED,
                    EventType.FILE_CREATED,
                    EventType.FILE_MODIFIED,
                },
                name="workflow_recorder",
            )
        except Exception as e:
            logger.debug(f"[WorkflowRec] EventBus subscription failed: {e}")

    def _unsubscribe_from_events(self) -> None:
        """Remove EventBus subscription."""
        if self._event_sub:
            try:
                from os_layer.event_bus import get_event_bus
                get_event_bus().unsubscribe(self._event_sub)
                self._event_sub = None
            except Exception:
                pass

    def _emit_event(self, event_type_str: str, payload: dict) -> None:
        """Emit a workflow event to the EventBus (best-effort)."""
        try:
            from os_layer.event_bus import get_event_bus, EventType
            type_map = {
                "workflow.started": EventType.WORKFLOW_STARTED,
                "workflow.step_recorded": EventType.WORKFLOW_STEP_RECORDED,
                "workflow.completed": EventType.WORKFLOW_COMPLETED,
                "workflow.replay_started": EventType.WORKFLOW_REPLAY_STARTED,
            }
            evt = type_map.get(event_type_str)
            if evt:
                get_event_bus().emit(evt, source="workflow_recorder", payload=payload)
        except Exception:
            pass


# ── Singleton ────────────────────────────────────────────────────────────────

_instance: Optional[WorkflowRecorder] = None
_instance_lock = threading.Lock()


def get_workflow_recorder() -> WorkflowRecorder:
    """Thread-safe singleton accessor."""
    global _instance
    if _instance is None:
        with _instance_lock:
            if _instance is None:
                _instance = WorkflowRecorder()
    return _instance
