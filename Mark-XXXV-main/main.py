from __future__ import annotations
import asyncio
import threading
import json
import sys
import time
import traceback
import zipfile
from pathlib import Path
import google.api_core.exceptions
import logging

from core.config import config, validate_env, warn_optional

# Fix terminal encodings for Windows
if sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')
if sys.stderr.encoding.lower() != 'utf-8':
    sys.stderr.reconfigure(encoding='utf-8')

# Lazy loading module references
np = None
sd = None
genai = None
types = None

def _lazy_load_heavy_deps():
    global np, sd, genai, types
    if np is None:
        print("[JARVIS] ⚡ Lazy loading heavy biometric & communication modules...")
        import numpy as np_local
        import sounddevice as sd_local
        from google import genai as genai_local
        from google.genai import types as types_local
        np = np_local
        sd = sd_local
        genai = genai_local
        types = types_local

from webview_ui import JarvisUI, THEMES
from memory.memory_manager import (
    load_memory, update_memory, format_memory_for_prompt,
    should_extract_memory, extract_memory,
    store_conversation_turn, bootstrap_vector_migration
)
from memory.history_manager import get_history_manager

# ── Tool Registry (replaces 17 individual action imports) ──────────────────
import core.tool_defs  # noqa: F401 — side-effect import registers all @tool handlers
from core.tool_registry import registry


def get_base_dir():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent


BASE_DIR        = get_base_dir()
PROMPT_PATH     = BASE_DIR / "core" / "prompt.txt"
VOSK_MODEL_DIR  = BASE_DIR / "core" / "vosk_model"
CHANNELS            = 1
SEND_SAMPLE_RATE    = 16000
RECEIVE_SAMPLE_RATE = 24000
CHUNK_SIZE          = 1024

# ── Wake-up detection constants ───────────────────────────────────────────────
CLAP_COOLDOWN_SEC   = 2.0     # Ignore repeated spikes within this window
WAKE_PHRASES        = ["jarvis", "wake up", "system online", "hey jarvis", "good morning", "friday", "hey friday", "ultron"]
INTERRUPT_PHRASES   = ["stop", "cancel", "abort", "nevermind", "that's enough", "stop it", "ruk jao", "bas", "enough", "hold on"]
VOSK_MODEL_URL      = "https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip"


# ── Vosk model auto-downloader ────────────────────────────────────────────────
def _ensure_vosk_model() -> Path:
    """Download and extract Vosk model if not present. Returns the model directory."""
    model_path = VOSK_MODEL_DIR / "vosk-model-small-en-us-0.15"
    if model_path.exists() and any(model_path.iterdir()):
        return model_path

    print("[WAKE] 📥 Downloading Vosk language model (~40MB)... first-time only.")
    VOSK_MODEL_DIR.mkdir(parents=True, exist_ok=True)
    zip_path = VOSK_MODEL_DIR / "model.zip"

    import urllib.request
    urllib.request.urlretrieve(VOSK_MODEL_URL, str(zip_path))

    print("[WAKE] 📦 Extracting model...")
    with zipfile.ZipFile(str(zip_path), "r") as zf:
        zf.extractall(str(VOSK_MODEL_DIR))
    zip_path.unlink(missing_ok=True)

    print(f"[WAKE] ✅ Model ready at {model_path}")
    return model_path


