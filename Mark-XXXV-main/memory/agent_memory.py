"""
memory/agent_memory.py
======================
SQLite-backed persistence for multi-day agentic tasks.

Provides durable storage for:
- Tasks: high-level goals with status tracking
- Episodes: individual execution runs within a task
- Steps: granular tool calls + results within an episode

Schema Design
-------------
- Tasks survive reboots, power cuts, and crashes.
- Each task has a lifecycle: pending → running → paused → done → failed.
- Episodes track individual planning/execution cycles.
- Steps log every tool call for audit, replay, and self-correction.

Thread Safety
-------------
All writes use WAL mode + serialized connection pooling.
Safe to call from async executors and daemon threads.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path
from typing import Any, Optional


# ── Constants ────────────────────────────────────────────────────────────────

DB_PATH = Path(__file__).parent / "agent_episodes.db"

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS tasks (
    id          TEXT PRIMARY KEY,
    goal        TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'pending',
    priority    TEXT NOT NULL DEFAULT 'normal',
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL,
    context     TEXT DEFAULT '{}',
    parent_id   TEXT REFERENCES tasks(id) ON DELETE SET NULL,
    result      TEXT DEFAULT NULL,
    error       TEXT DEFAULT NULL,
    max_steps   INTEGER NOT NULL DEFAULT 25,
    step_count  INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS episodes (
    id          TEXT PRIMARY KEY,
    task_id     TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    started_at  REAL NOT NULL,
    ended_at    REAL DEFAULT NULL,
    status      TEXT NOT NULL DEFAULT 'running',
    plan        TEXT DEFAULT '[]',
    outcome     TEXT DEFAULT NULL
);

CREATE TABLE IF NOT EXISTS steps (
    id          TEXT PRIMARY KEY,
    episode_id  TEXT NOT NULL REFERENCES episodes(id) ON DELETE CASCADE,
    task_id     TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    step_num    INTEGER NOT NULL,
    tool_name   TEXT NOT NULL,
    tool_args   TEXT NOT NULL DEFAULT '{}',
    result      TEXT DEFAULT NULL,
    error       TEXT DEFAULT NULL,
    started_at  REAL NOT NULL,
    ended_at    REAL DEFAULT NULL,
    observation TEXT DEFAULT NULL
);

CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_episodes_task ON episodes(task_id);
CREATE INDEX IF NOT EXISTS idx_steps_episode ON steps(episode_id);
CREATE INDEX IF NOT EXISTS idx_steps_task ON steps(task_id);
"""


# ── Enums ────────────────────────────────────────────────────────────────────

class TaskStatus(str, Enum):
    PENDING  = "pending"
    RUNNING  = "running"
    PAUSED   = "paused"
    DONE     = "done"
    FAILED   = "failed"


class TaskPriority(str, Enum):
    LOW    = "low"
    NORMAL = "normal"
    HIGH   = "high"


class EpisodeStatus(str, Enum):
    RUNNING = "running"
    SUCCESS = "success"
    FAILED  = "failed"
    TIMEOUT = "timeout"


# ── Data Classes ─────────────────────────────────────────────────────────────

@dataclass
class Task:
    id: str
    goal: str
    status: str = TaskStatus.PENDING
    priority: str = TaskPriority.NORMAL
    created_at: float = 0.0
    updated_at: float = 0.0
    context: dict = field(default_factory=dict)
    parent_id: Optional[str] = None
    result: Optional[str] = None
    error: Optional[str] = None
    max_steps: int = 25
    step_count: int = 0


@dataclass
class Episode:
    id: str
    task_id: str
    started_at: float = 0.0
    ended_at: Optional[float] = None
    status: str = EpisodeStatus.RUNNING
    plan: list = field(default_factory=list)
    outcome: Optional[str] = None


@dataclass
class Step:
    id: str
    episode_id: str
    task_id: str
    step_num: int
    tool_name: str
    tool_args: dict = field(default_factory=dict)
    result: Optional[str] = None
    error: Optional[str] = None
    started_at: float = 0.0
    ended_at: Optional[float] = None
    observation: Optional[str] = None


# ── Connection Pool ──────────────────────────────────────────────────────────

