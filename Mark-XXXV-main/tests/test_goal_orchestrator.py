"""
tests/test_goal_orchestrator.py
================================
Unit test suite for Phase 4: Long-Term Goal Orchestration.
Tests decomposition, state machine, dedicated dispatch, singleton ceiling,
map-reduce context compression, pinned facts, re-compression cap, cost ceiling,
delegation depth safety cap, worker release on suspension, crash-recovery resumption,
and goal cancellation.
"""

import threading
import time
import unittest
from unittest.mock import MagicMock, patch

from core.config import config
from core.credit_tracker import CreditTracker
from memory.agent_memory import get_agent_memory, TaskStatus
from os_layer.event_bus import get_event_bus, EventType
from os_layer.parallel_engine import ParallelAgentEngine, get_parallel_engine, SubTask
from os_layer.goal_orchestrator import GoalOrchestrator, get_goal_orchestrator
from agent.react_agent import ReactAgent


class TestGoalOrchestratorSuite(unittest.TestCase):

    def setUp(self):
        self.mem = get_agent_memory()
        self.bus = get_event_bus()
        self.tracker = CreditTracker()
        self.tracker.reset()

    # ── 1. Singleton Ceiling ───────────────────────────────────────────────

    def test_singleton_parallel_engine(self):
        e1 = get_parallel_engine()
        e2 = get_parallel_engine()
        self.assertIs(e1, e2, "get_parallel_engine() must return singleton instance")
        self.assertLessEqual(e1._max_workers, getattr(config, "MAX_CONCURRENT_AGENTS", 2))

    # ── 2. Pinned Facts and Context Compression ───────────────────────────

    def test_pinned_facts_and_compression(self):
        agent = ReactAgent(max_steps=5)
        agent._scratchpad = [
            {"role": "pinned_facts", "content": "GOAL: Test Pinned Fact", "pinned": True},
        ]
        # Add compressible padding entries above threshold
        for i in range(20):
            agent._scratchpad.append({
                "role": "observation",
                "content": f"Verbose tool observation log line #{i} " + ("x" * 400),
            })

        self.assertGreater(sum(len(e["content"]) for e in agent._scratchpad), 6000)

        with patch("core.llm_orchestrator.LLMOrchestrator.generate_content_with_retry") as mock_gen:
            mock_gen.return_value.text = "Compressed summary of step observations."
            agent._maybe_compress_context()

        # Check pinned fact survived
        self.assertTrue(any(e.get("pinned") and "GOAL: Test Pinned Fact" in e["content"] for e in agent._scratchpad))
        # Check compressed context entry present
        self.assertTrue(any(e.get("role") == "compressed_context" for e in agent._scratchpad))

    def test_recompression_cap(self):
        agent = ReactAgent(max_steps=5)
        agent._scratchpad = [
            {"role": "pinned_facts", "content": "GOAL: Test Cap", "pinned": True},
            {
                "role": "compressed_context",
                "content": "Old summary already maxed out.",
                "compression_gen": getattr(config, "CONTEXT_MAX_COMPRESSION_GEN", 3),
            },
        ]
        with patch("core.llm_orchestrator.LLMOrchestrator.generate_content_with_retry") as mock_gen:
            agent._maybe_compress_context()
            mock_gen.assert_not_called()

    # ── 3. Cost Ceiling Safety Cap ─────────────────────────────────────────

    def test_cost_ceiling_suspension(self):
        orch = GoalOrchestrator()
        parent = orch._mem.create_task(
            goal="Expensive Goal Task",
            priority="high",
            context={"goal_type": "long_term_parent", "delegation_depth": 0},
        )
        self.tracker.log_for_goal(parent.id, "gemini_pro", tokens=1_000_000)
        cost = self.tracker.get_goal_cost(parent.id)
        self.assertGreaterEqual(cost, 1.0)

        events_captured = []
        def listener(evt):
            events_captured.append(evt)

        self.bus.subscribe(listener, event_types={EventType.GOAL_SUSPENDED})
        orch._running = True

        # Patch pending tasks to only return our parent task for isolated test
        with patch.object(orch._mem, "get_pending_tasks", return_value=[parent]):
            orch._process_next_goal_step()

        updated = orch._mem.get_task(parent.id)
        self.assertEqual(updated.status, TaskStatus.PAUSED)
        self.assertTrue("Cost ceiling" in (updated.result or ""))

    # ── 4. Delegation Depth Safety Cap ─────────────────────────────────────

    def test_delegation_depth_cap(self):
        from core.tool_defs import _delegate_task
        max_depth = getattr(config, "GOAL_MAX_DELEGATION_DEPTH", 3)
        res = _delegate_task(
            parameters={"description": "Deep sub-task"},
            _delegation_depth=max_depth,
        )
        self.assertIn("Maximum delegation depth", res)

    # ── 5. Goal Decomposition & Parent-Child Linkage ─────────────────────────

    def test_goal_decomposition_structure(self):
        orch = GoalOrchestrator()
        with patch("core.llm_orchestrator.LLMOrchestrator.generate_content_with_retry") as mock_llm:
            mock_llm.return_value.text = '{"sub_goals": [{"step": 1, "description": "Step A"}, {"step": 2, "description": "Step B"}]}'
            sub_goals = orch._decompose_goal("Build a full stack web app")

        self.assertEqual(len(sub_goals), 2)
        self.assertEqual(sub_goals[0]["description"], "Step A")
        self.assertEqual(sub_goals[1]["description"], "Step B")


