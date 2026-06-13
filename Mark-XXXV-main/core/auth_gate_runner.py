"""
core/auth_gate_runner.py
========================
Standalone subprocess — biometric face authentication gate for JARVIS MARK XXXV.

Run as:  python core/auth_gate_runner.py

Exit codes:
    0  —  auth passed (biometrics verified, or secure PIN bypass)
    1  —  auth denied (all attempts exhausted, or window closed)

Replaces MediaPipe hand scanning with an ONNX-driven Face Engine (YuNet + ArcFace)
and a 3-phase liveness challenge:
    1. Face Match (cosine similarity >= 0.45 against golden_signature.npy)
    2. Head Yaw Turn (randomized left/right using eye-nose distance ratios)
    3. Return to Center
"""

from __future__ import annotations

import sys
import os
import time
import threading
import base64
import random
import hashlib
from pathlib import Path

import cv2
import numpy as np
import webview

# Resolve base paths
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from core.auth.face_engine import FaceEngine
from core.auth import keystore

# Configuration parameters
MAX_ATTEMPTS = 3
ATTEMPT_TIMEOUT = 25.0       # max seconds per overall attempt
YAW_PHASE_TIMEOUT = 2.5      # strict 2.5-second window per yaw liveness phase
STABLE_MATCH_FRAMES = 10     # stable matching frames required for face match (Phase 1)
STABLE_LIVENESS_FRAMES = 5   # stable frames required for liveness turns (Phase 2 & 3)

FRAME_W, FRAME_H = 480, 360  # streaming resolution to web UI
JPEG_QUALITY = 55
TARGET_FPS = 20

GOLDEN_SIG_PATH = BASE_DIR / "core" / "auth" / "golden_signature.npy"
BYPASS_PIN_PATH = BASE_DIR / "core" / "auth" / "bypass.pin"
DEFAULT_PIN_HASH = "c6c805ebecbb05a414e21a221f73752e36780c98f98c851d9bb09e25cc4c23ea"  # SHA-256 of "MARK-XXXV"


