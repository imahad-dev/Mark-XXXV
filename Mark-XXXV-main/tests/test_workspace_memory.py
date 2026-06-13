"""
tests/test_workspace_memory.py — Workspace Memory Tests
=========================================================
Tests for save/restore lifecycle, task tracking, session briefing
generation, and snapshot pruning.
"""

from __future__ import annotations

import platform
import time
import unittest

IS_WINDOWS = platform.system() == "Windows"


class TestWorkspaceMemoryBasic(unittest.TestCase):
    """Test basic workspace memory operations."""

    def setUp(self):
        from os_layer.workspace_memory import WorkspaceMemory
        self.wm = WorkspaceMemory()

    def test_session_id_generated(self):
        self.assertTrue(len(self.wm._session_id) == 8)

    def test_format_time_gap_seconds(self):
        from os_layer.workspace_memory import WorkspaceMemory
        result = WorkspaceMemory._format_time_gap(time.time() - 30)
        self.assertEqual(result, "moments ago")

    def test_format_time_gap_minutes(self):
        from os_layer.workspace_memory import WorkspaceMemory
        result = WorkspaceMemory._format_time_gap(time.time() - 300)
        self.assertIn("minute", result)

    def test_format_time_gap_hours(self):
        from os_layer.workspace_memory import WorkspaceMemory
        result = WorkspaceMemory._format_time_gap(time.time() - 7200)
        self.assertIn("hour", result)

    def test_format_time_gap_days(self):
        from os_layer.workspace_memory import WorkspaceMemory
        result = WorkspaceMemory._format_time_gap(time.time() - 172800)
        self.assertIn("day", result)


class TestWorkspaceMemoryTasks(unittest.TestCase):
    """Test incomplete task tracking."""

    def setUp(self):
        from os_layer.workspace_memory import WorkspaceMemory
        self.wm = WorkspaceMemory()
        self._test_task = f"_test_task_{int(time.time())}"

    def tearDown(self):
        self.wm.complete_task(self._test_task)

    def test_add_and_list_task(self):
        self.wm.mark_task_incomplete(self._test_task)
        tasks = self.wm.get_incomplete_tasks()
        self.assertIn(self._test_task, tasks)

    def test_complete_task_removes_from_list(self):
        self.wm.mark_task_incomplete(self._test_task)
        self.wm.complete_task(self._test_task)
        tasks = self.wm.get_incomplete_tasks()
        self.assertNotIn(self._test_task, tasks)

    def test_duplicate_task_not_added(self):
        self.wm.mark_task_incomplete(self._test_task)
        self.wm.mark_task_incomplete(self._test_task)  # duplicate
        tasks = self.wm.get_incomplete_tasks()
        count = tasks.count(self._test_task)
        self.assertEqual(count, 1)


class TestWorkspaceMemoryBriefing(unittest.TestCase):
    """Test briefing generation."""

    def test_build_briefing_empty(self):
        from os_layer.workspace_memory import WorkspaceMemory
        result = WorkspaceMemory._build_briefing(
            active_app="",
            task_context="",
            gap_str="",
            restored=0,
            total=0,
            incomplete_tasks=[],
        )
        self.assertIn("Welcome back", result)

    def test_build_briefing_with_data(self):
        from os_layer.workspace_memory import WorkspaceMemory
        result = WorkspaceMemory._build_briefing(
            active_app="VS Code",
            task_context="Refactoring the API",
            gap_str="2 hours ago",
            restored=3,
            total=5,
            incomplete_tasks=["Fix bug #42", "Write docs"],
        )
        self.assertIn("VS Code", result)
        self.assertIn("Refactoring the API", result)
        self.assertIn("2 hours ago", result)
        self.assertIn("3 of 5", result)
        self.assertIn("Fix bug #42", result)

    def test_build_briefing_many_tasks_truncated(self):
        from os_layer.workspace_memory import WorkspaceMemory
        tasks = [f"Task {i}" for i in range(10)]
        result = WorkspaceMemory._build_briefing(
            active_app="Chrome",
            task_context="",
            gap_str="1 day ago",
            restored=0,
            total=0,
            incomplete_tasks=tasks,
        )
        self.assertIn("and 7 more", result)


class TestWorkspaceMemorySessionBrief(unittest.TestCase):
    """Test get_last_session_brief."""

    def test_returns_string(self):
        from os_layer.workspace_memory import WorkspaceMemory
        wm = WorkspaceMemory()
        brief = wm.get_last_session_brief()
        self.assertIsInstance(brief, str)


@unittest.skipUnless(IS_WINDOWS, "Windows-only: requires OS layer")
class TestWorkspaceMemorySaveRestore(unittest.TestCase):
    """Live save/restore cycle."""

    def test_save_workspace_returns_snapshot(self):
        from os_layer.workspace_memory import WorkspaceMemory
        wm = WorkspaceMemory()
        snapshot = wm.save_workspace_state()
        self.assertIsNotNone(snapshot)
        self.assertGreater(snapshot.timestamp, 0)

    def test_restore_workspace_returns_result(self):
        from os_layer.workspace_memory import WorkspaceMemory
        wm = WorkspaceMemory()
        # Ensure we have at least one snapshot
        wm.save_workspace_state()
        result = wm.restore_workspace()
        self.assertIsNotNone(result)
        self.assertIsInstance(result.briefing, str)
        self.assertTrue(len(result.briefing) > 0)


if __name__ == "__main__":
    unittest.main()
