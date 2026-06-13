"""
agent/scheduler.py
==================
Background Task Scheduler for JARVIS.

Runs whitelisted tasks on cron/interval schedules via APScheduler.
Non-whitelisted tasks are rejected — JARVIS cannot self-schedule.

Architecture
------------
  - APScheduler BackgroundScheduler runs in its own daemon thread.
  - Each job dispatches into the existing TaskQueue at LOW priority.
  - All scheduled tasks carry source="scheduler" for log disambiguation.
  - Jobs respect UI mute state: if muted, results log silently (no TTS).
  - max_instances=1 prevents overlap (e.g., health check > 30min).

Safety
------
  - ONLY tasks in APPROVED_BACKGROUND_TASKS can be scheduled.
  - No API exists for the LLM to add new jobs at runtime.
  - clear_temp_files is marked safe=False (requires voice confirmation).
  - Failed jobs log the error and do NOT retry automatically.
"""

from __future__ import annotations

import logging
import threading
from typing import Callable, Optional

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from agent.task_queue import get_queue, TaskPriority


# Silence APScheduler's verbose default logging
logging.getLogger("apscheduler").setLevel(logging.WARNING)


# ── Approved Background Tasks (WHITELIST) ────────────────────────────────────
# This is the ONLY place scheduled tasks are defined.
# JARVIS cannot add, remove, or modify entries at runtime.

APPROVED_BACKGROUND_TASKS: list[dict] = [
    {
        "id":       "daily_news_briefing",
        "goal":     "Give me a brief daily news summary covering top world headlines",
        "trigger":  "cron",
        "schedule": {"hour": 8, "minute": 0},
        "safe":     True,
    },
    {
        "id":       "system_health_check",
        "goal":     "Run a system health check: CPU, RAM, disk usage, and report any issues",
        "trigger":  "interval",
        "schedule": {"minutes": 30},
        "safe":     True,
    },
    {
        "id":       "clear_temp_files",
        "goal":     "Clear temporary files from the system temp directory to free disk space",
        "trigger":  "cron",
        "schedule": {"day_of_week": "sun", "hour": 3, "minute": 0},
        "safe":     False,  # File deletion — requires voice confirmation
    },
    {
        "id":       "daily_task_summary",
        "goal":     "Summarize all tasks I completed today and any pending ones",
        "trigger":  "cron",
        "schedule": {"hour": 20, "minute": 0},
        "safe":     True,
    },
]


# ── Scheduler ────────────────────────────────────────────────────────────────

