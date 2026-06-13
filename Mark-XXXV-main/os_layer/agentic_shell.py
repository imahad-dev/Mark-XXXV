"""
os_layer/agentic_shell.py — Persistent PowerShell Engine
==========================================================
Provides JARVIS with a persistent, sandboxed PowerShell session
for developer-level system control: running scripts, managing git
repos, installing packages, and executing multi-step workflows.

Process Model:
    A single powershell.exe process persists across commands.
    Each command is bracketed by a UUID delimiter and an exit-code
    capture line so the engine can detect completion and extract
    the process return code from a long-running session.

Security Model:
    - Command injection prevention: structured params, metachar rejection.
    - ExecutionPolicy RemoteSigned by default; Bypass only for scripts
      whose SHA-256 hash matches a compile-time whitelist in security.py.
    - Working directory sandbox: paths outside AGENTIC_SHELL_WORKSPACE_ROOT
      trigger a MEDIUM risk alert.

Failure Handling:
    - Per-stream buffer caps (stdout/stderr independently) prevent OOM.
    - Timeout terminates the entire process and spawns a fresh one.
    - A readiness probe (`_ready` Event) prevents commands from racing
      against a still-initializing process after a restart.

Thread Safety:
    All public methods are thread-safe. The execute() method serializes
    command submission via a Lock. Stdout/stderr are consumed by daemon
    threads feeding thread-safe queues.
"""

from __future__ import annotations

import hashlib
import logging
import os
import queue
import re
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Optional

from core.config import config

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────

_READY_PROBE = "JARVIS_SHELL_READY"
_EXIT_CODE_PREFIX = "JARVIS_EXITCODE:"
_STARTUP_TIMEOUT = 10.0

# Shell metacharacters that indicate injection when present in raw strings
_INJECTION_PATTERNS = re.compile(
    r"[;`|&$]"                     # PowerShell pipeline/execution chars
    r"|(?:^|\s)(?:Invoke-Expression|iex|Start-Process)\b"  # Dangerous cmdlets
    r"|(?:\.\\|\.\/)\S+\.ps1"      # Relative script execution
    r"|['\"].*?[;|&].*?['\"]",     # Injection inside quoted strings
    re.IGNORECASE,
)

# File-system-touching commands that need absolute path extraction
_FS_COMMANDS = re.compile(
    r"\b(?:Remove-Item|Copy-Item|Move-Item|Set-Content|Out-File"
    r"|New-Item|Rename-Item|del|rd|rmdir|copy|move|rename)\b",
    re.IGNORECASE,
)

# Absolute path pattern (Windows drive letter)
_ABS_PATH_PATTERN = re.compile(r"[A-Za-z]:\\[^\s\"']+")


class ShellResult:
    """Structured result of a shell command execution."""

    __slots__ = ("stdout", "stderr", "exit_code", "timed_out")

    def __init__(
        self,
        stdout: str = "",
        stderr: str = "",
        exit_code: int = -1,
        timed_out: bool = False,
    ):
        self.stdout = stdout
        self.stderr = stderr
        self.exit_code = exit_code
        self.timed_out = timed_out

    def to_dict(self) -> dict:
        return {
            "stdout": self.stdout,
            "stderr": self.stderr,
            "exit_code": self.exit_code,
            "timed_out": self.timed_out,
        }


