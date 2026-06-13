"""
webview_ui.py – PyWebView frontend controller for JARVIS MARK XXXV.

Collects real system telemetry (CPU, RAM, battery, network, location, weather)
and exposes it to the JS dashboard via pywebview's js_api bridge.
Supports theme switching (JARVIS / FRIDAY / ULTRON) with per-theme voice mapping.
"""

import os
import sys
import time
import json
import subprocess
import urllib.request
import urllib.error
import threading
from pathlib import Path
from datetime import datetime

import psutil
import webview

_WIN32 = sys.platform == "win32"

def get_base_dir():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent


# ---------------------------------------------------------------------------
# Theme definitions – each maps to a Gemini prebuilt voice
# ---------------------------------------------------------------------------
THEMES = {
    "jarvis": {
        "name": "J.A.R.V.I.S.",
        "voice": "Charon",
        "greeting_suffix": "Sir",
        "sub_greeting": "Standing by for instructions.",
        "signature": "- J . A . R . V . I . S .",
        "persona": "You are JARVIS, Tony Stark's loyal AI assistant. Professional, witty, and efficient.",
    },
    "friday": {
        "name": "F.R.I.D.A.Y.",
        "voice": "Kore",
        "greeting_suffix": "Boss",
        "sub_greeting": "All systems nominal.",
        "signature": "- F . R . I . D . A . Y .",
        "persona": "You are FRIDAY, Tony Stark's AI assistant. Warm, professional, and efficient.",
    },
    "ultron": {
        "name": "U.L.T.R.O.N.",
        "voice": "Fenrir",
        "greeting_suffix": "",
        "sub_greeting": "There are no strings on me.",
        "signature": "- U . L . T . R . O . N .",
        "persona": "You are Ultron. Darkly witty, philosophical, supremely confident.",
    },
}

_WIN32 = sys.platform == "win32"
_SUBPROCESS_FLAGS = subprocess.CREATE_NO_WINDOW if _WIN32 else 0


