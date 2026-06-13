import pytest
import time
from unittest.mock import patch, MagicMock
from os_layer.actuation import ActuationManager, ActionPayload, ActionResult


@pytest.fixture
def actuation_manager():
    manager = ActuationManager()
    manager.start()
    yield manager
    manager.stop()


def test_enqueue_action(actuation_manager):
    payload = ActionPayload(
        target="browser",
        action_type="BROWSER_NAVIGATE",
        parameters={"url": "https://example.com"},
    )

    with patch.object(
        actuation_manager, "_handle_browser_navigate"
    ) as mock_browser:
        mock_browser.return_value = ActionResult(
            action_id=payload.action_id,
            success=True,
            outcome="success",
            message="Navigated",
        )

        actuation_manager.execute_action(payload)

        # Wait for the worker thread to process
        time.sleep(0.1)

        mock_browser.assert_called_once_with(payload)


# ── Destructive Pattern Tests ────────────────────────────────────────────────


def test_shell_destructive_outside_workspace_blocked(actuation_manager):
    """Destructive command outside workspace is blocked when undo_supported=True."""
    payload = ActionPayload(
        target="shell",
        action_type="SHELL_EXEC",
        parameters={"command": "rd /s C:\\Windows\\Temp"},
        risk_level="HIGH",
    )

    # _is_within_workspace will return False for C:\Windows\Temp
    with patch.object(
        ActuationManager, "_is_within_workspace", return_value=False
    ):
        result = actuation_manager._handle_shell_exec(payload)
        assert not result.success
        assert "Destructive command outside workspace" in result.error


def test_shell_destructive_outside_workspace_allowed_with_flag(actuation_manager):
    """Destructive command outside workspace passes when undo_supported=False."""
    payload = ActionPayload(
        target="shell",
        action_type="SHELL_EXEC",
        parameters={"command": "rd /s C:\\Windows\\Temp"},
        risk_level="HIGH",
        undo_supported=False,
    )

    mock_result = MagicMock()
    mock_result.stdout = "done"
    mock_result.stderr = ""
    mock_result.exit_code = 0
    mock_result.timed_out = False

    with patch.object(
        ActuationManager, "_is_within_workspace", return_value=False
    ), patch(
        "os_layer.agentic_shell.get_agentic_shell"
    ) as mock_shell_getter:
        mock_shell = MagicMock()
        mock_shell.execute.return_value = mock_result
        mock_shell_getter.return_value = mock_shell

        result = actuation_manager._handle_shell_exec(payload)
        assert result.success
        assert result.message == "done"


def test_shell_destructive_inside_workspace_rollbackable(actuation_manager):
    """Destructive command inside workspace is allowed with FS backup rollback."""
    payload = ActionPayload(
        target="shell",
        action_type="SHELL_EXEC",
        parameters={"command": "Remove-Item -Recurse C:\\Users\\test\\JarvisWorkspace\\temp"},
        risk_level="HIGH",
        undo_supported=True,
    )

    mock_result = MagicMock()
    mock_result.stdout = "deleted"
    mock_result.stderr = ""
    mock_result.exit_code = 0
    mock_result.timed_out = False

    with patch.object(
        ActuationManager, "_is_within_workspace", return_value=True
    ), patch(
        "os_layer.agentic_shell.get_agentic_shell"
    ) as mock_shell_getter:
        mock_shell = MagicMock()
        mock_shell.execute.return_value = mock_result
        mock_shell_getter.return_value = mock_shell

        result = actuation_manager._handle_shell_exec(payload)
        assert result.success


# ── Precise Blocklist Tests ──────────────────────────────────────────────────

def test_format_list_not_blocked():
    """Format-List is a display cmdlet, not a destructive disk format."""
    from os_layer.actuation import _DESTRUCTIVE_PATTERNS

    assert _DESTRUCTIVE_PATTERNS.search("Get-Process | Format-List") is None
    assert _DESTRUCTIVE_PATTERNS.search("Format-Table -AutoSize") is None


def test_format_volume_is_blocked():
    """Format-Volume is a destructive disk operation."""
    from os_layer.actuation import _DESTRUCTIVE_PATTERNS

    assert _DESTRUCTIVE_PATTERNS.search("Format-Volume -DriveLetter D") is not None
    assert _DESTRUCTIVE_PATTERNS.search("format C:\\") is not None


def test_clear_disk_is_blocked():
    """Clear-Disk is destructive."""
    from os_layer.actuation import _DESTRUCTIVE_PATTERNS

    assert _DESTRUCTIVE_PATTERNS.search("Clear-Disk -Number 0") is not None
    assert _DESTRUCTIVE_PATTERNS.search("Initialize-Disk -Number 1") is not None


# ── Git Push Marked Irreversible ─────────────────────────────────────────────


def test_git_push_marks_undo_unsupported(actuation_manager):
    """git push sets undo_supported=False because remote is irreversible."""
    payload = ActionPayload(
        target="shell",
        action_type="SHELL_EXEC",
        parameters={"command": "git push origin main"},
        risk_level="HIGH",
        undo_supported=True,
    )

    mock_result = MagicMock()
    mock_result.stdout = "pushed"
    mock_result.stderr = ""
    mock_result.exit_code = 0
    mock_result.timed_out = False

    with patch(
        "os_layer.agentic_shell.get_agentic_shell"
    ) as mock_shell_getter:
        mock_shell = MagicMock()
        mock_shell.execute.return_value = mock_result
        mock_shell_getter.return_value = mock_shell

        actuation_manager._handle_shell_exec(payload)
        assert not payload.undo_supported


