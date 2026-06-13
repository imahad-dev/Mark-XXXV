"""
tests/test_parallel_engine.py — ParallelAgentEngine Unit Tests
===============================================================
Tests concurrent execution, cancellation, rate-limit backoff,
and edge cases. All tests use mock executor functions —
no LLM calls are made.
"""

import sys
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

# Ensure project root is on sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from os_layer.parallel_engine import (
    ParallelAgentEngine,
    SubTask,
    SubTaskResult,
    OrchestrateResult,
)


class TestParallelEngineBasic(unittest.TestCase):
    """Test basic execution paths."""

    def test_empty_task_list(self):
        engine = ParallelAgentEngine()
        result = engine.execute_concurrently([], executor_fn=lambda t, c: "ok")
        self.assertEqual(len(result.results), 0)
        self.assertEqual(result.total_duration_ms, 0)

    def test_single_task_success(self):
        tasks = [SubTask(id="t1", description="say hello")]

        def executor(task, cancel_event):
            return f"Hello from {task.id}"

        engine = ParallelAgentEngine()
        result = engine.execute_concurrently(tasks, executor_fn=executor)

        self.assertEqual(len(result.results), 1)
        self.assertTrue(result.results[0].success)
        self.assertEqual(result.results[0].task_id, "t1")
        self.assertIn("Hello from t1", result.results[0].result)
        self.assertFalse(result.cancelled)

    def test_multiple_tasks_success(self):
        tasks = [
            SubTask(id="t1", description="task 1"),
            SubTask(id="t2", description="task 2"),
            SubTask(id="t3", description="task 3"),
        ]

        def executor(task, cancel_event):
            time.sleep(0.05)  # Simulate work
            return f"Done: {task.id}"

        engine = ParallelAgentEngine(max_workers=3)
        result = engine.execute_concurrently(tasks, executor_fn=executor)

        self.assertEqual(len(result.results), 3)
        self.assertEqual(result.success_count, 3)
        self.assertEqual(result.failure_count, 0)
        self.assertFalse(result.cancelled)

    def test_task_with_context(self):
        """SubTask.context should be accessible inside the executor."""
        tasks = [SubTask(id="t1", description="use context", context={"key": "value"})]

        def executor(task, cancel_event):
            return f"context={task.context.get('key')}"

        engine = ParallelAgentEngine()
        result = engine.execute_concurrently(tasks, executor_fn=executor)

        self.assertTrue(result.results[0].success)
        self.assertIn("value", result.results[0].result)


class TestParallelEngineFailures(unittest.TestCase):
    """Test error handling and partial failure scenarios."""

    def test_executor_raises_non_retryable(self):
        tasks = [SubTask(id="t1", description="will fail")]

        def executor(task, cancel_event):
            raise ValueError("Something broke")

        engine = ParallelAgentEngine()
        result = engine.execute_concurrently(tasks, executor_fn=executor)

        self.assertEqual(len(result.results), 1)
        self.assertFalse(result.results[0].success)
        self.assertIn("ValueError", result.results[0].error)

    def test_partial_failure(self):
        """Some tasks succeed, others fail — results should reflect both."""
        tasks = [
            SubTask(id="pass", description="succeed"),
            SubTask(id="fail", description="fail"),
        ]

        def executor(task, cancel_event):
            if task.id == "fail":
                raise RuntimeError("Intentional failure")
            return "ok"

        engine = ParallelAgentEngine(max_workers=2)
        result = engine.execute_concurrently(tasks, executor_fn=executor)

        self.assertEqual(result.success_count, 1)
        self.assertEqual(result.failure_count, 1)

        passed = [r for r in result.results if r.task_id == "pass"][0]
        failed = [r for r in result.results if r.task_id == "fail"][0]
        self.assertTrue(passed.success)
        self.assertFalse(failed.success)

    def test_all_tasks_fail(self):
        tasks = [
            SubTask(id="t1", description="fail1"),
            SubTask(id="t2", description="fail2"),
        ]

        def executor(task, cancel_event):
            raise Exception("boom")

        engine = ParallelAgentEngine()
        result = engine.execute_concurrently(tasks, executor_fn=executor)

        self.assertEqual(result.success_count, 0)
        self.assertEqual(result.failure_count, 2)


class TestParallelEngineCancellation(unittest.TestCase):
    """Test cancellation propagation."""

    def test_cancel_before_execution(self):
        """If cancel_event is set before execution, tasks should be skipped."""
        cancel = threading.Event()
        cancel.set()  # Pre-cancelled

        tasks = [SubTask(id="t1", description="should not run")]

        def executor(task, cancel_event):
            return "should not reach here"

        engine = ParallelAgentEngine(cancel_event=cancel)
        result = engine.execute_concurrently(tasks, executor_fn=executor)

        self.assertEqual(len(result.results), 1)
        self.assertFalse(result.results[0].success)
        self.assertIn("Cancelled", result.results[0].error)

    def test_cancel_during_execution(self):
        """Setting cancel_event during execution should stop running tasks."""
        cancel = threading.Event()
        tasks = [SubTask(id="t1", description="long task")]

        def executor(task, cancel_event):
            # Simulate a long-running task that checks cancellation
            for _ in range(100):
                if cancel_event.is_set():
                    raise Exception("Cancelled during execution")
                time.sleep(0.01)
            return "completed"

        # Cancel after a short delay
        def cancel_after_delay():
            time.sleep(0.05)
            cancel.set()

        threading.Thread(target=cancel_after_delay, daemon=True).start()

        engine = ParallelAgentEngine(cancel_event=cancel)
        result = engine.execute_concurrently(tasks, executor_fn=executor)

        # Task should have failed due to cancellation
        self.assertTrue(result.cancelled)

    def test_cancel_method(self):
        engine = ParallelAgentEngine()
        self.assertFalse(engine._cancel_event.is_set())
        engine.cancel()
        self.assertTrue(engine._cancel_event.is_set())


