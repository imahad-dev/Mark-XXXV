"""
os_layer/goal_orchestrator.py — Long-Term Goal Orchestration Daemon
=====================================================================
Manages continuous, multi-hour goal loops. Decomposes high-level goals
into sub-tasks, dispatches them via ParallelAgentEngine (dedicated background pool),
handles suspension/approval gates, and persists state to SQLite via AgentMemory.

Architecture Constraints:
    1. Dedicated Background Dispatch: Does NOT use interactive TaskQueue.
    2. Shared Concurrency Ceiling: Calls get_parallel_engine() singleton.
    3. Safety Caps: Checks GOAL_MAX_COST_USD and GOAL_MAX_DELEGATION_DEPTH.
    4. Event-Driven: Emits and listens to EventBus events for goal lifecycle.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from enum import Enum
from typing import Any, Callable, Optional

from core.config import config
from core.credit_tracker import CreditTracker
from memory.agent_memory import get_agent_memory, Task, TaskStatus

logger = logging.getLogger(__name__)


# ── Enums ────────────────────────────────────────────────────────────────────

class GoalState(str, Enum):
    PLANNING = "planning"
    EXECUTING = "executing"
    SUSPENDED = "suspended"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


# ── GoalOrchestrator Class ───────────────────────────────────────────────────

class GoalOrchestrator:
    """
    Autonomous daemon managing long-running, multi-phase goals.
    """

    def __init__(self, speak: Optional[Callable] = None):
        self._mem = get_agent_memory()
        self._speak = speak
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._cancel_events: dict[str, threading.Event] = {}
        self._approval_events: dict[str, threading.Event] = {}

    def set_speak(self, speak: Callable) -> None:
        """Set or update the speech output callback."""
        self._speak = speak

    def start(self) -> None:
        """Start the background orchestration loop thread."""
        with self._lock:
            if self._running:
                return
            self._running = True
            self._thread = threading.Thread(
                target=self._orchestration_loop,
                daemon=True,
                name="GoalOrchestratorDaemon",
            )
            self._thread.start()
            logger.info("[GoalOrchestrator] Daemon started")

    def stop(self) -> None:
        """Stop the orchestration loop thread."""
        with self._lock:
            self._running = False
            for cancel_evt in list(self._cancel_events.values()):
                cancel_evt.set()
            for app_evt in list(self._approval_events.values()):
                app_evt.set()
            logger.info("[GoalOrchestrator] Daemon stopped")

    # ── Public API ────────────────────────────────────────────────────────────

    def submit_goal(self, goal: str, priority: str = "high") -> str:
        """
        Decompose a high-level goal into sub-tasks and submit for execution.
        Returns the parent goal task ID.
        """
        task = self._mem.create_task(
            goal=goal,
            priority=priority,
            max_steps=getattr(config, "GOAL_MAX_TOTAL_STEPS", 100),
            context={"goal_type": "long_term_parent", "delegation_depth": 0},
        )

        with self._lock:
            self._cancel_events[task.id] = threading.Event()
            self._approval_events[task.id] = threading.Event()

        # Emit GOAL_STARTED event
        self._publish_event("GOAL_STARTED", {
            "goal_id": task.id,
            "goal": goal,
            "priority": priority,
        })

        if self._speak:
            self._speak(f"Long-term goal accepted, sir: {goal[:60]}")

        logger.info(f"[GoalOrchestrator] Goal submitted: [{task.id}] {goal[:60]}")
        return task.id

    def cancel_goal(self, goal_id: str) -> bool:
        """Cancel a goal and all its sub-tasks."""
        with self._lock:
            cancel_evt = self._cancel_events.get(goal_id)
            if cancel_evt:
                cancel_evt.set()

            app_evt = self._approval_events.get(goal_id)
            if app_evt:
                app_evt.set()

            task = self._mem.get_task(goal_id)
            if not task:
                return False

            self._mem.update_task_status(goal_id, TaskStatus.PAUSED, result="Goal cancelled by user")
            
            # Cancel any child tasks
            pending = self._mem.get_pending_tasks()
            for child in pending:
                if child.parent_id == goal_id:
                    self._mem.update_task_status(child.id, TaskStatus.PAUSED, result="Parent goal cancelled")

            logger.info(f"[GoalOrchestrator] Goal [{goal_id}] cancelled")
            return True

    def resume_goal(self, event_or_goal_id: Any) -> None:
        """
        Resume a suspended goal (e.g., after user confirmation via EventBus or voice).
        """
        goal_id = ""
        if isinstance(event_or_goal_id, str):
            goal_id = event_or_goal_id
        elif hasattr(event_or_goal_id, "payload") and isinstance(event_or_goal_id.payload, dict):
            goal_id = event_or_goal_id.payload.get("goal_id", "")

        if not goal_id:
            logger.warning("[GoalOrchestrator] Invalid resume payload")
            return

        with self._lock:
            task = self._mem.get_task(goal_id)
            if not task:
                logger.warning(f"[GoalOrchestrator] Cannot resume unknown goal: {goal_id}")
                return

            self._mem.update_task_status(goal_id, TaskStatus.RUNNING)
            app_evt = self._approval_events.get(goal_id)
            if app_evt:
                app_evt.set()

            logger.info(f"[GoalOrchestrator] Goal [{goal_id}] resumed")

            if self._speak:
                self._speak("Resuming goal execution, sir.")

    def get_goal_status(self, goal_id: str) -> dict:
        """Return full status object for a goal including child sub-tasks."""
        task = self._mem.get_task(goal_id)
        if not task:
            return {"error": f"Goal {goal_id} not found"}

        summary = self._mem.get_task_summary(goal_id)
        cost = CreditTracker().get_goal_cost(goal_id)
        summary["cumulative_cost_usd"] = f"${cost:.4f}"

        # Fetch child sub-tasks using public indexed method
        children_tasks = self._mem.get_children(goal_id)
        children = [
            {
                "id": c.id,
                "goal": c.goal,
                "status": c.status,
                "step_count": c.step_count,
            }
            for c in children_tasks
        ]

        summary["sub_tasks"] = children
        return summary

    # ── Daemon Loop ───────────────────────────────────────────────────────────

    def _orchestration_loop(self) -> None:
        """Background thread executing pending long-term goals."""
        cooldown = getattr(config, "GOAL_STEP_COOLDOWN_SEC", 1.0)

        while self._running:
            try:
                self._process_next_goal_step()
            except Exception as exc:
                logger.error(f"[GoalOrchestrator] Unexpected error in loop: {exc}", exc_info=True)
            time.sleep(cooldown)

    def _process_next_goal_step(self) -> None:
        """Pick the next active parent goal and advance its sub-goals."""
        pending_tasks = self._mem.get_pending_tasks()
        parent_tasks = [
            t for t in pending_tasks
            if t.context.get("goal_type") == "long_term_parent" and t.status in (TaskStatus.PENDING, TaskStatus.RUNNING)
        ]

        if not parent_tasks:
            return

        for parent in parent_tasks:
            if not self._running:
                break

            goal_id = parent.id
            with self._lock:
                cancel_evt = self._cancel_events.get(goal_id)
                if not cancel_evt:
                    cancel_evt = threading.Event()
                    self._cancel_events[goal_id] = cancel_evt
                if goal_id not in self._approval_events:
                    self._approval_events[goal_id] = threading.Event()

            if cancel_evt.is_set():
                continue

            # 1. Check Safety Cap: Cost Ceiling
            max_cost = getattr(config, "GOAL_MAX_COST_USD", 1.00)
            current_cost = CreditTracker().get_goal_cost(goal_id)
            if current_cost >= max_cost:
                self._suspend_goal(goal_id, f"Cost ceiling of ${max_cost:.2f} reached (current: ${current_cost:.4f})")
                continue

            # 2. Check for child sub-tasks
            children = self._get_child_tasks(goal_id)

            if not children:
                # Need to decompose parent goal into sub-goals
                self._mem.update_task_status(goal_id, TaskStatus.RUNNING)
                sub_goals = self._decompose_goal(parent.goal)
                if not sub_goals:
                    # Fallback single sub-goal if decomposition failed
                    sub_goals = [{"step": 1, "description": parent.goal}]

                for i, sg in enumerate(sub_goals):
                    self._mem.create_task(
                        goal=sg.get("description", parent.goal),
                        priority=parent.priority,
                        parent_id=goal_id,
                        context={
                            "sub_step": i + 1,
                            "delegation_depth": parent.context.get("delegation_depth", 0) + 1,
                        },
                    )
                children = self._get_child_tasks(goal_id)

            # Find next incomplete sub-task
            next_child = None
            for child in children:
                if child.status == TaskStatus.RUNNING:
                    # In-flight task is already executing asynchronously
                    next_child = None
                    break
                if child.status == TaskStatus.PENDING:
                    next_child = child
                    break

            if next_child is None:
                # If any sub-task is still running asynchronously, wait for next tick
                if any(c.status == TaskStatus.RUNNING for c in children):
                    continue

                # All sub-tasks completed!
                self._mem.update_task_status(goal_id, TaskStatus.DONE, result="All sub-goals completed successfully.")
                CreditTracker().clear_goal(goal_id)
                self._publish_event("GOAL_COMPLETED", {"goal_id": goal_id, "goal": parent.goal})
                if self._speak:
                    self._speak(f"Long-term goal completed, sir: {parent.goal[:50]}")
                continue

            # Execute the selected sub-task via ParallelAgentEngine (dedicated background dispatch)
            self._execute_sub_task(parent, next_child, cancel_evt)

    def _execute_sub_task(self, parent: Task, child: Task, cancel_evt: threading.Event) -> None:
        """Dispatch sub-task to the shared ParallelAgentEngine pool."""
        from os_layer.parallel_engine import get_parallel_engine, SubTask

        engine = get_parallel_engine()
        engine.start()

        sub_task = SubTask(
            id=child.id,
            description=child.goal,
            context=child.context,
        )

        def executor_wrapper(st: SubTask, evt: threading.Event) -> str:
            from agent.executor import AgentExecutor
            exec_inst = AgentExecutor()
            res = exec_inst.execute(
                goal=st.description,
                speak=None,  # Suppress individual sub-task speech to keep background silent
                cancel_flag=cancel_evt,
            )
            # Attribute costs
            CreditTracker().log_for_goal(parent.id, "gemini_flash", tokens=2000)
            return res

        future = engine.submit_task(sub_task, executor_wrapper)

        self._publish_event("GOAL_PROGRESS", {
            "goal_id": parent.id,
            "sub_task_id": child.id,
            "sub_task_goal": child.goal,
            "status": "executing",
        })

        def _on_sub_task_done(fut):
            try:
                result_str = fut.result()
                self._mem.update_task_status(child.id, TaskStatus.DONE, result=result_str)
                logger.info(f"[GoalOrchestrator] Sub-task [{child.id}] finished: {result_str[:80]}")
            except Exception as exc:
                logger.error(f"[GoalOrchestrator] Sub-task [{child.id}] failed: {exc}")
                self._mem.update_task_status(child.id, TaskStatus.FAILED, error=str(exc))

        self._mem.update_task_status(child.id, TaskStatus.RUNNING)
        future.add_done_callback(_on_sub_task_done)

    def _suspend_goal(self, goal_id: str, reason: str) -> None:
        """Suspend a goal and wait for user approval."""
        self._mem.update_task_status(goal_id, TaskStatus.PAUSED, result=f"Suspended: {reason}")
        self._publish_event("GOAL_SUSPENDED", {"goal_id": goal_id, "reason": reason})

        if self._speak:
            self._speak(f"Sir, goal execution paused: {reason}")

        logger.warning(f"[GoalOrchestrator] Goal [{goal_id}] suspended: {reason}")

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _get_child_tasks(self, parent_id: str) -> list[Task]:
        """Fetch child tasks for a parent goal sorted by sub_step."""
        children = self._mem.get_children(parent_id)
        children.sort(key=lambda t: t.context.get("sub_step", 999))
        return children

    def _decompose_goal(self, goal: str) -> list[dict]:
        """Call LLM (LOGIC tier) to split goal into ordered sub-tasks."""
        from core.llm_orchestrator import LLMOrchestrator, TaskTier
        orchestrator = LLMOrchestrator()

        max_sub = getattr(config, "GOAL_MAX_SUB_GOALS", 10)
        prompt = (
            f"Break this goal into a sequential plan of 2 to {max_sub} concise sub-tasks:\n"
            f"GOAL: {goal}\n\n"
            "Return JSON ONLY in this format:\n"
            '{"sub_goals": [{"step": 1, "description": "do X"}, {"step": 2, "description": "do Y"}]}'
        )

        try:
            resp = orchestrator.generate_content_with_retry(TaskTier.LOGIC, prompt)
            raw = resp.text.strip()
            cleaned = re.sub(r"```(?:json)?", "", raw).strip().rstrip("`").strip()
            data = json.loads(cleaned)
            return data.get("sub_goals", [])
        except Exception as exc:
            logger.warning(f"[GoalOrchestrator] Goal decomposition failed: {exc}")
            return [{"step": 1, "description": goal}]

    def _publish_event(self, event_type_name: str, payload: dict) -> None:
        """Publish event through EventBus if available."""
        try:
            from os_layer.event_bus import get_event_bus, EventType
            event_type = getattr(EventType, event_type_name, EventType.CUSTOM)
            bus = get_event_bus()
            bus.emit(event_type, source="goal_orchestrator", payload=payload)
        except Exception as exc:
            logger.debug(f"[GoalOrchestrator] Event publish skipped: {exc}")


# ── Singleton ────────────────────────────────────────────────────────────────

_orchestrator_instance: Optional[GoalOrchestrator] = None
_orchestrator_lock = threading.Lock()


def get_goal_orchestrator() -> GoalOrchestrator:
    """Thread-safe singleton accessor."""
    global _orchestrator_instance
    if _orchestrator_instance is None:
        with _orchestrator_lock:
            if _orchestrator_instance is None:
                _orchestrator_instance = GoalOrchestrator()
    return _orchestrator_instance