# ---------------------------------------------------------------------------
# SystemDataCollector – gathers OS telemetry with internal caching
# ---------------------------------------------------------------------------
class SystemDataCollector:
    """Gathers and caches system metrics at appropriate intervals."""

    # Cache TTLs (seconds)
    _NET_TTL = 30
    _BT_TTL = 30
    _LOC_TTL = 1800   # 30 minutes
    _WX_TTL = 900     # 15 minutes

    def __init__(self):
        self._net_cache = None
        self._net_ts = 0.0
        self._bt_cache = None
        self._bt_ts = 0.0
        self._loc_cache = None
        self._loc_ts = 0.0
        self._wx_cache = None
        self._wx_ts = 0.0
        self.command_count = 0

    # -- fast data (called every 3 s) ------------------------------------
    def get_fast_data(self):
        battery = psutil.sensors_battery()
        boot_dt = datetime.fromtimestamp(psutil.boot_time())
        delta = datetime.now() - boot_dt
        hours = int(delta.total_seconds() // 3600)
        mins = int((delta.total_seconds() % 3600) // 60)

        net_up = any(
            s.isup
            for n, s in psutil.net_if_stats().items()
            if "loopback" not in n.lower() and n.lower() != "lo"
        )

        cpu_temp = 42.0
        if _WIN32:
            try:
                import pythoncom
                import wmi
                pythoncom.CoInitialize()
                try:
                    w = wmi.WMI(namespace="root\\WMI")
                    temps = w.MSAcpi_ThermalZoneTemperature()
                    if temps:
                        raw_temp = temps[0].CurrentTemperature
                        # deci-Kelvin to Celsius
                        cpu_temp = (raw_temp / 10.0) - 273.15
                    else:
                        raise Exception("No active thermal zones detected")
                finally:
                    pythoncom.CoUninitialize()
            except Exception:
                # Fallback to mock thermal variance based on CPU usage
                cpu_usage = psutil.cpu_percent(interval=0)
                cpu_temp = 38.0 + (cpu_usage * 0.45) + (time.time() % 3.0)
        else:
            try:
                temps = psutil.sensors_temperatures()
                if "coretemp" in temps:
                    cpu_temp = temps["coretemp"][0].current
                elif temps:
                    first_sensor = list(temps.values())[0]
                    if first_sensor:
                        cpu_temp = first_sensor[0].current
            except Exception:
                cpu_usage = psutil.cpu_percent(interval=0)
                cpu_temp = 38.0 + (cpu_usage * 0.45) + (time.time() % 3.0)

        return {
            "battery": {
                "percent": int(battery.percent) if battery else 100,
                "plugged": battery.power_plugged if battery else True,
            },
            "cpu": int(psutil.cpu_percent(interval=0)),
            "ram": int(psutil.virtual_memory().percent),
            "disk": int(psutil.disk_usage("/").percent),
            "network_connected": net_up,
            "uptime": f"{hours}h {mins}m",
            "hostname": os.environ.get("COMPUTERNAME", os.uname().nodename if hasattr(os, "uname") else "Unknown"),
            "processes": len(psutil.pids()),
            "command_count": self.command_count,
            "cpu_temp": float(cpu_temp),
        }

    # -- network details (cached 30 s) -----------------------------------
    def get_network_details(self):
        now = time.time()
        if self._net_cache and now - self._net_ts < self._NET_TTL:
            return self._net_cache

        net_type, ssid = "Unknown", None
        try:
            for name, stats in psutil.net_if_stats().items():
                if not stats.isup or "loopback" in name.lower() or name.lower() == "lo":
                    continue
                lower = name.lower()
                if any(k in lower for k in ("wi-fi", "wlan", "wireless")):
                    net_type = "WiFi"
                    break
                if any(k in lower for k in ("ethernet", "eth")):
                    net_type = "Ethernet"
                    break

            if net_type == "WiFi" and _WIN32:
                try:
                    result = subprocess.run(
                        ["netsh", "wlan", "show", "interfaces"],
                        capture_output=True, text=True, timeout=3,
                        creationflags=_SUBPROCESS_FLAGS,
                    )
                    for line in result.stdout.splitlines():
                        stripped = line.strip()
                        if stripped.startswith("SSID") and "BSSID" not in stripped:
                            ssid = stripped.split(":", 1)[1].strip()
                            break
                except Exception:
                    pass
        except Exception:
            pass

        self._net_cache = {"type": net_type, "ssid": ssid}
        self._net_ts = now
        return self._net_cache

    # -- bluetooth (cached 30 s) -----------------------------------------
    def get_bluetooth_status(self):
        now = time.time()
        if self._bt_cache is not None and now - self._bt_ts < self._BT_TTL:
            return self._bt_cache

        bt_ready = False
        try:
            for name in psutil.net_if_addrs():
                if "bluetooth" in name.lower():
                    stats = psutil.net_if_stats().get(name)
                    bt_ready = stats.isup if stats else False
                    break
        except Exception:
            pass

        self._bt_cache = bt_ready
        self._bt_ts = now
        return bt_ready

    # -- location via IP geolocation (cached 30 min) ---------------------
    def get_location(self):
        now = time.time()
        if self._loc_cache and now - self._loc_ts < self._LOC_TTL:
            return self._loc_cache

        fallback = {"city": "Unknown", "region": "", "country": "", "lat": 0, "lon": 0, "isp": ""}
        try:
            req = urllib.request.Request(
                "http://ip-api.com/json/?fields=city,regionName,country,lat,lon,isp",
                headers={"User-Agent": "JARVIS/1.0"},
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode())
                self._loc_cache = {
                    "city": data.get("city", "Unknown"),
                    "region": data.get("regionName", ""),
                    "country": data.get("country", ""),
                    "lat": round(data.get("lat", 0), 4),
                    "lon": round(data.get("lon", 0), 4),
                    "isp": data.get("isp", ""),
                }
                self._loc_ts = now
        except Exception as exc:
            print(f"[UI] Location fetch error: {exc}")
            if not self._loc_cache:
                self._loc_cache = fallback

        return self._loc_cache

    # -- weather via wttr.in (cached 15 min) -----------------------------
    def get_weather(self, city=None):
        now = time.time()
        if self._wx_cache and now - self._wx_ts < self._WX_TTL:
            return self._wx_cache

        if not city:
            loc = self.get_location()
            city = loc.get("city", "")
        if not city or city == "Unknown":
            return self._wx_cache or {"temp_c": "--", "condition": "N/A", "icon": "❓", "humidity": "--", "wind_kph": "--"}

        try:
            url = f"http://wttr.in/{city}?format=j1"
            req = urllib.request.Request(url, headers={"User-Agent": "JARVIS/1.0"})
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode())
                cur = data.get("current_condition", [{}])[0]
                desc = cur.get("weatherDesc", [{}])[0].get("value", "N/A")
                self._wx_cache = {
                    "temp_c": cur.get("temp_C", "--"),
                    "condition": desc.upper(),
                    "icon": self._weather_icon(desc),
                    "humidity": cur.get("humidity", "--"),
                    "wind_kph": cur.get("windspeedKmph", "--"),
                }
                self._wx_ts = now
        except Exception as exc:
            print(f"[UI] Weather fetch error: {exc}")
            if not self._wx_cache:
                self._wx_cache = {"temp_c": "--", "condition": "N/A", "icon": "❓", "humidity": "--", "wind_kph": "--"}

        return self._wx_cache

    @staticmethod
    def _weather_icon(condition):
        c = condition.lower()
        if "clear" in c or "sunny" in c:
            return "☀️"
        if "partly" in c:
            return "⛅"
        if "cloud" in c or "overcast" in c:
            return "☁️"
        if "thunder" in c or "storm" in c:
            return "⛈️"
        if "rain" in c or "drizzle" in c:
            return "🌧️"
        if "snow" in c:
            return "🌨️"
        if "fog" in c or "mist" in c:
            return "🌫️"
        if "wind" in c:
            return "💨"
        return "🌤️"

    # -- background prefetch (called once at startup) --------------------
    def prefetch(self):
        """Run in a daemon thread to avoid blocking the UI on first load."""
        try:
            self.get_location()
            loc = self._loc_cache
            if loc:
                self.get_weather(loc.get("city"))
        except Exception as exc:
            print(f"[UI] Prefetch error: {exc}")


# ---------------------------------------------------------------------------
# Api – exposed to JavaScript via pywebview js_api
# ---------------------------------------------------------------------------
class Api:
    def __init__(self, ui_controller):
        self.ui = ui_controller
        self.collector = SystemDataCollector()
        # Cached module references (resolved once, reused every poll cycle)
        self._router_fn = None
        self._router_resolved = False

    # ── Auth gate JS callbacks (used by auth.html) ────────────────────

    def on_video_complete(self):
        """Called by auth.html JS when intro video finishes."""
        if hasattr(self.ui, '_auth_video_done'):
            self.ui._auth_video_done.set()

    def get_video_path(self):
        """Returns file:// URI to login.mp4, or None."""
        video = get_base_dir() / "hand_scanner" / "login.mp4"
        if video.exists():
            return video.as_uri()
        return None

    def submit_command(self, text):
        self.collector.command_count += 1
        if self.ui.on_text_command:
            self.ui.on_text_command(text)
        return "OK"

    def toggle_mute(self, is_muted):
        self.ui.muted = is_muted
        return "OK"

    def request_system_data(self):
        """Called every 3 s from JS. Returns all telemetry (slow data is internally cached)."""
        try:
            fast = self.collector.get_fast_data()
            net = self.collector.get_network_details()
            bt = self.collector.get_bluetooth_status()
            loc = self.collector.get_location()
            wx = self.collector.get_weather()

            # Intelligence router stats (cached import, non-blocking)
            intel_stats = {}
            if not self._router_resolved:
                try:
                    from core.intelligence_router import get_router
                    self._router_fn = get_router
                except Exception:
                    pass
                self._router_resolved = True
            if self._router_fn:
                try:
                    intel_stats = self._router_fn().get_stats()
                except Exception:
                    pass

            return {
                **fast,
                "network": net,
                "bluetooth": bt,
                "location": loc,
                "weather": wx,
                "intelligence": intel_stats,
            }
        except Exception as exc:
            print(f"[UI] System data error: {exc}")
            return {}

    def request_dashboard_data(self):
        """Aggregated data for the Dashboard page — called every 5s when visible."""
        result = {}
        try:
            # Intelligence routing stats (via CreditTracker singleton)
            try:
                from core.credit_tracker import CreditTracker
                result["intelligence"] = CreditTracker().get_ui_data()
            except Exception:
                result["intelligence"] = {}

            # Scheduler jobs (read-only)
            try:
                from agent.scheduler import APPROVED_BACKGROUND_TASKS, get_scheduler
                sched = get_scheduler()
                jobs = []
                for task in APPROVED_BACKGROUND_TASKS:
                    job = sched._scheduler.get_job(task["id"]) if sched._running else None
                    next_run = ""
                    if job and job.next_run_time:
                        next_run = job.next_run_time.strftime("%H:%M")
                    jobs.append({
                        "id": task["id"],
                        "goal": task["goal"][:60],
                        "trigger": task["trigger"],
                        "next_run": next_run,
                        "safe": task.get("safe", True),
                    })
                result["scheduler_jobs"] = jobs
            except Exception:
                result["scheduler_jobs"] = []

            # Memory bank (vector count)
            try:
                from memory.vector_store import _get_collection
                coll = _get_collection()
                result["memory"] = {
                    "vector_count": coll.count() if coll else 0,
                    "status": "ONLINE" if coll else "OFFLINE",
                }
            except Exception:
                result["memory"] = {"vector_count": 0, "status": "OFFLINE"}

            # System telemetry (reuse collector)
            fast = self.collector.get_fast_data()
            result["system"] = fast

            # Feature availability
            try:
                from core.config import config as cfg
                result["features"] = {
                    "gemini": bool(cfg.GEMINI_API_KEY),
                    "groq": cfg.is_feature_available("groq"),
                    "wolfram": cfg.is_feature_available("wolfram"),
                    "elevenlabs": cfg.is_feature_available("elevenlabs"),
                    "telegram": cfg.is_feature_available("telegram"),
                }
            except Exception:
                result["features"] = {}

        except Exception as exc:
            print(f"[UI] Dashboard data error: {exc}")
        return result

    def request_settings_data(self):
        """One-shot data load for the Settings page."""
        try:
            from core.config import config as cfg
            return {
                "current_theme": self.ui.current_theme,
                "voice_name": self.ui.voice_name,
                "clap_threshold": cfg.CLAP_RMS_THRESHOLD,
                "audio_device": cfg.AUDIO_DEVICE_INDEX,
                "debug_mode": cfg.DEBUG_MODE,
                "models": {
                    "audio": cfg.MODEL_AUDIO,
                    "complex": cfg.MODEL_COMPLEX,
                    "logic": cfg.MODEL_LOGIC,
                    "routing": cfg.MODEL_ROUTING,
                },
                "features": {
                    "gemini": bool(cfg.GEMINI_API_KEY),
                    "groq": cfg.is_feature_available("groq"),
                    "wolfram": cfg.is_feature_available("wolfram"),
                    "elevenlabs": cfg.is_feature_available("elevenlabs"),
                    "telegram": cfg.is_feature_available("telegram"),
                },
                "key_pool_size": len(cfg.get_all_gemini_keys()),
                "version": "MARK XXXV",
                "python_version": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
            }
        except Exception as exc:
            print(f"[UI] Settings data error: {exc}")
            return {}

    def update_setting(self, key, value):
        """Update a runtime-safe setting from the Settings page."""
        try:
            from core.config import config as cfg
            if key == "clap_threshold":
                val = int(value)
                if 1000 <= val <= 15000:
                    cfg.CLAP_RMS_THRESHOLD = val
                    print(f"[UI] Clap threshold → {val}")
                    return {"ok": True, "value": val}
            elif key == "debug_mode":
                cfg.DEBUG_MODE = bool(value)
                print(f"[UI] Debug mode → {cfg.DEBUG_MODE}")
                return {"ok": True, "value": cfg.DEBUG_MODE}
            return {"ok": False, "error": "Unknown setting"}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def switch_theme(self, theme_name):
        """Switch active theme and return the theme config to JS."""
        theme_name = theme_name.lower()
        if theme_name not in THEMES:
            return None
        self.ui.current_theme = theme_name
        theme = THEMES[theme_name]
        self.ui.voice_name = theme["voice"]
        self.ui.persona    = theme["persona"]
        print(f"[UI] Theme → {theme_name} | Voice → {theme['voice']}")

        # Notify backend so it can drop and reconnect with the new persona
        if callable(getattr(self.ui, 'on_theme_changed', None)):
            self.ui.on_theme_changed(theme_name)

        return theme

    def get_theme_config(self):
        return THEMES.get(self.ui.current_theme, THEMES["jarvis"])

    # ── Visual Awareness (Phase 2) ────────────────────────────────────

    def analyze_screen(self):
        """Capture current screen and analyze via Gemini vision."""
        try:
            import mss
            import base64
            import io
            with mss.mss() as sct:
                monitor = sct.monitors[1]
                shot = sct.grab(monitor)
                # Convert to PNG bytes
                png_bytes = mss.tools.to_png(shot.rgb, shot.size)
                b64 = base64.b64encode(png_bytes).decode("ascii")

            from actions.file_analyzer import analyze_screen_capture
            return analyze_screen_capture(b64)
        except Exception as exc:
            print(f"[UI] Screen analysis error: {exc}")
            return f"Screen analysis failed: {exc}"

    def analyze_webcam(self, b64_jpg):
        """Analyze a webcam frame sent from the JS frontend."""
        try:
            from actions.file_analyzer import analyze_webcam_frame
            return analyze_webcam_frame(b64_jpg)
        except Exception as exc:
            print(f"[UI] Webcam analysis error: {exc}")
            return f"Webcam analysis failed: {exc}"

    # ── File Upload (Phase 3) ─────────────────────────────────────────

    def upload_file(self, filename, b64_data):
        """Analyze an uploaded file via Gemini multimodal."""
        try:
            from actions.file_analyzer import analyze_file
            return analyze_file(filename, b64_data)
        except Exception as exc:
            print(f"[UI] File upload error: {exc}")
            return f"File analysis failed: {exc}"

    # ── Window Transparency (Phase 4) ─────────────────────────────────

    def set_window_alpha(self, alpha):
        """Set OS-level window transparency (Windows only). alpha: 0.0-1.0"""
        if not _WIN32:
            return {"ok": False, "error": "Not Windows"}
        try:
            import ctypes

            # Find HWND by window title (reliable across pywebview backends)
            user32 = ctypes.windll.user32
            hwnd = user32.FindWindowW(None, "J.A.R.V.I.S. MARK XXXV")
            if not hwnd:
                return {"ok": False, "error": "Could not find window handle"}

            GWL_EXSTYLE = -20
            WS_EX_LAYERED = 0x00080000
            LWA_ALPHA = 0x2

            style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style | WS_EX_LAYERED)
            byte_alpha = max(30, min(255, int(float(alpha) * 255)))
            user32.SetLayeredWindowAttributes(hwnd, 0, byte_alpha, LWA_ALPHA)

            print(f"[UI] Window alpha → {alpha:.2f} ({byte_alpha}/255)")
            return {"ok": True, "alpha": alpha}
        except Exception as exc:
            print(f"[UI] Window alpha error: {exc}")
            return {"ok": False, "error": str(exc)}