class TestParallelEngineRateLimit(unittest.TestCase):
    """Test exponential backoff on rate-limit errors."""

    def test_rate_limit_retry_then_success(self):
        """Rate limit on first attempt, succeed on retry."""
        call_count = {"n": 0}
        tasks = [SubTask(id="t1", description="rate limited then ok")]

        def executor(task, cancel_event):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise Exception("429 Resource Exhausted")
            return "success after retry"

        engine = ParallelAgentEngine()
        result = engine.execute_concurrently(tasks, executor_fn=executor)

        self.assertEqual(len(result.results), 1)
        self.assertTrue(result.results[0].success)
        self.assertIn("success", result.results[0].result)
        self.assertGreaterEqual(call_count["n"], 2)

    def test_rate_limit_all_retries_exhausted(self):
        """Rate limit on all attempts should eventually fail."""
        tasks = [SubTask(id="t1", description="always rate limited")]

        def executor(task, cancel_event):
            raise Exception("429 quota exceeded")

        engine = ParallelAgentEngine()
        result = engine.execute_concurrently(tasks, executor_fn=executor)

        self.assertEqual(len(result.results), 1)
        self.assertFalse(result.results[0].success)
        self.assertIn("429", result.results[0].error)

    def test_rate_limit_cancel_during_backoff(self):
        """Cancellation during backoff sleep should respond quickly."""
        cancel = threading.Event()
        tasks = [SubTask(id="t1", description="rate limited then cancelled")]
        attempt = {"n": 0}

        def executor(task, cancel_event):
            attempt["n"] += 1
            raise Exception("429 rate limited")

        # Cancel shortly after first backoff starts
        def cancel_soon():
            time.sleep(0.5)
            cancel.set()

        threading.Thread(target=cancel_soon, daemon=True).start()

        engine = ParallelAgentEngine(cancel_event=cancel)
        start = time.monotonic()
        result = engine.execute_concurrently(tasks, executor_fn=executor)
        elapsed = time.monotonic() - start

        self.assertFalse(result.results[0].success)
        # Should not have waited the full backoff duration
        self.assertLess(elapsed, 10.0, "Cancellation during backoff took too long")


class TestParallelEngineConcurrencyLimit(unittest.TestCase):
    """Test that max_workers is respected."""

    def test_respects_config_ceiling(self):
        """Engine should never exceed config.MAX_CONCURRENT_AGENTS."""
        max_concurrent = {"value": 0}
        current_concurrent = {"value": 0}
        lock = threading.Lock()

        tasks = [SubTask(id=f"t{i}", description=f"task {i}") for i in range(6)]

        def executor(task, cancel_event):
            with lock:
                current_concurrent["value"] += 1
                max_concurrent["value"] = max(
                    max_concurrent["value"], current_concurrent["value"]
                )
            time.sleep(0.1)  # Simulate work
            with lock:
                current_concurrent["value"] -= 1
            return "done"

        with patch("os_layer.parallel_engine.config") as mock_config:
            mock_config.MAX_CONCURRENT_AGENTS = 2
            engine = ParallelAgentEngine(max_workers=10)  # Request 10, capped to 2

        result = engine.execute_concurrently(tasks, executor_fn=executor)

        self.assertEqual(result.success_count, 6)
        self.assertLessEqual(max_concurrent["value"], 2)


class TestDataClasses(unittest.TestCase):
    """Test data class properties."""

    def test_subtask_defaults(self):
        t = SubTask(id="x", description="test")
        self.assertEqual(t.context, {})

    def test_subtask_result_defaults(self):
        r = SubTaskResult(task_id="x", success=True)
        self.assertEqual(r.result, "")
        self.assertEqual(r.error, "")
        self.assertEqual(r.duration_ms, 0)

    def test_orchestrate_result_counts(self):
        results = [
            SubTaskResult(task_id="a", success=True),
            SubTaskResult(task_id="b", success=False),
            SubTaskResult(task_id="c", success=True),
        ]
        o = OrchestrateResult(results=results)
        self.assertEqual(o.success_count, 2)
        self.assertEqual(o.failure_count, 1)

    def test_orchestrate_result_empty(self):
        o = OrchestrateResult(results=[])
        self.assertEqual(o.success_count, 0)
        self.assertEqual(o.failure_count, 0)


class TestParallelEngineTimingMetrics(unittest.TestCase):
    """Test that duration metrics are captured correctly."""

    def test_duration_captured(self):
        tasks = [SubTask(id="t1", description="timed task")]

        def executor(task, cancel_event):
            time.sleep(0.1)
            return "done"

        engine = ParallelAgentEngine()
        result = engine.execute_concurrently(tasks, executor_fn=executor)

        self.assertTrue(result.results[0].success)
        self.assertGreater(result.results[0].duration_ms, 50)
        self.assertGreater(result.total_duration_ms, 50)


if __name__ == "__main__":
    unittest.main()
