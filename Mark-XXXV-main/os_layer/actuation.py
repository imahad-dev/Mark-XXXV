"""
os_layer/actuation.py — Universal Actuation Engine
====================================================
Safe, serialized, rollback-supported execution of UI, filesystem,
shell, browser, and communication actions.

Concurrency Model:
    - LOW risk actions execute immediately (read-only, no lock).
    - MEDIUM/HIGH risk actions enter a FIFO queue protected by
      a global Mutex. Only one executes at a time.
    - A background worker thread dequeues and processes actions
      sequentially, sleeping ACTUATION_COOLDOWN_SEC between each.

Rollback Contract:
    Every state-mutating action must either:
      a) Populate undo_payload with a recovery strategy, OR
      b) Set undo_supported=False (forces hard-gate confirmation).

Action History:
    All executed actions (success or failure) are logged to the
    ``action_history`` table in agent_episodes.db for audit and
    retention-based pruning by the PredictiveEngine.

Architecture Constraint:
    Does NOT import from core.llm_orchestrator. Action dispatch
    is driven via ToolRegistry tool calls or EventBus commands.
"""

from __future__ import annotations

import json
import logging
import os
import queue
import re
import shutil
import sqlite3
import subprocess
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Literal, Optional

from core.config import config

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────

DB_PATH = Path(__file__).resolve().parent.parent / "memory" / "agent_episodes.db"
BACKUP_DIR = Path(__file__).resolve().parent.parent / ".agent" / "scratch" / "backups"

_ACTION_HISTORY_SCHEMA = """
CREATE TABLE IF NOT EXISTS action_history (
    id              TEXT PRIMARY KEY,
    timestamp       REAL    NOT NULL,
    action_type     TEXT    NOT NULL,
    target          TEXT    NOT NULL DEFAULT '',
    parameters      TEXT    DEFAULT '{}',
    risk_level      TEXT    NOT NULL DEFAULT 'LOW',
    undo_supported  INTEGER NOT NULL DEFAULT 0,
    outcome         TEXT    NOT NULL DEFAULT 'pending',
    error_message   TEXT    DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_action_hist_ts
    ON action_history(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_action_hist_risk
    ON action_history(risk_level);
"""

# Regex patterns for detecting registry-mutating shell commands
_REG_MUTATING_PATTERNS = re.compile(
    r"(?:^|\s)(?:reg\s+(?:add|delete|copy)|regedit)"
    r"|Set-ItemProperty|New-ItemProperty|Remove-ItemProperty",
    re.IGNORECASE,
)

# Patterns for irreversible/destructive shell commands
# NOTE: \bformat\b was removed — it false-positives on Format-List, Format-Table.
# Format-Volume and disk format syntax are matched explicitly.
_DESTRUCTIVE_PATTERNS = re.compile(
    r"\bFormat-Volume\b"
    r"|\bformat\s+[A-Za-z]:\\"
    r"|\brd\s+/s\b"
    r"|\brm\s+-rf\b"
    r"|\bRemove-Item\s+-Recurse\b"
    r"|\bClear-Disk\b"
    r"|\bInitialize-Disk\b",
    re.IGNORECASE,
)

# Pip command detection for diff-based rollback
_PIP_INSTALL_PATTERN = re.compile(
    r"\bpip3?\s+install\b", re.IGNORECASE,
)

# Git command detection for state-specific rollback
_GIT_DESTRUCTIVE_PATTERN = re.compile(
    r"\bgit\s+(?:commit|merge|rebase|reset|push)\b", re.IGNORECASE,
)
_GIT_PUSH_PATTERN = re.compile(r"\bgit\s+push\b", re.IGNORECASE)

# Patterns for filesystem-affecting shell commands
_FS_MUTATING_PATTERNS = re.compile(
    r"(?:^|\s)(?:copy|move|rename|ren|del\s|rd\s|mkdir|rmdir"
    r"|Move-Item|Copy-Item|Remove-Item|New-Item|Rename-Item)",
    re.IGNORECASE,
)


# ── Enums & Data Classes ────────────────────────────────────────────────────

class ActionType(str, Enum):
    FS_WRITE = "FS_WRITE"
    FS_DELETE = "FS_DELETE"
    PROCESS_SPAWN = "PROCESS_SPAWN"
    BROWSER_INTERACT = "BROWSER_INTERACT"
    BROWSER_NAVIGATE = "BROWSER_NAVIGATE"
    COM_EMAIL_DRAFT = "COM_EMAIL_DRAFT"
    SHELL_EXEC = "SHELL_EXEC"