class _ConnectionPool:
    """Thread-local SQLite connection pool with WAL mode."""

    def __init__(self, db_path: Path):
        self._db_path = db_path
        self._local = threading.local()
        self._lock = threading.Lock()
        self._initialized = False

    def _ensure_schema(self, conn: sqlite3.Connection) -> None:
        if not self._initialized:
            with self._lock:
                if not self._initialized:
                    conn.executescript(_SCHEMA_SQL)
                    self._initialized = True

    def get(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(
                str(self._db_path),
                check_same_thread=False,
                timeout=10.0,
            )
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA busy_timeout=5000")
            conn.row_factory = sqlite3.Row
            self._local.conn = conn
        self._ensure_schema(conn)
        return conn


_pool = _ConnectionPool(DB_PATH)


@contextmanager
def _transaction():
    """Context manager: yields a connection, auto-commits or rolls back."""
    conn = _pool.get()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


# ── AgentMemory (public API) ─────────────────────────────────────────────────

class AgentMemory:
    """
    Persistent memory for the JARVIS agentic system.

    Usage
    -----
        mem = AgentMemory()

        # Create a multi-day task
        task = mem.create_task("Learn Python in a week", priority="high")

        # Start an episode (execution run)
        ep = mem.start_episode(task.id, plan=["search resources", "create schedule"])

        # Log tool calls
        step = mem.log_step(ep.id, task.id, step_num=1,
                            tool_name="web_search", tool_args={"query": "python tutorials"})
        mem.complete_step(step.id, result="Found 10 tutorials")

        # Complete the episode
        mem.complete_episode(ep.id, status="success", outcome="Created day 1 plan")

        # Resume tomorrow
        pending = mem.get_pending_tasks()
    """

    # ── Task CRUD ────────────────────────────────────────────────────────

    def create_task(
        self,
        goal: str,
        priority: str = "normal",
        context: Optional[dict] = None,
        parent_id: Optional[str] = None,
        max_steps: int = 25,
    ) -> Task:
        now = time.time()
        task = Task(
            id=str(uuid.uuid4())[:12],
            goal=goal,
            status=TaskStatus.PENDING,
            priority=priority,
            created_at=now,
            updated_at=now,
            context=context or {},
            parent_id=parent_id,
            max_steps=max_steps,
        )
        with _transaction() as conn:
            conn.execute(
                """INSERT INTO tasks
                   (id, goal, status, priority, created_at, updated_at,
                    context, parent_id, max_steps)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (task.id, task.goal, task.status, task.priority,
                 task.created_at, task.updated_at,
                 json.dumps(task.context), task.parent_id, task.max_steps),
            )
        print(f"[AgentMemory] Task created: {task.id} -> {goal[:60]}")
        return task

    def get_task(self, task_id: str) -> Optional[Task]:
        conn = _pool.get()
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if not row:
            return None
        return self._row_to_task(row)

    def update_task_status(
        self,
        task_id: str,
        status: str,
        result: Optional[str] = None,
        error: Optional[str] = None,
    ) -> None:
        with _transaction() as conn:
            conn.execute(
                """UPDATE tasks
                   SET status = ?, updated_at = ?, result = ?, error = ?
                   WHERE id = ?""",
                (status, time.time(), result, error, task_id),
            )
        print(f"[AgentMemory] Task {task_id} -> {status}")

    def increment_steps(self, task_id: str) -> int:
        """Increment step_count, return new count."""
        with _transaction() as conn:
            conn.execute(
                "UPDATE tasks SET step_count = step_count + 1, updated_at = ? WHERE id = ?",
                (time.time(), task_id),
            )
            row = conn.execute(
                "SELECT step_count FROM tasks WHERE id = ?", (task_id,)
            ).fetchone()
            return row["step_count"] if row else 0

    def get_pending_tasks(self) -> list[Task]:
        conn = _pool.get()
        rows = conn.execute(
            """SELECT * FROM tasks
               WHERE status IN ('pending', 'running', 'paused')
               ORDER BY
                 CASE priority WHEN 'high' THEN 0 WHEN 'normal' THEN 1 ELSE 2 END,
                 created_at ASC"""
        ).fetchall()
        return [self._row_to_task(r) for r in rows]

    def get_recent_tasks(self, limit: int = 20) -> list[Task]:
        conn = _pool.get()
        rows = conn.execute(
            "SELECT * FROM tasks ORDER BY updated_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [self._row_to_task(r) for r in rows]

    # ── Episode CRUD ─────────────────────────────────────────────────────

    def start_episode(
        self,
        task_id: str,
        plan: Optional[list[str]] = None,
    ) -> Episode:
        now = time.time()
        ep = Episode(
            id=str(uuid.uuid4())[:12],
            task_id=task_id,
            started_at=now,
            plan=plan or [],
        )
        with _transaction() as conn:
            conn.execute(
                """INSERT INTO episodes (id, task_id, started_at, plan)
                   VALUES (?, ?, ?, ?)""",
                (ep.id, ep.task_id, ep.started_at, json.dumps(ep.plan)),
            )
            # Also mark the task as running
            conn.execute(
                "UPDATE tasks SET status = 'running', updated_at = ? WHERE id = ?",
                (now, task_id),
            )
        print(f"[AgentMemory] Episode {ep.id} started for task {task_id}")
        return ep

    def complete_episode(
        self,
        episode_id: str,
        status: str = "success",
        outcome: Optional[str] = None,
    ) -> None:
        with _transaction() as conn:
            conn.execute(
                """UPDATE episodes
                   SET ended_at = ?, status = ?, outcome = ?
                   WHERE id = ?""",
                (time.time(), status, outcome, episode_id),
            )
        print(f"[AgentMemory] Episode {episode_id} -> {status}")

    def get_episodes(self, task_id: str) -> list[Episode]:
        conn = _pool.get()
        rows = conn.execute(
            "SELECT * FROM episodes WHERE task_id = ? ORDER BY started_at ASC",
            (task_id,),
        ).fetchall()
        return [self._row_to_episode(r) for r in rows]

    # ── Step CRUD ────────────────────────────────────────────────────────

    def log_step(
        self,
        episode_id: str,
        task_id: str,
        step_num: int,
        tool_name: str,
        tool_args: Optional[dict] = None,
    ) -> Step:
        now = time.time()
        step = Step(
            id=str(uuid.uuid4())[:12],
            episode_id=episode_id,
            task_id=task_id,
            step_num=step_num,
            tool_name=tool_name,
            tool_args=tool_args or {},
            started_at=now,
        )
        with _transaction() as conn:
            conn.execute(
                """INSERT INTO steps
                   (id, episode_id, task_id, step_num, tool_name, tool_args, started_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (step.id, step.episode_id, step.task_id, step.step_num,
                 step.tool_name, json.dumps(step.tool_args), step.started_at),
            )
        return step

    def complete_step(
        self,
        step_id: str,
        result: Optional[str] = None,
        error: Optional[str] = None,
        observation: Optional[str] = None,
    ) -> None:
        with _transaction() as conn:
            conn.execute(
                """UPDATE steps
                   SET ended_at = ?, result = ?, error = ?, observation = ?
                   WHERE id = ?""",
                (time.time(), result, error, observation, step_id),
            )

    def get_steps(self, episode_id: str) -> list[Step]:
        conn = _pool.get()
        rows = conn.execute(
            "SELECT * FROM steps WHERE episode_id = ? ORDER BY step_num ASC",
            (episode_id,),
        ).fetchall()
        return [self._row_to_step(r) for r in rows]

    def get_task_history(self, task_id: str) -> list[Step]:
        """Get all steps across all episodes for a task — full audit trail."""
        conn = _pool.get()
        rows = conn.execute(
            """SELECT s.* FROM steps s
               JOIN episodes e ON s.episode_id = e.id
               WHERE s.task_id = ?
               ORDER BY e.started_at ASC, s.step_num ASC""",
            (task_id,),
        ).fetchall()
        return [self._row_to_step(r) for r in rows]

    # ── Context helpers ──────────────────────────────────────────────────

    def update_context(self, task_id: str, updates: dict) -> None:
        """Merge new key/value pairs into a task's context blob."""
        with _transaction() as conn:
            row = conn.execute(
                "SELECT context FROM tasks WHERE id = ?", (task_id,)
            ).fetchone()
            if not row:
                return
            ctx = json.loads(row["context"] or "{}")
            ctx.update(updates)
            conn.execute(
                "UPDATE tasks SET context = ?, updated_at = ? WHERE id = ?",
                (json.dumps(ctx), time.time(), task_id),
            )

    def get_context(self, task_id: str) -> dict:
        conn = _pool.get()
        row = conn.execute(
            "SELECT context FROM tasks WHERE id = ?", (task_id,)
        ).fetchone()
        if not row:
            return {}
        return json.loads(row["context"] or "{}")

    # ── Summary / Reporting ──────────────────────────────────────────────

    def get_task_summary(self, task_id: str) -> dict[str, Any]:
        """Build a compact summary dict for injection into LLM prompts."""
        task = self.get_task(task_id)
        if not task:
            return {}
        episodes = self.get_episodes(task_id)
        total_steps = sum(
            len(self.get_steps(ep.id)) for ep in episodes
        )
        return {
            "task_id": task.id,
            "goal": task.goal,
            "status": task.status,
            "priority": task.priority,
            "step_count": task.step_count,
            "total_steps_logged": total_steps,
            "episodes": len(episodes),
            "context": task.context,
            "last_updated": task.updated_at,
            "result": task.result,
            "error": task.error,
        }

    def get_active_summary(self) -> str:
        """
        One-liner summary of all active tasks, suitable for prompt injection.
        Returns empty string if no active tasks.
        """
        tasks = self.get_pending_tasks()
        if not tasks:
            return ""
        lines = ["Active tasks:"]
        for t in tasks:
            age = _format_age(t.created_at)
            lines.append(
                f"  [{t.priority.upper()}] {t.goal[:50]} "
                f"({t.status}, {t.step_count} steps, {age})"
            )
        return "\n".join(lines)

    # ── ReAct Integration ─────────────────────────────────────────────────

    def get_or_resume_task(
        self,
        goal: str,
        priority: str = "normal",
        max_steps: int = 25,
    ) -> Task:
        """
        Find an existing pending/paused/running task with the same goal,
        or create a new one. Prevents duplicate tasks for the same objective.

        Match criteria: exact goal text + status in (pending, running, paused).
        """
        conn = _pool.get()
        row = conn.execute(
            """SELECT * FROM tasks
               WHERE goal = ? AND status IN ('pending', 'running', 'paused')
               ORDER BY updated_at DESC LIMIT 1""",
            (goal,),
        ).fetchone()

        if row:
            task = self._row_to_task(row)
            print(f"[AgentMemory] Resuming existing task: {task.id} ({task.status})")
            return task

        return self.create_task(goal=goal, priority=priority, max_steps=max_steps)

    def format_scratchpad(self, task_id: str) -> str:
        """
        Format all steps from the latest episode of a task into a text
        scratchpad suitable for LLM prompt injection.

        Returns empty string if no steps exist.
        """
        episodes = self.get_episodes(task_id)
        if not episodes:
            return ""

        latest = episodes[-1]
        steps = self.get_steps(latest.id)
        if not steps:
            return ""

        lines = [f"Previous execution (episode {latest.id}):"]
        for step in steps:
            status = "OK" if step.result else "FAILED"
            obs = step.observation or step.result or step.error or "no output"
            lines.append(
                f"  Step {step.step_num}: [{step.tool_name}] -> {status}: "
                f"{obs[:200]}"
            )

        return "\n".join(lines)

    # ── Cleanup ──────────────────────────────────────────────────────────

    def prune_old_tasks(self, max_age_days: int = 30) -> int:
        """Delete completed/failed tasks older than max_age_days."""
        cutoff = time.time() - (max_age_days * 86400)
        with _transaction() as conn:
            cursor = conn.execute(
                """DELETE FROM tasks
                   WHERE status IN ('done', 'failed')
                   AND updated_at < ?""",
                (cutoff,),
            )
            deleted = cursor.rowcount
        if deleted:
            print(f"[AgentMemory] Pruned {deleted} old tasks")
        return deleted

    # ── Row Converters ───────────────────────────────────────────────────

    @staticmethod
    def _row_to_task(row: sqlite3.Row) -> Task:
        return Task(
            id=row["id"],
            goal=row["goal"],
            status=row["status"],
            priority=row["priority"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            context=json.loads(row["context"] or "{}"),
            parent_id=row["parent_id"],
            result=row["result"],
            error=row["error"],
            max_steps=row["max_steps"],
            step_count=row["step_count"],
        )

    @staticmethod
    def _row_to_episode(row: sqlite3.Row) -> Episode:
        return Episode(
            id=row["id"],
            task_id=row["task_id"],
            started_at=row["started_at"],
            ended_at=row["ended_at"],
            status=row["status"],
            plan=json.loads(row["plan"] or "[]"),
            outcome=row["outcome"],
        )

    @staticmethod
    def _row_to_step(row: sqlite3.Row) -> Step:
        return Step(
            id=row["id"],
            episode_id=row["episode_id"],
            task_id=row["task_id"],
            step_num=row["step_num"],
            tool_name=row["tool_name"],
            tool_args=json.loads(row["tool_args"] or "{}"),
            result=row["result"],
            error=row["error"],
            started_at=row["started_at"],
            ended_at=row["ended_at"],
            observation=row["observation"],
        )


# ── Helpers ──────────────────────────────────────────────────────────────────

def _format_age(created_at: float) -> str:
    """Human-readable age string."""
    delta = time.time() - created_at
    if delta < 60:
        return "just now"
    if delta < 3600:
        return f"{int(delta / 60)}m ago"
    if delta < 86400:
        return f"{int(delta / 3600)}h ago"
    return f"{int(delta / 86400)}d ago"


# ── Singleton ────────────────────────────────────────────────────────────────

_instance: Optional[AgentMemory] = None
_instance_lock = threading.Lock()


def get_agent_memory() -> AgentMemory:
    """Thread-safe singleton accessor."""
    global _instance
    if _instance is None:
        with _instance_lock:
            if _instance is None:
                _instance = AgentMemory()
    return _instance