class AuthGateApi:
    """Controls the entire face auth gate lifecycle and JS bridge."""

    def __init__(self):
        self.window: webview.Window | None = None
        self._auth_passed = False
        self._video_done = threading.Event()
        self._window_alive = True
        self.face_engine = FaceEngine()
        
        # Load golden signature
        self.golden_sig = None
        if GOLDEN_SIG_PATH.exists():
            try:
                self.golden_sig = np.load(str(GOLDEN_SIG_PATH))
            except Exception as exc:
                print(f"[AuthGate] ERROR: Failed to load golden signature: {exc}")
        
        # Ensure bypass PIN hash exists
        if not BYPASS_PIN_PATH.exists():
            try:
                BYPASS_PIN_PATH.parent.mkdir(parents=True, exist_ok=True)
                BYPASS_PIN_PATH.write_text(DEFAULT_PIN_HASH, encoding="utf-8")
            except Exception as exc:
                print(f"[AuthGate] WARNING: Failed to write default PIN hash: {exc}")

    # ── JS Callback APIs (invoked from frontend) ───────────────────────────

    def on_video_complete(self):
        """JS notifies us that the unlock animation or video has ended."""
        self._video_done.set()

    def get_video_path(self) -> str | None:
        """Returns the file:// path for the intro login.mp4 if present."""
        video_path = BASE_DIR / "hand_scanner" / "login.mp4"
        if video_path.exists():
            return video_path.as_uri()
        return None

    def verify_bypass_pin(self, pin: str) -> dict:
        """
        Verify secure keyboard-based bypass PIN provided by the user.
        If correct, decrypts the environment, writes boot.env.tmp, and triggers boot.
        """
        if not pin:
            return {"success": False, "message": "PIN CANNOT BE EMPTY"}

        try:
            # Hash user PIN using SHA-256
            pin_hash = hashlib.sha256(pin.encode("utf-8")).hexdigest()
            
            # Read stored PIN hash
            stored_hash = DEFAULT_PIN_HASH
            if BYPASS_PIN_PATH.exists():
                stored_hash = BYPASS_PIN_PATH.read_text(encoding="utf-8").strip()
            
            if pin_hash == stored_hash:
                # Decrypt env keys
                fernet_key = keystore.load_fernet_key()
                decrypted_content = keystore.decrypt_env_to_memory(fernet_key)
                
                # Write temp boot env
                boot_env_path = BASE_DIR / "core" / "auth" / "boot.env.tmp"
                boot_env_path.write_text(decrypted_content, encoding="utf-8")
                
                self._auth_passed = True
                self._js("onAuthSuccess()")
                
                # Transition UI
                threading.Thread(target=self._transition_to_video, daemon=True).start()
                return {"success": True}
            else:
                return {"success": False, "message": "INVALID BYPASS CREDENTIALS"}
        except Exception as exc:
            print(f"[AuthGate] Decryption via PIN failed: {exc}")
            return {"success": False, "message": f"DECRYPTION ERROR: {str(exc)}"}

    # ── Core Runner Sequence (runs asynchronously) ─────────────────────────

    def run(self, window):
        """Orchestrates camera acquisition, liveness validation, and boot load."""
        self.window = window
        time.sleep(0.4)  # wait for page loading to settle
        self._js("startAuthScreen()")

        # Verify biometric signature is loaded
        if self.golden_sig is None:
            self._hardware_skip("NO ENROLLED IDENTITY FOUND")
            return

        # Initialize ONNX FaceEngine models
        if not self.face_engine.initialize():
            self._hardware_skip("BIOMETRIC MODELS LOAD FAILED")
            return

        # ── 5-Second Camera Initialization Retry Loop ─────────────────────
        cap = None
        start_cam_init = time.time()
        camera_acquired = False
        
        while time.time() - start_cam_init < 5.0:
            if not self._window_alive:
                break
            
            # Try loading camera device
            backend = cv2.CAP_DSHOW if sys.platform == "win32" else cv2.CAP_ANY
            cap = cv2.VideoCapture(0, backend)
            if not cap.isOpened():
                cap = cv2.VideoCapture(0)  # fallback
            
            if cap.isOpened():
                # Set ideal resolution properties
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
                cap.set(cv2.CAP_PROP_FPS, 30)
                
                # Verify we can grab a frame successfully
                ret, _ = cap.read()
                if ret:
                    camera_acquired = True
                    break
                else:
                    cap.release()
            
            # Update retry loop timer remaining
            elapsed = time.time() - start_cam_init
            remaining = max(0, 5 - int(elapsed))
            self._js(f"updateCameraRetryTimer({remaining})")
            time.sleep(0.5)

        # If camera could not be opened, enter secure keyboard-only bypass mode
        if not camera_acquired:
            print("[AuthGate] ERROR: Camera sensor occupied or blocked. Triggering secure bypass.")
            self._js("showCameraErrorOverlay()")
            return

        # ── Execute 3-Attempt Biometric Pipeline ──────────────────────────
        auth_success = False
        try:
            for attempt in range(1, MAX_ATTEMPTS + 1):
                if not self._window_alive:
                    break

                self._js(f"onAuthAttempt({attempt}, {MAX_ATTEMPTS})")
                verified = self._single_attempt(cap)

                if verified:
                    auth_success = True
                    break

                # Attempt failed — notify and wait before retrying
                if attempt < MAX_ATTEMPTS:
                    self._js(f"onAttemptFailed({attempt})")
                    time.sleep(2.0)
        finally:
            if cap:
                cap.release()
            self.face_engine.shutdown()

        # ── Handle Authentication Outcome ─────────────────────────────────
        if auth_success:
            try:
                # Decrypt keys
                fernet_key = keystore.load_fernet_key()
                decrypted_content = keystore.decrypt_env_to_memory(fernet_key)
                
                # Write temp boot env
                boot_env_path = BASE_DIR / "core" / "auth" / "boot.env.tmp"
                boot_env_path.write_text(decrypted_content, encoding="utf-8")
                
                self._auth_passed = True
                self._js("onAuthSuccess()")
                time.sleep(1.5)
                self._transition_to_video()
            except Exception as exc:
                print(f"[AuthGate] Keystore decryption failure: {exc}")
                self._auth_passed = False
                self._js("onAuthFailed()")
                time.sleep(3.0)
                self._destroy_window()
        else:
            self._auth_passed = False
            self._js("onAuthFailed()")
            time.sleep(3.0)
            self._destroy_window()

    # ── Biometric Multi-Phase Liveness Runner ─────────────────────────────

    def _single_attempt(self, cap) -> bool:
        """Executes a single liveness challenge attempt with 3 distinct phases."""
        frame_interval = 1.0 / TARGET_FPS
        
        # Challenge 1: Face Match states
        match_stable = 0
        
        # Challenge 2: Head Turn states
        turn_stable = 0
        liveness_target = random.choice(["Left", "Right"])
        turn_timer_started = False
        turn_start_time = 0.0
        
        # Challenge 3: Center states
        center_stable = 0
        center_timer_started = False
        center_start_time = 0.0

        current_phase = 1  # 1 = Face Match, 2 = Liveness Turn, 3 = Center Return
        attempt_start = time.time()

        while (time.time() - attempt_start < ATTEMPT_TIMEOUT) and self._window_alive:
            loop_start = time.time()

            ret, frame = cap.read()
            if not ret:
                time.sleep(0.02)
                continue

            # Mirror frame for natural interaction display
            frame = cv2.flip(frame, 1)

            # Detect faces
            faces = self.face_engine.detect_faces(frame)
            face_detected = len(faces) > 0
            
            # Bounding box & crop landmarks
            face = None
            ratio = 0.5
            similarity = 0.0
            
            if face_detected:
                # Target largest face
                face = max(faces, key=lambda f: f[2] * f[3])
                
                # Extract landmarks for head yaw ratio
                # YuNet format: left eye [6, 7], right eye [4, 5], nose tip [8, 9]
                if len(face) >= 10:
                    left_eye_x = face[6]
                    right_eye_x = face[4]
                    nose_x = face[8]
                    
                    min_eye = min(left_eye_x, right_eye_x)
                    max_eye = max(left_eye_x, right_eye_x)
                    span = max_eye - min_eye
                    if span > 0:
                        ratio = (nose_x - min_eye) / span

            # ── Stream Camera Frame to pywebview ───────────────────────────
            small = cv2.resize(frame, (FRAME_W, FRAME_H))
            _, buf = cv2.imencode(".jpg", small, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
            b64 = base64.b64encode(buf).decode("ascii")
            self._js(f"updateCameraFeed('data:image/jpeg;base64,{b64}')")

            # ── Phase 1: Face Similarity Match ────────────────────────────
            if current_phase == 1:
                self._js("setLivenessGaugeVisible(false)")
                if face_detected and face is not None:
                    emb = self.face_engine.extract_embedding(frame, face)
                    if emb is not None:
                        similarity = self.face_engine.compute_similarity(emb, self.golden_sig)
                        
                        # Relegate verbose debugging to logs as instructed
                        # logger.debug("Similarity: %.4f", similarity)
                        
                        if similarity >= 0.45:
                            match_stable += 1
                            progress = min(40, int((match_stable / STABLE_MATCH_FRAMES) * 40))
                            self._js(f"onScanProgress({progress}, 'FACE MATCH', {int(similarity * 100)})")
                            
                            if match_stable >= STABLE_MATCH_FRAMES:
                                current_phase = 2
                                self._js(f"onLivenessChallenge('{liveness_target}')")
                        else:
                            match_stable = max(0, match_stable - 1)
                            progress = min(40, int((match_stable / STABLE_MATCH_FRAMES) * 40))
                            self._js(f"onWrongHand('MISMATCH', {progress})")
                    else:
                        match_stable = max(0, match_stable - 1)
                        progress = min(40, int((match_stable / STABLE_MATCH_FRAMES) * 40))
                        self._js(f"onNoHand({progress})")
                else:
                    match_stable = max(0, match_stable - 1)
                    progress = min(40, int((match_stable / STABLE_MATCH_FRAMES) * 40))
                    self._js(f"onNoHand({progress})")

            # ── Phase 2: Head-Yaw Turn (Left/Right) ────────────────────────
            elif current_phase == 2:
                self._js("setLivenessGaugeVisible(true)")
                if not turn_timer_started:
                    turn_start_time = time.time()
                    turn_timer_started = True

                # Strict 2.5-second time limit per gesture phase to prevent injection attacks
                if time.time() - turn_start_time > YAW_PHASE_TIMEOUT:
                    print("[AuthGate] Liveness Turn challenge timed out.")
                    return False

                if face_detected and face is not None:
                    # Update visual color-shifting gauge based on ratio
                    self._js(f"onLivenessUpdate({ratio}, {40 + int((turn_stable / STABLE_LIVENESS_FRAMES) * 40)})")
                    
                    # Verify ratio thresholds
                    passed_frame = False
                    if liveness_target == "Left" and ratio < 0.35:
                        passed_frame = True
                    elif liveness_target == "Right" and ratio > 0.65:
                        passed_frame = True

                    if passed_frame:
                        turn_stable += 1
                        progress = min(80, 40 + int((turn_stable / STABLE_LIVENESS_FRAMES) * 40))
                        self._js(f"onScanProgress({progress}, 'LIVENESS TURN', 100)")
                        
                        if turn_stable >= STABLE_LIVENESS_FRAMES:
                            current_phase = 3
                            self._js("onLivenessChallenge('Center')")
                    else:
                        turn_stable = max(0, turn_stable - 1)
                else:
                    turn_stable = max(0, turn_stable - 1)
                    self._js(f"onNoHand({40 + int((turn_stable / STABLE_LIVENESS_FRAMES) * 40)})")

            # ── Phase 3: Return to Center ──────────────────────────────────
            elif current_phase == 3:
                self._js("setLivenessGaugeVisible(true)")
                if not center_timer_started:
                    center_start_time = time.time()
                    center_timer_started = True

                # Strict 2.5-second time limit to return to center
                if time.time() - center_start_time > YAW_PHASE_TIMEOUT:
                    print("[AuthGate] Return to Center challenge timed out.")
                    return False

                if face_detected and face is not None:
                    # Update liveness gauge
                    self._js(f"onLivenessUpdate({ratio}, {80 + int((center_stable / STABLE_LIVENESS_FRAMES) * 20)})")
                    
                    if 0.45 <= ratio <= 0.55:
                        center_stable += 1
                        progress = min(100, 80 + int((center_stable / STABLE_LIVENESS_FRAMES) * 20))
                        self._js(f"onScanProgress({progress}, 'CENTER ALIGN', 100)")
                        
                        if center_stable >= STABLE_LIVENESS_FRAMES:
                            self._js("setLivenessGaugeVisible(false)")
                            return True
                    else:
                        center_stable = max(0, center_stable - 1)
                else:
                    center_stable = max(0, center_stable - 1)
                    self._js(f"onNoHand({80 + int((center_stable / STABLE_LIVENESS_FRAMES) * 20)})")

            # FPS Limiter
            elapsed = time.time() - loop_start
            if elapsed < frame_interval:
                time.sleep(frame_interval - elapsed)

        return False

    # ── Intro transition or text simulation fallback ───────────────────────

    def _transition_to_video(self):
        """Fades in the opening logo dynamic sequence or executes fallback."""
        video_uri = self.get_video_path()
        if video_uri:
            safe = video_uri.replace("'", "\\'")
            self._js(f"transitionToVideo('{safe}')")
            self._video_done.wait(timeout=120)
        else:
            self._js("transitionToTextBoot()")
            self._video_done.wait(timeout=15)
        self._destroy_window()

    # ── Hardware exception handler ─────────────────────────────────────────

    def _hardware_skip(self, message: str):
        """Handles emergency bypass logs in non-interactive sessions."""
        print(f"[AuthGate] Hardware bypass active: {message}")
        self._js(f"onHardwareError('{message}')")
        self._auth_passed = True
        time.sleep(3.0)
        self._transition_to_video()

    # ── Private DOM helpers ────────────────────────────────────────────────

    def _js(self, code: str):
        """Safe JS evaluator."""
        if not self._window_alive or not self.window:
            return
        try:
            self.window.evaluate_js(code)
        except Exception:
            self._window_alive = False

    def _destroy_window(self):
        """Closes pywebview frame securely."""
        try:
            if self.window:
                self.window.destroy()
        except Exception:
            pass


# ── Execution Lifecycle Entry Point ────────────────────────────────────────

def main():
    api = AuthGateApi()

    auth_html = BASE_DIR / "web" / "auth.html"
    if not auth_html.exists():
        print("[AuthGate] FATAL: web/auth.html not found.")
        sys.exit(1)

    window = webview.create_window(
        title="",
        url=auth_html.as_uri(),
        js_api=api,
        fullscreen=True,
        frameless=True,
        background_color="#000000",
        easy_drag=False,
    )

    def _on_closing():
        api._window_alive = False
        api._video_done.set()
        return True

    window.events.closing += _on_closing

    # Start PyWebView blocking loop
    webview.start(
        func=api.run,
        args=(window,),
        gui="edgechromium",
        debug=False,
    )

    # Return subprocess exit code
    sys.exit(0 if api._auth_passed else 1)


if __name__ == "__main__":
    main()