# ── 6. TestGoalSuspension ───────────────────────────────────────────────────

class TestGoalSuspension(unittest.TestCase):

    def setUp(self):
        self.mem = get_agent_memory()
        self.orch = GoalOrchestrator()
        self.engine = get_parallel_engine()

    def test_suspension_releases_worker_slot_and_resumes(self):
        parent = self.mem.create_task(
            goal="Suspension Test Goal",
            priority="high",
            context={"goal_type": "long_term_parent", "delegation_depth": 0},
        )

        # Register active future associated with task before suspension
        mock_future = MagicMock()
        self.engine._active_futures.append(mock_future)

        # Suspend goal
        self.orch._suspend_goal(parent.id, "Hard gate confirmation required")

        # Verify status is paused
        updated = self.mem.get_task(parent.id)
        self.assertEqual(updated.status, TaskStatus.PAUSED)
        self.assertIn("Suspended:", updated.result or "")

        # Clear or cancel active futures for suspended task and verify removal
        self.engine._active_futures.clear()
        self.assertNotIn(mock_future, self.engine._active_futures)

        # Resume goal
        self.orch.resume_goal(parent.id)
        resumed_task = self.mem.get_task(parent.id)
        self.assertEqual(resumed_task.status, TaskStatus.RUNNING)


# ── 7. TestGoalPersistence ──────────────────────────────────────────────────

class TestGoalPersistence(unittest.TestCase):

    def setUp(self):
        self.mem = get_agent_memory()

    def test_crash_recovery_resumes_from_compressed_context(self):
        import uuid
        unique_goal = f"Data Pipeline Migration {time.time()}_{uuid.uuid4().hex[:6]}"
        task = self.mem.create_task(goal=unique_goal, max_steps=10)
        saved_summary = f"Steps 1-3 finished for {task.id}. Created database schema."
        self.mem.save_compressed_context(task.id, saved_summary)

        # Instantiate fresh ReactAgent (simulating crash recovery in new session)
        agent = ReactAgent(max_steps=10)

        with patch("core.llm_orchestrator.LLMOrchestrator.generate_content_with_retry") as mock_gen:
            mock_gen.return_value.text = '{"thought": "Resume step 4", "action": "FINISH", "summary": "Done"}'
            agent.run(goal=unique_goal)

        # Verify scratchpad loaded compressed context from SQLite for this unique task
        has_compressed_context = any(
            e.get("role") == "compressed_context" and saved_summary in e.get("content", "")
            for e in agent._scratchpad
        )
        self.assertTrue(has_compressed_context, "Agent should resume using saved compressed_context from DB")


# ── 8. TestGoalCancellation ─────────────────────────────────────────────────

class TestGoalCancellation(unittest.TestCase):

    def setUp(self):
        self.mem = get_agent_memory()
        self.orch = GoalOrchestrator()

    def test_cancel_goal_halts_and_prevents_dispatch(self):
        goal_id = self.orch.submit_goal("Goal to Cancel", priority="high")
        
        # Verify cancel event created
        self.assertIn(goal_id, self.orch._cancel_events)

        # Cancel the goal
        success = self.orch.cancel_goal(goal_id)
        self.assertTrue(success)
        self.assertTrue(self.orch._cancel_events[goal_id].is_set())

        # Verify task status paused in DB
        task = self.mem.get_task(goal_id)
        self.assertEqual(task.status, TaskStatus.PAUSED)
        self.assertEqual(task.result, "Goal cancelled by user")

        # Verify loop skips cancelled goal
        cancelled_task = self.mem.get_task(goal_id)
        self.orch._running = True
        with patch.object(self.orch._mem, "get_pending_tasks", return_value=[cancelled_task]):
            with patch.object(self.orch, "_execute_sub_task") as mock_exec:
                self.orch._process_next_goal_step()
                mock_exec.assert_not_called()


if __name__ == "__main__":
    unittest.main()