class RiskLevel(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


@dataclass
class ActionPayload:
    """Structured representation of an actuation request."""
    action_type: str
    target: str
    parameters: dict = field(default_factory=dict)
    risk_level: str = "LOW"
    undo_supported: bool = True
    undo_payload: Optional[dict] = None
    action_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ActionResult:
    """Outcome of an executed action."""
    action_id: str
    success: bool
    outcome: str  # "success" | "failed" | "rolled_back" | "cancelled"
    message: str = ""
    error: str = ""


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
        conn.executescript(_ACTION_HISTORY_SCHEMA)
        conn.commit()
        _schema_initialized = True


# ── Actuation Manager ────────────────────────────────────────────────────────

class ActuationManager:
    """
    Serialized, risk-gated actuation engine.

    LOW risk actions execute immediately on the caller thread.
    MEDIUM/HIGH risk actions are queued into a FIFO and processed
    by a single background worker thread with a mutex lock.

    Usage:
        mgr = ActuationManager()
        mgr.start()
        result = mgr.execute_action(payload)
        mgr.stop()
    """

    _VALID_ACTION_TYPES = {e.value for e in ActionType}
    _VALID_RISK_LEVELS = {e.value for e in RiskLevel}

    def __init__(self) -> None:
        self._actuation_lock = threading.Lock()
        self._action_queue: queue.Queue[ActionPayload] = queue.Queue()
        self._worker_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._running = False

        # Confirmation callback: injected by main.py to prompt the user
        # Signature: (payload: ActionPayload) -> bool (True = confirmed)
        self._confirm_callback: Optional[Callable[[ActionPayload], bool]] = None

        # ── Approved Shell Session State ─────────────────────────────────
        # When True, subsequent SHELL_EXEC HIGH risk actions skip the
        # Hard Gate Modal for the duration of the approved session.
        # Expires on: (1) task chain completion, (2) shell timeout/restart,
        # (3) idle expiry (APPROVED_SESSION_EXPIRY_SEC), or (4) manual revoke.
        self._shell_session_approved = False
        self._shell_session_approved_at: float = 0.0

        _ensure_schema()

    # ── Public API ───────────────────────────────────────────────────────

    def start(self) -> None:
        """Start the background worker thread for queued action processing."""
        if self._running:
            logger.warning("[Actuation] Already running")
            return
        self._running = True
        self._stop_event.clear()
        self._worker_thread = threading.Thread(
            target=self._worker_loop,
            daemon=True,
            name="actuation-worker",
        )
        self._worker_thread.start()
        logger.info("[Actuation] ✅ Engine started")

    def stop(self) -> None:
        """Signal the worker thread to drain and exit."""
        if not self._running:
            return
        self._running = False
        self._stop_event.set()
        # Unblock the worker if it's waiting on the queue
        self._action_queue.put(None)  # sentinel
        if self._worker_thread:
            self._worker_thread.join(timeout=5.0)
            self._worker_thread = None
        logger.info("[Actuation] 🛑 Engine stopped")

    def execute_action(self, payload: ActionPayload) -> ActionResult:
        """
        Dispatch an action for execution.

        LOW risk: executed immediately, inline.
        MEDIUM/HIGH risk: queued for the background worker.
        Returns the result for LOW; for queued actions, returns
        a 'queued' placeholder (actual result is logged asynchronously).
        """
        # Validate payload
        if payload.action_type not in self._VALID_ACTION_TYPES:
            return ActionResult(
                action_id=payload.action_id,
                success=False,
                outcome="failed",
                error=f"Invalid action_type: {payload.action_type}",
            )
        if payload.risk_level not in self._VALID_RISK_LEVELS:
            return ActionResult(
                action_id=payload.action_id,
                success=False,
                outcome="failed",
                error=f"Invalid risk_level: {payload.risk_level}",
            )

        if payload.risk_level == RiskLevel.LOW:
            return self._execute_inline(payload)

        # MEDIUM/HIGH — queue for sequential processing
        self._action_queue.put(payload)
        logger.info(
            f"[Actuation] 📋 Queued {payload.action_type} "
            f"(risk={payload.risk_level}, id={payload.action_id[:8]})"
        )
        return ActionResult(
            action_id=payload.action_id,
            success=True,
            outcome="queued",
            message="Action queued for sequential processing",
        )

    # ── Background Worker ────────────────────────────────────────────────

    def _worker_loop(self) -> None:
        """
        Sequential action processor.

        Dequeues MEDIUM/HIGH actions one at a time, acquires the
        actuation lock, optionally prompts the user for confirmation,
        executes, logs the result, and sleeps for the cooldown period.
        """
        while not self._stop_event.is_set():
            try:
                payload = self._action_queue.get(timeout=1.0)
            except queue.Empty:
                continue

            if payload is None:
                break  # Sentinel — shutdown requested

            with self._actuation_lock:
                result = self._execute_with_confirmation(payload)
                self._log_action(payload, result)

            # Cooldown between sequential actions
            cooldown = config.ACTUATION_COOLDOWN_SEC
            if cooldown > 0 and not self._stop_event.is_set():
                self._stop_event.wait(timeout=cooldown)

    # ── Execution Logic ──────────────────────────────────────────────────

    def _execute_inline(self, payload: ActionPayload) -> ActionResult:
        """Execute a LOW risk action immediately (no lock, no queue)."""
        try:
            result = self._dispatch(payload)
            self._log_action(payload, result)
            return result
        except Exception as e:
            result = ActionResult(
                action_id=payload.action_id,
                success=False,
                outcome="failed",
                error=str(e),
            )
            self._log_action(payload, result)
            return result

    def _execute_with_confirmation(self, payload: ActionPayload) -> ActionResult:
        """
        Execute a MEDIUM/HIGH action, optionally prompting for confirmation.

        HIGH risk actions always require explicit user confirmation,
        UNLESS an Approved Shell Session is active for SHELL_EXEC actions.
        MEDIUM risk actions show a toast (auto-dismiss after 5s).
        """
        # HIGH risk: hard-gate confirmation
        if payload.risk_level == RiskLevel.HIGH:
            # Check approved shell session bypass
            if (
                payload.action_type == ActionType.SHELL_EXEC
                and self.is_shell_session_approved()
            ):
                logger.info(
                    f"[Actuation] ✅ Approved session bypass for "
                    f"{payload.action_id[:8]}"
                )
            elif self._confirm_callback:
                confirmed = self._confirm_callback(payload)
                if not confirmed:
                    logger.info(
                        f"[Actuation] ❌ User rejected HIGH action: "
                        f"{payload.action_type} ({payload.action_id[:8]})"
                    )
                    return ActionResult(
                        action_id=payload.action_id,
                        success=False,
                        outcome="cancelled",
                        message="User rejected the action",
                    )
            else:
                # No confirmation callback wired — reject HIGH risk by default
                logger.warning(
                    f"[Actuation] ⚠️ No confirmation handler — "
                    f"rejecting HIGH risk action: {payload.action_type}"
                )
                return ActionResult(
                    action_id=payload.action_id,
                    success=False,
                    outcome="cancelled",
                    message="No confirmation handler available for HIGH risk action",
                )

        try:
            return self._dispatch(payload)
        except Exception as e:
            logger.error(f"[Actuation] Action failed: {e}")
            return ActionResult(
                action_id=payload.action_id,
                success=False,
                outcome="failed",
                error=str(e),
            )

    def _dispatch(self, payload: ActionPayload) -> ActionResult:
        """Route to the correct handler based on action_type."""
        handlers = {
            ActionType.FS_WRITE: self._handle_fs_write,
            ActionType.FS_DELETE: self._handle_fs_delete,
            ActionType.PROCESS_SPAWN: self._handle_process_spawn,
            ActionType.SHELL_EXEC: self._handle_shell_exec,
            ActionType.BROWSER_INTERACT: self._handle_browser_interact,
            ActionType.BROWSER_NAVIGATE: self._handle_browser_navigate,
            ActionType.COM_EMAIL_DRAFT: self._handle_com_email_draft,
        }

        handler = handlers.get(ActionType(payload.action_type))
        if not handler:
            return ActionResult(
                action_id=payload.action_id,
                success=False,
                outcome="failed",
                error=f"No handler for action_type: {payload.action_type}",
            )

        return handler(payload)

    # ── Action Handlers ──────────────────────────────────────────────────

    def _handle_fs_write(self, payload: ActionPayload) -> ActionResult:
        """Write content to a file, backing up the original first."""
        target_path = Path(payload.target)
        backup_path = BACKUP_DIR / payload.action_id

        try:
            # Backup existing file if it exists
            if target_path.exists():
                backup_path.mkdir(parents=True, exist_ok=True)
                shutil.copy2(str(target_path), str(backup_path / target_path.name))
                payload.undo_payload = {
                    "rollback_type": "restore_file",
                    "rollback_parameters": {
                        "backup_path": str(backup_path / target_path.name),
                        "original_path": str(target_path),
                    },
                }

            # Write content
            content = payload.parameters.get("content", "")
            target_path.parent.mkdir(parents=True, exist_ok=True)
            target_path.write_text(content, encoding="utf-8")

            return ActionResult(
                action_id=payload.action_id,
                success=True,
                outcome="success",
                message=f"Written to {target_path}",
            )
        except Exception as e:
            # Attempt rollback
            if payload.undo_payload:
                self._rollback_fs_restore(payload.undo_payload["rollback_parameters"])
            return ActionResult(
                action_id=payload.action_id,
                success=False,
                outcome="rolled_back",
                error=str(e),
            )

    def _handle_fs_delete(self, payload: ActionPayload) -> ActionResult:
        """Delete a file, backing it up first."""
        target_path = Path(payload.target)
        backup_path = BACKUP_DIR / payload.action_id

        try:
            if target_path.exists():
                backup_path.mkdir(parents=True, exist_ok=True)
                shutil.copy2(str(target_path), str(backup_path / target_path.name))
                payload.undo_payload = {
                    "rollback_type": "restore_file",
                    "rollback_parameters": {
                        "backup_path": str(backup_path / target_path.name),
                        "original_path": str(target_path),
                    },
                }
                target_path.unlink()
            return ActionResult(
                action_id=payload.action_id,
                success=True,
                outcome="success",
                message=f"Deleted {target_path}",
            )
        except Exception as e:
            if payload.undo_payload:
                self._rollback_fs_restore(payload.undo_payload["rollback_parameters"])
            return ActionResult(
                action_id=payload.action_id,
                success=False,
                outcome="rolled_back",
                error=str(e),
            )

    def _handle_process_spawn(self, payload: ActionPayload) -> ActionResult:
        """Spawn a subprocess and track its PID."""
        cmd = payload.parameters.get("command", "")
        if not cmd:
            return ActionResult(
                action_id=payload.action_id,
                success=False,
                outcome="failed",
                error="No command specified",
            )

        try:
            proc = subprocess.Popen(
                cmd,
                shell=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            payload.undo_payload = {
                "rollback_type": "kill_process",
                "rollback_parameters": {"pid": proc.pid},
            }
            return ActionResult(
                action_id=payload.action_id,
                success=True,
                outcome="success",
                message=f"Spawned PID {proc.pid}",
            )
        except Exception as e:
            return ActionResult(
                action_id=payload.action_id,
                success=False,
                outcome="failed",
                error=str(e),
            )

    def _handle_shell_exec(self, payload: ActionPayload) -> ActionResult:
        """
        Execute a shell command through the persistent AgenticShell.

        Pre-execution classification:
            - Destructive patterns (outside workspace): undo_supported=False
            - Destructive patterns (inside workspace): HIGH risk, FS backup
            - Registry-modifying: backup registry keys first
            - FS-mutating: snapshot targeted directories
            - pip install: diff-based rollback (before/after freeze)
            - git destructive: state-specific rollback (stash, SHA, abort)
            - git push: undo_supported=False (remote is irreversible)
        """
        cmd = payload.parameters.get("command", "")
        if not cmd:
            return ActionResult(
                action_id=payload.action_id,
                success=False,
                outcome="failed",
                error="No command specified",
            )

        # ── Destructive command classification ───────────────────────────
        if _DESTRUCTIVE_PATTERNS.search(cmd):
            if self._is_within_workspace(cmd):
                # Inside workspace — rollbackable via FS backup, but HIGH risk
                target_dir = payload.parameters.get("target_dir", payload.target)
                if target_dir and Path(target_dir).exists():
                    backup_path = BACKUP_DIR / payload.action_id
                    backup_path.mkdir(parents=True, exist_ok=True)
                    try:
                        if Path(target_dir).is_dir():
                            shutil.copytree(
                                target_dir,
                                str(backup_path / Path(target_dir).name),
                            )
                        else:
                            shutil.copy2(target_dir, str(backup_path))
                        payload.undo_payload = {
                            "rollback_type": "restore_directory",
                            "rollback_parameters": {
                                "backup_path": str(backup_path),
                                "original_path": target_dir,
                            },
                        }
                    except Exception as e:
                        logger.warning(f"[Actuation] FS snapshot failed: {e}")
            else:
                # Outside workspace — unrecoverable
                if payload.undo_supported:
                    return ActionResult(
                        action_id=payload.action_id,
                        success=False,
                        outcome="failed",
                        error=(
                            "Destructive command outside workspace detected. "
                            "Set undo_supported=False and risk_level=HIGH "
                            "to execute irreversible commands."
                        ),
                    )

        # ── Registry backup ──────────────────────────────────────────────
        elif _REG_MUTATING_PATTERNS.search(cmd):
            reg_key = payload.parameters.get("registry_key", "")
            if reg_key:
                backup_file = BACKUP_DIR / payload.action_id / "reg_backup.reg"
                backup_file.parent.mkdir(parents=True, exist_ok=True)
                try:
                    subprocess.run(
                        ["reg", "export", reg_key, str(backup_file), "/y"],
                        check=True,
                        capture_output=True,
                        timeout=10,
                    )
                    payload.undo_payload = {
                        "rollback_type": "import_registry",
                        "rollback_parameters": {
                            "backup_file": str(backup_file),
                        },
                    }
                except subprocess.CalledProcessError as e:
                    logger.warning(f"[Actuation] Registry backup failed: {e}")

        # ── Git push — irreversible ──────────────────────────────────────
        elif _GIT_PUSH_PATTERN.search(cmd):
            payload.undo_supported = False

        # ── Git destructive ops — state-specific rollback ────────────────
        elif _GIT_DESTRUCTIVE_PATTERN.search(cmd):
            self._snapshot_git_state(payload, cmd)

        # ── Pip install — diff-based rollback ────────────────────────────
        elif _PIP_INSTALL_PATTERN.search(cmd):
            self._snapshot_pip_state(payload)

        # ── FS-mutating commands — directory snapshot ────────────────────
        elif _FS_MUTATING_PATTERNS.search(cmd):
            target_dir = payload.parameters.get("target_dir", payload.target)
            if target_dir and Path(target_dir).exists():
                backup_path = BACKUP_DIR / payload.action_id
                backup_path.mkdir(parents=True, exist_ok=True)
                try:
                    if Path(target_dir).is_dir():
                        shutil.copytree(
                            target_dir,
                            str(backup_path / Path(target_dir).name),
                        )
                    else:
                        shutil.copy2(target_dir, str(backup_path))
                    payload.undo_payload = {
                        "rollback_type": "restore_directory",
                        "rollback_parameters": {
                            "backup_path": str(backup_path),
                            "original_path": target_dir,
                        },
                    }
                except Exception as e:
                    logger.warning(f"[Actuation] FS snapshot failed: {e}")

        # ── Execute via AgenticShell ─────────────────────────────────────
        try:
            from os_layer.agentic_shell import get_agentic_shell, AgenticShell
            shell = get_agentic_shell()
            structured = payload.parameters.get("structured", False)
            result = shell.execute(cmd, structured=structured)

            # Classify and emit event for predictive engine
            category = AgenticShell.classify_command(cmd)
            AgenticShell.emit_command_executed(category)

            # Capture pip diff after execution
            if _PIP_INSTALL_PATTERN.search(cmd) and not result.timed_out:
                self._capture_pip_diff(payload)

            if result.timed_out:
                # Timeout fired — revoke any approved session
                self.revoke_shell_session()
                return ActionResult(
                    action_id=payload.action_id,
                    success=False,
                    outcome="failed",
                    error=f"Command timed out (shell restarted): {result.stderr[:200]}",
                )

            if result.exit_code != 0:
                return ActionResult(
                    action_id=payload.action_id,
                    success=False,
                    outcome="failed",
                    error=f"Exit code {result.exit_code}: {result.stderr[:200]}",
                )

            return ActionResult(
                action_id=payload.action_id,
                success=True,
                outcome="success",
                message=result.stdout[:500] if result.stdout else "Command completed",
            )
        except Exception as e:
            logger.error(f"[Actuation] Shell execution error: {e}")
            return ActionResult(
                action_id=payload.action_id,
                success=False,
                outcome="failed",
                error=str(e),
            )

    def _handle_browser_interact(self, payload: ActionPayload) -> ActionResult:
        """
        Browser DOM interaction (click/type/hover/check).

        Rollback scoping:
            - type:  captures current input value before overwriting.
            - check: captures current boolean checked state.
            - click/hover: irreversible — undo_supported=False.

        Dispatch: extension bridge first, Playwright fallback if offline.
        """
        action = payload.parameters.get("action", "click")
        selector = payload.target
        value = payload.parameters.get("value")

        # ── Rollback scoping ─────────────────────────────────────────────
        if action == "type":
            original_value = self._get_element_value(selector)
            if original_value is not None:
                payload.undo_payload = {
                    "rollback_type": "browser_type",
                    "rollback_parameters": {
                        "selector": selector,
                        "value": original_value,
                    },
                }
            else:
                payload.undo_supported = False

        elif action == "check":
            original_checked = self._get_checked_state(selector)
            if original_checked is not None:
                payload.undo_payload = {
                    "rollback_type": "browser_check",
                    "rollback_parameters": {
                        "selector": selector,
                        "checked": original_checked,
                    },
                }
            else:
                payload.undo_supported = False

        else:
            # click / hover — no recoverable state
            payload.undo_supported = False

        # ── Dispatch ─────────────────────────────────────────────────────
        logger.info(
            f"[Actuation] 🌐 Browser {action}: {selector} "
            f"(value={value!r})"
        )
        result = self._dispatch_browser_action({
            "action_id": payload.action_id,
            "command": "interact",
            "selector": selector,
            "action": action,
            "value": value,
        })

        if result and result.get("status") == "success":
            return ActionResult(
                action_id=payload.action_id,
                success=True,
                outcome="success",
                message=f"Browser {action} on {selector}",
            )

        error_msg = (result or {}).get("error", "Extension and Playwright both unavailable")
        return ActionResult(
            action_id=payload.action_id,
            success=False,
            outcome="failed",
            error=error_msg,
        )

    def _handle_browser_navigate(self, payload: ActionPayload) -> ActionResult:
        """
        Browser page navigation.

        Captures the current URL before navigating for rollback.
        Dispatch: extension bridge first, Playwright fallback if offline.
        """
        url = payload.target

        # ── Capture current URL for rollback ─────────────────────────────
        current_url = self._get_current_url()
        if current_url:
            payload.undo_payload = {
                "rollback_type": "browser_navigate",
                "rollback_parameters": {
                    "url": current_url,
                },
            }
        else:
            payload.undo_supported = False

        # ── Dispatch ─────────────────────────────────────────────────────
        logger.info(f"[Actuation] 🌐 Browser navigate: {url}")
        result = self._dispatch_browser_action({
            "action_id": payload.action_id,
            "command": "navigate",
            "url": url,
        })

        if result and result.get("status") == "success":
            return ActionResult(
                action_id=payload.action_id,
                success=True,
                outcome="success",
                message=f"Navigated to {url}",
            )

        error_msg = (result or {}).get("error", "Extension and Playwright both unavailable")
        return ActionResult(
            action_id=payload.action_id,
            success=False,
            outcome="failed",
            error=error_msg,
        )

    def _handle_com_email_draft(self, payload: ActionPayload) -> ActionResult:
        """
        Create an email draft in Outlook via COM.

        Saves to Drafts folder only — never calls .Send().
        Stores the EntryID for rollback (delete draft).
        """
        try:
            import win32com.client
            outlook = win32com.client.Dispatch("Outlook.Application")
            mail = outlook.CreateItem(0)  # olMailItem
            mail.Subject = payload.parameters.get("subject", "")
            mail.Body = payload.parameters.get("body", "")
            to = payload.parameters.get("to", "")
            if to:
                mail.To = to
            mail.Save()

            entry_id = mail.EntryID
            payload.undo_payload = {
                "rollback_type": "delete_draft",
                "rollback_parameters": {"entry_id": entry_id},
            }

            return ActionResult(
                action_id=payload.action_id,
                success=True,
                outcome="success",
                message=f"Draft saved (EntryID: {entry_id[:12]}...)",
            )
        except ImportError:
            return ActionResult(
                action_id=payload.action_id,
                success=False,
                outcome="failed",
                error="win32com not available — Outlook COM disabled",
            )
        except Exception as e:
            return ActionResult(
                action_id=payload.action_id,
                success=False,
                outcome="failed",
                error=str(e),
            )

    # ── Rollback Helpers ─────────────────────────────────────────────────

    @staticmethod
    def _rollback_fs_restore(params: dict) -> None:
        """Restore a backed-up file to its original location."""
        backup = params.get("backup_path", "")
        original = params.get("original_path", "")
        if backup and original and Path(backup).exists():
            try:
                shutil.copy2(backup, original)
                logger.info(f"[Actuation] ↩️ Restored {original} from backup")
            except Exception as e:
                logger.error(f"[Actuation] Rollback restore failed: {e}")

    # ── Browser Dispatch & State Helpers ──────────────────────────────────

    @staticmethod
    def _dispatch_browser_action(action: dict) -> Optional[dict]:
        """
        Route a browser action to the extension bridge (primary)
        or Playwright engine (fallback).

        Returns the result dict from whichever backend handled it,
        or None if both are unavailable.
        """
        # Try extension bridge first
        try:
            from os_layer.browser_bridge import get_browser_bridge
            bridge = get_browser_bridge()
            if bridge.is_connected():
                result = bridge.send_action(action)
                if result is not None:
                    return result
                logger.info("[Actuation] Extension returned None — falling through to Playwright")
        except Exception as e:
            logger.debug(f"[Actuation] Bridge dispatch skipped: {e}")

        # Fallback to Playwright
        try:
            from os_layer.playwright_engine import get_playwright_engine
            engine = get_playwright_engine()

            command = action.get("command", "")
            if command == "navigate":
                return engine.navigate(action.get("url", ""))
            elif command == "interact":
                return engine.interact(
                    action.get("selector", ""),
                    action.get("action", "click"),
                    action.get("value"),
                )
        except Exception as e:
            logger.error(f"[Actuation] Playwright fallback failed: {e}")

        return None

    @staticmethod
    def _get_current_url() -> Optional[str]:
        """Get the current page URL from whichever browser backend is active."""
        try:
            from os_layer.browser_bridge import get_browser_bridge
            bridge = get_browser_bridge()
            if bridge.is_connected():
                result = bridge.send_action({"command": "get_url"})
                if result and "url" in result:
                    return result["url"]
        except Exception:
            pass

        try:
            from os_layer.playwright_engine import get_playwright_engine
            return get_playwright_engine().get_current_url()
        except Exception:
            return None

    @staticmethod
    def _get_element_value(selector: str) -> Optional[str]:
        """Get the current text value of an input element."""
        try:
            from os_layer.browser_bridge import get_browser_bridge
            bridge = get_browser_bridge()
            if bridge.is_connected():
                result = bridge.send_action({
                    "command": "get_value",
                    "selector": selector,
                })
                if result and "value" in result:
                    return result["value"]
        except Exception:
            pass

        try:
            from os_layer.playwright_engine import get_playwright_engine
            return get_playwright_engine().get_input_value(selector)
        except Exception:
            return None

    @staticmethod
    def _get_checked_state(selector: str) -> Optional[bool]:
        """Get the current checked state of a checkbox."""
        try:
            from os_layer.browser_bridge import get_browser_bridge
            bridge = get_browser_bridge()
            if bridge.is_connected():
                result = bridge.send_action({
                    "command": "get_checked",
                    "selector": selector,
                })
                if result and "checked" in result:
                    return result["checked"]
        except Exception:
            pass

        try:
            from os_layer.playwright_engine import get_playwright_engine
            return get_playwright_engine().get_checked_state(selector)
        except Exception:
            return None

    def _rollback_browser_navigate(self, params: dict) -> None:
        """Rollback: navigate back to the original URL."""
        url = params.get("url", "")
        if not url:
            return
        logger.info(f"[Actuation] ↩️ Rolling back navigation to {url}")
        self._dispatch_browser_action({
            "command": "navigate",
            "url": url,
        })

    def _rollback_browser_type(self, params: dict) -> None:
        """Rollback: restore the original input field value."""
        selector = params.get("selector", "")
        value = params.get("value", "")
        if not selector:
            return
        logger.info(f"[Actuation] ↩️ Restoring input {selector}")
        self._dispatch_browser_action({
            "command": "interact",
            "selector": selector,
            "action": "type",
            "value": value,
        })

    def _rollback_browser_check(self, params: dict) -> None:
        """Rollback: restore the original checkbox state."""
        selector = params.get("selector", "")
        original_checked = params.get("checked")
        if not selector or original_checked is None:
            return

        try:
            from os_layer.playwright_engine import get_playwright_engine
            engine = get_playwright_engine()
            if engine.ensure_ready():
                locator = engine._page.locator(selector)
                current = locator.is_checked()
                if current != original_checked:
                    if original_checked:
                        locator.check(timeout=5000)
                    else:
                        locator.uncheck(timeout=5000)
                    logger.info(
                        f"[Actuation] ↩️ Restored checkbox {selector} "
                        f"to {original_checked}"
                    )
        except Exception as e:
            logger.error(f"[Actuation] Checkbox rollback failed: {e}")

    # ── Approved Shell Session ─────────────────────────────────────────────

    def approve_shell_session(self) -> None:
        """
        Grant session-scoped auto-approval for SHELL_EXEC HIGH risk actions.

        Expires when:
            1. The autonomous task chain completes (caller revokes).
            2. AGENTIC_SHELL_TIMEOUT_SEC fires and triggers a restart.
            3. APPROVED_SESSION_EXPIRY_SEC idle time elapses.
            4. Manual revocation via revoke_shell_session().
        """
        self._shell_session_approved = True
        self._shell_session_approved_at = time.time()
        logger.info("[Actuation] ✅ Shell session approved")

    def revoke_shell_session(self) -> None:
        """Revoke the approved shell session immediately."""
        was_active = self._shell_session_approved
        self._shell_session_approved = False
        self._shell_session_approved_at = 0.0
        if was_active:
            logger.info("[Actuation] 🛑 Shell session revoked")

    def is_shell_session_approved(self) -> bool:
        """
        Check if the approved shell session is still active.

        Automatically expires after APPROVED_SESSION_EXPIRY_SEC of idle time.
        """
        if not self._shell_session_approved:
            return False
        elapsed = time.time() - self._shell_session_approved_at
        if elapsed > config.APPROVED_SESSION_EXPIRY_SEC:
            logger.info("[Actuation] ⏱️ Shell session expired (idle timeout)")
            self.revoke_shell_session()
            return False
        # Refresh the timestamp on each check (activity-based expiry)
        self._shell_session_approved_at = time.time()
        return True

    # ── Workspace Bounds Check ────────────────────────────────────────────

    @staticmethod
    def _is_within_workspace(cmd: str) -> bool:
        """
        Check if a destructive command targets paths within the workspace.

        Extracts absolute paths from the command and verifies they all
        fall within AGENTIC_SHELL_WORKSPACE_ROOT. Returns True only if
        ALL paths are inside the workspace (or no absolute paths found).
        """
        workspace = Path(config.AGENTIC_SHELL_WORKSPACE_ROOT).expanduser().resolve()
        abs_path_pattern = re.compile(r"[A-Za-z]:\\[^\s\"']+")

        paths_found = abs_path_pattern.findall(cmd)
        if not paths_found:
            return False  # No paths = can't confirm workspace scope

        for p in paths_found:
            target = Path(p).resolve()
            try:
                target.relative_to(workspace)
            except ValueError:
                return False
        return True

    # ── Git State Snapshot ────────────────────────────────────────────────

    @staticmethod
    def _snapshot_git_state(payload: ActionPayload, cmd: str) -> None:
        """
        Capture git state before a destructive operation.

        Rollback strategies by operation type:
            - commit: git reset --hard <SHA>
            - merge: git merge --abort (if in-progress) or reset --hard
            - rebase: git rebase --abort (if in-progress) or reset --hard
            - reset: not rollbackable (already destructive)
        """
        cwd = payload.parameters.get("cwd", ".")

        try:
            # Capture HEAD SHA before the operation
            sha_result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                capture_output=True, text=True, timeout=5, cwd=cwd,
            )
            head_sha = sha_result.stdout.strip() if sha_result.returncode == 0 else ""

            # Detect the operation type
            cmd_lower = cmd.lower()
            if "merge" in cmd_lower:
                op_type = "merge"
            elif "rebase" in cmd_lower:
                op_type = "rebase"
            elif "commit" in cmd_lower:
                op_type = "commit"
            elif "reset" in cmd_lower:
                # git reset itself is destructive — mark unrecoverable
                payload.undo_supported = False
                return
            else:
                op_type = "unknown"

            # Stash uncommitted changes with a unique message
            stash_msg = f"JARVIS pre-action backup {payload.action_id[:8]}"
            stash_result = subprocess.run(
                ["git", "stash", "push", "-m", stash_msg],
                capture_output=True, text=True, timeout=10, cwd=cwd,
            )

            # Parse the stash ref from git stash list
            stash_ref = None
            if "No local changes" not in stash_result.stdout:
                list_result = subprocess.run(
                    ["git", "stash", "list"],
                    capture_output=True, text=True, timeout=5, cwd=cwd,
                )
                for line in list_result.stdout.splitlines():
                    if stash_msg in line:
                        # Extract stash@{N} from "stash@{N}: On branch: message"
                        stash_ref = line.split(":")[0].strip()
                        break

            payload.undo_payload = {
                "rollback_type": "git_restore",
                "rollback_parameters": {
                    "head_sha": head_sha,
                    "stash_ref": stash_ref,
                    "op_type": op_type,
                    "cwd": cwd,
                },
            }
            logger.info(
                f"[Actuation] Git snapshot: SHA={head_sha[:8]}, "
                f"stash={stash_ref}, op={op_type}"
            )

        except Exception as e:
            logger.warning(f"[Actuation] Git state snapshot failed: {e}")

    # ── Pip State Snapshot ────────────────────────────────────────────────

    def _snapshot_pip_state(self, payload: ActionPayload) -> None:
        """
        Capture pip freeze output BEFORE a pip install command.

        The diff is completed in _capture_pip_diff() after execution.
        """
        try:
            result = subprocess.run(
                ["pip", "freeze"],
                capture_output=True, text=True, timeout=15,
            )
            if result.returncode == 0:
                before_set = set(result.stdout.strip().splitlines())
                # Store temporarily in parameters for post-execution diff
                payload.parameters["_pip_before"] = list(before_set)
                logger.debug(
                    f"[Actuation] Pip snapshot: {len(before_set)} packages"
                )
        except Exception as e:
            logger.warning(f"[Actuation] Pip freeze snapshot failed: {e}")

    @staticmethod
    def _capture_pip_diff(payload: ActionPayload) -> None:
        """
        Capture the diff between before/after pip freeze.

        Stores the list of newly installed packages in undo_payload.
        Rollback = pip uninstall -y <newly_installed>.
        """
        before_raw = payload.parameters.get("_pip_before", [])
        if not before_raw:
            return

        try:
            result = subprocess.run(
                ["pip", "freeze"],
                capture_output=True, text=True, timeout=15,
            )
            if result.returncode != 0:
                return

            before_set = set(before_raw)
            after_set = set(result.stdout.strip().splitlines())
            newly_installed = after_set - before_set

            if newly_installed:
                # Extract package names (strip ==version)
                pkg_names = [
                    pkg.split("==")[0] for pkg in newly_installed
                ]
                payload.undo_payload = {
                    "rollback_type": "pip_uninstall",
                    "rollback_parameters": {
                        "packages": pkg_names,
                    },
                }
                logger.info(
                    f"[Actuation] Pip diff: +{len(pkg_names)} packages "
                    f"({', '.join(pkg_names[:5])})"
                )
        except Exception as e:
            logger.warning(f"[Actuation] Pip diff capture failed: {e}")

    # ── Action History Logging ───────────────────────────────────────────

    def _log_action(self, payload: ActionPayload, result: ActionResult) -> None:
        """Write action execution record to SQLite for audit and retention."""
        try:
            conn = _get_conn()
            conn.execute(
                """INSERT OR REPLACE INTO action_history
                   (id, timestamp, action_type, target, parameters,
                    risk_level, undo_supported, outcome, error_message)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    payload.action_id,
                    payload.timestamp,
                    payload.action_type,
                    payload.target,
                    json.dumps(payload.parameters),
                    payload.risk_level,
                    1 if payload.undo_supported else 0,
                    result.outcome,
                    result.error,
                ),
            )
            conn.commit()
        except Exception as e:
            logger.warning(f"[Actuation] Action log failed: {e}")


# ── Singleton ────────────────────────────────────────────────────────────────

_instance: Optional[ActuationManager] = None
_instance_lock = threading.Lock()


def get_actuation_manager() -> ActuationManager:
    """Thread-safe singleton accessor."""
    global _instance
    if _instance is None:
        with _instance_lock:
            if _instance is None:
                _instance = ActuationManager()
    return _instance
