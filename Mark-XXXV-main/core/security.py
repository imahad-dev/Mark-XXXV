"""
core/security.py — Session Security Manager
=============================================
Generates, stores, and validates a per-session cryptographic token
used to authenticate the Browser Extension WebSocket handshake.

Security Model:
    - Token is a UUIDv4 string, regenerated every JARVIS boot.
    - Written to SESSION_TOKEN_PATH with owner-only file permissions
      enforced via Windows DACLs (win32security).
    - Any WebSocket connection must present a matching token in
      query parameters; mismatches are rejected with 403.

Script Bypass Whitelist:
    BYPASS_SCRIPT_WHITELIST maps script basenames to their expected
    SHA-256 hashes. Only scripts matching both path (inside JARVIS
    install dir) and hash are eligible for ExecutionPolicy Bypass.

Thread Safety:
    All public methods are thread-safe. Token file I/O is
    protected by a threading.Lock.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import uuid
from pathlib import Path
from typing import Optional

from core.config import config

logger = logging.getLogger(__name__)

# ── ExecutionPolicy Bypass Whitelist ─────────────────────────────────────────
# Maps script basenames to their expected SHA-256 hashes.
# Only scripts inside the JARVIS installation directory AND matching
# a hash here are permitted to run with -ExecutionPolicy Bypass.
# Populate this dict when creating internal .ps1 scripts that require
# elevated policy. Example:
#     "setup_env.ps1": "a1b2c3d4e5f6..."
BYPASS_SCRIPT_WHITELIST: dict[str, str] = {}


class SessionSecurityManager:
    """
    Manages the per-session authentication token for local IPC.

    Usage:
        mgr = SessionSecurityManager()
        mgr.initialize()         # Generate token + write file
        ok = mgr.validate("...")  # Check incoming connection
        mgr.cleanup()            # Delete token file on shutdown
    """

    def __init__(self) -> None:
        self._token: Optional[str] = None
        self._lock = threading.Lock()
        self._initialized = False

    # ── Public API ───────────────────────────────────────────────────────

    def initialize(self) -> str:
        """Generate a new session token and write it to disk with owner-only ACLs."""
        with self._lock:
            self._token = uuid.uuid4().hex
            self._write_token_file()
            self._initialized = True
            logger.info("[Security] ✅ Session token generated and secured")
            return self._token

    def validate(self, token: str) -> bool:
        """Check if the provided token matches the current session token."""
        with self._lock:
            if not self._token:
                return False
            return token == self._token

    def get_token(self) -> Optional[str]:
        """Return the current session token (None if not initialized)."""
        with self._lock:
            return self._token

    def cleanup(self) -> None:
        """Delete the token file and clear the in-memory token."""
        with self._lock:
            self._token = None
            self._initialized = False
            token_path = Path(config.SESSION_TOKEN_PATH)
            try:
                if token_path.exists():
                    token_path.unlink()
                    logger.info("[Security] 🧹 Session token file deleted")
            except Exception as e:
                logger.warning(f"[Security] Token file cleanup failed: {e}")

    @property
    def is_initialized(self) -> bool:
        return self._initialized

    # ── Private Methods ──────────────────────────────────────────────────

    def _write_token_file(self) -> None:
        """Write the token to JSON and lock the file to the current user's SID."""
        token_path = Path(config.SESSION_TOKEN_PATH)
        token_path.parent.mkdir(parents=True, exist_ok=True)

        payload = {"token": self._token, "pid": os.getpid()}
        token_path.write_text(json.dumps(payload), encoding="utf-8")

        # Attempt to restrict file permissions to owner-only via Windows DACLs
        self._set_owner_only_acl(str(token_path))

    @staticmethod
    def _set_owner_only_acl(filepath: str) -> None:
        """
        Restrict file access to the current user's SID only.

        Uses win32security to build a DACL that grants GENERIC_ALL
        exclusively to the file owner, denying access to all other
        principals (including Administrators unless running as owner).

        Falls back to os.chmod (read-only flag) on non-Windows or if
        win32security is unavailable.
        """
        try:
            import ntsecuritycon as con
            import win32security

            # Get the current user's SID
            username = os.environ.get("USERNAME", os.getlogin())
            domain = os.environ.get("USERDOMAIN", "")
            user_sid, _, _ = win32security.LookupAccountName(domain, username)

            # Build a DACL granting only the owner full control
            dacl = win32security.ACL()
            dacl.AddAccessAllowedAce(
                win32security.ACL_REVISION,
                con.FILE_GENERIC_READ | con.FILE_GENERIC_WRITE | con.DELETE,
                user_sid,
            )

            # Apply the DACL to the file's security descriptor
            sd = win32security.GetFileSecurity(
                filepath, win32security.DACL_SECURITY_INFORMATION
            )
            sd.SetSecurityDescriptorDacl(True, dacl, False)
            win32security.SetFileSecurity(
                filepath,
                win32security.DACL_SECURITY_INFORMATION,
                sd,
            )
            logger.debug(f"[Security] Owner-only DACL applied to {filepath}")

        except ImportError:
            # win32security not available — best-effort chmod
            logger.warning(
                "[Security] win32security unavailable — "
                "falling back to os.chmod for token file"
            )
            try:
                os.chmod(filepath, 0o600)
            except Exception as e:
                logger.warning(f"[Security] os.chmod fallback failed: {e}")

        except Exception as e:
            logger.warning(f"[Security] DACL application failed: {e}")
            try:
                os.chmod(filepath, 0o600)
            except Exception:
                pass


# ── Singleton ────────────────────────────────────────────────────────────────

_instance: Optional[SessionSecurityManager] = None
_instance_lock = threading.Lock()


def get_session_security() -> SessionSecurityManager:
    """Thread-safe singleton accessor."""
    global _instance
    if _instance is None:
        with _instance_lock:
            if _instance is None:
                _instance = SessionSecurityManager()
    return _instance
