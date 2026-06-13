import logging
import threading
import time
from enum import Enum
from google import genai
from google.genai import types

from core.config import config

class TaskTier(Enum):
    ROUTING = "routing"
    LOGIC = "logic"
    COMPLEX = "complex"
    VISION = "vision"
    AUDIO = "audio"

class FallbackResult:
    def __init__(self, text, model_used):
        self.text = text
        self.model_used = model_used

class LLMOrchestrator:
    _instance = None
    
    MODELS = {
        TaskTier.ROUTING: config.MODEL_ROUTING,
        TaskTier.LOGIC:   config.MODEL_LOGIC,
        TaskTier.COMPLEX: config.MODEL_COMPLEX,
        TaskTier.VISION:  config.MODEL_LOGIC,
        TaskTier.AUDIO:   config.MODEL_AUDIO,
    }

    FALLBACK_CHAIN = {
        TaskTier.COMPLEX: TaskTier.LOGIC,
        TaskTier.LOGIC: TaskTier.ROUTING,
        TaskTier.ROUTING: None,
        TaskTier.VISION: TaskTier.LOGIC, # Fallback to logic (no vision support, but graceful degrade if purely text eventually, or fail)
        TaskTier.AUDIO: None
    }

    COOLDOWN_SECONDS = 60

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(LLMOrchestrator, cls).__new__(cls)
            cls._instance._init_once()
        return cls._instance

    def _init_once(self):
        self._api_key = config.GEMINI_API_KEY
        if not self._api_key:
            print("[ORCHESTRATOR] FATAL: GEMINI_API_KEY is empty. Check your .env file.")
        self._client = genai.Client(api_key=self._api_key)
        self._usage = {tier: {"calls": 0, "tokens": 0} for tier in TaskTier}
        self._rate_limited_until = {tier: 0 for tier in TaskTier}
        self._exhausted_keys: set[str] = set()  # keys that hit 429 this session
        self._lock = threading.Lock()  # guards _rotate_key for parallel agent safety

    def _rotate_key(self) -> bool:
        """Attempt to rotate to a fresh Gemini API key. Returns True if successful.

        Thread-safe: guarded by self._lock to prevent concurrent agent threads
        from racing on key rotation.
        """
        with self._lock:
            new_key = config.next_gemini_key()
            if not new_key or new_key in self._exhausted_keys:
                # Try to find any non-exhausted key
                for candidate in config.get_all_gemini_keys():
                    if candidate not in self._exhausted_keys:
                        new_key = candidate
                        break
                else:
                    print("[ORCHESTRATOR] ⚠️ All API keys exhausted.")
                    return False

            self._api_key = new_key
            self._client = genai.Client(api_key=new_key)
            # Reset all tier cooldowns since we have a fresh key
            self._rate_limited_until = {tier: 0 for tier in TaskTier}
            print(f"[ORCHESTRATOR] 🔄 Rotated to new API key (index {config._key_index})")
            return True

    def get_api_key(self) -> str:
        return self._api_key

    def _get_rest_model(self, tier: TaskTier) -> str:
        """Returns the model name string for the given tier."""
        if tier == TaskTier.AUDIO:
            raise ValueError("AUDIO tier requires LiveConnect streaming API, not REST.")
        return self.MODELS.get(tier)

    def _get_live_config(self, tier: TaskTier):
        """Returns the types.LiveConnectConfig for google.genai asynchronous websocket API."""
        if tier != TaskTier.AUDIO:
            raise ValueError("Only AUDIO tier utilizes the live connection config currently.")
        from google.genai import types
        # Usually config details can be added here
        return types.LiveConnectConfig()
    
    def _is_rate_limited(self, tier: TaskTier) -> bool:
        return time.time() < self._rate_limited_until[tier]

    def _mark_rate_limited(self, tier: TaskTier):
        self._rate_limited_until[tier] = time.time() + self.COOLDOWN_SECONDS
        print(f"[JARVIS] [!] Rate limit hit! Tier {tier.name} blocked for {self.COOLDOWN_SECONDS}s.")

    def _is_rate_limit_error(self, error: Exception) -> bool:
        msg = str(error).lower()
        return "429" in msg or "quota" in msg or "resource_exhausted" in msg

    def _track_usage(self, tier: TaskTier, prompt_tokens: int = 0, completion_tokens: int = 0):
        self._usage[tier]["calls"] += 1
        self._usage[tier]["tokens"] += (prompt_tokens + completion_tokens)

    def print_usage(self):
        print("\n--- [ORCHESTRATOR] Cost Tracking ---")
        for tier, data in self._usage.items():
            print(f"{tier.name: <8} : {data['calls']} calls, {data['tokens']} tokens")
        print("------------------------------------\n")

    def generate_content_with_retry(self, tier: TaskTier, prompt: str, system_instruction=None, tools=None, ui=None, speak=None) -> FallbackResult:
        """
        Executes generation using REST API with transparent fallback.
        """
        current_tier = tier

        while current_tier is not None:
            if self._is_rate_limited(current_tier):
                print(f"[JARVIS] [!] FALLBACK MODE: Skipping {current_tier.name} due to active cooldown.")
            else:
                try:
                    gen_config = {}
                    if system_instruction:
                        gen_config["system_instruction"] = system_instruction

                    # Build config for the new SDK
                    config_kwargs = {}
                    if tools:
                        config_kwargs["tools"] = tools

                    response = self._client.models.generate_content(
                        model=self.MODELS[current_tier],
                        contents=prompt,
                        config=types.GenerateContentConfig(
                            **gen_config,
                            **config_kwargs,
                        ) if gen_config or config_kwargs else None,
                    )
                    
                    # Track usage if metadata exists
                    tokens = 0
                    if hasattr(response, "usage_metadata") and response.usage_metadata:
                        meta = response.usage_metadata
                        tokens = getattr(meta, "total_token_count", 0)
                    self._track_usage(current_tier, prompt_tokens=0, completion_tokens=tokens)

                    text = ""
                    if response.text:
                        text = response.text
                    
                    return FallbackResult(text=text, model_used=current_tier)

                except Exception as e:
                    if self._is_rate_limit_error(e):
                        self._mark_rate_limited(current_tier)
                        self._exhausted_keys.add(self._api_key)
                        # Try rotating to a fresh key BEFORE falling down the tier chain
                        if self._rotate_key():
                            print(f"[JARVIS] 🔑 Retrying {current_tier.name} with new API key...")
                            if ui:
                                ui.write_log(f"SYS: Rotated API key, retrying...")
                            continue  # retry SAME tier with new key
                        if ui:
                            ui.write_log(f"FALLBACK MODE: {current_tier.name} rate limited.")
                    else:
                        print(f"[JARVIS] [!] Orchestrator Error on {current_tier.name}: {e}")
                        # Not a rate limit error, might be content block etc. Fallback anyway or fail? Let's fallback.
            
            # Advancing fallback chain
            next_tier = self.FALLBACK_CHAIN.get(current_tier)
            if next_tier:
                msg = f"Falling back from {current_tier.name} to {next_tier.name}..."
                print(f"[JARVIS] {msg}")
                if ui: ui.write_log(f"FALLBACK MODE: {msg}")
                if speak: speak(f"Downgrading to {next_tier.name} mode, sir.")
                
            current_tier = next_tier

        # If we exited loop, ALL falls back are exhausted or tier had None.
        if config.GROQ_API_KEY:
            msg = "Gemini rate limits exhausted, switching to Groq fallback..."
            print(f"[JARVIS] {msg}")
            if ui: ui.write_log(f"SYS: {msg}")
            try:
                import requests
                headers = {
                    "Authorization": f"Bearer {config.GROQ_API_KEY}",
                    "Content-Type": "application/json"
                }
                messages = []
                if system_instruction:
                    messages.append({"role": "system", "content": system_instruction})
                messages.append({"role": "user", "content": prompt})
                
                payload = {
                    "model": "llama-3.3-70b-versatile",
                    "messages": messages,
                    "temperature": 0.7,
                }
                
                resp = requests.post("https://api.groq.com/openai/v1/chat/completions", headers=headers, json=payload, timeout=30)
                resp.raise_for_status()
                text = resp.json()["choices"][0]["message"]["content"]
                return FallbackResult(text=text, model_used="groq-llama3-70b")
            except Exception as e:
                print(f"[JARVIS] [!] Groq Fallback Error: {e}")

        if tier == TaskTier.ROUTING:
            msg = "I'm sorry sir, all logic routing nodes are currently exhausted or blocked."
            if ui: ui.write_log(f"SYS: {msg}")
            if speak: speak(msg)
            return FallbackResult(text="RATE_LIMIT_EXHAUSTED", model_used=None)
        
        # Audio gets special 3x retry instead of fallback, but that usually applies to Live websocket. 
        # For REST fallback exhaustion:
        msg = "I am unable to process the request due to quota limitations across all tiers, sir."
        if ui: ui.write_log(f"SYS: {msg}")
        if speak: speak(msg)
        raise RuntimeError("All fallback tiers exhausted.")

    def _mock_rate_limit(self, tier: TaskTier):
        """For testing purposes only."""
        self._mark_rate_limited(tier)
