"""
os_layer/predictive.py — Usage Pattern Analysis & Predictive Execution
=======================================================================
First-order Markov chain over normalized application states.
Records user transitions (window focus changes, file edits),
maintains a frequency matrix in SQLite, and predicts next
likely actions with confidence scores.

Architecture:
    - Subscribes to ``window.focus_changed`` and ``file.modified``
      events from the central event bus.
    - Normalizes window titles to collapse dynamic content
      (file paths, issue numbers, timestamps) into stable states.
    - Stores raw actions in ``user_action_history`` and aggregated
      frequencies in ``state_transitions``.
    - Rolling window cleanup (configurable via PREDICTIVE_HISTORY_DAYS)
      prevents stale behavioral data from skewing predictions.
    - Risk-gated action dispatch: safe actions trigger silently,
      side-effect actions surface as UI recommendations.

Hardware Budget:
    Two SQL queries per prediction. Zero model files.
    Cleanup runs once per session at startup.

Thread Safety:
    All public methods are thread-safe. Database access uses
    thread-local connections with WAL mode.
"""

from __future__ import annotations

import logging
import re
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Optional

from core.config import config

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────

DB_PATH = Path(__file__).resolve().parent.parent / "memory" / "agent_episodes.db"

_TRIGGER_COOLDOWN_SEC = 60.0   # Deduplication window for repeated predictions

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS user_action_history (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp        REAL    NOT NULL,
    app_name         TEXT    NOT NULL DEFAULT '',
    window_title     TEXT    NOT NULL DEFAULT '',
    normalized_state TEXT    NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_action_hist_ts
    ON user_action_history(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_action_hist_state
    ON user_action_history(normalized_state);

CREATE TABLE IF NOT EXISTS state_transitions (
    source_state TEXT    NOT NULL,
    target_state TEXT    NOT NULL,
    count        INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (source_state, target_state)
);

CREATE INDEX IF NOT EXISTS idx_transitions_source
    ON state_transitions(source_state);
"""


# ── Data Classes ─────────────────────────────────────────────────────────────

@dataclass
class Prediction:
    """A predicted next action with confidence and risk classification."""
    predicted_state: str
    confidence: float
    risk_level: Literal["safe", "side_effect"]


# ── State Normalization ──────────────────────────────────────────────────────

# Patterns to strip from dynamic window titles
_RE_ISSUE_NUM = re.compile(r"#\d+")
_RE_FILE_PATH = re.compile(r"[\w/\\]+\.\w+")
_RE_LONG_NUMS = re.compile(r"\d{2,}")
_STRIP_CHARS = " —-|·"


def _normalize_state(app_name: str, window_title: str) -> str:
    """
    Collapse variable window titles to stable state signatures.

    Examples:
        ("Code", "main.py - Visual Studio Code") → "Code::Visual Studio Code"
        ("Chrome", "Issue #847 - GitHub")        → "Chrome::Issue  - GitHub"
        ("Code", "config.py - Visual Studio Code") → "Code::Visual Studio Code"

    This prevents state-space fragmentation where every unique
    file/issue/tab creates an unrepeatable transition row.
    """
    title = _RE_ISSUE_NUM.sub("", window_title)
    title = _RE_FILE_PATH.sub("", title)
    title = _RE_LONG_NUMS.sub("", title)
    title = title.strip(_STRIP_CHARS)
    return f"{app_name}::{title[:60]}"


# ── Safe vs Side-Effect Classification ───────────────────────────────────────

# States containing these keywords trigger safe (silent) precomputation
_SAFE_ACTION_KEYWORDS = frozenset([
    "code", "visual studio", "notepad", "editor", "chrome", "firefox",
    "edge", "browser", "explorer", "terminal", "powershell", "cmd",
])


def _classify_risk(state: str) -> Literal["safe", "side_effect"]:
    """
    Classify a predicted state as safe or side-effect.

    Safe: read-only transitions (switching editors, browsers, file managers).
    Side-effect: launching tests, build tools, communication apps, etc.
    """
    lower = state.lower()
    for keyword in _SAFE_ACTION_KEYWORDS:
        if keyword in lower:
            return "safe"
    return "side_effect"


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


# ── Predictive Engine ────────────────────────────────────────────────────────

class PredictiveEngine:
    """
    First-order Markov chain predictor over normalized user states.

    Subscribes to event bus window/file events, records transitions,
    and computes P(target | source) = count / SUM(count) for each
    source state.

    Usage:
        engine = PredictiveEngine()
        engine.start()
        # ... events flow in automatically ...
        predictions = engine.predict_next_action("Code::Visual Studio Code")
    """

    def __init__(self) -> None:
        self._running = False
        self._last_state: Optional[str] = None
        self._subscriptions: list = []
        self._lock = threading.Lock()

        # Deduplication cache: {predicted_state: last_trigger_timestamp}
        self._last_triggered: dict[str, float] = {}

        _ensure_schema()

    # ── Public API ───────────────────────────────────────────────────────

    def start(self) -> None:
        """Subscribe to event bus and run startup cleanup."""
        if self._running:
            logger.warning("[Predictive] Already running")
            return

        self._running = True

        # Subscribe to relevant events
        from os_layer.event_bus import get_event_bus, EventType
        bus = get_event_bus()

        sub_focus = bus.subscribe(
            callback=self._handle_focus_change,
            event_types={EventType.WINDOW_FOCUS_CHANGED},
            name="predictive_focus_tracker",
        )
        sub_file = bus.subscribe(
            callback=self._handle_file_event,
            event_types={EventType.FILE_MODIFIED},
            name="predictive_file_tracker",
        )
        self._subscriptions = [sub_focus, sub_file]

        # Run rolling-window cleanup once per session at startup
        cleanup_thread = threading.Thread(
            target=self._cleanup_old_history,
            daemon=True,
            name="predictive-history-cleanup",
        )
        cleanup_thread.start()

        logger.info("[Predictive] ✅ Engine started")

    def stop(self) -> None:
        """Unsubscribe and shut down."""
        if not self._running:
            return
        self._running = False

        from os_layer.event_bus import get_event_bus
        bus = get_event_bus()
        for sub in self._subscriptions:
            bus.unsubscribe(sub)
        self._subscriptions.clear()

        logger.info("[Predictive] 🛑 Engine stopped")

    def record_transition(self, source_state: str, target_state: str) -> None:
        """
        Record a state transition in SQLite.

        Updates both the raw action history and the aggregated
        transition frequency matrix.
        """
        if not source_state or not target_state:
            return
        if source_state == target_state:
            return  # Self-loops carry no predictive value

        conn = _get_conn()
        now = time.time()

        # Insert raw action record
        conn.execute(
            """INSERT INTO user_action_history
               (timestamp, app_name, window_title, normalized_state)
               VALUES (?, '', '', ?)""",
            (now, target_state),
        )

        # Upsert transition frequency
        conn.execute(
            """INSERT INTO state_transitions (source_state, target_state, count)
               VALUES (?, ?, 1)
               ON CONFLICT(source_state, target_state)
               DO UPDATE SET count = count + 1""",
            (source_state, target_state),
        )
        conn.commit()

    def predict_next_action(
        self,
        current_state: str,
        limit: int = 3,
    ) -> list[Prediction]:
        """
        Predict the most likely next states from ``current_state``.

        Returns up to ``limit`` predictions sorted by descending
        confidence, each annotated with a risk level.

        Confidence = count / SUM(count) WHERE source_state = current_state.
        """
        if not current_state:
            return []

        conn = _get_conn()

        rows = conn.execute(
            """SELECT target_state, count,
                      CAST(count AS REAL) / SUM(count) OVER () AS confidence
               FROM state_transitions
               WHERE source_state = ?
               ORDER BY count DESC
               LIMIT ?""",
            (current_state, limit),
        ).fetchall()

        predictions = []
        for row in rows:
            target = row["target_state"]
            conf = row["confidence"]
            risk = _classify_risk(target)
            predictions.append(Prediction(
                predicted_state=target,
                confidence=round(conf, 4),
                risk_level=risk,
            ))

        return predictions

    def trigger_precomputation(self, prediction: Prediction) -> bool:
        """
        Dispatch a predicted action based on risk level and confidence.

        Returns True if the action was triggered, False if skipped
        (due to deduplication cooldown or insufficient confidence).

        Routing:
            safe (>= 80%):        Silent background warming
            side_effect (>= 85%): UI recommendation button
        """
        threshold = config.PREDICTIVE_CONFIDENCE_THRESHOLD

        # Risk-specific confidence gates
        if prediction.risk_level == "safe":
            min_confidence = threshold
        else:
            # Side-effect actions require higher confidence
            min_confidence = max(threshold, 0.85)

        if prediction.confidence < min_confidence:
            return False

        # Deduplication guard: skip if same action triggered recently
        now = time.time()
        with self._lock:
            last_ts = self._last_triggered.get(prediction.predicted_state, 0.0)
            if (now - last_ts) < _TRIGGER_COOLDOWN_SEC:
                return False
            self._last_triggered[prediction.predicted_state] = now

        if prediction.risk_level == "safe":
            logger.info(
                f"[Predictive] 🔮 Silent precompute: "
                f"{prediction.predicted_state} "
                f"(confidence={prediction.confidence:.0%})"
            )
            # Future: warm doc index, ChromaDB query, URL prefetch
            return True
        else:
            logger.info(
                f"[Predictive] 💡 UI recommendation: "
                f"{prediction.predicted_state} "
                f"(confidence={prediction.confidence:.0%})"
            )
            # Future: push recommendation to WebView UI
            return True

    # ── Event Handlers ───────────────────────────────────────────────────

    def _handle_focus_change(self, event) -> None:
        """Handle window.focus_changed events."""
        if not self._running:
            return

        payload = event.payload
        app_name = payload.get("app_name", "")
        window_title = payload.get("window_title", "")

        if not app_name:
            return

        new_state = _normalize_state(app_name, window_title)

        with self._lock:
            prev_state = self._last_state
            self._last_state = new_state

        if prev_state and prev_state != new_state:
            self.record_transition(prev_state, new_state)

            # Check predictions after recording
            predictions = self.predict_next_action(new_state)
            for pred in predictions:
                self.trigger_precomputation(pred)

    def _handle_file_event(self, event) -> None:
        """Handle file.modified events as contextual signals."""
        if not self._running:
            return

        payload = event.payload
        file_path = payload.get("path", "")
        if not file_path:
            return

        # Use the file extension as context, not the full path
        ext = Path(file_path).suffix.lower()
        if not ext:
            return

        state = f"file_edit::{ext}"

        with self._lock:
            prev_state = self._last_state
            self._last_state = state

        if prev_state and prev_state != state:
            self.record_transition(prev_state, state)

    # ── History Cleanup ──────────────────────────────────────────────────

    def _cleanup_old_history(self) -> None:
        """
        Prune action history older than PREDICTIVE_HISTORY_DAYS
        and rebuild the transition frequency matrix from the
        remaining rows.

        Also prunes the actuation action_history table using
        tiered retention: LOW=30d, MEDIUM=90d, HIGH=365d.

        Runs once per session at startup on a background thread.
        """
        days = config.PREDICTIVE_HISTORY_DAYS
        cutoff = time.time() - (days * 86400)

        try:
            conn = _get_conn()

            # Count rows before pruning
            before = conn.execute(
                "SELECT COUNT(*) FROM user_action_history"
            ).fetchone()[0]

            # Delete old history
            conn.execute(
                "DELETE FROM user_action_history WHERE timestamp < ?",
                (cutoff,),
            )

            after = conn.execute(
                "SELECT COUNT(*) FROM user_action_history"
            ).fetchone()[0]

            pruned = before - after

            if pruned > 0:
                logger.info(
                    f"[Predictive] 🧹 Pruned {pruned} history rows "
                    f"older than {days} days"
                )

                # Rebuild transition matrix from remaining history
                self._rebuild_transitions(conn)

            conn.commit()
            logger.info(
                f"[Predictive] History cleanup done. "
                f"Remaining rows: {after}"
            )

        except Exception as e:
            logger.error(f"[Predictive] History cleanup failed: {e}")

        # Prune actuation action_history with tiered retention
        self._prune_action_history()

    def _prune_action_history(self) -> None:
        """
        Prune the actuation action_history table using tiered retention.

        Retention windows (configurable via .env):
            LOW risk:    ACTION_RETENTION_LOW_DAYS    (default 30)
            MEDIUM risk: ACTION_RETENTION_MEDIUM_DAYS (default 90)
            HIGH risk:   ACTION_RETENTION_HIGH_DAYS   (default 365)

        This prevents DB bloat from frequent LOW-risk read-only actions
        while preserving the audit trail for HIGH-risk shell/FS operations.
        """
        now = time.time()
        tiers = [
            ("LOW",    config.ACTION_RETENTION_LOW_DAYS),
            ("MEDIUM", config.ACTION_RETENTION_MEDIUM_DAYS),
            ("HIGH",   config.ACTION_RETENTION_HIGH_DAYS),
        ]

        total_pruned = 0
        try:
            conn = _get_conn()

            # Check if the table exists (actuation module may not be initialized)
            table_exists = conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name='action_history'"
            ).fetchone()

            if not table_exists:
                return

            for risk_level, retention_days in tiers:
                cutoff = now - (retention_days * 86400)
                cursor = conn.execute(
                    "DELETE FROM action_history "
                    "WHERE risk_level = ? AND timestamp < ?",
                    (risk_level, cutoff),
                )
                pruned = cursor.rowcount
                if pruned > 0:
                    total_pruned += pruned
                    logger.info(
                        f"[Predictive] 🧹 Pruned {pruned} "
                        f"{risk_level} action_history rows "
                        f"(>{retention_days}d old)"
                    )

            conn.commit()

            if total_pruned > 0:
                remaining = conn.execute(
                    "SELECT COUNT(*) FROM action_history"
                ).fetchone()[0]
                logger.info(
                    f"[Predictive] Action history cleanup done. "
                    f"Total pruned: {total_pruned}, remaining: {remaining}"
                )

        except Exception as e:
            logger.error(f"[Predictive] Action history prune failed: {e}")

    @staticmethod
    def _rebuild_transitions(conn: sqlite3.Connection) -> None:
        """
        Recompute state_transitions from user_action_history.

        Pairs consecutive rows (ordered by timestamp) to derive
        source→target transitions and their frequencies.
        """
        conn.execute("DELETE FROM state_transitions")

        conn.execute("""
            INSERT INTO state_transitions (source_state, target_state, count)
            SELECT
                prev_state,
                curr_state,
                COUNT(*) as cnt
            FROM (
                SELECT
                    normalized_state AS curr_state,
                    LAG(normalized_state) OVER (ORDER BY timestamp) AS prev_state
                FROM user_action_history
                WHERE normalized_state != ''
            ) paired
            WHERE prev_state IS NOT NULL
              AND prev_state != curr_state
            GROUP BY prev_state, curr_state
            ON CONFLICT(source_state, target_state)
            DO UPDATE SET count = excluded.count
        """)


# ── Singleton ────────────────────────────────────────────────────────────────

_instance: Optional[PredictiveEngine] = None
_instance_lock = threading.Lock()


def get_predictive_engine() -> PredictiveEngine:
    """Thread-safe singleton accessor."""
    global _instance
    if _instance is None:
        with _instance_lock:
            if _instance is None:
                _instance = PredictiveEngine()
    return _instance
