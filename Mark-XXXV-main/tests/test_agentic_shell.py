"""
tests/test_agentic_shell.py — AgenticShell Unit Tests
=======================================================
Covers: persistent state, CWD preservation, readiness probes,
timeout recovery, per-stream buffer caps, injection sanitization,
workspace sandboxing, bypass validation, and command classification.
"""

import os
import threading
import time
from pathlib import Path
from unittest.mock import patch, MagicMock, PropertyMock

import pytest

from os_layer.agentic_shell import (
    AgenticShell,
    ShellResult,
    _INJECTION_PATTERNS,
    _FS_COMMANDS,
    _ABS_PATH_PATTERN,
    _READY_PROBE,
    _EXIT_CODE_PREFIX,
)


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def shell_config():
    """Patch config values for testing without touching real config."""
    with patch("os_layer.agentic_shell.config") as mock_config:
        mock_config.AGENTIC_SHELL_TIMEOUT_SEC = 5.0
        mock_config.AGENTIC_SHELL_MAX_OUTPUT_BYTES = 1024
        mock_config.AGENTIC_SHELL_WORKSPACE_ROOT = os.path.join(
            os.path.expanduser("~"), "JarvisWorkspace"
        )
        yield mock_config


# ── Injection Sanitization ───────────────────────────────────────────────────


class TestInjectionPrevention:
    """Verify the sanitizer catches shell metacharacters and dangerous cmdlets."""

    def test_semicolon_blocked(self):
        assert AgenticShell._check_injection("echo hello; rm -rf /") is not None

    def test_backtick_blocked(self):
        assert AgenticShell._check_injection("echo `whoami`") is not None

    def test_pipe_blocked(self):
        assert AgenticShell._check_injection("cat file | evil") is not None

    def test_ampersand_blocked(self):
        assert AgenticShell._check_injection("cmd1 & cmd2") is not None

    def test_dollar_sign_blocked(self):
        assert AgenticShell._check_injection("echo $env:PATH") is not None

    def test_invoke_expression_blocked(self):
        assert AgenticShell._check_injection("Invoke-Expression 'bad'") is not None

    def test_iex_blocked(self):
        assert AgenticShell._check_injection("iex download-string") is not None

    def test_clean_command_passes(self):
        assert AgenticShell._check_injection("Get-ChildItem") is None

    def test_clean_git_passes(self):
        assert AgenticShell._check_injection("git status") is None

    def test_clean_pip_passes(self):
        assert AgenticShell._check_injection("pip install requests") is None


# ── Path Bounds Checking ─────────────────────────────────────────────────────


class TestPathBounds:
    """Verify workspace boundary enforcement on file-system commands."""

    def test_inside_workspace_no_violation(self, shell_config):
        workspace = Path(shell_config.AGENTIC_SHELL_WORKSPACE_ROOT).expanduser()
        cmd = f"Remove-Item {workspace}\\temp\\file.txt"
        violations = AgenticShell._check_path_bounds(cmd)
        assert len(violations) == 0

    def test_outside_workspace_violation(self, shell_config):
        cmd = "Remove-Item C:\\Windows\\System32\\evil.txt"
        violations = AgenticShell._check_path_bounds(cmd)
        assert len(violations) > 0
        assert any("Windows" in v for v in violations)

    def test_non_fs_command_no_check(self, shell_config):
        cmd = "echo C:\\Windows\\System32\\something"
        violations = AgenticShell._check_path_bounds(cmd)
        assert len(violations) == 0

    def test_mixed_paths(self, shell_config):
        workspace = Path(shell_config.AGENTIC_SHELL_WORKSPACE_ROOT).expanduser()
        cmd = f"Copy-Item {workspace}\\a.txt C:\\Windows\\b.txt"
        violations = AgenticShell._check_path_bounds(cmd)
        assert len(violations) == 1


# ── Command Classification ───────────────────────────────────────────────────


