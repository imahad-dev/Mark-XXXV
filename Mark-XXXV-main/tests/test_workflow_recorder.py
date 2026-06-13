"""
tests/test_workflow_recorder.py — Workflow Recorder Tests
==========================================================
Tests for recording lifecycle, step capture, save/load CRUD,
replay mechanics, pause/resume, and discard operations.
"""

from __future__ import annotations

import platform
import time
import unittest

IS_WINDOWS = platform.system() == "Windows"


class TestStepActionEnum(unittest.TestCase):
    """Test StepAction enum values."""

    def test_all_actions_exist(self):
        from os_layer.workflow_recorder import StepAction
        expected = [
            "focus_window", "open_app", "close_window", "snap_window",
            "move_window", "file_open", "file_save", "navigate_url",
            "type_text", "wait", "custom",
        ]
        for val in expected:
            self.assertEqual(StepAction(val).value, val)

    def test_invalid_action_raises(self):
        from os_layer.workflow_recorder import StepAction
        with self.assertRaises(ValueError):
            StepAction("nonexistent_action")


class TestRecordingState(unittest.TestCase):
    """Test RecordingState enum."""

    def test_states(self):
        from os_layer.workflow_recorder import RecordingState
        self.assertEqual(RecordingState.IDLE.value, "idle")
        self.assertEqual(RecordingState.RECORDING.value, "recording")
        self.assertEqual(RecordingState.PAUSED.value, "paused")


class TestWorkflowDataClasses(unittest.TestCase):
    """Test data class defaults."""

    def test_workflow_step_defaults(self):
        from os_layer.workflow_recorder import WorkflowStep
        s = WorkflowStep()
        self.assertEqual(s.step_index, 0)
        self.assertEqual(s.delay_ms, 0)
        self.assertIsInstance(s.parameters, dict)

    def test_workflow_defaults(self):
        from os_layer.workflow_recorder import Workflow
        w = Workflow(name="test")
        self.assertEqual(w.name, "test")
        self.assertEqual(w.step_count, 0)
        self.assertEqual(w.total_duration_ms, 0)

    def test_replay_result_defaults(self):
        from os_layer.workflow_recorder import ReplayResult
        r = ReplayResult()
        self.assertFalse(r.success)
        self.assertEqual(r.steps_executed, 0)
        self.assertIsInstance(r.errors, list)


class TestWorkflowRecorderLifecycle(unittest.TestCase):
    """Test recording lifecycle."""

    def setUp(self):
        from os_layer.workflow_recorder import WorkflowRecorder
        self.rec = WorkflowRecorder()
        self._test_name = f"_test_wf_{int(time.time())}"

    def tearDown(self):
        self.rec.delete_workflow(self._test_name)
        if self.rec.is_recording:
            self.rec.discard_recording()

    def test_start_stop_recording(self):
        from os_layer.workflow_recorder import StepAction, RecordingState
        ok = self.rec.start_recording(self._test_name)
        self.assertTrue(ok)
        self.assertEqual(self.rec.state, RecordingState.RECORDING)

        self.rec.record_step(StepAction.OPEN_APP, target="notepad.exe")
        self.rec.record_step(StepAction.FOCUS_WINDOW, target="Notepad")

        wf = self.rec.stop_recording()
        self.assertIsNotNone(wf)
        self.assertEqual(wf.name, self._test_name)
        self.assertEqual(wf.step_count, 2)
        self.assertEqual(self.rec.state, RecordingState.IDLE)

    def test_double_start_fails(self):
        self.rec.start_recording(self._test_name)
        ok = self.rec.start_recording("another_name")
        self.assertFalse(ok)
        self.rec.discard_recording()

    def test_stop_without_start(self):
        wf = self.rec.stop_recording()
        self.assertIsNone(wf)

    def test_record_step_when_not_recording(self):
        from os_layer.workflow_recorder import StepAction
        ok = self.rec.record_step(StepAction.CUSTOM, target="test")
        self.assertFalse(ok)

    def test_pause_resume(self):
        from os_layer.workflow_recorder import StepAction, RecordingState
        self.rec.start_recording(self._test_name)
        self.rec.record_step(StepAction.CUSTOM, target="step1")

        self.rec.pause_recording()
        self.assertEqual(self.rec.state, RecordingState.PAUSED)

        self.rec.resume_recording()
        self.assertEqual(self.rec.state, RecordingState.RECORDING)

        self.rec.record_step(StepAction.CUSTOM, target="step2")
        wf = self.rec.stop_recording()
        self.assertEqual(wf.step_count, 2)

    def test_discard_recording(self):
        from os_layer.workflow_recorder import StepAction, RecordingState
        self.rec.start_recording(self._test_name)
        self.rec.record_step(StepAction.CUSTOM, target="discarded")
        self.rec.discard_recording()
        self.assertEqual(self.rec.state, RecordingState.IDLE)

        # Verify workflow was NOT saved
        wf = self.rec.get_workflow(self._test_name)
        self.assertIsNone(wf)

    def test_empty_recording_discarded(self):
        self.rec.start_recording(self._test_name)
        # Stop with zero steps
        wf = self.rec.stop_recording()
        self.assertIsNone(wf)


