"""
elevenlabs_tts.py — ElevenLabs Text-to-Speech for JARVIS personas.
==================================================================
Routes text through the ElevenLabs streaming TTS API and plays audio
in real-time via sounddevice. Used for the Ultron persona to get a
deeper, more cinematic voice than Gemini's pre-built options.
"""

import io
import logging
import threading

import numpy as np
import sounddevice as sd

logger = logging.getLogger(__name__)

# Defaults — can be overridden per-persona
DEFAULT_VOICE_ID = "onwK4e9ZLuTAKqWW03F9"   # Daniel (deep male)
DEFAULT_MODEL_ID = "eleven_multilingual_v2"
SAMPLE_RATE = 24000
CHANNELS = 1


def speak_elevenlabs(
    text: str,
    api_key: str,
    voice_id: str = DEFAULT_VOICE_ID,
    model_id: str = DEFAULT_MODEL_ID,
    on_start: callable = None,
    on_end: callable = None,
) -> bool:
    """
    Synthesize `text` via ElevenLabs and play it through speakers.

    Args:
        text:      The text to speak.
        api_key:   ElevenLabs API key.
        voice_id:  ElevenLabs voice ID (default: Daniel).
        model_id:  ElevenLabs model to use.
        on_start:  Callback fired when audio playback begins.
        on_end:    Callback fired when audio playback ends.

    Returns:
        True if audio was played successfully, False on error.
    """
    if not text or not text.strip():
        return False

    if not api_key:
        logger.warning("[ElevenLabs] No API key configured — skipping TTS.")
        return False

    try:
        from elevenlabs import ElevenLabs

        client = ElevenLabs(api_key=api_key)

        if callable(on_start):
            on_start()

        audio_generator = client.text_to_speech.convert(
            text=text,
            voice_id=voice_id,
            model_id=model_id,
            output_format="pcm_24000",
        )

        # Collect all audio chunks
        audio_bytes = b"".join(audio_generator)

        if not audio_bytes:
            logger.warning("[ElevenLabs] Empty audio response.")
            return False

        # Convert PCM bytes to numpy int16 array
        audio_array = np.frombuffer(audio_bytes, dtype=np.int16)

        # Play synchronously via sounddevice
        sd.play(audio_array, samplerate=SAMPLE_RATE, blocksize=1024)
        sd.wait()

        if callable(on_end):
            on_end()

        return True

    except ImportError:
        logger.error("[ElevenLabs] elevenlabs package not installed — pip install elevenlabs")
        print("[ElevenLabs] ❌ Package not installed — pip install elevenlabs")
        return False
    except Exception as e:
        logger.error(f"[ElevenLabs] TTS error: {e}")
        print(f"[ElevenLabs] ❌ TTS error: {e}")
        if callable(on_end):
            on_end()
        return False


def speak_elevenlabs_async(
    text: str,
    api_key: str,
    voice_id: str = DEFAULT_VOICE_ID,
    model_id: str = DEFAULT_MODEL_ID,
    on_start: callable = None,
    on_end: callable = None,
) -> threading.Thread:
    """
    Non-blocking wrapper — runs speak_elevenlabs in a daemon thread.
    Returns the thread object for optional join().
    """
    t = threading.Thread(
        target=speak_elevenlabs,
        kwargs={
            "text": text,
            "api_key": api_key,
            "voice_id": voice_id,
            "model_id": model_id,
            "on_start": on_start,
            "on_end": on_end,
        },
        daemon=True,
        name="ElevenLabsTTS",
    )
    t.start()
    return t