class AgenticShell:
    """
    Persistent PowerShell process with sandboxing and injection prevention.

    Usage:
        shell = AgenticShell()
        shell.start()
        result = shell.execute("Get-ChildItem")
        shell.stop()
    """

    def __init__(self) -> None:
        self._process: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()  # serializes command submission
        self._ready = threading.Event()
        self._running = False
        self._pid: Optional[int] = None

        # Per-stream daemon threads and output accumulators
        self._stdout_thread: Optional[threading.Thread] = None
        self._stderr_thread: Optional[threading.Thread] = None
        self._stdout_lines: list[str] = []
        self._stderr_lines: list[str] = []
        self._stdout_bytes: int = 0
        self._stderr_bytes: int = 0

        # Delimiter tracking
        self._current_delimiter: Optional[str] = None
        self._delimiter_hit = threading.Event()
        self._captured_exit_code: int = -1

    # ── Public API ───────────────────────────────────────────────────────

    def start(self) -> None:
        """Spawn the persistent PowerShell process."""
        if self._running:
            logger.warning("[AgenticShell] Already running")
            return
        self._running = True
        self._spawn_process()
        logger.info("[AgenticShell] ✅ Persistent shell started")

    def stop(self) -> None:
        """Terminate the persistent process and clean up threads."""
        self._running = False
        self._ready.clear()
        self._kill_process()
        logger.info("[AgenticShell] 🛑 Shell stopped")

    def execute(
        self,
        command: str,
        timeout: Optional[float] = None,
        structured: bool = False,
    ) -> ShellResult:
        """
        Execute a command in the persistent shell.

        Args:
            command: The command string to execute.
            timeout: Seconds to wait before killing the process.
                     Defaults to config.AGENTIC_SHELL_TIMEOUT_SEC.
            structured: If True, skip injection sanitization (command
                        was assembled from structured params, not raw input).

        Returns:
            ShellResult with stdout, stderr, exit_code, and timed_out.
        """
        if timeout is None:
            timeout = config.AGENTIC_SHELL_TIMEOUT_SEC

        # Wait for process to be ready (handles restart races)
        if not self._ready.wait(timeout=_STARTUP_TIMEOUT):
            logger.error("[AgenticShell] Process not ready — timed out waiting for probe")
            return ShellResult(
                stderr="Shell process not ready (startup timeout)",
                exit_code=-1,
                timed_out=True,
            )

        # ── Injection sanitization ───────────────────────────────────────
        if not structured:
            violation = self._check_injection(command)
            if violation:
                logger.warning(
                    f"[AgenticShell] 🚫 Injection detected: {violation}"
                )
                self._emit_system_error(f"Command injection blocked: {violation}")
                return ShellResult(
                    stderr=f"Command blocked by sanitizer: {violation}",
                    exit_code=-1,
                )

        # ── Workspace sandbox check ──────────────────────────────────────
        path_violations = self._check_path_bounds(command)
        if path_violations:
            logger.warning(
                f"[AgenticShell] ⚠️ Out-of-bounds paths: {path_violations}"
            )
            # Emit as MEDIUM risk alert — does not block, but logs
            self._emit_path_violation(path_violations)

        # ── Serialize and execute ────────────────────────────────────────
        with self._lock:
            return self._send_command(command, timeout)

    def is_ready(self) -> bool:
        """Return True if the shell process is alive and ready."""
        return self._ready.is_set() and self._process is not None

    @property
    def pid(self) -> Optional[int]:
        """Return the PID of the current PowerShell process."""
        return self._pid

    # ── Process Lifecycle ────────────────────────────────────────────────

    def _spawn_process(self) -> None:
        """Start a new powershell.exe and send the readiness probe."""
        self._ready.clear()

        # Ensure workspace root exists
        workspace = Path(config.AGENTIC_SHELL_WORKSPACE_ROOT).expanduser()
        workspace.mkdir(parents=True, exist_ok=True)

        self._process = subprocess.Popen(
            [
                "powershell.exe",
                "-NoProfile",
                "-NoLogo",
                "-ExecutionPolicy", "RemoteSigned",
                "-Command", "-",
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,  # line-buffered
            cwd=str(workspace),
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        self._pid = self._process.pid
        logger.info(f"[AgenticShell] Spawned PID {self._pid}")

        # Start reader daemon threads
        self._stdout_thread = threading.Thread(
            target=self._read_stream,
            args=(self._process.stdout, "stdout"),
            daemon=True,
            name="shell-stdout-reader",
        )
        self._stderr_thread = threading.Thread(
            target=self._read_stream,
            args=(self._process.stderr, "stderr"),
            daemon=True,
            name="shell-stderr-reader",
        )
        self._stdout_thread.start()
        self._stderr_thread.start()

        # Send readiness probe
        self._send_raw(f"echo {_READY_PROBE}\n")

        # Wait for the probe to arrive on stdout
        deadline = time.monotonic() + _STARTUP_TIMEOUT
        while time.monotonic() < deadline:
            if self._ready.is_set():
                return
            time.sleep(0.05)

        logger.warning("[AgenticShell] ⚠️ Readiness probe timed out")

    def _kill_process(self) -> None:
        """Terminate the PowerShell process."""
        if self._process:
            try:
                self._process.kill()
                self._process.wait(timeout=5.0)
            except Exception:
                pass
            self._process = None
            self._pid = None

    def _restart_after_timeout(self) -> None:
        """
        Full process recovery after a timeout.

        Kills the hung process, resets all internal state, spawns a
        fresh process, and emits a recovery event on the EventBus.
        """
        logger.warning("[AgenticShell] ⏱️ Timeout recovery — restarting process")
        self._kill_process()

        # Reset accumulators
        self._stdout_lines.clear()
        self._stderr_lines.clear()
        self._stdout_bytes = 0
        self._stderr_bytes = 0
        self._current_delimiter = None
        self._delimiter_hit.clear()
        self._captured_exit_code = -1

        if self._running:
            self._spawn_process()
            self._emit_timeout_recovery()

    # ── Stream Readers ───────────────────────────────────────────────────

    def _read_stream(self, stream, stream_name: str) -> None:
        """
        Daemon thread: continuously read lines from stdout or stderr.

        Handles:
            - Readiness probe detection (stdout only)
            - Exit code extraction (stdout only)
            - Delimiter detection (stdout only)
            - Per-stream buffer cap enforcement
        """
        max_bytes = config.AGENTIC_SHELL_MAX_OUTPUT_BYTES
        is_stdout = stream_name == "stdout"
        lines_list = self._stdout_lines if is_stdout else self._stderr_lines
        truncated = False

        try:
            for raw_line in stream:
                line = raw_line.rstrip("\r\n")

                # ── Readiness probe ──────────────────────────────────────
                if is_stdout and line.strip() == _READY_PROBE:
                    self._ready.set()
                    continue

                # ── Exit code capture ────────────────────────────────────
                if is_stdout and line.startswith(_EXIT_CODE_PREFIX):
                    code_str = line[len(_EXIT_CODE_PREFIX):].strip()
                    try:
                        self._captured_exit_code = int(code_str)
                    except (ValueError, TypeError):
                        self._captured_exit_code = -1
                    continue

                # ── Delimiter detection (always after processing) ────────
                if is_stdout and self._current_delimiter and line.strip() == self._current_delimiter:
                    self._delimiter_hit.set()
                    continue

                # ── Buffer cap enforcement ───────────────────────────────
                current_bytes = (
                    self._stdout_bytes if is_stdout else self._stderr_bytes
                )
                line_bytes = len(line.encode("utf-8", errors="replace"))

                if current_bytes + line_bytes > max_bytes:
                    if not truncated:
                        truncated = True
                        lines_list.append(
                            f"\n[TRUNCATED — {stream_name} exceeded "
                            f"{max_bytes // (1024 * 1024)}MB limit]"
                        )
                        if is_stdout:
                            self._delimiter_hit.set()
                    continue

                lines_list.append(line)
                if is_stdout:
                    self._stdout_bytes += line_bytes
                else:
                    self._stderr_bytes += line_bytes

        except Exception as e:
            if self._running:
                logger.error(f"[AgenticShell] {stream_name} reader died: {e}")

    # ── Command Execution ────────────────────────────────────────────────

    def _send_command(self, command: str, timeout: float) -> ShellResult:
        """
        Send a command, wait for the delimiter, and return the result.

        The command is wrapped with an exit-code capture and a UUID delimiter:
            [user command]
            echo "JARVIS_EXITCODE:$LASTEXITCODE"
            echo "[UUID]"
        """
        if not self._process or self._process.poll() is not None:
            return ShellResult(stderr="Shell process not running", exit_code=-1)

        delimiter = uuid.uuid4().hex
        self._current_delimiter = delimiter
        self._delimiter_hit.clear()
        self._captured_exit_code = -1

        # Clear accumulators for this command
        self._stdout_lines.clear()
        self._stderr_lines.clear()
        self._stdout_bytes = 0
        self._stderr_bytes = 0

        # Build the wrapped command
        wrapped = (
            f"{command}\n"
            f'echo "{_EXIT_CODE_PREFIX}$LASTEXITCODE"\n'
            f'echo "{delimiter}"\n'
        )
        self._send_raw(wrapped)

        # Wait for delimiter or timeout
        if not self._delimiter_hit.wait(timeout=timeout):
            # Timeout — recover
            self._restart_after_timeout()
            return ShellResult(
                stdout="\n".join(self._stdout_lines),
                stderr="\n".join(self._stderr_lines),
                exit_code=-1,
                timed_out=True,
            )

        # Small sleep to let the stderr reader flush residual lines
        time.sleep(0.05)

        return ShellResult(
            stdout="\n".join(self._stdout_lines),
            stderr="\n".join(self._stderr_lines),
            exit_code=self._captured_exit_code,
            timed_out=False,
        )

    def _send_raw(self, text: str) -> None:
        """Write raw text to the process stdin."""
        try:
            if self._process and self._process.stdin:
                self._process.stdin.write(text)
                self._process.stdin.flush()
        except Exception as e:
            logger.error(f"[AgenticShell] stdin write failed: {e}")

    # ── Sanitization & Sandbox ───────────────────────────────────────────

    @staticmethod
    def _check_injection(command: str) -> Optional[str]:
        """
        Reject commands containing shell injection metacharacters.

        Returns the matched pattern string on violation, or None if clean.
        """
        match = _INJECTION_PATTERNS.search(command)
        if match:
            return match.group(0)
        return None

    @staticmethod
    def _check_path_bounds(command: str) -> list[str]:
        """
        Extract absolute paths from file-system-touching commands
        and verify they fall within AGENTIC_SHELL_WORKSPACE_ROOT.

        Returns a list of out-of-bounds paths (empty if all clean).
        """
        if not _FS_COMMANDS.search(command):
            return []

        workspace = Path(config.AGENTIC_SHELL_WORKSPACE_ROOT).expanduser().resolve()
        violations = []

        for match in _ABS_PATH_PATTERN.finditer(command):
            target = Path(match.group(0)).resolve()
            try:
                target.relative_to(workspace)
            except ValueError:
                violations.append(str(target))

        return violations

    @staticmethod
    def validate_bypass_script(script_path: str) -> bool:
        """
        Validate that a script is eligible for ExecutionPolicy Bypass.

        Requirements:
            1. Script is inside JARVIS's installation directory.
            2. Script's SHA-256 hash matches the compile-time whitelist
               in core.security.

        Returns True if bypass is permitted, False otherwise.
        """
        from core.security import BYPASS_SCRIPT_WHITELIST

        script = Path(script_path).resolve()
        install_dir = Path(__file__).resolve().parent.parent

        # Requirement 1: must be inside installation directory
        try:
            script.relative_to(install_dir)
        except ValueError:
            logger.warning(
                f"[AgenticShell] 🚫 Bypass refused: {script} outside install dir"
            )
            return False

        # Requirement 2: SHA-256 must match whitelist
        try:
            sha = hashlib.sha256(script.read_bytes()).hexdigest()
        except Exception as e:
            logger.error(f"[AgenticShell] Cannot hash {script}: {e}")
            return False

        if sha not in BYPASS_SCRIPT_WHITELIST.values():
            logger.warning(
                f"[AgenticShell] 🚫 Bypass refused: hash {sha[:16]}... "
                f"not in whitelist"
            )
            return False

        logger.info(f"[AgenticShell] ✅ Bypass approved for {script.name}")
        return True

    # ── EventBus Emission ────────────────────────────────────────────────

    @staticmethod
    def _emit_system_error(message: str) -> None:
        """Emit a SYSTEM_ERROR event for injection detection."""
        try:
            from os_layer.event_bus import get_event_bus, EventType
            get_event_bus().emit(
                EventType.SYSTEM_ERROR,
                source="agentic_shell",
                payload={"error": message, "severity": "CRITICAL"},
            )
        except Exception:
            pass

    @staticmethod
    def _emit_timeout_recovery() -> None:
        """Emit a shell.timeout_recovery event after process restart."""
        try:
            from os_layer.event_bus import get_event_bus, EventType
            get_event_bus().emit(
                EventType.SHELL_TIMEOUT_RECOVERY,
                source="agentic_shell",
                payload={"recovered": True},
            )
        except Exception:
            pass

    @staticmethod
    def _emit_path_violation(paths: list[str]) -> None:
        """Emit a warning event for out-of-bounds path access."""
        try:
            from os_layer.event_bus import get_event_bus, EventType
            get_event_bus().emit(
                EventType.SYSTEM_ERROR,
                source="agentic_shell",
                payload={
                    "error": "Path sandbox violation",
                    "paths": paths,
                    "severity": "MEDIUM",
                },
            )
        except Exception:
            pass

    @staticmethod
    def emit_command_executed(command_category: str) -> None:
        """
        Emit shell.command_executed for the predictive engine.

        Args:
            command_category: Normalized category string, e.g.
                VERSION_CONTROL, PACKAGE_MANAGEMENT, FILE_OPERATION,
                BUILD_TOOL, NETWORK, OTHER.
        """
        try:
            from os_layer.event_bus import get_event_bus, EventType
            get_event_bus().emit(
                EventType.SHELL_COMMAND_EXECUTED,
                source="agentic_shell",
                payload={"category": command_category},
            )
        except Exception:
            pass

    @staticmethod
    def classify_command(command: str) -> str:
        """
        Classify a shell command into a semantic category for the
        predictive engine's Markov model.

        Returns one of: VERSION_CONTROL, PACKAGE_MANAGEMENT,
        FILE_OPERATION, BUILD_TOOL, NETWORK, OTHER.
        """
        first_token = command.strip().split()[0].lower() if command.strip() else ""

        if first_token in ("git", "svn", "hg"):
            return "VERSION_CONTROL"
        if first_token in ("pip", "pip3", "npm", "yarn", "pnpm", "conda", "poetry"):
            return "PACKAGE_MANAGEMENT"
        if first_token in (
            "copy", "move", "del", "rd", "mkdir", "rmdir", "rename", "ren",
            "remove-item", "copy-item", "move-item", "new-item", "rename-item",
            "set-content", "get-content", "out-file",
        ):
            return "FILE_OPERATION"
        if first_token in (
            "make", "cmake", "msbuild", "dotnet", "cargo", "go", "javac",
            "gradle", "mvn", "pytest", "python", "node",
        ):
            return "BUILD_TOOL"
        if first_token in ("curl", "wget", "invoke-webrequest", "ssh", "scp"):
            return "NETWORK"
        return "OTHER"


# ── Singleton ────────────────────────────────────────────────────────────────

_instance: Optional[AgenticShell] = None
_instance_lock = threading.Lock()


def get_agentic_shell() -> AgenticShell:
    """Thread-safe singleton accessor."""
    global _instance
    if _instance is None:
        with _instance_lock:
            if _instance is None:
                _instance = AgenticShell()
    return _instance
