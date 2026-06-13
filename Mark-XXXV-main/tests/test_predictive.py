"""
tests/test_predictive.py — Predictive Engine Tests
====================================================
Tests for state normalization, SQLite transition logging,
Markov probability calculation, rolling window cleanup,
risk gating, and deduplication guard.

Uses an isolated in-memory-like temp database to avoid
polluting production data.
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock


class TestStateNormalization(unittest.TestCase):
    """Test the _normalize_state function for title collapsing."""

    def test_strips_file_paths(self):
        from os_layer.predictive import _normalize_state
        result = _normalize_state("Code", "main.py - Visual Studio Code")
        self.assertNotIn("main.py", result)
        self.assertIn("Code::", result)

    def test_strips_issue_numbers(self):
        from os_layer.predictive import _normalize_state
        result = _normalize_state("Chrome", "Issue #847 - GitHub")
        self.assertNotIn("#847", result)
        self.assertIn("Chrome::", result)

    def test_strips_long_numbers(self):
        from os_layer.predictive import _normalize_state
        result = _normalize_state("Browser", "Order 123456 - Dashboard")
        self.assertNotIn("123456", result)

    def test_same_app_different_files_collapse(self):
        from os_layer.predictive import _normalize_state
        state1 = _normalize_state("Code", "config.py - Visual Studio Code")
        state2 = _normalize_state("Code", "main.py - Visual Studio Code")
        self.assertEqual(state1, state2)

    def test_different_apps_stay_distinct(self):
        from os_layer.predictive import _normalize_state
        state1 = _normalize_state("Code", "file.py - Visual Studio Code")
        state2 = _normalize_state("Chrome", "Google - Chrome")
        self.assertNotEqual(state1, state2)

    def test_truncates_long_titles(self):
        from os_layer.predictive import _normalize_state
        long_title = "A" * 200
        result = _normalize_state("App", long_title)
        # app_name:: + 60 chars max
        self.assertLessEqual(len(result), len("App::") + 60)

    def test_empty_inputs(self):
        from os_layer.predictive import _normalize_state
        result = _normalize_state("", "")
        self.assertEqual(result, "::")


class TestRiskClassification(unittest.TestCase):
    """Test the risk classification logic."""

    def test_code_editor_is_safe(self):
        from os_layer.predictive import _classify_risk
        self.assertEqual(_classify_risk("Code::Visual Studio Code"), "safe")

    def test_browser_is_safe(self):
        from os_layer.predictive import _classify_risk
        self.assertEqual(_classify_risk("Chrome::Google"), "safe")

    def test_terminal_is_safe(self):
        from os_layer.predictive import _classify_risk
        self.assertEqual(_classify_risk("Terminal::PowerShell"), "safe")

    def test_unknown_app_is_side_effect(self):
        from os_layer.predictive import _classify_risk
        self.assertEqual(_classify_risk("Slack::General"), "side_effect")

    def test_build_tool_is_side_effect(self):
        from os_layer.predictive import _classify_risk
        self.assertEqual(_classify_risk("Maven::Build"), "side_effect")


class TestPredictiveEngineSQLite(unittest.TestCase):
    """Test SQLite schema, transitions, and predictions with real DB."""

    def setUp(self):
        """Create a temp database and redirect the module DB_PATH."""
        self._tmp_dir = tempfile.mkdtemp()
        self._tmp_db = Path(self._tmp_dir) / "test_episodes.db"

        # Patch DB_PATH and reset schema state
        import os_layer.predictive as pred_mod
        self._orig_db = pred_mod.DB_PATH
        pred_mod.DB_PATH = self._tmp_db
        pred_mod._schema_initialized = False
        # Reset thread-local connection
        if hasattr(pred_mod._db_local, "conn"):
            del pred_mod._db_local.conn

    def tearDown(self):
        import os_layer.predictive as pred_mod
        # Close thread-local connection
        conn = getattr(pred_mod._db_local, "conn", None)
        if conn:
            conn.close()
            del pred_mod._db_local.conn
        # Restore original DB_PATH
        pred_mod.DB_PATH = self._orig_db
        pred_mod._schema_initialized = False
        # Cleanup temp files
        try:
            self._tmp_db.unlink(missing_ok=True)
            Path(self._tmp_dir).rmdir()
        except Exception:
            pass

    def test_schema_creates_tables(self):
        from os_layer.predictive import _ensure_schema, _get_conn
        _ensure_schema()
        conn = _get_conn()

        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        table_names = {row["name"] for row in tables}
        self.assertIn("user_action_history", table_names)
        self.assertIn("state_transitions", table_names)

    def test_record_transition_inserts(self):
        from os_layer.predictive import PredictiveEngine, _get_conn, _ensure_schema
        _ensure_schema()

        engine = PredictiveEngine()
        engine.record_transition("A", "B")
        engine.record_transition("A", "B")
        engine.record_transition("A", "C")

        conn = _get_conn()
        rows = conn.execute(
            "SELECT * FROM state_transitions WHERE source_state = 'A' ORDER BY count DESC"
        ).fetchall()

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["target_state"], "B")
        self.assertEqual(rows[0]["count"], 2)
        self.assertEqual(rows[1]["target_state"], "C")
        self.assertEqual(rows[1]["count"], 1)

    def test_self_loop_ignored(self):
        from os_layer.predictive import PredictiveEngine, _get_conn, _ensure_schema
        _ensure_schema()

        engine = PredictiveEngine()
        engine.record_transition("A", "A")

        conn = _get_conn()
        count = conn.execute(
            "SELECT COUNT(*) FROM state_transitions"
        ).fetchone()[0]
        self.assertEqual(count, 0)

    def test_empty_states_ignored(self):
        from os_layer.predictive import PredictiveEngine, _get_conn, _ensure_schema
        _ensure_schema()

        engine = PredictiveEngine()
        engine.record_transition("", "B")
        engine.record_transition("A", "")

        conn = _get_conn()
        count = conn.execute(
            "SELECT COUNT(*) FROM state_transitions"
        ).fetchone()[0]
        self.assertEqual(count, 0)


class TestMarkovPrediction(unittest.TestCase):
    """Test probability calculation and prediction ranking."""

    def setUp(self):
        self._tmp_dir = tempfile.mkdtemp()
        self._tmp_db = Path(self._tmp_dir) / "test_episodes.db"

        import os_layer.predictive as pred_mod
        self._orig_db = pred_mod.DB_PATH
        pred_mod.DB_PATH = self._tmp_db
        pred_mod._schema_initialized = False
        if hasattr(pred_mod._db_local, "conn"):
            del pred_mod._db_local.conn

    def tearDown(self):
        import os_layer.predictive as pred_mod
        conn = getattr(pred_mod._db_local, "conn", None)
        if conn:
            conn.close()
            del pred_mod._db_local.conn
        pred_mod.DB_PATH = self._orig_db
        pred_mod._schema_initialized = False
        try:
            self._tmp_db.unlink(missing_ok=True)
            Path(self._tmp_dir).rmdir()
        except Exception:
            pass

    def test_predict_from_frequency(self):
        """P(B|A) = 2/3, P(C|A) = 1/3 after A→B, A→B, A→C."""
        from os_layer.predictive import PredictiveEngine, _ensure_schema
        _ensure_schema()

        engine = PredictiveEngine()
        engine.record_transition("A", "B")
        engine.record_transition("A", "B")
        engine.record_transition("A", "C")

        predictions = engine.predict_next_action("A")
        self.assertEqual(len(predictions), 2)

        # Highest confidence should be B
        self.assertEqual(predictions[0].predicted_state, "B")
        self.assertAlmostEqual(predictions[0].confidence, 2 / 3, places=3)

        self.assertEqual(predictions[1].predicted_state, "C")
        self.assertAlmostEqual(predictions[1].confidence, 1 / 3, places=3)

    def test_predict_empty_state(self):
        from os_layer.predictive import PredictiveEngine, _ensure_schema
        _ensure_schema()

        engine = PredictiveEngine()
        predictions = engine.predict_next_action("")
        self.assertEqual(predictions, [])

    def test_predict_unknown_state(self):
        from os_layer.predictive import PredictiveEngine, _ensure_schema
        _ensure_schema()

        engine = PredictiveEngine()
        predictions = engine.predict_next_action("NONEXISTENT")
        self.assertEqual(predictions, [])

    def test_predict_respects_limit(self):
        from os_layer.predictive import PredictiveEngine, _ensure_schema
        _ensure_schema()

        engine = PredictiveEngine()
        for i in range(10):
            engine.record_transition("X", f"Y{i}")

        predictions = engine.predict_next_action("X", limit=3)
        self.assertEqual(len(predictions), 3)

    def test_predictions_have_risk_level(self):
        from os_layer.predictive import PredictiveEngine, _ensure_schema
        _ensure_schema()

        engine = PredictiveEngine()
        engine.record_transition("A", "Code::Visual Studio Code")

        predictions = engine.predict_next_action("A")
        self.assertEqual(len(predictions), 1)
        self.assertIn(predictions[0].risk_level, ("safe", "side_effect"))


class TestTriggerDeduplication(unittest.TestCase):
    """Test the 60-second cooldown deduplication guard."""

    def setUp(self):
        self._tmp_dir = tempfile.mkdtemp()
        self._tmp_db = Path(self._tmp_dir) / "test_episodes.db"

        import os_layer.predictive as pred_mod
        self._orig_db = pred_mod.DB_PATH
        pred_mod.DB_PATH = self._tmp_db
        pred_mod._schema_initialized = False
        if hasattr(pred_mod._db_local, "conn"):
            del pred_mod._db_local.conn

    def tearDown(self):
        import os_layer.predictive as pred_mod
        conn = getattr(pred_mod._db_local, "conn", None)
        if conn:
            conn.close()
            del pred_mod._db_local.conn
        pred_mod.DB_PATH = self._orig_db
        pred_mod._schema_initialized = False
        try:
            self._tmp_db.unlink(missing_ok=True)
            Path(self._tmp_dir).rmdir()
        except Exception:
            pass

    def test_first_trigger_succeeds(self):
        from os_layer.predictive import PredictiveEngine, Prediction, _ensure_schema
        _ensure_schema()

        engine = PredictiveEngine()
        pred = Prediction(
            predicted_state="Code::Visual Studio Code",
            confidence=0.95,
            risk_level="safe",
        )
        result = engine.trigger_precomputation(pred)
        self.assertTrue(result)

    def test_duplicate_trigger_within_cooldown_blocked(self):
        from os_layer.predictive import PredictiveEngine, Prediction, _ensure_schema
        _ensure_schema()

        engine = PredictiveEngine()
        pred = Prediction(
            predicted_state="Code::Visual Studio Code",
            confidence=0.95,
            risk_level="safe",
        )

        # First trigger succeeds
        self.assertTrue(engine.trigger_precomputation(pred))
        # Second trigger within 60s is blocked
        self.assertFalse(engine.trigger_precomputation(pred))

    def test_low_confidence_skipped(self):
        from os_layer.predictive import PredictiveEngine, Prediction, _ensure_schema
        _ensure_schema()

        engine = PredictiveEngine()
        pred = Prediction(
            predicted_state="Code::Visual Studio Code",
            confidence=0.5,  # Below 0.8 threshold
            risk_level="safe",
        )
        result = engine.trigger_precomputation(pred)
        self.assertFalse(result)

    def test_side_effect_needs_higher_confidence(self):
        from os_layer.predictive import PredictiveEngine, Prediction, _ensure_schema
        _ensure_schema()

        engine = PredictiveEngine()

        # 82% confidence — above safe threshold (0.8) but below side_effect (0.85)
        pred = Prediction(
            predicted_state="Slack::General",
            confidence=0.82,
            risk_level="side_effect",
        )
        result = engine.trigger_precomputation(pred)
        self.assertFalse(result)

        # 90% confidence — above side_effect threshold
        pred_high = Prediction(
            predicted_state="Slack::DM",
            confidence=0.90,
            risk_level="side_effect",
        )
        result_high = engine.trigger_precomputation(pred_high)
        self.assertTrue(result_high)


class TestHistoryCleanup(unittest.TestCase):
    """Test rolling window history pruning and transition rebuild."""

    def setUp(self):
        self._tmp_dir = tempfile.mkdtemp()
        self._tmp_db = Path(self._tmp_dir) / "test_episodes.db"

        import os_layer.predictive as pred_mod
        self._orig_db = pred_mod.DB_PATH
        pred_mod.DB_PATH = self._tmp_db
        pred_mod._schema_initialized = False
        if hasattr(pred_mod._db_local, "conn"):
            del pred_mod._db_local.conn

    def tearDown(self):
        import os_layer.predictive as pred_mod
        conn = getattr(pred_mod._db_local, "conn", None)
        if conn:
            conn.close()
            del pred_mod._db_local.conn
        pred_mod.DB_PATH = self._orig_db
        pred_mod._schema_initialized = False
        try:
            self._tmp_db.unlink(missing_ok=True)
            Path(self._tmp_dir).rmdir()
        except Exception:
            pass

    @patch("os_layer.predictive.config")
    def test_cleanup_removes_old_rows(self, mock_config):
        from os_layer.predictive import PredictiveEngine, _ensure_schema, _get_conn

        mock_config.PREDICTIVE_HISTORY_DAYS = 30
        _ensure_schema()
        conn = _get_conn()

        now = time.time()
        old_ts = now - (31 * 86400)  # 31 days ago
        recent_ts = now - (1 * 86400)  # 1 day ago

        # Insert old + recent history
        conn.execute(
            "INSERT INTO user_action_history (timestamp, app_name, window_title, normalized_state) VALUES (?, 'old', 'old', 'A')",
            (old_ts,),
        )
        conn.execute(
            "INSERT INTO user_action_history (timestamp, app_name, window_title, normalized_state) VALUES (?, 'old2', 'old2', 'B')",
            (old_ts + 1,),
        )
        conn.execute(
            "INSERT INTO user_action_history (timestamp, app_name, window_title, normalized_state) VALUES (?, 'new', 'new', 'C')",
            (recent_ts,),
        )
        conn.execute(
            "INSERT INTO user_action_history (timestamp, app_name, window_title, normalized_state) VALUES (?, 'new2', 'new2', 'D')",
            (recent_ts + 1,),
        )
        conn.commit()

        engine = PredictiveEngine()
        engine._cleanup_old_history()

        remaining = conn.execute(
            "SELECT COUNT(*) FROM user_action_history"
        ).fetchone()[0]
        self.assertEqual(remaining, 2)  # Only recent rows survive

    @patch("os_layer.predictive.config")
    def test_rebuild_transitions_from_history(self, mock_config):
        from os_layer.predictive import PredictiveEngine, _ensure_schema, _get_conn

        mock_config.PREDICTIVE_HISTORY_DAYS = 30
        _ensure_schema()
        conn = _get_conn()

        now = time.time()
        # Insert sequence: A → B → A → B (recent)
        for i, state in enumerate(["A", "B", "A", "B"]):
            conn.execute(
                "INSERT INTO user_action_history (timestamp, app_name, window_title, normalized_state) VALUES (?, '', '', ?)",
                (now + i, state),
            )
        conn.commit()

        engine = PredictiveEngine()
        engine._rebuild_transitions(conn)
        conn.commit()

        # Should have transitions: A→B (count=2), B→A (count=1)
        rows = conn.execute(
            "SELECT * FROM state_transitions ORDER BY count DESC"
        ).fetchall()

        transition_map = {
            (r["source_state"], r["target_state"]): r["count"]
            for r in rows
        }

        self.assertEqual(transition_map.get(("A", "B")), 2)
        self.assertEqual(transition_map.get(("B", "A")), 1)


if __name__ == "__main__":
    unittest.main()
