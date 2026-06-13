"""
hand_scanner/scanner.py
=======================
Biometric hand verification module for JARVIS MARK XXXV.

Public API
----------
    authenticate(timeout, camera_index, silent) -> bool
        Blocks until the correct hand is held steady for VERIFY_DURATION
        seconds, or until timeout expires.  On success plays login.mp4
        inside the scanner window, then returns True.

    enroll_hand(camera_index) -> dict | None
        Captures a hand geometry snapshot for multi-user profiles.
        Returns a serialisable dict or None on failure.

Design notes
------------
- Zero global state; every call is self-contained.
- OpenCV window runs BEFORE the PyWebView window starts, so there is no
  GUI conflict.  The webview stays untouched.
- Cross-platform camera backend selected automatically.
- MediaPipe new API (0.10+) only; matches what main.py already uses.
- Alpha blending vectorised with NumPy (no per-channel Python loops).
- All blocking work happens in the calling thread; use asyncio.to_thread()
  from async contexts (see actions/hand_auth.py).
"""

from __future__ import annotations

import platform
import sys
import time
import threading
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

# ---------------------------------------------------------------------------
# MediaPipe – new task-based API (mediapipe >= 0.10)
# ---------------------------------------------------------------------------
try:
    import mediapipe as mp
    from mediapipe.tasks.python import vision as mp_vision
    from mediapipe.tasks.python.core import base_options as mp_base
    from mediapipe import ImageFormat
    _MP_OK = True
except ImportError:
    _MP_OK = False

# ---------------------------------------------------------------------------
# Configuration — edit these to change scanner behaviour
# ---------------------------------------------------------------------------
_MODEL_PATHS = [
    Path(__file__).parent / "hand_landmarker.task",          # bundled
    Path(__file__).parent.parent / "hand_landmarker.task",   # project root
]

ACCEPTED_HAND: str = "Left"      # "Left" or "Right"
SWAP_HANDEDNESS: bool = True      # mirror flip compensation (webcam feeds)
VERIFY_DURATION: float = 2.0     # seconds hand must stay still
WINDOW_W: int = 900
WINDOW_H: int = 540
WINDOW_TITLE: str = "JARVIS — BIOMETRIC VERIFICATION"

# Colours (BGR)
_C = {
    "primary":  (255, 229,   0),   # cyan-ish in BGR
    "accent":   ( 88, 255,   0),   # green
    "red":      (  0,  51, 255),
    "white":    (255, 255, 255),
    "dim":      (100, 136, 170),
    "bg":       (  8,   4,   0),
}
# Override per-theme if you want — caller can pass theme="friday"/"ultron"
_THEME_COLOURS = {
    "jarvis": {"primary": (255, 229,   0), "accent": ( 88, 255,   0)},
    "friday": {"primary": (  0, 149, 255), "accent": (  0, 215, 255)},
    "ultron": {"primary": (  0,  34, 255), "accent": (  0, 102, 255)},
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_model_path() -> Optional[Path]:
    for p in _MODEL_PATHS:
        if p.exists():
            return p
    return None


def _get_camera_backend() -> int:
    """Select the best OpenCV camera backend for the current OS."""
    os_name = platform.system()
    if os_name == "Darwin":
        return cv2.CAP_AVFOUNDATION
    if os_name == "Windows":
        return cv2.CAP_DSHOW
    return cv2.CAP_ANY


def _open_camera(index: int) -> Optional[cv2.VideoCapture]:
    backend = _get_camera_backend()
    cap = cv2.VideoCapture(index, backend)
    if not cap.isOpened():
        # Fallback: try CAP_ANY
        cap = cv2.VideoCapture(index)
    if not cap.isOpened():
        return None
    cap.set(cv2.CAP_PROP_FRAME_WIDTH,  1280)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT,  720)
    cap.set(cv2.CAP_PROP_FPS, 30)
    return cap


# Path to the login video — sits next to scanner.py inside hand_scanner/
VIDEO_PATH: Path = Path(__file__).parent / "video" / "login.mp4"