class TestWorkflowRecorderCRUD(unittest.TestCase):
    """Test CRUD operations."""

    def setUp(self):
        from os_layer.workflow_recorder import WorkflowRecorder, StepAction
        self.rec = WorkflowRecorder()
        self._test_name = f"_test_crud_{int(time.time())}"

        # Create a test workflow
        self.rec.start_recording(self._test_name, description="Test workflow")
        self.rec.record_step(StepAction.OPEN_APP, target="code.exe")
        self.rec.record_step(StepAction.SNAP_WINDOW, target="VS Code",
                             parameters={"position": "left"})
        self.rec.stop_recording()

    def tearDown(self):
        self.rec.delete_workflow(self._test_name)

    def test_get_workflow(self):
        wf = self.rec.get_workflow(self._test_name)
        self.assertIsNotNone(wf)
        self.assertEqual(wf.name, self._test_name)
        self.assertEqual(wf.step_count, 2)
        self.assertEqual(wf.description, "Test workflow")

    def test_get_nonexistent_workflow(self):
        wf = self.rec.get_workflow("nonexistent_workflow_xyz")
        self.assertIsNone(wf)

    def test_list_workflows(self):
        workflows = self.rec.list_workflows()
        self.assertIsInstance(workflows, list)
        names = [w["name"] for w in workflows]
        self.assertIn(self._test_name, names)

    def test_delete_workflow(self):
        ok = self.rec.delete_workflow(self._test_name)
        self.assertTrue(ok)

        wf = self.rec.get_workflow(self._test_name)
        self.assertIsNone(wf)

    def test_delete_nonexistent(self):
        ok = self.rec.delete_workflow("nonexistent_xyz")
        self.assertFalse(ok)

    def test_overwrite_workflow(self):
        from os_layer.workflow_recorder import StepAction
        # Record same name again
        self.rec.start_recording(self._test_name, description="Updated")
        self.rec.record_step(StepAction.CUSTOM, target="single_step")
        self.rec.stop_recording()

        wf = self.rec.get_workflow(self._test_name)
        self.assertIsNotNone(wf)
        self.assertEqual(wf.step_count, 1)
        self.assertEqual(wf.description, "Updated")


class TestWorkflowReplay(unittest.TestCase):
    """Test replay mechanics (dry run only — no actual window moves)."""

    def setUp(self):
        from os_layer.workflow_recorder import WorkflowRecorder, StepAction
        self.rec = WorkflowRecorder()
        self._test_name = f"_test_replay_{int(time.time())}"

        self.rec.start_recording(self._test_name)
        self.rec.record_step(StepAction.WAIT, parameters={"duration_ms": 100})
        self.rec.record_step(StepAction.CUSTOM, target="test_action")
        self.rec.stop_recording()

    def tearDown(self):
        self.rec.delete_workflow(self._test_name)

    def test_dry_run_replay(self):
        result = self.rec.replay_workflow(self._test_name, dry_run=True)
        self.assertTrue(result.success)
        self.assertEqual(result.steps_executed, 2)
        self.assertEqual(result.steps_total, 2)
        self.assertEqual(len(result.errors), 0)

    def test_replay_nonexistent(self):
        result = self.rec.replay_workflow("nonexistent_xyz")
        self.assertFalse(result.success)
        self.assertTrue(len(result.errors) > 0)

    def test_speed_factor(self):
        # 10x speed should complete very fast
        start = time.time()
        result = self.rec.replay_workflow(self._test_name, speed_factor=10.0, dry_run=True)
        elapsed = time.time() - start
        self.assertTrue(result.success)
        self.assertLess(elapsed, 2.0)


if __name__ == "__main__":
    unittest.main()