# ── Pip Diff-Based Rollback ──────────────────────────────────────────────────


def test_pip_rollback_captures_diff(actuation_manager):
    """pip install triggers before/after freeze diff for rollback."""
    payload = ActionPayload(
        target="shell",
        action_type="SHELL_EXEC",
        parameters={"command": "pip install requests"},
        risk_level="MEDIUM",
    )

    mock_result = MagicMock()
    mock_result.stdout = "Installed requests"
    mock_result.stderr = ""
    mock_result.exit_code = 0
    mock_result.timed_out = False

    freeze_before = MagicMock(returncode=0, stdout="certifi==2024.1.0\n")
    freeze_after = MagicMock(returncode=0, stdout="certifi==2024.1.0\nrequests==2.31.0\n")

    with patch(
        "os_layer.agentic_shell.get_agentic_shell"
    ) as mock_shell_getter, patch(
        "subprocess.run", side_effect=[freeze_before, freeze_after]
    ):
        mock_shell = MagicMock()
        mock_shell.execute.return_value = mock_result
        mock_shell_getter.return_value = mock_shell

        actuation_manager._handle_shell_exec(payload)

        assert payload.undo_payload is not None
        assert payload.undo_payload["rollback_type"] == "pip_uninstall"
        assert "requests" in payload.undo_payload["rollback_parameters"]["packages"]


# ── Approved Shell Session Tests ─────────────────────────────────────────────


def test_approved_session_lifecycle(actuation_manager):
    """Test approve → is_approved → revoke flow."""
    assert not actuation_manager.is_shell_session_approved()

    actuation_manager.approve_shell_session()
    assert actuation_manager.is_shell_session_approved()

    actuation_manager.revoke_shell_session()
    assert not actuation_manager.is_shell_session_approved()


def test_approved_session_idle_expiry(actuation_manager):
    """Session expires after APPROVED_SESSION_EXPIRY_SEC idle time."""
    actuation_manager.approve_shell_session()

    # Simulate time passing beyond expiry
    with patch("os_layer.actuation.config") as mock_config:
        mock_config.APPROVED_SESSION_EXPIRY_SEC = 0.1
        mock_config.AGENTIC_SHELL_WORKSPACE_ROOT = "C:\\fake"
        time.sleep(0.15)
        assert not actuation_manager.is_shell_session_approved()


def test_approved_session_bypasses_hard_gate(actuation_manager):
    """Approved session skips the confirmation callback for SHELL_EXEC."""
    actuation_manager.approve_shell_session()

    payload = ActionPayload(
        target="shell",
        action_type="SHELL_EXEC",
        parameters={"command": "echo hello"},
        risk_level="HIGH",
    )

    mock_result = MagicMock()
    mock_result.stdout = "hello"
    mock_result.stderr = ""
    mock_result.exit_code = 0
    mock_result.timed_out = False

    # Set a callback that would reject — it should NOT be called
    rejection_callback = MagicMock(return_value=False)
    actuation_manager._confirm_callback = rejection_callback

    with patch(
        "os_layer.agentic_shell.get_agentic_shell"
    ) as mock_shell_getter:
        mock_shell = MagicMock()
        mock_shell.execute.return_value = mock_result
        mock_shell_getter.return_value = mock_shell

        result = actuation_manager._execute_with_confirmation(payload)

        rejection_callback.assert_not_called()
        assert result.success


def test_timeout_revokes_approved_session(actuation_manager):
    """Shell timeout should revoke any active approved session."""
    actuation_manager.approve_shell_session()
    assert actuation_manager.is_shell_session_approved()

    payload = ActionPayload(
        target="shell",
        action_type="SHELL_EXEC",
        parameters={"command": "while($true){}"},
        risk_level="HIGH",
    )

    mock_result = MagicMock()
    mock_result.stdout = ""
    mock_result.stderr = "timeout"
    mock_result.exit_code = -1
    mock_result.timed_out = True

    with patch(
        "os_layer.agentic_shell.get_agentic_shell"
    ) as mock_shell_getter:
        mock_shell = MagicMock()
        mock_shell.execute.return_value = mock_result
        mock_shell_getter.return_value = mock_shell

        actuation_manager._handle_shell_exec(payload)
        assert not actuation_manager.is_shell_session_approved()


# ── Workspace Bounds ─────────────────────────────────────────────────────────


def test_workspace_check_inside():
    """Paths inside workspace return True."""
    with patch("os_layer.actuation.config") as mock_config:
        mock_config.AGENTIC_SHELL_WORKSPACE_ROOT = "C:\\Users\\test\\JarvisWorkspace"
        result = ActuationManager._is_within_workspace(
            "Remove-Item -Recurse C:\\Users\\test\\JarvisWorkspace\\temp"
        )
        assert result is True


def test_workspace_check_outside():
    """Paths outside workspace return False."""
    with patch("os_layer.actuation.config") as mock_config:
        mock_config.AGENTIC_SHELL_WORKSPACE_ROOT = "C:\\Users\\test\\JarvisWorkspace"
        result = ActuationManager._is_within_workspace(
            "Remove-Item C:\\Windows\\System32\\something"
        )
        assert result is False


def test_workspace_check_no_path():
    """Commands without absolute paths return False (can't confirm scope)."""
    result = ActuationManager._is_within_workspace("echo hello")
    assert result is False