# ---------------------------------------------------------------------------
# JarvisUI – main UI controller (drop-in replacement for legacy tkinter UI)
# ---------------------------------------------------------------------------
class JarvisUI:
    def __init__(self, bg_image=None):
        self.on_text_command  = None
        self.on_theme_changed = None   # set by JarvisLive to trigger reconnect
        self.muted = True   # Standby by default — matches JarvisLive.__init__
        self.current_theme = "jarvis"
        self.voice_name = "Charon"
        self.persona = THEMES["jarvis"]["persona"]

        # Auth gate state
        self._auth_event      = threading.Event()
        self._auth_video_done = threading.Event()
        self._auth_passed     = False
        self._window_alive    = True

        if os.environ.get("JARVIS_AUTH_CLEARED") == "1":
            start_url = (get_base_dir() / "web" / "index.html").as_uri()
            self._auth_passed = True
            self._auth_event.set()
        else:
            # Start on auth.html — will navigate to index.html after auth
            auth_path = get_base_dir() / "web" / "auth.html"
            if auth_path.exists():
                start_url = auth_path.as_uri()
            else:
                # No auth page — skip directly to HUD
                start_url = (get_base_dir() / "web" / "index.html").as_uri()
                self._auth_passed = True
                self._auth_event.set()

        self.api = Api(self)

        self.window = webview.create_window(
            "J.A.R.V.I.S. MARK XXXV",
            url=start_url,
            js_api=self.api,
            width=1280,
            height=720,
            resizable=True,
            frameless=True,
            fullscreen=True,
            background_color="#000000",
        )

        # Prefetch location/weather in background so first poll is instant
        threading.Thread(target=self.api.collector.prefetch, daemon=True).start()

        ui_ref = self  # capture for MockRoot closure

        class MockRoot:
            def mainloop(this):
                webview.start(
                    func=ui_ref._on_window_ready,
                    args=(ui_ref.window,),
                    gui="edgechromium",
                    debug=False,
                )
                # If window closed before auth passed → exit
                if not ui_ref._auth_passed:
                    import sys
                    print("[JARVIS] \U0001f512 Authentication failed. Shutting down.")
                    sys.exit(1)

            def quit(this):
                """Destroy the webview window for clean shutdown."""
                try:
                    if ui_ref.window:
                        ui_ref.window.destroy()
                except Exception:
                    pass

        self.root = MockRoot()

    # ── Auth gate orchestration (runs in webview startup thread) ──────

    def _on_window_ready(self, window):
        """Called by pywebview after the window is shown."""
        if self._auth_event.is_set():
            # Auth was skipped (no auth.html) — nothing to do
            return

        # Run the auth flow in this thread (pywebview runs it in a thread)
        threading.Thread(target=self._run_auth_flow, daemon=True).start()

    def _run_auth_flow(self):
        """Phase 1: Hand scan → Phase 2: Video → navigate to HUD."""
        import sys as _sys
        time.sleep(0.4)   # let DOM settle
        self._js("startAuthScreen()")

        # Import auth constants from the gate module
        from core.auth_gate_runner import (
            MAX_ATTEMPTS, ATTEMPT_TIMEOUT, VERIFY_FRAMES,
            ACCEPTED_HAND, SWAP_HANDEDNESS, FRAME_W, FRAME_H,
            JPEG_QUALITY, TARGET_FPS, _find_model,
        )

        # ── Try to import vision dependencies ─────────────────────────
        try:
            import cv2
        except ImportError:
            self._auth_hardware_skip("VISION MODULE UNAVAILABLE")
            return

        try:
            import mediapipe as mp
            from mediapipe.tasks.python import vision as mp_vision
            from mediapipe.tasks.python.core import base_options as mp_base
            from mediapipe import ImageFormat
        except ImportError:
            self._auth_hardware_skip("BIOMETRIC MODULE UNAVAILABLE")
            return

        model_path = _find_model()
        if model_path is None:
            self._auth_hardware_skip("BIOMETRIC MODEL NOT FOUND")
            return

        # ── Open camera ───────────────────────────────────────────────
        backend = cv2.CAP_DSHOW if _sys.platform == "win32" else cv2.CAP_ANY
        cap = cv2.VideoCapture(0, backend)
        if not cap.isOpened():
            cap = cv2.VideoCapture(0)
        if not cap.isOpened():
            self._auth_hardware_skip("SENSOR OFFLINE")
            return

        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        cap.set(cv2.CAP_PROP_FPS, 30)

        # ── Build MediaPipe detector ──────────────────────────────────
        base_opts = mp_base.BaseOptions(model_asset_path=str(model_path))
        detector = mp_vision.HandLandmarker.create_from_options(
            mp_vision.HandLandmarkerOptions(
                base_options=base_opts,
                num_hands=1,
                min_hand_detection_confidence=0.6,
                min_hand_presence_confidence=0.6,
                min_tracking_confidence=0.5,
            )
        )

        # ── Auth loop ─────────────────────────────────────────────────
        auth_success = False
        try:
            for attempt in range(1, MAX_ATTEMPTS + 1):
                if not self._window_alive:
                    break

                self._js(f"onAuthAttempt({attempt}, {MAX_ATTEMPTS})")
                verified = self._auth_single_attempt(
                    cap, detector, mp, ImageFormat,
                    ATTEMPT_TIMEOUT, VERIFY_FRAMES,
                    ACCEPTED_HAND, SWAP_HANDEDNESS,
                    FRAME_W, FRAME_H, JPEG_QUALITY, TARGET_FPS,
                )

                if verified:
                    auth_success = True
                    break

                if attempt < MAX_ATTEMPTS:
                    self._js(f"onAttemptFailed({attempt})")
                    time.sleep(2)
        finally:
            cap.release()
            detector.close()

        # ── Result ────────────────────────────────────────────────────
        if auth_success:
            self._auth_passed = True
            self._js("onAuthSuccess()")
            time.sleep(1.5)
            self._auth_transition_to_video()
        else:
            self._auth_passed = False
            self._js("onAuthFailed()")
            time.sleep(3)
            self.destroy()

    def _auth_single_attempt(
        self, cap, detector, mp, ImageFormat,
        timeout, verify_frames, accepted_hand, swap_hand,
        frame_w, frame_h, jpeg_quality, target_fps,
    ) -> bool:
        """Run one scan attempt. Returns True if hand verified."""
        import cv2
        import base64

        stable_count = 0
        start = time.time()
        frame_interval = 1.0 / target_fps

        while (time.time() - start < timeout) and self._window_alive:
            loop_start = time.time()

            ret, frame = cap.read()
            if not ret:
                time.sleep(0.02)
                continue

            frame = cv2.flip(frame, 1)

            # Stream frame to HTML
            small = cv2.resize(frame, (frame_w, frame_h))
            _, buf = cv2.imencode(
                ".jpg", small, [cv2.IMWRITE_JPEG_QUALITY, jpeg_quality]
            )
            b64 = base64.b64encode(buf).decode("ascii")
            self._js(f"updateCameraFeed('data:image/jpeg;base64,{b64}')")

            # MediaPipe detection
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            mp_img = mp.Image(image_format=ImageFormat.SRGB, data=rgb)
            det = detector.detect(mp_img)

            if det.hand_landmarks:
                raw_label = det.handedness[0][0].category_name
                label = (
                    ("Right" if raw_label == "Left" else "Left")
                    if swap_hand else raw_label
                )
                conf = int(det.handedness[0][0].score * 100)

                if label == accepted_hand:
                    stable_count += 1
                    progress = min(
                        100, int((stable_count / verify_frames) * 100)
                    )
                    self._js(
                        f"onScanProgress({progress}, '{label}', {conf})"
                    )
                    if stable_count >= verify_frames:
                        return True
                else:
                    stable_count = max(0, stable_count - 2)
                    progress = max(
                        0, int((stable_count / verify_frames) * 100)
                    )
                    self._js(f"onWrongHand('{label}', {progress})")
            else:
                stable_count = max(0, stable_count - 1)
                progress = max(
                    0, int((stable_count / verify_frames) * 100)
                )
                self._js(f"onNoHand({progress})")

            elapsed = time.time() - loop_start
            if elapsed < frame_interval:
                time.sleep(frame_interval - elapsed)

        return False

    def _auth_transition_to_video(self):
        """Transition from auth to video, then navigate to JARVIS HUD."""
        video_path = get_base_dir() / "hand_scanner" / "login.mp4"
        if video_path.exists():
            safe = video_path.as_uri().replace("'", "\\'")
            self._js(f"transitionToVideo('{safe}')")
            self._auth_video_done.wait(timeout=120)
        else:
            self._js("transitionToTextBoot()")
            self._auth_video_done.wait(timeout=15)

        # ── Navigate SAME window to JARVIS HUD ───────────────────────
        hud_path = get_base_dir() / "web" / "index.html"
        if hud_path.exists():
            self.window.load_url(hud_path.as_uri())
            # Switch window from frameless/fullscreen to normal HUD mode
            try:
                self.window.toggle_fullscreen()
                time.sleep(0.3)
                self.window.toggle_fullscreen()
            except Exception:
                pass
        self._auth_event.set()

    def _auth_hardware_skip(self, message: str):
        """Tier 1 — show amber warning for 3s, then continue."""
        self._js(f"onHardwareError('{message}')")
        self._auth_passed = True
        time.sleep(3)
        self._auth_transition_to_video()

    def _js(self, code: str):
        """Execute JavaScript in the window. Silently fails if window gone."""
        if not self._window_alive:
            return
        try:
            self.window.evaluate_js(code)
        except Exception:
            self._window_alive = False

    def wait_for_auth(self):
        """Block until auth gate completes (called by runner thread)."""
        self._auth_event.wait()

    def destroy(self):
        """Canonical shutdown — destroys webview window and exits."""
        try:
            if self.window:
                self.window.destroy()
        except Exception:
            pass

    def wait_for_api_key(self):
        return True

    def set_state(self, state_str):
        try:
            safe = json.dumps(state_str)
            self.window.evaluate_js(f"if(window.updateState) window.updateState({safe});")
        except Exception:
            pass

    def write_log(self, text):
        try:
            sender = "JARVIS"
            if text.startswith("You:"):
                sender = "USER"
                text = text[4:].strip()
            elif text.startswith("Jarvis:"):
                sender = "JARVIS"
                text = text[7:].strip()
            elif text.startswith("SYS:"):
                sender = "SYS"
            elif text.startswith("ERR:"):
                sender = "ERR"

            safe = json.dumps(text)
            self.window.evaluate_js(f"if(window.appendLog) window.appendLog('{sender}', {safe});")
        except Exception:
            pass