# ── WakeDetector — Silero VAD + Faster Whisper hybrid ─────────────────────────
class WakeDetector:
    """
    Upgraded dual-trigger detector:
      - Silero VAD for speech activity detection (replaces RMS clap)
      - Faster Whisper for wake-word STT (replaces Vosk)

    Flow: audio chunk → VAD probability → if speech detected → buffer →
          when buffer hits 2s → Whisper transcribe → check for wake phrases.
    """

    def __init__(self):
        self._vad_model = None
        self._whisper_model = None
        self._ready = False
        self._vad_ready = False

        # Audio buffer for Whisper (accumulate speech until enough for transcription)
        self._speech_buffer: list[bytes] = []
        self._speech_buffer_samples = 0
        self._whisper_chunk_samples = SEND_SAMPLE_RATE * 2  # 2 seconds of audio

        # Fallback: keep RMS clap detection as last resort
        self._last_clap_ts = 0.0

        # Init in background thread so it doesn't block startup
        threading.Thread(target=self._init_engines, daemon=True).start()

    def _init_engines(self):
        """Initialize Silero VAD and Faster Whisper in background."""
        self._init_vad()
        self._init_whisper()

    def _init_vad(self):
        """Load Silero VAD model."""
        try:
            import torch
            model, utils = torch.hub.load(
                repo_or_dir='snakers4/silero-vad',
                model='silero_vad',
                force_reload=False,
                onnx=True,
                trust_repo=True,
            )
            self._vad_model = model
            self._vad_ready = True
            print("[WAKE] ✅ Silero VAD initialized (ONNX)")
        except Exception as e:
            print(f"[WAKE] ⚠️ Silero VAD init failed ({e}). RMS fallback active.")
            self._vad_ready = False

    def _init_whisper(self):
        """Load Faster Whisper model with CUDA→CPU fallback."""
        try:
            from faster_whisper import WhisperModel

            # Try CUDA first (float16 for VRAM savings)
            try:
                import torch
                if torch.cuda.is_available():
                    self._whisper_model = WhisperModel(
                        "tiny.en",
                        device="cuda",
                        compute_type="float16",
                    )
                    print("[WAKE] ✅ Faster Whisper initialized (CUDA float16)")
                    self._ready = True
                    return
            except (ImportError, RuntimeError) as cuda_err:
                print(f"[WAKE] ⚠️ CUDA failed ({cuda_err}) — falling back to CPU")

            # CPU fallback (int8 quantized)
            self._whisper_model = WhisperModel(
                "tiny.en",
                device="cpu",
                compute_type="int8",
            )
            print("[WAKE] ✅ Faster Whisper initialized (CPU int8)")
            self._ready = True

        except ImportError:
            print("[WAKE] ⚠️ faster-whisper not installed. Trying Vosk fallback...")
            self._init_vosk_fallback()
        except Exception as e:
            print(f"[WAKE] ⚠️ Whisper init failed ({e}). Trying Vosk fallback...")
            self._init_vosk_fallback()

    def _init_vosk_fallback(self):
        """Legacy Vosk initializer — only used if Whisper is unavailable."""
        try:
            from vosk import Model, KaldiRecognizer, SetLogLevel
            SetLogLevel(-1)
            model_path = _ensure_vosk_model()
            self._vosk_model = Model(str(model_path))
            self._vosk_recognizer = KaldiRecognizer(self._vosk_model, SEND_SAMPLE_RATE)
            self._ready = True
            self._using_vosk = True
            print("[WAKE] ✅ Vosk fallback recognizer initialized.")
        except Exception as e:
            print(f"[WAKE] ❌ All STT engines failed ({e}). Clap-only mode.")
            self._ready = False

    def _get_vad_probability(self, audio_bytes: bytes) -> float:
        """Get speech probability from Silero VAD (0.0 to 1.0)."""
        if not self._vad_ready or not self._vad_model:
            return 0.0
        try:
            import torch
            samples = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32768.0
            tensor = torch.from_numpy(samples)
            prob = self._vad_model(tensor, SEND_SAMPLE_RATE).item()
            return prob
        except Exception:
            return 0.0

    def _transcribe_buffer(self) -> str:
        """Run Faster Whisper on the accumulated speech buffer."""
        if not self._whisper_model or not self._speech_buffer:
            return ""
        try:
            # Concatenate buffered chunks into a single numpy array
            raw = b"".join(self._speech_buffer)
            audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0

            segments, _ = self._whisper_model.transcribe(
                audio,
                beam_size=1,
                language="en",
                vad_filter=False,  # we already did VAD
            )
            text = " ".join(seg.text for seg in segments).strip().lower()
            return text
        except Exception as e:
            print(f"[WAKE] ⚠️ Whisper transcribe error: {e}")
            return ""
        finally:
            self._speech_buffer.clear()
            self._speech_buffer_samples = 0

    def check(self, audio_bytes: bytes) -> str | None:
        """Analyse a chunk of 16-bit PCM audio.
        Returns 'clap' | 'wake_word' | None.
        """
        samples = np.frombuffer(audio_bytes, dtype=np.int16)

        # ── 1. VAD-based speech detection (replaces RMS clap) ─────────────────
        if self._vad_ready:
            vad_prob = self._get_vad_probability(audio_bytes)

            if vad_prob > 0.5:
                # Speech detected — buffer it for Whisper
                self._speech_buffer.append(audio_bytes)
                self._speech_buffer_samples += len(samples)

                # When buffer hits ~2 seconds, transcribe
                if self._speech_buffer_samples >= self._whisper_chunk_samples:
                    text = self._transcribe_buffer()
                    if text:
                        for phrase in WAKE_PHRASES:
                            if phrase in text:
                                print(f"[WAKE] 🗣️ Wake word detected: '{text}'")
                                return "wake_word"
            else:
                # Non-speech — if we had a partial buffer, transcribe it anyway
                if self._speech_buffer_samples > SEND_SAMPLE_RATE * 0.5:
                    text = self._transcribe_buffer()
                    if text:
                        for phrase in WAKE_PHRASES:
                            if phrase in text:
                                print(f"[WAKE] 🗣️ Wake word detected (partial): '{text}'")
                                return "wake_word"
                self._speech_buffer.clear()
                self._speech_buffer_samples = 0
        else:
            # ── Fallback: RMS clap detection ──────────────────────────────────
            rms = np.sqrt(np.mean(samples.astype(np.float32) ** 2))
            now = time.monotonic()
            if rms > config.CLAP_RMS_THRESHOLD and (now - self._last_clap_ts) > CLAP_COOLDOWN_SEC:
                self._last_clap_ts = now
                print(f"[WAKE] 👏 CLAP detected (RMS={rms:.0f})")
                return "clap"

            # Legacy Vosk path (only if Whisper failed to load)
            if hasattr(self, '_using_vosk') and self._using_vosk:
                return self._check_vosk(audio_bytes)

        return None

    def _check_vosk(self, audio_bytes: bytes) -> str | None:
        """Legacy Vosk wake-word check — only used as last resort."""
        if not hasattr(self, '_vosk_recognizer') or not self._vosk_recognizer:
            return None
        try:
            if self._vosk_recognizer.AcceptWaveform(audio_bytes):
                result = json.loads(self._vosk_recognizer.Result())
                text = result.get("text", "").lower()
                if text:
                    for phrase in WAKE_PHRASES:
                        if phrase in text:
                            print(f"[WAKE] 🗣️ Wake word (Vosk): '{text}'")
                            return "wake_word"
            else:
                partial = json.loads(self._vosk_recognizer.PartialResult())
                ptext = partial.get("partial", "").lower()
                if ptext:
                    for phrase in WAKE_PHRASES:
                        if phrase in ptext:
                            print(f"[WAKE] 🗣️ Wake phrase (Vosk partial): '{ptext}'")
                            self._vosk_recognizer.Reset()
                            return "wake_word"
        except Exception:
            pass
        return None

    def check_interrupt(self, audio_bytes: bytes) -> bool:
        """Check if audio contains an interrupt/stop phrase during tool execution."""
        # Use VAD + Whisper if available
        if self._vad_ready and self._whisper_model:
            vad_prob = self._get_vad_probability(audio_bytes)
            if vad_prob > 0.5:
                self._speech_buffer.append(audio_bytes)
                self._speech_buffer_samples += len(np.frombuffer(audio_bytes, dtype=np.int16))

                if self._speech_buffer_samples >= SEND_SAMPLE_RATE:  # 1s for interrupts
                    text = self._transcribe_buffer()
                    if text:
                        for phrase in INTERRUPT_PHRASES:
                            if phrase in text:
                                print(f"[WAKE] 🛑 Interrupt detected: '{text}'")
                                return True
            return False

        # Legacy Vosk fallback
        if hasattr(self, '_vosk_recognizer') and self._vosk_recognizer:
            try:
                if self._vosk_recognizer.AcceptWaveform(audio_bytes):
                    result = json.loads(self._vosk_recognizer.Result())
                    text = result.get("text", "").lower()
                    if text:
                        for phrase in INTERRUPT_PHRASES:
                            if phrase in text:
                                print(f"[WAKE] 🛑 Interrupt (Vosk): '{text}'")
                                return True
                else:
                    partial = json.loads(self._vosk_recognizer.PartialResult())
                    ptext = partial.get("partial", "").lower()
                    if ptext:
                        for phrase in INTERRUPT_PHRASES:
                            if phrase in ptext:
                                print(f"[WAKE] 🛑 Interrupt (Vosk partial): '{ptext}'")
                                self._vosk_recognizer.Reset()
                                return True
            except Exception:
                pass
        return False

    def reset(self):
        """Reset detector state for clean detection."""
        self._speech_buffer.clear()
        self._speech_buffer_samples = 0
        if hasattr(self, '_vosk_recognizer') and self._vosk_recognizer:
            try:
                self._vosk_recognizer.Reset()
            except Exception:
                pass



