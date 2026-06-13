import os
import sys
import logging
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)


def _get_env_int(key: str, default: int) -> int:
    """Parse an integer from env, falling back to *default* on ValueError."""
    raw = os.environ.get(key)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        logger.warning("[CONFIG] Invalid integer for %s=%r, falling back to %d", key, raw, default)
        return default


def _get_env_float(key: str, default: float) -> float:
    """Parse a float from env, falling back to *default* on ValueError."""
    raw = os.environ.get(key)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        logger.warning("[CONFIG] Invalid float for %s=%r, falling back to %.1f", key, raw, default)
        return default

REQUIRED_KEYS = [
    "GEMINI_API_KEY",
]

OPTIONAL_KEYS = {
    "GROQ_API_KEY":             "Groq (free code agent) will be unavailable",
    "OPENWEATHER_API_KEY":      "Weather will use fallback wttr.in",
    "CLAP_RMS_THRESHOLD":       "Will default to 4000",
    "TELEGRAM_BOT_TOKEN":       "Telegram remote control will be disabled",
    "TELEGRAM_ALLOWED_USER_ID": "Telegram bot will reject all messages",
    "ELEVENLABS_API_KEY":       "ElevenLabs TTS (Ultron voice) will be unavailable",
    "GEMINI_API_KEYS":          "Multi-key rotation will be disabled (single key only)",
    "WOLFRAM_APP_ID":           "Wolfram Alpha math/science will be unavailable",
    "OLLAMA_BASE_URL":          "Local LLM classifier will use default localhost:11434",
}

def validate_env() -> list[str]:
    missing = []
    for key in REQUIRED_KEYS:
        if not os.environ.get(key):
            missing.append(key)
    return missing

def warn_optional():
    for key, msg in OPTIONAL_KEYS.items():
        if not os.environ.get(key):
            print(f"[CONFIG] Optional key {key} not set — {msg}")