class JarvisScheduler:
    """
    Background task scheduler backed by APScheduler.

    Args:
        speak_fn:   TTS callback from JarvisLive.speak — sends text to audio session.
        ui:         JarvisUI reference — used to check mute state before speaking.
    """

    _TIMEZONE = "Asia/Karachi"  # PKT — all schedule times are local

    def __init__(
        self,
        speak_fn: Optional[Callable[[str], None]] = None,
        ui: object = None,
    ):
        self._speak_fn = speak_fn
        self._ui = ui
        self._scheduler: Optional[BackgroundScheduler] = None
        self._started = False
        self._lock = threading.Lock()

    # ── Lifecycle ─────────────────────────────────────────────────────────

    def start(self) -> None:
        """Register all whitelisted jobs and start the scheduler."""
        with self._lock:
            if self._started:
                return

            self._scheduler = BackgroundScheduler(
                timezone=self._TIMEZONE,
                job_defaults={
                    "max_instances": 1,
                    "coalesce": True,       # Merge missed runs into one
                    "misfire_grace_time": 300,  # 5 min grace for missed triggers
                },
            )

            registered = 0
            for task_def in APPROVED_BACKGROUND_TASKS:
                try:
                    self._register_job(task_def)
                    registered += 1
                except Exception as exc:
                    print(f"[Scheduler] Failed to register '{task_def['id']}': {exc}")

            self._scheduler.start()
            self._started = True
            print(f"[Scheduler] Started with {registered} background jobs (tz={self._TIMEZONE})")

            # Log next-run times (only available after start)
            for job in self._scheduler.get_jobs():
                nrt = job.next_run_time
                next_str = nrt.strftime('%H:%M %Z') if nrt else 'N/A'
                print(f"[Scheduler]   {job.id} -> next: {next_str}")

    def stop(self) -> None:
        """Graceful shutdown — waits for running jobs to finish."""
        with self._lock:
            if self._scheduler and self._started:
                self._scheduler.shutdown(wait=True)
                self._started = False
                print("[Scheduler] Stopped")

    # ── Job registration ──────────────────────────────────────────────────

    def _register_job(self, task_def: dict) -> None:
        """Register a single whitelisted task as an APScheduler job."""
        job_id = task_def["id"]
        trigger_type = task_def["trigger"]
        schedule = task_def["schedule"]

        if trigger_type == "cron":
            trigger = CronTrigger(**schedule, timezone=self._TIMEZONE)
        elif trigger_type == "interval":
            trigger = IntervalTrigger(**schedule, timezone=self._TIMEZONE)
        else:
            raise ValueError(f"Unknown trigger type: {trigger_type}")

        self._scheduler.add_job(
            func=self._dispatch_task,
            trigger=trigger,
            args=[task_def],
            id=job_id,
            name=task_def["goal"][:60],
            replace_existing=True,
        )
        print(f"[Scheduler] Registered: {job_id} ({trigger_type})")

    # ── Task dispatch ─────────────────────────────────────────────────────

    def _dispatch_task(self, task_def: dict) -> None:
        """
        Submit a scheduled task to the TaskQueue for execution.

        Safety rules:
          - safe=True  → runs silently in background
          - safe=False → logs a warning, skips execution (voice confirmation not available
                         in background context — user must manually approve)
          - Muted UI   → suppress TTS, log result silently
        """
        job_id = task_def["id"]
        goal = task_def["goal"]
        is_safe = task_def.get("safe", False)

        # ── Safety gate: non-safe tasks cannot run unattended ─────────────
        if not is_safe:
            print(
                f"[Scheduler] BLOCKED: '{job_id}' is not marked safe. "
                f"Requires manual voice confirmation. Skipping."
            )
            return

        # ── Mute-aware speak wrapper ──────────────────────────────────────
        def _speak_if_unmuted(text: str) -> None:
            is_muted = getattr(self._ui, "muted", False) if self._ui else True
            if is_muted:
                print(f"[Scheduler] (muted) {text[:120]}")
                return
            if self._speak_fn:
                try:
                    self._speak_fn(text)
                except Exception as exc:
                    print(f"[Scheduler] Speak failed: {exc}")

        # ── Submit to TaskQueue ───────────────────────────────────────────
        try:
            queue = get_queue()
            task_id = queue.submit(
                goal=goal,
                priority=TaskPriority.LOW,
                speak=_speak_if_unmuted,
                source="scheduler",
            )
            print(f"[Scheduler] Dispatched '{job_id}' -> TaskQueue [{task_id}]")

        except Exception as exc:
            print(f"[Scheduler] Dispatch failed for '{job_id}': {exc}")
            # No retry — prevents spam loops

    # ── Status / Introspection ────────────────────────────────────────────

    def list_jobs(self) -> list[dict]:
        """Return a snapshot of all scheduled jobs for status display."""
        if not self._scheduler:
            return []

        result = []
        for job in self._scheduler.get_jobs():
            next_time = None
            if job.next_run_time:
                next_time = job.next_run_time.strftime("%Y-%m-%d %H:%M %Z")

            # Find the matching whitelist entry for the safe flag
            task_def = next(
                (t for t in APPROVED_BACKGROUND_TASKS if t["id"] == job.id),
                {},
            )

            result.append({
                "id": job.id,
                "goal": job.name,
                "next_run": next_time,
                "safe": task_def.get("safe", False),
            })

        return result

    @property
    def is_running(self) -> bool:
        return self._started


# ── Module-level singleton ────────────────────────────────────────────────────

_scheduler: Optional[JarvisScheduler] = None
_init_lock = threading.Lock()


def get_scheduler() -> JarvisScheduler:
    """Get or create the module-level scheduler singleton (NOT started yet)."""
    global _scheduler
    with _init_lock:
        if _scheduler is None:
            _scheduler = JarvisScheduler()
    return _scheduler