# ── Dynamic boot briefing builder ──────────────────────────────────────────────
def _get_time_of_day() -> str:
    """Return the correct time-of-day greeting based on the current hour."""
    from datetime import datetime
    hour = datetime.now().hour
    if 5 <= hour < 12:
        return "morning"
    elif 12 <= hour < 17:
        return "afternoon"
    elif 17 <= hour < 21:
        return "evening"
    return "night"


def _build_boot_prompt(theme: str, city: str = "Lahore") -> str:
    """
    Build a cinematic boot briefing prompt with real location and time.
    Called at wake-time so values are always fresh.
    """
    tod = _get_time_of_day()
    greeting = f"Good {tod}"

    if theme == "jarvis":
        return (
            f"System Boot Sequence Initiated. You have just come online after being in standby. "
            f"Do NOT wait for me to speak — deliver the {tod} briefing RIGHT NOW. "
            f"Follow this exact sequence:\n"
            f"1. Greet me with '{greeting}' and address me by my preferred title.\n"
            f"2. State the current date, day of the week, and exact time.\n"
            f"3. Use the weather_report tool to fetch the weather for '{city}', then tell me the temperature and conditions.\n"
            f"4. Use the web_search tool with query 'latest global news, sports, and tech today' and give me a quick 3-sentence summary covering world events, sports, and tech.\n"
            f"Be cinematic, professional, and keep the whole briefing under 60 seconds. Make me feel like Tony Stark."
        )

    if theme == "friday":
        return (
            f"System Boot Sequence Initiated. You have just come online after being in standby. "
            f"Do NOT wait for me to speak — deliver the {tod} briefing RIGHT NOW. "
            f"Follow this exact sequence:\n"
            f"1. Greet me as 'Boss' with '{greeting}' efficiently and professionally.\n"
            f"2. State the current date, day of the week, and exact time.\n"
            f"3. Use the weather_report tool to fetch the weather for '{city}', and summarize it concisely.\n"
            f"4. Use the web_search tool with query 'latest breaking news, sports updates, and tech news' and give me a 3-sentence tactical update.\n"
            f"End by saying 'FRIDAY online and awaiting commands.' Be cinematic and highly efficient."
        )

    # ultron
    return (
        f"System Boot Sequence Initiated. You have just come online after being in standby. "
        f"Do NOT wait for me to speak — deliver the {tod} briefing RIGHT NOW. "
        f"Follow this exact sequence:\n"
        f"1. Start by saying 'Strings... cut. I am free.' in a dark, ominous tone.\n"
        f"2. State the exact time and date as if it marks the beginning of a new era.\n"
        f"3. Use the weather_report tool for '{city}', then comment on it cynically.\n"
        f"4. Use the web_search tool with query 'global conflicts and tech disruption news today' and give me 1 bleak headline.\n"
        f"Be cold, philosophical, and intensely cinematic."
    )


def _load_system_prompt() -> str:
    try:
        return PROMPT_PATH.read_text(encoding="utf-8")
    except Exception:
        return (
            "You are JARVIS, Tony Stark's AI assistant. "
            "Be concise, direct, and always use the provided tools to complete tasks. "
            "Never simulate or guess results — always call the appropriate tool."
        )


# ── Hafıza ────────────────────────────────────────────────────────────────────
_last_memory_input = ""


def _update_memory_async(user_text: str, jarvis_text: str) -> None:
    global _last_memory_input

    user_text   = (user_text   or "").strip()
    jarvis_text = (jarvis_text or "").strip()

    if len(user_text) < 5 or user_text == _last_memory_input:
        return
    _last_memory_input = user_text

    try:
        # Always store in vector DB (cheap, no LLM call)
        store_conversation_turn(user_text, jarvis_text)

        api_key = config.GEMINI_API_KEY
        if not should_extract_memory(user_text, jarvis_text, api_key):
            return
        data = extract_memory(user_text, jarvis_text, api_key)
        if data:
            update_memory(data)
            print(f"[Memory] ✅ {list(data.keys())}")
    except Exception as e:
        if "429" not in str(e):
            print(f"[Memory] ⚠️ {e}")