def _play_login_video() -> None:
    """
    Play login.mp4 inside the existing JARVIS scanner OpenCV window.

    Behaviour
    ---------
    - Reuses the already-open WINDOW_TITLE window (no new window).
    - Scales each frame to WINDOW_W × WINDOW_H to fill the panel.
    - Overlays a cyan HUD border + "WELCOME BACK, SIR" text so it
      feels like part of the JARVIS interface, not a raw video.
    - Plays at the video's native FPS; skips gracefully if file missing.
    - ESC skips the video early (does NOT cancel auth — auth already passed).
    - Audio: uses ffplay (cross-platform), afplay (macOS), or PowerShell
      (Windows) launched in a daemon thread so video + audio stay in sync.
    """
    if not VIDEO_PATH.exists():
        print(f"[HandScanner] login.mp4 not found at {VIDEO_PATH} — skipping.")
        return

    # ── Audio thread ──────────────────────────────────────────────────
    def _audio():
        import subprocess, shutil
        path = str(VIDEO_PATH)
        try:
            if shutil.which("ffplay"):
                subprocess.Popen(
                    ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", path],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
            elif platform.system() == "Darwin":
                subprocess.Popen(
                    ["afplay", path],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
            elif platform.system() == "Windows":
                ps_cmd = (
                    f'$p = New-Object System.Windows.Media.MediaPlayer;'
                    f'$p.Open("{path}"); $p.Play();'
                    f'Start-Sleep -s 30'
                )
                subprocess.Popen(
                    ["powershell", "-WindowStyle", "Hidden", "-Command", ps_cmd],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
        except Exception as exc:
            print(f"[HandScanner] Audio error: {exc}")

    threading.Thread(target=_audio, daemon=True).start()

    # ── Video playback ────────────────────────────────────────────────
    vcap = cv2.VideoCapture(str(VIDEO_PATH))
    if not vcap.isOpened():
        print("[HandScanner] Could not open login.mp4")
        return

    fps      = vcap.get(cv2.CAP_PROP_FPS) or 30.0
    delay_ms = max(1, int(1000 / fps))
    primary  = _C["primary"]   # cyan
    accent   = _C["accent"]    # green

    print("[HandScanner] ▶  Playing login video...")

    while True:
        ret, vframe = vcap.read()
        if not ret:
            break   # video finished

        # Scale to scanner window size
        vframe = cv2.resize(vframe, (WINDOW_W, WINDOW_H),
                            interpolation=cv2.INTER_LINEAR)

        # ── HUD overlay on video ──────────────────────────────────────
        # Corner brackets
        _draw_corner_brackets(vframe, 4, 4, WINDOW_W-8, WINDOW_H-8,
                              primary, size=28, thick=2)

        # Title
        cv2.rectangle(vframe, (0, 0), (WINDOW_W, 32), (0, 0, 0, 180), -1)
        _put_text(vframe, "J.A.R.V.I.S.  //  IDENTITY CONFIRMED",
                  (WINDOW_W//2, 20), scale=0.44, colour=primary,
                  align="center")

        # Bottom bar
        cv2.rectangle(vframe, (0, WINDOW_H-28), (WINDOW_W, WINDOW_H),
                      (0, 0, 0), -1)
        _put_text(vframe, "WELCOME BACK, SIR  —  ALL SYSTEMS ONLINE",
                  (WINDOW_W//2, WINDOW_H-10), scale=0.44,
                  colour=accent, align="center")

        cv2.imshow(WINDOW_TITLE, vframe)

        key = cv2.waitKey(delay_ms) & 0xFF
        if key == 27:   # ESC skips video but auth is already granted
            print("[HandScanner] Video skipped by user.")
            break

    vcap.release()
    print("[HandScanner] Login video complete.")


def _play_sound(kind: str) -> None:
    """Non-blocking sound effect — daemon thread."""
    def _beep():
        try:
            if platform.system() == "Windows":
                import winsound
                freqs = {"tick": 700, "success": 1100, "fail": 220}
                winsound.Beep(freqs.get(kind, 700), 120)
            elif platform.system() == "Darwin":
                import subprocess
                sounds = {"success": "Glass", "fail": "Basso", "tick": "Tink"}
                subprocess.Popen(
                    ["afplay", f"/System/Library/Sounds/{sounds.get(kind,'Tink')}.aiff"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
        except Exception:
            pass
    threading.Thread(target=_beep, daemon=True).start()


# ---------------------------------------------------------------------------
# Drawing helpers
# ---------------------------------------------------------------------------

# MediaPipe hand connection pairs (21 landmarks)
_CONNECTIONS = [
    (0,1),(1,2),(2,3),(3,4),
    (0,5),(5,6),(6,7),(7,8),
    (0,9),(9,10),(10,11),(11,12),
    (0,13),(13,14),(14,15),(15,16),
    (0,17),(17,18),(18,19),(19,20),
    (5,9),(9,13),(13,17),
]


def _lm_to_px(lm, w: int, h: int) -> list[tuple[int, int]]:
    return [(int(p.x * w), int(p.y * h)) for p in lm]


def _draw_skeleton(frame, pts: list[tuple[int,int]],
                   primary, accent, alpha: float = 1.0) -> None:
    """Draw hand skeleton + glow dots onto frame (in-place)."""
    overlay = frame.copy()

    # Connections
    for a, b in _CONNECTIONS:
        cv2.line(overlay, pts[a], pts[b], primary, 2, cv2.LINE_AA)

    # Dots
    for i, p in enumerate(pts):
        cv2.circle(overlay, p, 8,  accent, -1, cv2.LINE_AA)
        cv2.circle(overlay, p, 4,  (255,255,255), -1, cv2.LINE_AA)

    # Vectorised alpha blend
    a = np.float32(alpha)
    frame[:] = (a * overlay + (1.0 - a) * frame).astype(np.uint8)


def _draw_corner_brackets(frame, x, y, w, h, colour, size=24, thick=2):
    tl, tr = (x, y), (x+w, y)
    bl, br = (x, y+h), (x+w, y+h)
    for pt, dx, dy in [(tl,1,1),(tr,-1,1),(bl,1,-1),(br,-1,-1)]:
        cv2.line(frame, pt, (pt[0]+dx*size, pt[1]),           colour, thick)
        cv2.line(frame, pt, (pt[0],          pt[1]+dy*size),  colour, thick)


def _put_text(frame, text, pos, scale=0.45, colour=(255,229,0),
              thickness=1, align="left"):
    font = cv2.FONT_HERSHEY_DUPLEX
    (tw, th), _ = cv2.getTextSize(text, font, scale, thickness)
    x, y = pos
    if align == "center":
        x -= tw // 2
    cv2.putText(frame, text, (x, y), font, scale, colour, thickness,
                cv2.LINE_AA)


def _draw_progress_arc(frame, cx, cy, r, pct, colour, thickness=3):
    """Draw a progress arc from 12 o'clock, anti-aliased."""
    if pct <= 0:
        return
    end_angle = -90 + 360 * pct
    cv2.ellipse(frame, (cx, cy), (r, r), 0, -90, end_angle,
                colour, thickness, cv2.LINE_AA)


def _draw_hud(frame, state: dict, primary, accent, red) -> None:
    """
    Render the full HUD on top of the frame.
    state keys: phase, hand_label, conf, progress, wrong_hand, msg
    """
    h, w = frame.shape[:2]
    fw, fh = WINDOW_W, WINDOW_H

    # ── scan-line ──────────────────────────────────────────────────────
    t = time.time()
    sl_y = int((t % 2.0) / 2.0 * fh)
    sl_overlay = frame.copy()
    cv2.line(sl_overlay, (0, sl_y), (fw, sl_y), primary, 1)
    frame[:] = (0.4 * sl_overlay + 0.6 * frame).astype(np.uint8)

    # ── corner brackets around viewport ───────────────────────────────
    _draw_corner_brackets(frame, 4, 4, fw-8, fh-8, primary)

    # ── title bar ─────────────────────────────────────────────────────
    cv2.rectangle(frame, (0, 0), (fw, 32), (0, 0, 0), -1)
    _put_text(frame, "JARVIS  //  BIOMETRIC VERIFICATION",
              (fw//2, 20), scale=0.45, colour=primary, align="center")

    # ── status message ────────────────────────────────────────────────
    msg_colour = accent if state["phase"] == "verifying" else \
                 red    if state["wrong_hand"]           else primary
    _put_text(frame, state["msg"],
              (fw//2, fh - 14), scale=0.46, colour=msg_colour, align="center")

    # ── biometric readout (top-right) ─────────────────────────────────
    readout = [
        f"HAND   : {state['hand_label'] or '--'}",
        f"CONF   : {state['conf']}",
        f"POINTS : {state['pts']}",
        f"STATUS : {state['phase'].upper()}",
    ]
    for i, line in enumerate(readout):
        _put_text(frame, line, (fw - 210, 52 + i * 18),
                  scale=0.38, colour=(170, 200, 120))

    # ── progress arc (bottom-right) ───────────────────────────────────
    cx, cy, r = fw - 56, fh - 56, 36
    cv2.circle(frame, (cx, cy), r, (40, 40, 40), 2)
    _draw_progress_arc(frame, cx, cy, r, state["progress"], accent)
    pct_str = f"{int(state['progress']*100)}%"
    _put_text(frame, pct_str, (cx, cy + 5),
              scale=0.44, colour=accent, align="center")

    # ── wrong-hand red fill bar ───────────────────────────────────────
    if state["wrong_hand"]:
        bar_overlay = frame.copy()
        cv2.rectangle(bar_overlay, (0, fh-28), (fw, fh), (0, 0, 180), -1)
        frame[:] = (0.35 * bar_overlay + 0.65 * frame).astype(np.uint8)
        _put_text(frame, f"WRONG HAND  —  PRESENT {ACCEPTED_HAND.upper()} HAND",
                  (fw//2, fh - 10), scale=0.44, colour=(0, 100, 255),
                  align="center")

    # ── ESC hint ─────────────────────────────────────────────────────
    _put_text(frame, "ESC  abort", (10, fh - 10),
              scale=0.34, colour=(80, 100, 100))


# ---------------------------------------------------------------------------
# Core verification loop
# ---------------------------------------------------------------------------

def authenticate(
    timeout: float = 20.0,
    camera_index: int = 0,
    silent: bool = False,
    theme: str = "jarvis",
) -> bool:
    """
    Run the hand-scanner verification loop.

    Parameters
    ----------
    timeout       : max seconds to wait before returning False
    camera_index  : which camera to open (default 0)
    silent        : if True, no OpenCV window is shown (headless mode)
    theme         : "jarvis" | "friday" | "ultron" — changes HUD colours

    Returns
    -------
    True   → hand verified, Jarvis may boot
    False  → timed out, wrong hand, or user pressed ESC
    """
    if not _MP_OK:
        print("[HandScanner] ERROR: mediapipe not installed. "
              "Run: pip install mediapipe>=0.10.0")
        return False

    model_path = _get_model_path()
    if model_path is None:
        print("[HandScanner] ERROR: hand_landmarker.task not found.\n"
              f"  Searched: {[str(p) for p in _MODEL_PATHS]}")
        return False

    colours = _THEME_COLOURS.get(theme, _THEME_COLOURS["jarvis"])
    primary = colours["primary"]
    accent  = colours["accent"]
    red     = _C["red"]

    # ── Build MediaPipe detector ───────────────────────────────────────
    base_opts = mp_base.BaseOptions(model_asset_path=str(model_path))
    detector  = mp_vision.HandLandmarker.create_from_options(
        mp_vision.HandLandmarkerOptions(
            base_options=base_opts,
            num_hands=1,
            min_hand_detection_confidence=0.6,
            min_hand_presence_confidence=0.6,
            min_tracking_confidence=0.5,
        )
    )

    cap = _open_camera(camera_index)
    if cap is None:
        print(f"[HandScanner] ERROR: Cannot open camera index {camera_index}")
        return False

    if not silent:
        cv2.namedWindow(WINDOW_TITLE, cv2.WINDOW_NORMAL)
        cv2.resizeWindow(WINDOW_TITLE, WINDOW_W, WINDOW_H)

    # ── State ─────────────────────────────────────────────────────────
    phase        = "waiting"   # waiting → detected → verifying → done
    verif_start  = 0.0
    wrong_hand   = False
    hand_label   = ""
    conf_str     = "--"
    pts_count    = 0
    last_tick    = 0.0
    deadline     = time.time() + timeout
    result       = False

    print(f"[HandScanner] Ready — present {ACCEPTED_HAND} hand "
          f"(timeout {timeout}s)")

    try:
        while time.time() < deadline:
            ret, frame = cap.read()
            if not ret:
                time.sleep(0.02)
                continue

            frame = cv2.flip(frame, 1)
            frame = cv2.resize(frame, (WINDOW_W, WINDOW_H))

            # ── MediaPipe detection ───────────────────────────────────
            rgb    = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            mp_img = mp.Image(image_format=ImageFormat.SRGB, data=rgb)
            det    = detector.detect(mp_img)

            wrong_hand = False

            if det.hand_landmarks:
                raw_label = det.handedness[0][0].category_name
                # Compensate for mirror flip
                label = ("Right" if raw_label == "Left" else "Left") \
                        if SWAP_HANDEDNESS else raw_label

                hand_label = label
                conf_val   = det.handedness[0][0].score
                conf_str   = f"{int(conf_val*100)}%"
                pts        = _lm_to_px(det.hand_landmarks[0],
                                       WINDOW_W, WINDOW_H)
                pts_count  = len(pts)

                # Draw skeleton
                _draw_skeleton(frame, pts, primary, accent,
                               alpha=0.9 if label == ACCEPTED_HAND else 0.5)

                if label == ACCEPTED_HAND:
                    wrong_hand = False
                    if phase == "waiting":
                        phase = "detected"
                        print("[HandScanner] Hand detected — hold position...")

                    if phase == "detected":
                        phase       = "verifying"
                        verif_start = time.time()
                        print("[HandScanner] Verifying...")

                    if phase == "verifying":
                        elapsed  = time.time() - verif_start
                        progress = min(elapsed / VERIFY_DURATION, 1.0)

                        # tick sound every ~0.5 s
                        if elapsed - last_tick > 0.5:
                            _play_sound("tick")
                            last_tick = elapsed

                        if progress >= 1.0:
                            result = True
                            phase  = "done"
                            _play_sound("success")
                            print("[HandScanner] SUCCESS: Verified - access granted")
                            break
                else:
                    wrong_hand  = True
                    phase       = "waiting"   # reset; wrong hand broke streak
                    verif_start = 0.0
                    _play_sound("fail")

            else:
                # No hand → reset to waiting
                if phase in ("detected", "verifying"):
                    phase       = "waiting"
                    verif_start = 0.0
                hand_label = ""
                conf_str   = "--"
                pts_count  = 0

            # ── Progress for HUD ──────────────────────────────────────
            progress = 0.0
            if phase == "verifying" and verif_start:
                progress = min((time.time() - verif_start) / VERIFY_DURATION,
                               1.0)

            # ── HUD ───────────────────────────────────────────────────
            if not silent:
                msg_map = {
                    "waiting":   f"PRESENT {ACCEPTED_HAND.upper()} HAND TO SCANNER",
                    "detected":  "HAND DETECTED — HOLD STEADY",
                    "verifying": "VERIFYING BIOMETRIC SIGNATURE...",
                    "done":      "ACCESS GRANTED",
                }
                state = {
                    "phase":      phase,
                    "hand_label": hand_label,
                    "conf":       conf_str,
                    "pts":        str(pts_count) if pts_count else "--",
                    "progress":   progress,
                    "wrong_hand": wrong_hand,
                    "msg":        msg_map.get(phase, ""),
                }
                _draw_hud(frame, state, primary, accent, red)

                # ── SUCCESS flash → login video ───────────────────────
                if phase == "done":
                    # 1. Green "ACCESS GRANTED" flash for 1 second
                    flash = frame.copy()
                    cv2.rectangle(flash, (0, 0), (WINDOW_W, WINDOW_H),
                                  accent, -1)
                    frame = cv2.addWeighted(flash, 0.15, frame, 0.85, 0)
                    _draw_corner_brackets(frame, 4, 4,
                                         WINDOW_W-8, WINDOW_H-8,
                                         accent, size=32, thick=3)
                    _put_text(frame, "ACCESS GRANTED",
                              (WINDOW_W//2, WINDOW_H//2 - 18),
                              scale=1.1, colour=accent,
                              thickness=2, align="center")
                    _put_text(frame, "IDENTITY CONFIRMED",
                              (WINDOW_W//2, WINDOW_H//2 + 18),
                              scale=0.5, colour=primary,
                              align="center")
                    cv2.imshow(WINDOW_TITLE, frame)
                    cv2.waitKey(1000)   # 1 s green flash

                    # 2. Play login.mp4 inside the same window
                    _play_login_video()

                    # 3. Brief black fade before handing off to Jarvis
                    black = np.zeros((WINDOW_H, WINDOW_W, 3), dtype=np.uint8)
                    _put_text(black, "BOOTING JARVIS...",
                              (WINDOW_W//2, WINDOW_H//2),
                              scale=0.55, colour=primary, align="center")
                    cv2.imshow(WINDOW_TITLE, black)
                    cv2.waitKey(800)
                    break

                cv2.imshow(WINDOW_TITLE, frame)
                key = cv2.waitKey(5) & 0xFF
                if key == 27:   # ESC
                    print("[HandScanner] Aborted by user (ESC)")
                    break

    finally:
        cap.release()
        if not silent:
            cv2.destroyAllWindows()
        detector.close()

    return result


# ---------------------------------------------------------------------------
# Enrollment helper (for future multi-user Mode D)
# ---------------------------------------------------------------------------

def enroll_hand(camera_index: int = 0) -> Optional[dict]:
    """
    Capture a hand geometry snapshot for profile storage.
    Returns a dict with normalised landmark coordinates, or None on failure.

    Usage:
        profile = enroll_hand()
        if profile:
            json.dump(profile, open("memory/hand_profiles.json", "w"))
    """
    if not _MP_OK:
        return None

    model_path = _get_model_path()
    if model_path is None:
        return None

    base_opts = mp_base.BaseOptions(model_asset_path=str(model_path))
    detector  = mp_vision.HandLandmarker.create_from_options(
        mp_vision.HandLandmarkerOptions(
            base_options=base_opts, num_hands=1)
    )
    cap = _open_camera(camera_index)
    if cap is None:
        return None

    snapshot = None
    print("[HandScanner] Enrollment mode — present hand and press SPACE")
    cv2.namedWindow("JARVIS — Enrollment", cv2.WINDOW_NORMAL)
    cv2.resizeWindow("JARVIS — Enrollment", WINDOW_W, WINDOW_H)

    try:
        while True:
            ret, frame = cap.read()
            if not ret:
                continue
            frame = cv2.flip(frame, 1)
            frame = cv2.resize(frame, (WINDOW_W, WINDOW_H))
            rgb   = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            det   = detector.detect(
                mp.Image(image_format=ImageFormat.SRGB, data=rgb)
            )
            if det.hand_landmarks:
                pts = [(lm.x, lm.y, lm.z)
                       for lm in det.hand_landmarks[0]]
                _draw_skeleton(
                    frame,
                    _lm_to_px(det.hand_landmarks[0], WINDOW_W, WINDOW_H),
                    _C["primary"], _C["accent"],
                )
                _put_text(frame, "SPACE = capture  |  ESC = cancel",
                          (WINDOW_W//2, WINDOW_H - 14),
                          scale=0.42, align="center")
                cv2.imshow("JARVIS — Enrollment", frame)
                key = cv2.waitKey(5) & 0xFF
                if key == 32:   # SPACE
                    snapshot = {
                        "landmarks": pts,
                        "hand": det.handedness[0][0].category_name,
                        "enrolled_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    }
                    print("[HandScanner] SUCCESS: Hand enrolled")
                    break
                if key == 27:
                    break
            else:
                _put_text(frame, "PRESENT HAND TO ENROLL",
                          (WINDOW_W//2, WINDOW_H//2),
                          scale=0.55, align="center")
                cv2.imshow("JARVIS — Enrollment", frame)
                if cv2.waitKey(5) & 0xFF == 27:
                    break
    finally:
        cap.release()
        cv2.destroyAllWindows()
        detector.close()

    return snapshot


# ---------------------------------------------------------------------------
# Quick standalone test:  python -m hand_scanner.scanner
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    print("Running standalone scanner test...")
    ok = authenticate(timeout=30, theme="jarvis")
    print("Result:", "GRANTED" if ok else "DENIED")
    sys.exit(0 if ok else 1)
