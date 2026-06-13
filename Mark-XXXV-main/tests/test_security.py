import os
import tempfile
import pytest
from pathlib import Path
import json

from core.security import SessionSecurityManager
from core.config import config

@pytest.fixture
def security_manager():
    # Use a temporary directory for testing
    with tempfile.TemporaryDirectory() as tmpdir:
        sm = SessionSecurityManager()
        # Override token_path for testing using config since SessionSecurityManager reads config.SESSION_TOKEN_PATH
        old_path = config.SESSION_TOKEN_PATH
        config.SESSION_TOKEN_PATH = str(Path(tmpdir) / ".jarvis_os_token")
        yield sm
        config.SESSION_TOKEN_PATH = old_path

def test_token_generation_and_validation(security_manager):
    # Initial state
    assert not security_manager.is_initialized

    # Generate token
    token = security_manager.initialize()
    assert token is not None
    assert len(token) > 0
    assert security_manager.is_initialized

    # Validate valid token
    assert security_manager.validate(token)

    # Validate invalid token
    assert not security_manager.validate("invalid-token")

def test_token_persistence(security_manager):
    token = security_manager.initialize()
    
    # Read the token directly from the file
    token_path = Path(config.SESSION_TOKEN_PATH)
    assert token_path.exists()
    payload = json.loads(token_path.read_text(encoding="utf-8"))
    assert payload["token"] == token

def test_revoke_token(security_manager):
    token = security_manager.initialize()
    assert security_manager.is_initialized
    
    security_manager.cleanup()
    assert not security_manager.is_initialized
    
    token_path = Path(config.SESSION_TOKEN_PATH)
    assert not token_path.exists()
    assert not security_manager.validate(token)