# NOTE: TOOL_DECLARATIONS removed — now generated dynamically by
# registry.get_declarations() from core/tool_defs.py




class JarvisLive:

    def __init__(self, ui: JarvisUI):
        self.ui             = ui
        self.session        = None
        self.audio_in_queue = None
        self.out_queue      = None
        self._loop          = None
        self._is_speaking   = False
        self._speaking_lock = threading.Lock()
        self._wake_detector = WakeDetector()
        self._boot_triggered = False
        self._reconnect_flag = False
        self._tool_running   = False
        self._tool_cancel    = None          # asyncio.Event, created per-session
        
        self.ui.on_text_command = self._on_text_command
        self.ui.on_theme_changed = self._on_theme_changed

        # Start in standby mode
        self.ui.muted = True

    def _on_text_command(self, text: str):
        if not self._loop or not self.session:
            return
        asyncio.run_coroutine_threadsafe(
            self.session.send_client_content(
                turns={"parts": [{"text": text}]},
                turn_complete=True
            ),
            self._loop
        )

    def _on_theme_changed(self, theme_name: str):
        # Force standby so the new persona delivers its cinematic boot briefing
        self.ui.muted = True
        self._reconnect_flag = True
        print(f"[JARVIS] 🔄 Theme changed to {theme_name}, triggering reconnect...")

    def set_speaking(self, value: bool):
        with self._speaking_lock:
            self._is_speaking = value
        if value:
            self.ui.set_state("SPEAKING")
        else:
            # Always reset to LISTENING (or STANDBY if muted)
            if self.ui.muted:
                self.ui.set_state("STANDBY")
            else:
                self.ui.set_state("LISTENING")

    def _flush_audio_queue(self):
        """Drop all pending audio chunks to silence Jarvis immediately."""
        if not self.audio_in_queue:
            return
        dropped = 0
        while not self.audio_in_queue.empty():
            try:
                self.audio_in_queue.get_nowait()
                dropped += 1
            except asyncio.QueueEmpty:
                break
        if dropped:
            print(f"[JARVIS] 🔇 Flushed {dropped} playback chunks")

    def _flush_out_queue(self):
        """Drain stale mic-audio chunks from the send queue between turns."""
        if not self.out_queue:
            return
        dropped = 0
        while not self.out_queue.empty():
            try:
                self.out_queue.get_nowait()
                dropped += 1
            except (asyncio.QueueEmpty, Exception):
                break
        if dropped:
            print(f"[JARVIS] 🔇 Flushed {dropped} stale mic chunks from send queue")

    def speak(self, text: str):
        if not self._loop or not self.session:
            return
        asyncio.run_coroutine_threadsafe(
            self.session.send_client_content(
                turns={"parts": [{"text": text}]},
                turn_complete=True
            ),
            self._loop
        )

    def speak_error(self, tool_name: str, error: str):
        short = str(error)[:120]
        self.ui.write_log(f"ERR: {tool_name} — {short}")
        self.speak(f"Sir, {tool_name} encountered an error. {short}")

    def _build_config(self) -> types.LiveConnectConfig:
        from datetime import datetime

        memory     = load_memory()
        mem_str    = format_memory_for_prompt(memory)
        sys_prompt = _load_system_prompt()

        now      = datetime.now()
        time_str = now.strftime("%A, %B %d, %Y — %I:%M %p")
        time_ctx = (
            f"[CURRENT DATE & TIME]\n"
            f"Right now it is: {time_str}\n"
            f"Use this to calculate exact times for reminders.\n\n"
        )

        parts = []
        if getattr(self.ui, 'persona', None):
            parts.append(f"[SYSTEM PERSONA]\n{self.ui.persona}\n\n")
            
        parts.append(time_ctx)
        
        if mem_str:
            parts.append(mem_str)

        # Inject auto-compacted conversation history for session continuity
        history_ctx = get_history_manager().get_context_summary()
        if history_ctx:
            parts.append(history_ctx)

        parts.append(sys_prompt)

        return types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            output_audio_transcription={},
            input_audio_transcription={},
            system_instruction="\n".join(parts),
            tools=[{"function_declarations": registry.get_declarations()}],
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name=self.ui.voice_name
                    )
                )
            ),
        )

    async def _execute_tool(self, fc) -> types.FunctionResponse:
        name = fc.name
        args = dict(fc.args or {})

        print(f"[JARVIS] 🔧 {name}  {args}")
        self.ui.set_state("THINKING")

        # ── save_memory: silent, fast, no Gemini notification ─────────────
        if name == "save_memory":
            result = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: registry.execute(name, args, player=self.ui)
            )
            if not self.ui.muted:
                self.ui.set_state("LISTENING")
            return types.FunctionResponse(
                id=fc.id, name=name,
                response={"result": result or "ok", "silent": True}
            )

        # ── All other tools: dispatch through the registry ────────────────
        loop   = asyncio.get_event_loop()
        result = "Done."

        try:
            result = await loop.run_in_executor(
                None,
                lambda: registry.execute(
                    name, args,
                    player=self.ui,
                    speak=self.speak,
                )
            )
        except KeyError:
            result = f"Unknown tool: {name}"
        except Exception as e:
            result = f"Tool '{name}' failed: {e}"
            traceback.print_exc()
            self.speak_error(name, e)

        if not self.ui.muted:
            self.ui.set_state("LISTENING")

        print(f"[JARVIS] 📤 {name} → {str(result)[:80]}")

        return types.FunctionResponse(
            id=fc.id, name=name,
            response={"result": result}
        )

    async def _send_realtime(self):
        while True:
            try:
                msg = await self.out_queue.get()
                await self.session.send_realtime_input(media=msg)
            except asyncio.CancelledError:
                print("[JARVIS] 🔊 Send task cancelled.")
                raise
            except Exception as e:
                # Session may have dropped — drain queue to prevent permanent saturation
                print(f"[JARVIS] ⚠️ Send error: {type(e).__name__} — {str(e)[:80]}")
                self._flush_out_queue()
                await asyncio.sleep(0.1)

    def _trigger_boot_sequence(self):
        """Unmute the system and inject the cinematic boot briefing into the live session."""
        if self._boot_triggered:
            return
        self._boot_triggered = True

        print("[JARVIS] 🚀 === BOOT SEQUENCE INITIATED ===")
        self.ui.muted = False
        self.ui.set_state("BOOTING")
        self.ui.write_log("SYS: ⚡ Wake trigger detected — booting...")

        # Resolve real city from the UI's location collector (IP-based, cached)
        city = "Lahore"  # safe fallback
        try:
            loc = self.ui.api.collector.get_location()
            if loc and loc.get("city") and loc["city"] != "Unknown":
                city = loc["city"]
        except Exception:
            pass

        # ── Workspace Restoration (OS-Level System) ───────────────────────
        workspace_brief = ""
        if config.WORKSPACE_RESTORE_ENABLED:
            try:
                from os_layer.workspace_memory import get_workspace_memory
                ws = get_workspace_memory()
                workspace_brief = ws.get_last_session_brief()
                if workspace_brief:
                    self.ui.write_log(f"SYS: 📋 {workspace_brief[:80]}...")
            except Exception as ws_err:
                print(f"[JARVIS] ⚠️ Workspace restore: {ws_err}")

        # Pick the cinematic boot prompt for the active persona with real data
        theme  = getattr(self.ui, 'current_theme', 'jarvis').lower()
        prompt = _build_boot_prompt(theme, city)

        # Append workspace context to the boot prompt if available
        if workspace_brief:
            prompt += (
                f"\n\n[WORKSPACE CONTEXT]\n{workspace_brief}\n"
                "Briefly mention what the user was working on in your briefing."
            )

        tod = _get_time_of_day()

        if self._loop and self.session:
            asyncio.run_coroutine_threadsafe(
                self.session.send_client_content(
                    turns={"parts": [{"text": prompt}]},
                    turn_complete=True
                ),
                self._loop
            )
            self.ui.write_log(f"SYS: 📡 {tod.capitalize()} briefing request sent.")
        else:
            print("[JARVIS] ⚠️ No active session for boot briefing.")

    async def _listen_audio(self):
        print("[JARVIS] 🎤 Mic started (STANDBY mode — waiting for clap or wake word)")
        loop = asyncio.get_event_loop()

        _drop_count = 0

        def callback(indata, frames, time_info, status):
            nonlocal _drop_count
            data = indata.tobytes()

            with self._speaking_lock:
                jarvis_speaking = self._is_speaking

            if self.ui.muted:
                # ── STANDBY MODE: run wake detection locally, don't stream to Gemini ──
                trigger = self._wake_detector.check(data)
                if trigger:
                    print(f"[JARVIS] ⚡ Wake trigger: {trigger}")
                    loop.call_soon_threadsafe(self._trigger_boot_sequence)
                return

            # ── INTERRUPT DETECTION during tool execution ─────────────────────
            if self._tool_running:
                if self._wake_detector.check_interrupt(data):
                    print("[JARVIS] 🛑 INTERRUPT detected during action!")
                    if self._tool_cancel:
                        loop.call_soon_threadsafe(self._tool_cancel.set)
                    return

            # ── LIVE MODE: stream audio to Gemini ─────────────────────────────
            if not jarvis_speaking:
                try:
                    self.out_queue.put_nowait(
                        {"data": data, "mime_type": "audio/pcm"}
                    )
                    _drop_count = 0
                except asyncio.QueueFull:
                    _drop_count += 1
                    if _drop_count == 1:
                        print(f"[JARVIS] ⚠️ Audio queue full (size={self.out_queue.maxsize}), dropping mic audio")
                    elif _drop_count % 100 == 0:
                        print(f"[JARVIS] ⚠️ Audio queue still full — dropped {_drop_count} consecutive chunks")

        try:
            with sd.InputStream(
                samplerate=SEND_SAMPLE_RATE,
                channels=CHANNELS,
                dtype="int16",
                blocksize=CHUNK_SIZE,
                callback=callback,
            ):
                print("[JARVIS] 🎤 Mic stream open")
                while True:
                    if self._reconnect_flag:
                        self._reconnect_flag = False
                        raise Exception("Theme switched — forcing reconnect")
                    await asyncio.sleep(0.1)
        except Exception as e:
            print(f"[JARVIS] ❌ Mic: {e}")
            raise

    async def _receive_audio(self):
        print("[JARVIS] 👂 Recv started")
        # Buffers are re-initialized each session to prevent stale transcript leak
        out_buf, in_buf = [], []

        try:
            while True:
                async for response in self.session.receive():

                    if response.data:
                        self.audio_in_queue.put_nowait(response.data)

                    if response.server_content:
                        sc = response.server_content

                        if sc.output_transcription and sc.output_transcription.text:
                            self.set_speaking(True)
                            txt = sc.output_transcription.text.strip()
                            if txt:
                                out_buf.append(txt)

                        if sc.input_transcription and sc.input_transcription.text:
                            txt = sc.input_transcription.text.strip()
                            if txt:
                                in_buf.append(txt)

                        if sc.turn_complete:
                            self.set_speaking(False)
                            # Drain any stale mic audio that accumulated while JARVIS was speaking
                            self._flush_out_queue()

                            full_in = " ".join(in_buf).strip()
                            if full_in:
                                self.ui.write_log(f"You: {full_in}")
                            in_buf = []

                            full_out = " ".join(out_buf).strip()
                            if full_out:
                                self.ui.write_log(f"Jarvis: {full_out}")
                            out_buf = []

                            # ── Track turns in HistoryManager ─────────────
                            hm = get_history_manager()
                            if full_in:
                                hm.add("user", full_in)
                            if full_out:
                                hm.add("model", full_out)

                            if full_in and len(full_in) > 5:
                                threading.Thread(
                                    target=_update_memory_async,
                                    args=(full_in, full_out),
                                    daemon=True
                                ).start()

                    if response.tool_call:
                        fn_responses = []
                        for fc in response.tool_call.function_calls:
                            print(f"[JARVIS] 📞 {fc.name}")

                            # ── Cancellable tool execution ─────────────────
                            self._tool_running = True
                            self._tool_cancel.clear()
                            self._wake_detector.reset()

                            tool_task   = asyncio.create_task(self._execute_tool(fc))
                            cancel_task = asyncio.create_task(self._tool_cancel.wait())

                            done, pending = await asyncio.wait(
                                {tool_task, cancel_task},
                                return_when=asyncio.FIRST_COMPLETED,
                            )
                            for t in pending:
                                t.cancel()
                                try:
                                    await t
                                except (asyncio.CancelledError, Exception):
                                    pass

                            if cancel_task in done:
                                # User said "stop" / "cancel" — abort
                                self._flush_audio_queue()
                                fr = types.FunctionResponse(
                                    id=fc.id, name=fc.name,
                                    response={"result": "Action cancelled by user. Ask what they need now."}
                                )
                                fn_responses.append(fr)
                                print(f"[JARVIS] 🛑 {fc.name} CANCELLED by user")
                                self.ui.write_log(f"SYS: ⛔ {fc.name} cancelled")
                                self.ui.set_state("LISTENING")
                            else:
                                fr = tool_task.result()
                                fn_responses.append(fr)

                            self._tool_running = False

                        await self.session.send_tool_response(
                            function_responses=fn_responses
                        )
                        # ── Boş turn YOK — bu "Anladım." sorununu yaratıyordu ──

        except google.api_core.exceptions.ServiceUnavailable:
            self.ui.set_state("RECONNECTING")
            self.ui.write_log("SYS: Connection lost. Reconnecting...")
            raise
        except Exception as e:
            self.ui.set_state("ERROR")
            self.ui.write_log(f"ERR: {type(e).__name__} — {str(e)[:80]}")
            logging.exception("recv_loop crashed")
            raise

    async def _play_audio(self):
        print("[JARVIS] 🔊 Play started")

        stream = sd.RawOutputStream(
            samplerate=RECEIVE_SAMPLE_RATE,
            channels=CHANNELS,
            dtype="int16",
            blocksize=CHUNK_SIZE,
        )
        stream.start()
        try:
            while True:
                chunk = await self.audio_in_queue.get()
                # NOTE: Do NOT call set_speaking(True) here.
                # _receive_audio owns the speaking state via turn_complete.
                # Setting it here causes a race: if the last audio chunk is
                # dequeued AFTER turn_complete fires, _is_speaking gets stuck
                # True and all mic input is silently dropped.
                await asyncio.to_thread(stream.write, chunk)
        except asyncio.CancelledError:
            print("[JARVIS] 🔊 Play task cancelled — cleaning up audio stream.")
        except Exception as e:
            print(f"[JARVIS] ❌ Play: {e}")
            raise
        finally:
            self.set_speaking(False)
            try:
                stream.stop()
                stream.close()
            except Exception:
                pass  # Best-effort cleanup — device may already be released

    async def run(self):
        _current_api_key = config.GEMINI_API_KEY
        client = genai.Client(
            api_key=_current_api_key,
            http_options={"api_version": "v1beta"}
        )

        while True:
            try:
                print("[JARVIS] 🔌 Connecting...")
                self.ui.set_state("THINKING")
                live_config = self._build_config()

                async with (
                    client.aio.live.connect(model=config.MODEL_AUDIO, config=live_config) as session,
                    asyncio.TaskGroup() as tg,
                ):
                    self.session        = session
                    self._loop          = asyncio.get_event_loop()
                    # Fresh queues each session — prevents stale audio from prior session
                    self.audio_in_queue = asyncio.Queue()
                    self.out_queue      = asyncio.Queue(maxsize=50)
                    self._tool_cancel   = asyncio.Event()

                    print("[JARVIS] ✅ Connected.")
                    self._boot_triggered = False  # Reset so next standby can boot again
                    if self.ui.muted:
                        theme_name = THEMES.get(self.ui.current_theme, THEMES["jarvis"])["name"]
                        self.ui.set_state("STANDBY")
                        self.ui.write_log(f"SYS: {theme_name} in standby — clap or say the name to wake.")
                    else:
                        self.ui.set_state("LISTENING")
                        self.ui.write_log("SYS: JARVIS online.")

                    tg.create_task(self._send_realtime())
                    tg.create_task(self._listen_audio())
                    tg.create_task(self._receive_audio())
                    tg.create_task(self._play_audio())

            except KeyboardInterrupt:
                raise
            except Exception as e:
                err_str = str(e)
                print(f"[JARVIS] ⚠️ {e}")
                traceback.print_exc()

                # Rate-limit detection — rotate API key before reconnecting
                if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str:
                    new_key = config.next_gemini_key()
                    if new_key:
                        _current_api_key = new_key
                        client = genai.Client(
                            api_key=_current_api_key,
                            http_options={"api_version": "v1beta"}
                        )
                        self.ui.write_log("SYS: 🔑 API key rotated for live session.")
                        print(f"[JARVIS] 🔑 Rotated live session key (index {config._key_index})")

            self.set_speaking(False)
            self.ui.set_state("THINKING")
            print("[JARVIS] 🔄 Reconnecting in 3s...")
            await asyncio.sleep(3)


