"""
os_layer/parallel_engine.py — Multi-Agent Concurrent Execution
===============================================================
Runs multiple sub-tasks in parallel using ThreadPoolExecutor.
Each sub-task gets a fresh, isolated execution context.

Architecture Constraints (user-specified):
    1. LLM-AGNOSTIC: Does NOT import LLMOrchestrator. Accepts an
       executor callable as dependency injection. The LLM wiring
       happens in tool_defs.py where the tool is registered.
    2. THREAD-SAFE: Each worker gets a fresh agent — no shared
       mutable state between parallel tasks.
    3. CANCELLABLE: A threading.Event propagates cancellation from
       the user's voice interrupt ("stop") through the registry.
    4. BOUNDED: Hard ceiling on concurrent threads tied to
       config.MAX_CONCURRENT_AGENTS (default 2).
    5. BACKOFF: Built-in exponential backoff when executor_fn
       raises rate-limit errors, preventing API key exhaustion.

Thread Safety:
    Results are collected via concurrent.futures (thread-safe by design).
    The cancel_event is a threading.Event shared across workers.
"""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, Future, as_completed
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from core.config import config

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────

_MAX_RETRIES = 3
_BASE_BACKOFF_SECONDS = 2.0
_RATE_LIMIT_MARKERS = ("429", "quota", "resource_exhausted")


# ── Data Classes ─────────────────────────────────────────────────────────────

@dataclass
class SubTask:
    """A single sub-task to execute in parallel."""
    id: str
    description: str
    context: dict = field(default_factory=dict)


@dataclass
class SubTaskResult:
    """Result of a single sub-task execution."""
    task_id: str
    success: bool
    result: str = ""
    error: str = ""
    duration_ms: int = 0


@dataclass
class OrchestrateResult:
    """Aggregated result of parallel execution."""
    results: list[SubTaskResult]
    total_duration_ms: int = 0
    cancelled: bool = False

    @property
    def success_count(self) -> int:
        return sum(1 for r in self.results if r.success)

    @property
    def failure_count(self) -> int:
        return sum(1 for r in self.results if not r.success)


# ── Parallel Agent Engine ────────────────────────────────────────────────────

class ParallelAgentEngine:
    """
    Executes sub-tasks concurrently with isolated worker threads.

    Args:
        cancel_event: External threading.Event for cancellation propagation.
                      When set, no new tasks start and running tasks check it
                      at their own cadence.
        max_workers:  Maximum concurrent threads. Capped at
                      config.MAX_CONCURRENT_AGENTS (hard API ceiling).

    Usage:
        engine = ParallelAgentEngine(cancel_event=stop_flag)
        result = engine.execute_concurrently(tasks, executor_fn=my_fn)
    """

    def __init__(
        self,
        cancel_event: threading.Event | None = None,
        max_workers: int | None = None,
    ):
        # Hard ceiling from config — never exceed API rate limit headroom
        hard_ceiling = getattr(config, "MAX_CONCURRENT_AGENTS", 2)
        self._max_workers = min(max_workers or hard_ceiling, hard_ceiling)
        self._cancel_event = cancel_event or threading.Event()
        self._active_futures: list[Future] = []

    def execute_concurrently(
        self,
        tasks: list[SubTask],
        executor_fn: Callable[[SubTask, threading.Event], str],
    ) -> OrchestrateResult:
        """
        Run tasks in parallel using ThreadPoolExecutor.

        Args:
            tasks:       List of SubTask objects to execute.
            executor_fn: Callable(SubTask, cancel_event) -> str
                         This is injected by the tool registration layer.
                         It must be thread-safe and create its own agent.

        Returns:
            OrchestrateResult with per-task outcomes and timing.
        """
        if not tasks:
            return OrchestrateResult(results=[], total_duration_ms=0)

        results: list[SubTaskResult] = []
        start_time = time.monotonic()

        with ThreadPoolExecutor(
            max_workers=self._max_workers,
            thread_name_prefix="parallel-agent",
        ) as pool:
            future_to_task: dict[Future, SubTask] = {}

            for task in tasks:
                # Check cancellation before submitting each task
                if self._cancel_event.is_set():
                    logger.info("[ParallelEngine] Cancellation detected, skipping remaining tasks")
                    results.append(SubTaskResult(
                        task_id=task.id,
                        success=False,
                        error="Cancelled before execution",
                    ))
                    continue

                future = pool.submit(
                    self._execute_with_retry,
                    task,
                    executor_fn,
                )
                future_to_task[future] = task
                self._active_futures.append(future)

            # Collect results as they complete
            for future in as_completed(future_to_task):
                task = future_to_task[future]
                try:
                    result = future.result()
                    results.append(result)
                except Exception as exc:
                    results.append(SubTaskResult(
                        task_id=task.id,
                        success=False,
                        error=f"Unhandled: {type(exc).__name__}: {exc}",
                    ))

        total_ms = int((time.monotonic() - start_time) * 1000)
        cancelled = self._cancel_event.is_set()
        self._active_futures.clear()

        return OrchestrateResult(
            results=results,
            total_duration_ms=total_ms,
            cancelled=cancelled,
        )

    def cancel(self) -> None:
        """Signal all workers to stop."""
        self._cancel_event.set()
        logger.info("[ParallelEngine] Cancel signal sent")

    # ── Private ──────────────────────────────────────────────────────────

    def _execute_with_retry(
        self,
        task: SubTask,
        executor_fn: Callable[[SubTask, threading.Event], str],
    ) -> SubTaskResult:
        """
        Execute a single task with exponential backoff on rate-limit errors.
        """
        start = time.monotonic()

        for attempt in range(_MAX_RETRIES):
            if self._cancel_event.is_set():
                return SubTaskResult(
                    task_id=task.id,
                    success=False,
                    error="Cancelled during execution",
                    duration_ms=int((time.monotonic() - start) * 1000),
                )

            try:
                result_text = executor_fn(task, self._cancel_event)
                elapsed = int((time.monotonic() - start) * 1000)
                return SubTaskResult(
                    task_id=task.id,
                    success=True,
                    result=result_text,
                    duration_ms=elapsed,
                )

            except Exception as exc:
                error_str = str(exc).lower()
                is_rate_limit = any(m in error_str for m in _RATE_LIMIT_MARKERS)

                if is_rate_limit and attempt < _MAX_RETRIES - 1:
                    backoff = _BASE_BACKOFF_SECONDS * (2 ** attempt)
                    logger.warning(
                        f"[ParallelEngine] Rate limit on task '{task.id}', "
                        f"retry {attempt + 1}/{_MAX_RETRIES} in {backoff:.1f}s"
                    )
                    # Sleep in small intervals so cancellation is responsive
                    deadline = time.monotonic() + backoff
                    while time.monotonic() < deadline:
                        if self._cancel_event.is_set():
                            return SubTaskResult(
                                task_id=task.id,
                                success=False,
                                error="Cancelled during backoff",
                                duration_ms=int((time.monotonic() - start) * 1000),
                            )
                        time.sleep(0.25)
                    continue

                # Non-retryable error or retries exhausted
                elapsed = int((time.monotonic() - start) * 1000)
                return SubTaskResult(
                    task_id=task.id,
                    success=False,
                    error=f"{type(exc).__name__}: {exc}",
                    duration_ms=elapsed,
                )

        # Should not reach here, but defensive
        return SubTaskResult(
            task_id=task.id,
            success=False,
            error="Max retries exhausted",
            duration_ms=int((time.monotonic() - start) * 1000),
        )