class TestCommandClassification:
    """Verify semantic command categories for the predictive engine."""

    def test_git_is_version_control(self):
        assert AgenticShell.classify_command("git commit -m 'test'") == "VERSION_CONTROL"

    def test_pip_is_package_management(self):
        assert AgenticShell.classify_command("pip install flask") == "PACKAGE_MANAGEMENT"

    def test_npm_is_package_management(self):
        assert AgenticShell.classify_command("npm install express") == "PACKAGE_MANAGEMENT"

    def test_remove_item_is_file_operation(self):
        assert AgenticShell.classify_command("remove-item file.txt") == "FILE_OPERATION"

    def test_python_is_build_tool(self):
        assert AgenticShell.classify_command("python -m pytest") == "BUILD_TOOL"

    def test_curl_is_network(self):
        assert AgenticShell.classify_command("curl https://example.com") == "NETWORK"

    def test_unknown_is_other(self):
        assert AgenticShell.classify_command("some-custom-tool") == "OTHER"

    def test_empty_is_other(self):
        assert AgenticShell.classify_command("") == "OTHER"


# ── Bypass Script Validation ─────────────────────────────────────────────────


class TestBypassValidation:
    """Verify SHA-256 whitelist enforcement for ExecutionPolicy escalation."""

    def test_script_outside_install_dir_rejected(self, tmp_path):
        script = tmp_path / "evil.ps1"
        script.write_text("Write-Host 'pwned'")

        with patch.dict(
            "core.security.BYPASS_SCRIPT_WHITELIST", {"evil.ps1": "fakehash"}
        ):
            assert AgenticShell.validate_bypass_script(str(script)) is False

    def test_script_with_wrong_hash_rejected(self, tmp_path):
        # Create a script inside the install dir
        install_dir = Path(__file__).resolve().parent.parent
        script = install_dir / "test_bypass_script.ps1"
        try:
            script.write_text("Write-Host 'legit'")
            with patch.dict(
                "core.security.BYPASS_SCRIPT_WHITELIST",
                {"test_bypass_script.ps1": "wronghash"},
            ):
                assert AgenticShell.validate_bypass_script(str(script)) is False
        finally:
            script.unlink(missing_ok=True)


# ── ShellResult ──────────────────────────────────────────────────────────────


class TestShellResult:
    """Verify the result dataclass serialization."""

    def test_to_dict(self):
        result = ShellResult(stdout="ok", stderr="", exit_code=0, timed_out=False)
        d = result.to_dict()
        assert d["stdout"] == "ok"
        assert d["exit_code"] == 0
        assert d["timed_out"] is False

    def test_default_values(self):
        result = ShellResult()
        assert result.stdout == ""
        assert result.stderr == ""
        assert result.exit_code == -1
        assert result.timed_out is False


# ── Readiness Probe ──────────────────────────────────────────────────────────


class TestReadinessProbe:
    """Verify the _ready event prevents commands during restart."""

    def test_execute_waits_for_ready(self, shell_config):
        """
        Send a command while _ready is unset, verify it waits
        and completes when _ready is set by another thread.
        """
        shell = AgenticShell()
        shell._running = True
        shell._ready.clear()

        # Set up a mock process
        mock_process = MagicMock()
        mock_process.poll.return_value = None
        mock_process.stdin = MagicMock()
        mock_process.stdout = iter([])
        mock_process.stderr = iter([])
        shell._process = mock_process

        results = []

        def delayed_ready():
            """Simulate process becoming ready after 0.3s."""
            time.sleep(0.3)
            shell._ready.set()
            # Wait for _send_command to clear the event
            time.sleep(0.1)
            # Also set the delimiter to unblock
            shell._delimiter_hit.set()
            shell._captured_exit_code = 0

        # Start the delayed readiness thread
        t = threading.Thread(target=delayed_ready, daemon=True)
        t.start()

        # This should block until _ready is set, then succeed
        result = shell.execute("echo test", timeout=5.0, structured=True)
        t.join(timeout=2.0)

        # Verify it waited and executed (exit_code 0)
        assert result.exit_code == 0
        assert result.timed_out is False

    def test_execute_returns_error_if_not_ready(self, shell_config):
        """If the process never becomes ready, execute returns a timeout error."""
        shell_config.AGENTIC_SHELL_TIMEOUT_SEC = 1.0
        shell = AgenticShell()
        shell._running = True
        shell._ready.clear()

        result = shell.execute("echo test", timeout=0.5, structured=True)
        assert result.timed_out is True
        assert "not ready" in result.stderr.lower()