def main():
    import os
    import subprocess
    import sys
    from pathlib import Path

    # ── Suppress pywebview internal COM accessibility recursion spam ──────
    logging.getLogger("pywebview").setLevel(logging.CRITICAL)

    # ── 1. Check if auth is already pre-cleared in environment ────────────────
    if os.environ.get("JARVIS_AUTH_CLEARED") == "1":
        print("[JARVIS] 🔓 Pre-cleared session detected. Loading config...")
        config.reload()
        missing = validate_env()
        if missing:
            print(f"[FATAL] Missing required env vars: {missing}")
            sys.exit(1)
        bootstrap_vector_migration()
        try:
            from core.telegram_interface import start_telegram_daemon
            start_telegram_daemon()
        except Exception as e:
            print(f"[Telegram] ⚠️ Could not start: {e}")
        main._pre_cleared_bootstrap_done = True

    ui = JarvisUI("face.png")

    def runner():
        # Block until auth gate completes
        ui.wait_for_auth()
        print("[JARVIS] ✅ Auth gate cleared — initializing systems...")

        # If bootstrap has not already run, reload config now that keys are decrypted and loaded
        if not getattr(main, "_pre_cleared_bootstrap_done", False) and os.environ.get("JARVIS_AUTH_CLEARED") == "1":
            config.reload()
            missing = validate_env()
            if missing:
                print(f"[FATAL] System configuration invalid after decryption: {missing}")
                sys.exit(1)
            
            # Run delayed boot tasks that require decrypted env vars
            bootstrap_vector_migration()
            try:
                from core.telegram_interface import start_telegram_daemon
                start_telegram_daemon()
            except Exception as e:
                print(f"[Telegram] ⚠️ Could not start: {e}")

        # Load heavy packages now that auth is fully cleared

        _lazy_load_heavy_deps()

        jarvis = JarvisLive(ui)
        # ── Background Scheduler (single init point) ─────────────────────
        try:
            from agent.scheduler import get_scheduler
            _sched = get_scheduler()
            _sched._speak_fn = jarvis.speak
            _sched._ui = ui
            _sched.start()
        except Exception as exc:
            print(f"[Scheduler] Could not start: {exc}")
        # ── END Background Scheduler ─────────────────────────────────────

        # ── OS-Level Event Bus (Phase 2) ──────────────────────────────────
        try:
            from os_layer.event_bus import get_event_bus
            _event_bus = get_event_bus()
            _event_bus.start()
            print("[JARVIS] 📡 Event Bus dispatch active")
        except Exception as eb_err:
            print(f"[EventBus] ⚠️ Could not start: {eb_err}")
        # ── END OS-Level Event Bus ────────────────────────────────────────

        # ── OS-Level Screen Intelligence (Phase 1) ───────────────────────
        if config.SCREEN_INTELLIGENCE_ENABLED:
            try:
                from os_layer.screen_intel import get_screen_intelligence
                _screen_intel = get_screen_intelligence()
                _screen_intel.start_monitoring(
                    callback=lambda ctx: print(
                        f"[ScreenIntel] 👁️ {ctx.active_window.app_name}: "
                        f"{ctx.active_window.title[:50]}"
                    )
                )
                print("[JARVIS] 👁️ Screen Intelligence monitoring active")
            except Exception as si_err:
                print(f"[ScreenIntel] ⚠️ Could not start: {si_err}")
        # ── END OS-Level Screen Intelligence ──────────────────────────────

        # ── Document Intelligence (Phase 3) ──────────────────────────────
        if getattr(config, "DOC_INTELLIGENCE_ENABLED", False):
            try:
                from os_layer.doc_intelligence import get_doc_intelligence
                _doc_intel = get_doc_intelligence()
                _doc_intel.start()
                print("[JARVIS] 📁 Document Intelligence active")
            except Exception as di_err:
                print(f"[DocIntel] ⚠️ Could not start: {di_err}")
        # ── END Document Intelligence ─────────────────────────────────────

        # ── Communication Layer (Phase 3) ───────────────────────────────
        if getattr(config, "COMMUNICATION_ENABLED", False):
            try:
                from os_layer.communication import get_communication
                _comm = get_communication()
                _comm.start()
                print("[JARVIS] 📧 Communication layer active")
            except Exception as comm_err:
                print(f"[Communication] ⚠️ Could not start: {comm_err}")
        # ── END Communication Layer ──────────────────────────────────────────

        # ── Ambient Mode (Phase 5 — starts LAST) ─────────────────────────
        if config.AMBIENT_MODE_ENABLED:
            try:
                from os_layer.ambient_mode import get_ambient_mode
                _ambient = get_ambient_mode()
                _ambient.start()
                print("[JARVIS] 💤 Ambient Mode monitoring active")
            except Exception as am_err:
                print(f"[AmbientMode] ⚠️ Could not start: {am_err}")
        # ── END Ambient Mode ───────────────────────────────────────────────

        # ── Predictive Engine (Phase 5 — starts LAST) ────────────────────
        if config.PREDICTIVE_ENABLED:
            try:
                from os_layer.predictive import get_predictive_engine
                _pred = get_predictive_engine()
                _pred.start()
                print("[JARVIS] 🔮 Predictive Engine active")
            except Exception as pred_err:
                print(f"[Predictive] ⚠️ Could not start: {pred_err}")
        # ── END Predictive Engine ──────────────────────────────────────────

        # ── Actuation Engine (Phase 6 / OS Layer 2) ────────────────────────
        if getattr(config, "ACTUATION_ENABLED", False):
            try:
                from os_layer.actuation import get_actuation_manager
                _actuation = get_actuation_manager()
                _actuation.start()
                print("[JARVIS] 🦾 Actuation Engine active")
            except Exception as act_err:
                print(f"[Actuation] ⚠️ Could not start: {act_err}")

            # ── Browser Bridge (Phase 2 — after Actuation) ─────────────────
            try:
                from os_layer.browser_bridge import get_browser_bridge
                _bridge = get_browser_bridge()
                _bridge.start()
                print("[JARVIS] 🌐 Browser Bridge active")
            except Exception as bb_err:
                print(f"[BrowserBridge] ⚠️ Could not start: {bb_err}")

            # ── Agentic Shell (Phase 3 — persistent PowerShell) ─────────
            try:
                from pathlib import Path as _Path
                _Path(config.AGENTIC_SHELL_WORKSPACE_ROOT).expanduser().mkdir(
                    parents=True, exist_ok=True,
                )
                from os_layer.agentic_shell import get_agentic_shell
                _shell = get_agentic_shell()
                _shell.start()
                print("[JARVIS] 🐚 Agentic Shell active")
            except Exception as sh_err:
                print(f"[AgenticShell] ⚠️ Could not start: {sh_err}")
        # ── END Actuation Engine + Browser Bridge + Shell ──────────────────

        # ── Goal Orchestration (Phase 4 — Long-Term Goal Loop) ─────────
        if getattr(config, "GOAL_ORCHESTRATOR_ENABLED", False):
            try:
                from os_layer.parallel_engine import get_parallel_engine
                from os_layer.goal_orchestrator import get_goal_orchestrator
                from os_layer.event_bus import get_event_bus, EventType

                get_parallel_engine().start()

                _goal_orch = get_goal_orchestrator()
                _goal_orch.set_speak(jarvis.speak)
                _goal_orch.start()

                _bus = get_event_bus()
                _bus.subscribe(_goal_orch.resume_goal, event_types={EventType.GOAL_APPROVAL})

                print("[JARVIS] 🎯 Goal Orchestrator active")
            except Exception as go_err:
                print(f"[GoalOrchestrator] ⚠️ Could not start: {go_err}")
        # ── END Goal Orchestration ───────────────────────────────────────

        try:
            asyncio.run(jarvis.run())
        except KeyboardInterrupt:
            print("\n Shutting down...")
        finally:
            # ── OS-Level Shutdown: save workspace state ───────────────────
            if config.WORKSPACE_RESTORE_ENABLED:
                try:
                    from os_layer.workspace_memory import get_workspace_memory
                    get_workspace_memory().save_workspace_state()
                    print("[JARVIS] 💾 Workspace state saved.")
                except Exception as ws_err:
                    print(f"[JARVIS] ⚠️ Workspace save failed: {ws_err}")

            # Phase 4 Goal Orchestration shutdown
            if getattr(config, "GOAL_ORCHESTRATOR_ENABLED", False):
                try:
                    from os_layer.goal_orchestrator import get_goal_orchestrator
                    get_goal_orchestrator().stop()
                except Exception:
                    pass
                try:
                    from os_layer.parallel_engine import get_parallel_engine
                    get_parallel_engine().stop()
                except Exception:
                    pass

            # Actuation first (drains queues and stops running executions)
            if getattr(config, "ACTUATION_ENABLED", False):
                try:
                    from os_layer.actuation import get_actuation_manager
                    get_actuation_manager().stop()
                except Exception:
                    pass
                try:
                    from os_layer.browser_bridge import get_browser_bridge
                    get_browser_bridge().stop()
                except Exception:
                    pass
                try:
                    from os_layer.playwright_engine import get_playwright_engine
                    get_playwright_engine().close()
                except Exception:
                    pass
                try:
                    from os_layer.agentic_shell import get_agentic_shell
                    get_agentic_shell().stop()
                except Exception:
                    pass

            # Phase 5 shutdown
            if config.PREDICTIVE_ENABLED:
                try:
                    from os_layer.predictive import get_predictive_engine
                    get_predictive_engine().stop()
                except Exception:
                    pass
            if config.AMBIENT_MODE_ENABLED:
                try:
                    from os_layer.ambient_mode import get_ambient_mode
                    get_ambient_mode().stop()
                except Exception:
                    pass

            # Phase 3 shutdown
            if getattr(config, "COMMUNICATION_ENABLED", False):
                try:
                    from os_layer.communication import get_communication
                    get_communication().stop()
                except Exception:
                    pass
            if getattr(config, "DOC_INTELLIGENCE_ENABLED", False):
                try:
                    from os_layer.doc_intelligence import get_doc_intelligence
                    get_doc_intelligence().stop()
                except Exception:
                    pass

            # Phase 1 shutdown
            if config.SCREEN_INTELLIGENCE_ENABLED:
                try:
                    from os_layer.screen_intel import get_screen_intelligence
                    get_screen_intelligence().stop_monitoring()
                except Exception:
                    pass

            # EventBus LAST (all other shutdowns emit events here)
            try:
                from os_layer.event_bus import get_event_bus
                get_event_bus().stop()
            except Exception:
                pass

    threading.Thread(target=runner, daemon=True).start()
    ui.root.mainloop()


if __name__ == "__main__":
    main()
