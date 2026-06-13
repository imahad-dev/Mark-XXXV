"""
core/auth/keystore.py
======================
Secure keystore using Windows DPAPI for master key protection.

Architecture:
    Face is the AUTHENTICATOR, not the key.
    ┌─────────────────┐
    │ golden_signature │  ← face embedding (fuzzy, similarity-based)
    └────────┬────────┘
             │ match > threshold?
             ▼
    ┌─────────────────┐
    │   master.key     │  ← DPAPI-protected (USER-scoped, machine-bound)
    └────────┬────────┘
             │ CryptUnprotectData
             ▼
    ┌─────────────────┐
    │  AES-256 key     │  ← Fernet key (in RAM only, never on disk)
    └────────┬────────┘
             │ Fernet.decrypt
             ▼
    ┌─────────────────┐
    │  .env contents   │  ← decrypted into os.environ, never to disk
    └─────────────────┘

Security model:
    - DPAPI USER-scoped: only the current Windows user can decrypt.
    - No CRYPTPROTECT_LOCAL_MACHINE flag: other accounts on same machine = denied.
    - master.key is useless on another machine (DPAPI binds to hardware + user).
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger("jarvis.auth.keystore")

AUTH_DIR = Path(__file__).resolve().parent
MASTER_KEY_PATH = AUTH_DIR / "master.key"
ENCRYPTED_ENV_PATH = AUTH_DIR.parent.parent / ".env.encrypted"


def _import_dpapi():
    """Lazy import win32crypt — only available on Windows."""
    try:
        import win32crypt
        return win32crypt
    except ImportError:
        logger.error("pywin32 not installed. Run: pip install pywin32")
        return None


def protect_key(raw_key: bytes, description: str = "JARVIS Master Key") -> bytes:
    """
    Encrypt a key using Windows DPAPI (USER-scoped).

    Only the current Windows user on this specific machine can decrypt it.
    No CRYPTPROTECT_LOCAL_MACHINE flag — intentionally USER-scoped.
    """
    win32crypt = _import_dpapi()
    if win32crypt is None:
        raise RuntimeError("DPAPI not available — Windows + pywin32 required.")

    # USER-scoped: flags=0 (no CRYPTPROTECT_LOCAL_MACHINE)
    protected = win32crypt.CryptProtectData(
        raw_key,
        description,
        None,       # optional entropy (additional secret)
        None,       # reserved
        None,       # prompt struct
        0,          # flags: 0 = USER-scoped only
    )
    return protected


def unprotect_key(protected_data: bytes) -> bytes:
    """
    Decrypt a DPAPI-protected key.

    Will raise on wrong user/machine — by design.
    """
    win32crypt = _import_dpapi()
    if win32crypt is None:
        raise RuntimeError("DPAPI not available — Windows + pywin32 required.")

    try:
        _desc, decrypted = win32crypt.CryptUnprotectData(
            protected_data,
            None,       # optional entropy
            None,       # reserved
            None,       # prompt struct
            0,          # flags
        )
        return decrypted
    except Exception as exc:
        logger.error("DPAPI decryption failed: %s", exc)
        raise PermissionError(
            "Cannot decrypt master key. Wrong user account or different machine."
        ) from exc


def save_protected_key(raw_key: bytes) -> Path:
    """Encrypt a Fernet key with DPAPI and save to master.key."""
    protected = protect_key(raw_key)
    MASTER_KEY_PATH.write_bytes(protected)
    logger.info("Master key saved (DPAPI USER-scoped): %s", MASTER_KEY_PATH)
    return MASTER_KEY_PATH


def load_fernet_key() -> bytes:
    """
    Load and decrypt the Fernet key from the DPAPI-protected master.key.

    Returns:
        Raw Fernet key bytes (44 bytes, url-safe base64).

    Raises:
        FileNotFoundError: master.key doesn't exist.
        PermissionError:   Wrong user/machine (DPAPI rejection).
    """
    if not MASTER_KEY_PATH.exists():
        raise FileNotFoundError(f"Master key not found: {MASTER_KEY_PATH}")

    protected = MASTER_KEY_PATH.read_bytes()
    return unprotect_key(protected)


def encrypt_env_file(fernet_key: bytes, env_path: Path) -> Path:
    """
    Encrypt a .env file using Fernet (AES-256-CBC).

    Args:
        fernet_key: Raw Fernet key (from Fernet.generate_key()).
        env_path:   Path to the plaintext .env file.

    Returns:
        Path to the encrypted .env.encrypted file.
    """
    from cryptography.fernet import Fernet

    if not env_path.exists():
        raise FileNotFoundError(f".env file not found: {env_path}")

    plaintext = env_path.read_bytes()
    cipher = Fernet(fernet_key)
    encrypted = cipher.encrypt(plaintext)

    ENCRYPTED_ENV_PATH.write_bytes(encrypted)
    logger.info("Encrypted .env saved: %s (%d bytes)", ENCRYPTED_ENV_PATH, len(encrypted))
    return ENCRYPTED_ENV_PATH


def decrypt_env_to_memory(fernet_key: bytes) -> str:
    """
    Decrypt .env.encrypted contents into a string (RAM only).

    The caller is responsible for parsing and injecting into os.environ.
    NEVER writes decrypted content to disk.

    Returns:
        Decrypted .env content as a string.
    """
    from cryptography.fernet import Fernet

    if not ENCRYPTED_ENV_PATH.exists():
        raise FileNotFoundError(f"Encrypted env not found: {ENCRYPTED_ENV_PATH}")

    encrypted = ENCRYPTED_ENV_PATH.read_bytes()
    cipher = Fernet(fernet_key)

    try:
        plaintext = cipher.decrypt(encrypted)
    except Exception as exc:
        raise PermissionError("Failed to decrypt .env — key mismatch or corrupted file.") from exc

    return plaintext.decode("utf-8")


def inject_env_from_decrypted(content: str) -> int:
    """
    Parse decrypted .env content and inject into os.environ.

    Returns:
        Number of variables injected.
    """
    count = 0
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue

        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()

        # Strip surrounding quotes if present
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]

        os.environ[key] = value
        count += 1

    logger.info("Injected %d environment variables into os.environ", count)
    return count