# ── Exit Code Capture ────────────────────────────────────────────────────────


class TestExitCodeCapture:
    """Verify the JARVIS_EXITCODE: line parsing in the stream reader."""

    def test_exit_code_parsed_from_stdout(self, shell_config):
        shell = AgenticShell()
        shell._running = True
        shell._ready.set()
        shell._current_delimiter = "abc123"

        # Simulate stream lines
        lines = [
            "command output\n",
            f"{_EXIT_CODE_PREFIX}42\n",
            "abc123\n",
        ]

        mock_stream = iter(lines)
        shell._read_stream(mock_stream, "stdout")

        assert shell._captured_exit_code == 42
        assert shell._delimiter_hit.is_set()
        assert "command output" in shell._stdout_lines

    def test_invalid_exit_code_defaults_to_minus_one(self, shell_config):
        shell = AgenticShell()
        shell._running = True
        shell._current_delimiter = "xyz789"

        lines = [
            f"{_EXIT_CODE_PREFIX}not_a_number\n",
            "xyz789\n",
        ]

        mock_stream = iter(lines)
        shell._read_stream(mock_stream, "stdout")

        assert shell._captured_exit_code == -1


# ── Buffer Cap ───────────────────────────────────────────────────────────────


class TestBufferCap:
    """Verify per-stream buffer cap prevents OOM."""

    def test_stdout_truncated_at_cap(self, shell_config):
        shell_config.AGENTIC_SHELL_MAX_OUTPUT_BYTES = 50
        shell = AgenticShell()
        shell._running = True
        shell._current_delimiter = "delim999"

        # Generate lines that exceed 50 bytes
        lines = [f"line-{i:04d} padding data\n" for i in range(20)]
        lines.append("delim999\n")

        mock_stream = iter(lines)
        shell._read_stream(mock_stream, "stdout")

        total_output = "\n".join(shell._stdout_lines)
        assert "TRUNCATED" in total_output

    def test_stderr_truncated_independently(self, shell_config):
        shell_config.AGENTIC_SHELL_MAX_OUTPUT_BYTES = 50
        shell = AgenticShell()
        shell._running = True

        lines = [f"error-{i:04d} padding data\n" for i in range(20)]

        mock_stream = iter(lines)
        shell._read_stream(mock_stream, "stderr")

        total_output = "\n".join(shell._stderr_lines)
        assert "TRUNCATED" in total_output


# ── Timeout Recovery ─────────────────────────────────────────────────────────


class TestTimeoutRecovery:
    """Verify timeout kills the process and spawns a fresh one."""

    def test_restart_clears_state(self, shell_config):
        shell = AgenticShell()
        shell._running = True
        shell._stdout_lines = ["leftover"]
        shell._stderr_lines = ["error"]
        shell._stdout_bytes = 100
        shell._stderr_bytes = 50
        shell._current_delimiter = "old"

        mock_process = MagicMock()
        shell._process = mock_process

        with patch.object(shell, "_spawn_process") as mock_spawn, patch.object(
            shell, "_emit_timeout_recovery"
        ) as mock_emit:
            shell._restart_after_timeout()

            mock_process.kill.assert_called_once()
            mock_spawn.assert_called_once()
            mock_emit.assert_called_once()

            assert shell._stdout_lines == []
            assert shell._stderr_lines == []
            assert shell._stdout_bytes == 0
            assert shell._stderr_bytes == 0
            assert shell._current_delimiter is None