class Config:
    # AI Models — primary key
    GEMINI_API_KEY        = os.environ.get("GEMINI_API_KEY", "")
    GROQ_API_KEY          = os.environ.get("GROQ_API_KEY", "")
    ELEVENLABS_API_KEY    = os.environ.get("ELEVENLABS_API_KEY", "")

    # Intelligence Router — specialist API keys
    WOLFRAM_APP_ID        = os.environ.get("WOLFRAM_APP_ID", "")
    OLLAMA_BASE_URL       = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")

    # Multi-key rotation: comma-separated list of Gemini keys
    # Falls back to the single GEMINI_API_KEY if not set
    _raw_keys             = os.environ.get("GEMINI_API_KEYS", "")
    GEMINI_API_KEYS: list[str] = [
        k.strip() for k in _raw_keys.split(",") if k.strip()
    ] if _raw_keys.strip() else []

    # ElevenLabs voice configuration (Ultron persona)
    ELEVENLABS_VOICE_ID   = os.environ.get("ELEVENLABS_VOICE_ID", "onwK4e9ZLuTAKqWW03F9")

    # Audio tuning
    CLAP_RMS_THRESHOLD    = _get_env_int("CLAP_RMS_THRESHOLD", 4000)
    AUDIO_DEVICE_INDEX    = _get_env_int("AUDIO_DEVICE_INDEX", 0)

    # Model names — single source of truth
    MODEL_AUDIO           = "gemini-2.5-flash-native-audio-latest"
    MODEL_COMPLEX         = "gemini-2.5-pro"
    MODEL_LOGIC           = "gemini-2.5-flash"
    MODEL_ROUTING         = "gemini-2.5-flash-lite"

    # Feature flags
    FALLBACK_ENABLED      = os.environ.get("FALLBACK_ENABLED", "true").lower() == "true"
    DEBUG_MODE            = os.environ.get("DEBUG_MODE", "false").lower() == "true"

    # OS-Level System (Phase 1+)
    SCREEN_INTELLIGENCE_ENABLED = os.environ.get("SCREEN_INTELLIGENCE_ENABLED", "false").lower() == "true"
    SCREEN_CAPTURE_INTERVAL     = _get_env_int("SCREEN_CAPTURE_INTERVAL", 30)
    WORKSPACE_RESTORE_ENABLED   = os.environ.get("WORKSPACE_RESTORE_ENABLED", "true").lower() == "true"
    MAX_CONCURRENT_AGENTS       = _get_env_int("MAX_CONCURRENT_AGENTS", 2)
    AMBIENT_MODE_ENABLED        = os.environ.get("AMBIENT_MODE_ENABLED", "false").lower() == "true"

    # Phase 5 — Ambient Intelligence & Predictive Execution
    PREDICTIVE_ENABLED              = os.environ.get("PREDICTIVE_ENABLED", "false").lower() == "true"
    AMBIENT_IDLE_THRESHOLD_SEC      = _get_env_int("AMBIENT_IDLE_THRESHOLD_SEC", 300)
    AMBIENT_MONITOR_INTERVAL_SEC    = _get_env_float("AMBIENT_MONITOR_INTERVAL_SEC", 5.0)
    PREDICTIVE_HISTORY_DAYS         = _get_env_int("PREDICTIVE_HISTORY_DAYS", 30)
    PREDICTIVE_CONFIDENCE_THRESHOLD = _get_env_float("PREDICTIVE_CONFIDENCE_THRESHOLD", 0.8)

    # OS Layer 2 — Actuation & Execution Engine
    ACTUATION_ENABLED               = os.environ.get("ACTUATION_ENABLED", "false").lower() == "true"
    ACTUATION_COOLDOWN_SEC          = _get_env_float("ACTUATION_COOLDOWN_SEC", 2.0)
    SESSION_TOKEN_PATH              = os.path.join(
        os.path.expanduser("~"), ".gemini", "antigravity-ide", "session_token.json"
    )
    ACTION_RETENTION_LOW_DAYS       = _get_env_int("ACTION_RETENTION_LOW_DAYS", 30)
    ACTION_RETENTION_MEDIUM_DAYS    = _get_env_int("ACTION_RETENTION_MEDIUM_DAYS", 90)
    ACTION_RETENTION_HIGH_DAYS      = _get_env_int("ACTION_RETENTION_HIGH_DAYS", 365)

    # OS Layer 2 — Phase 2: Deep Browser Automation
    BROWSER_BRIDGE_PORT             = _get_env_int("BROWSER_BRIDGE_PORT", 8765)
    ALLOWED_EXTENSION_ID            = os.environ.get("ALLOWED_EXTENSION_ID", "")
    PLAYWRIGHT_HEADLESS             = os.environ.get("PLAYWRIGHT_HEADLESS", "false").lower() == "true"
    BROWSER_ACTION_TIMEOUT_SEC      = _get_env_float("BROWSER_ACTION_TIMEOUT_SEC", 10.0)

    # OS Layer 2 — Phase 3: Agentic Shell & System Control
    AGENTIC_SHELL_TIMEOUT_SEC       = _get_env_float("AGENTIC_SHELL_TIMEOUT_SEC", 60.0)
    AGENTIC_SHELL_MAX_OUTPUT_BYTES  = _get_env_int("AGENTIC_SHELL_MAX_OUTPUT_BYTES", 5 * 1024 * 1024)
    AGENTIC_SHELL_WORKSPACE_ROOT    = os.environ.get(
        "AGENTIC_SHELL_WORKSPACE_ROOT",
        os.path.join(os.path.expanduser("~"), "JarvisWorkspace"),
    )
    APPROVED_SESSION_EXPIRY_SEC     = _get_env_float("APPROVED_SESSION_EXPIRY_SEC", 300.0)

    # Phase 3 — Document Intelligence
    DOC_INTELLIGENCE_ENABLED    = os.environ.get("DOC_INTELLIGENCE_ENABLED", "false").lower() == "true"
    DOC_INTEL_DEBOUNCE_SEC      = _get_env_float("DOC_INTEL_DEBOUNCE_SEC", 2.0)
    DOC_INTEL_MAX_FILES_ON_BOOT = _get_env_int("DOC_INTEL_MAX_FILES_ON_BOOT", 100)

    # Phase 3 — Communication (Email / Calendar)
    COMMUNICATION_ENABLED       = os.environ.get("COMMUNICATION_ENABLED", "false").lower() == "true"

    # Telegram remote control
    TELEGRAM_BOT_TOKEN       = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    TELEGRAM_ALLOWED_USER_ID = os.environ.get("TELEGRAM_ALLOWED_USER_ID", "")

    # Key rotation state (managed by LLMOrchestrator at runtime)
    _key_index = 0

    @classmethod
    def get_all_gemini_keys(cls) -> list[str]:
        """Return the full pool of available Gemini API keys."""
        pool = list(cls.GEMINI_API_KEYS)  # copy
        if cls.GEMINI_API_KEY and cls.GEMINI_API_KEY not in pool:
            pool.insert(0, cls.GEMINI_API_KEY)
        return pool

    @classmethod
    def next_gemini_key(cls) -> str | None:
        """Rotate to the next available Gemini key. Returns None if exhausted."""
        pool = cls.get_all_gemini_keys()
        if len(pool) <= 1:
            return None  # nothing to rotate to
        cls._key_index = (cls._key_index + 1) % len(pool)
        new_key = pool[cls._key_index]
        logger.info(f"[CONFIG] Rotated to Gemini key index {cls._key_index}")
        return new_key

    @classmethod
    def reload(cls):
        """Re-read environment variables from os.environ and update all class fields."""
        cls.GEMINI_API_KEY        = os.environ.get("GEMINI_API_KEY", "")
        cls.GROQ_API_KEY          = os.environ.get("GROQ_API_KEY", "")
        cls.ELEVENLABS_API_KEY    = os.environ.get("ELEVENLABS_API_KEY", "")
        cls.WOLFRAM_APP_ID        = os.environ.get("WOLFRAM_APP_ID", "")
        cls.OLLAMA_BASE_URL       = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
        cls._raw_keys             = os.environ.get("GEMINI_API_KEYS", "")
        cls.GEMINI_API_KEYS = [
            k.strip() for k in cls._raw_keys.split(",") if k.strip()
        ] if cls._raw_keys.strip() else []
        cls.ELEVENLABS_VOICE_ID   = os.environ.get("ELEVENLABS_VOICE_ID", "onwK4e9ZLuTAKqWW03F9")
        cls.CLAP_RMS_THRESHOLD    = _get_env_int("CLAP_RMS_THRESHOLD", 4000)
        cls.AUDIO_DEVICE_INDEX    = _get_env_int("AUDIO_DEVICE_INDEX", 0)
        cls.TELEGRAM_BOT_TOKEN       = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        cls.TELEGRAM_ALLOWED_USER_ID = os.environ.get("TELEGRAM_ALLOWED_USER_ID", "")
        cls.FALLBACK_ENABLED      = os.environ.get("FALLBACK_ENABLED", "true").lower() == "true"
        cls.DEBUG_MODE            = os.environ.get("DEBUG_MODE", "false").lower() == "true"
        # OS-Level System
        cls.SCREEN_INTELLIGENCE_ENABLED = os.environ.get("SCREEN_INTELLIGENCE_ENABLED", "false").lower() == "true"
        cls.SCREEN_CAPTURE_INTERVAL     = _get_env_int("SCREEN_CAPTURE_INTERVAL", 30)
        cls.WORKSPACE_RESTORE_ENABLED   = os.environ.get("WORKSPACE_RESTORE_ENABLED", "true").lower() == "true"
        cls.MAX_CONCURRENT_AGENTS       = _get_env_int("MAX_CONCURRENT_AGENTS", 2)
        cls.AMBIENT_MODE_ENABLED        = os.environ.get("AMBIENT_MODE_ENABLED", "false").lower() == "true"
        # Phase 3
        cls.DOC_INTELLIGENCE_ENABLED    = os.environ.get("DOC_INTELLIGENCE_ENABLED", "false").lower() == "true"
        cls.DOC_INTEL_DEBOUNCE_SEC      = _get_env_float("DOC_INTEL_DEBOUNCE_SEC", 2.0)
        cls.DOC_INTEL_MAX_FILES_ON_BOOT = _get_env_int("DOC_INTEL_MAX_FILES_ON_BOOT", 100)
        cls.COMMUNICATION_ENABLED       = os.environ.get("COMMUNICATION_ENABLED", "false").lower() == "true"
        # Phase 5
        cls.PREDICTIVE_ENABLED              = os.environ.get("PREDICTIVE_ENABLED", "false").lower() == "true"
        cls.AMBIENT_IDLE_THRESHOLD_SEC      = _get_env_int("AMBIENT_IDLE_THRESHOLD_SEC", 300)
        cls.AMBIENT_MONITOR_INTERVAL_SEC    = _get_env_float("AMBIENT_MONITOR_INTERVAL_SEC", 5.0)
        cls.PREDICTIVE_HISTORY_DAYS         = _get_env_int("PREDICTIVE_HISTORY_DAYS", 30)
        cls.PREDICTIVE_CONFIDENCE_THRESHOLD = _get_env_float("PREDICTIVE_CONFIDENCE_THRESHOLD", 0.8)
        # OS Layer 2
        cls.ACTUATION_ENABLED               = os.environ.get("ACTUATION_ENABLED", "false").lower() == "true"
        cls.ACTUATION_COOLDOWN_SEC          = _get_env_float("ACTUATION_COOLDOWN_SEC", 2.0)
        cls.SESSION_TOKEN_PATH              = os.path.join(
            os.path.expanduser("~"), ".gemini", "antigravity-ide", "session_token.json"
        )
        cls.ACTION_RETENTION_LOW_DAYS       = _get_env_int("ACTION_RETENTION_LOW_DAYS", 30)
        cls.ACTION_RETENTION_MEDIUM_DAYS    = _get_env_int("ACTION_RETENTION_MEDIUM_DAYS", 90)
        cls.ACTION_RETENTION_HIGH_DAYS      = _get_env_int("ACTION_RETENTION_HIGH_DAYS", 365)
        # Phase 2 browser bridge
        cls.BROWSER_BRIDGE_PORT             = _get_env_int("BROWSER_BRIDGE_PORT", 8765)
        cls.ALLOWED_EXTENSION_ID            = os.environ.get("ALLOWED_EXTENSION_ID", "")
        cls.PLAYWRIGHT_HEADLESS             = os.environ.get("PLAYWRIGHT_HEADLESS", "false").lower() == "true"
        cls.BROWSER_ACTION_TIMEOUT_SEC      = _get_env_float("BROWSER_ACTION_TIMEOUT_SEC", 10.0)
        # Phase 3 agentic shell
        cls.AGENTIC_SHELL_TIMEOUT_SEC       = _get_env_float("AGENTIC_SHELL_TIMEOUT_SEC", 60.0)
        cls.AGENTIC_SHELL_MAX_OUTPUT_BYTES  = _get_env_int("AGENTIC_SHELL_MAX_OUTPUT_BYTES", 5 * 1024 * 1024)
        cls.AGENTIC_SHELL_WORKSPACE_ROOT    = os.environ.get(
            "AGENTIC_SHELL_WORKSPACE_ROOT",
            os.path.join(os.path.expanduser("~"), "JarvisWorkspace"),
        )
        cls.APPROVED_SESSION_EXPIRY_SEC     = _get_env_float("APPROVED_SESSION_EXPIRY_SEC", 300.0)

    @classmethod
    def is_feature_available(cls, feature: str) -> bool:
        feature_map = {
            "groq":                bool(cls.GROQ_API_KEY),
            "elevenlabs":          bool(cls.ELEVENLABS_API_KEY),
            "weather":             bool(os.environ.get("OPENWEATHER_API_KEY")),
            "telegram":            bool(cls.TELEGRAM_BOT_TOKEN and cls.TELEGRAM_ALLOWED_USER_ID),
            "wolfram":             bool(cls.WOLFRAM_APP_ID),
            "screen_intelligence": cls.SCREEN_INTELLIGENCE_ENABLED,
            "workspace_restore":   cls.WORKSPACE_RESTORE_ENABLED,
            "ambient_mode":        cls.AMBIENT_MODE_ENABLED,
            "doc_intelligence":    cls.DOC_INTELLIGENCE_ENABLED,
            "communication":       cls.COMMUNICATION_ENABLED,
            "predictive":          cls.PREDICTIVE_ENABLED,
            "actuation":           cls.ACTUATION_ENABLED,
            "browser_bridge":      cls.ACTUATION_ENABLED and bool(cls.ALLOWED_EXTENSION_ID),
            "agentic_shell":       cls.ACTUATION_ENABLED,
        }
        return feature_map.get(feature, False)

# Singleton
config = Config()

