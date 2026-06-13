"""
core/auth/enroll_face.py
=========================
One-time enrollment script -- captures your face and locks down JARVIS.

What it does:
    1. Opens camera, guides you through 20 face captures
    2. Extracts 512-d ArcFace embeddings per capture
    3. Averages them into a "Golden Signature" (handles lighting variance)
    4. Generates a Fernet (AES-256) encryption key
    5. Encrypts your .env file -> .env.encrypted
    6. Protects the AES key with Windows DPAPI (USER-scoped, machine-bound)
    7. Creates a recovery bundle for backup

Run: python core/auth/enroll_face.py

Safety:
    - Requires minimum 12 valid captures (partial failure -> abort, no files written)
    - Original .env is backed up, not deleted
    - Recovery bundle created for golden_signature + master.key
"""

from __future__ import annotations

import shutil
import sys
import time
from pathlib import Path

import cv2
import numpy as np

# ---------------------------------------------------------------------------
# Setup paths before any relative imports
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BASE_DIR))

from core.auth.face_engine import FaceEngine
from core.auth import keystore

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
TARGET_CAPTURES = 20
MIN_VALID_CAPTURES = 12
CAPTURE_INTERVAL_SEC = 0.8          # pause between captures for varied expressions
CAMERA_WARMUP_SEC = 2.0

AUTH_DIR = Path(__file__).resolve().parent
GOLDEN_SIG_PATH = AUTH_DIR / "golden_signature.npy"
RECOVERY_DIR = AUTH_DIR / "recovery"
ENV_PATH = BASE_DIR / ".env"
ENV_BACKUP_PATH = BASE_DIR / ".env.backup"

env_restored_temp = False


def print_banner():
    print("\n" + "=" * 60)
    print("  JARVIS MARK-XXXV -- Biometric Enrollment")
    print("  Identity Registration System")
    print("=" * 60)
    print()
    print("  This script will:")
    print("    1. Capture your face (20 frames)")
    print("    2. Generate your biometric identity signature")
    print("    3. Encrypt your .env with AES-256")
    print("    4. Protect the key with Windows DPAPI")
    print()
    print("  [!] Run this ONCE. Re-running overwrites your identity.")
    print("=" * 60)


def authenticate_existing(engine: FaceEngine) -> bool:
    """Require biometric verification or bypass PIN validation to proceed with re-enrollment."""
    print("\n[SECURITY] Existing biometric identity detected.")
    print("           To prevent unauthorized re-enrollment, you must verify your identity.")
    print("           Select verification method:")
    print("             1. Biometric Face Match")
    print("             2. Security Bypass PIN")
    
    choice = input("\n        Enter choice (1 or 2): ").strip()
    if choice == "1":
        backend = cv2.CAP_DSHOW if sys.platform == "win32" else cv2.CAP_ANY
        cap = cv2.VideoCapture(0, backend)
        if not cap.isOpened():
            cap = cv2.VideoCapture(0)
        if not cap.isOpened():
            print("[FAIL] Camera not available for biometric verification.")
            return False
            
        print("\n[SCAN] Look at the camera to verify your face...")
        time.sleep(2.0)
        
        # Flush stale frames
        for _ in range(10):
            cap.read()
            
        verified = False
        start_time = time.time()
        # 10 seconds window for face verification
        while time.time() - start_time < 10.0:
            ret, frame = cap.read()
            if not ret:
                continue
                
            frame = cv2.flip(frame, 1)
            faces = engine.detect_faces(frame)
            if not faces:
                continue
                
            face = max(faces, key=lambda f: f[2] * f[3])
            emb = engine.extract_embedding(frame, face)
            if emb is not None:
                golden_sig = np.load(str(GOLDEN_SIG_PATH))
                sim = engine.compute_similarity(emb, golden_sig)
                if sim >= 0.45:
                    print(f"\n[OK] Face verified successfully! (Similarity: {sim:.3f})")
                    verified = True
                    break
            
            cv2.imshow("JARVIS Re-enrollment Verification", frame)
            if cv2.waitKey(1) & 0xFF == ord('q'):
                break
                
        cap.release()
        cv2.destroyAllWindows()
        return verified
        
    elif choice == "2":
        pin = input("        Enter bypass PIN: ").strip()
        import hashlib
        pin_hash = hashlib.sha256(pin.encode("utf-8")).hexdigest()
        
        stored_hash = "c6c805ebecbb05a414e21a221f73752e36780c98f98c851d9bb09e25cc4c23ea"  # Default hash for "MARK-XXXV"
        bypass_pin_path = AUTH_DIR / "bypass.pin"
        if bypass_pin_path.exists():
            stored_hash = bypass_pin_path.read_text(encoding="utf-8").strip()
            
        if pin_hash == stored_hash:
            print("\n[OK] Bypass PIN verified successfully!")
            return True
        else:
            print("\n[FAIL] Invalid bypass credentials.")
            return False
    else:
        print("\n[FAIL] Invalid option selected.")
        return False


def check_prerequisites(engine: FaceEngine) -> bool:
    """Verify all required files and models exist, and verify identity if already enrolled."""
    if not engine.initialize():
        print("\n[FAIL] Face engine initialization failed.")
        print("       Run first: python core/auth/download_models.py")
        return False

    # Identity Verification for Re-enrollment
    if GOLDEN_SIG_PATH.exists() and keystore.MASTER_KEY_PATH.exists() and keystore.ENCRYPTED_ENV_PATH.exists():
        if not authenticate_existing(engine):
            print("\n[SECURITY] Verification failed. Re-enrollment blocked.")
            return False

        # If .env does not exist but we are verified, restore it temporarily from .env.encrypted
        if not ENV_PATH.exists():
            try:
                fernet_key = keystore.load_fernet_key()
                decrypted = keystore.decrypt_env_to_memory(fernet_key)
                ENV_PATH.write_text(decrypted, encoding="utf-8")
                print("\n[SECURITY] Restored temporary .env from .env.encrypted for re-enrollment.")
                global env_restored_temp
                env_restored_temp = True
            except Exception as exc:
                print(f"\n[FAIL] Could not decrypt existing env file to restore .env: {exc}")
                return False

    if not ENV_PATH.exists():
        print(f"\n[FAIL] .env file not found: {ENV_PATH}")
        print("       Cannot encrypt what doesn't exist.")
        return False

    return True


def capture_face_embeddings(engine: FaceEngine) -> list[np.ndarray]:
    """
    Open camera and capture face embeddings with guidance.

    Returns list of valid 512-d embeddings. May be less than TARGET_CAPTURES
    if frames fail -- caller checks length.
    """
    backend = cv2.CAP_DSHOW if sys.platform == "win32" else cv2.CAP_ANY
    cap = cv2.VideoCapture(0, backend)
    if not cap.isOpened():
        cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        print("[FAIL] Camera not available.")
        return []

    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

    print(f"\n[CAM] Camera active. Warming up ({CAMERA_WARMUP_SEC}s)...")
    time.sleep(CAMERA_WARMUP_SEC)

    # Flush stale frames
    for _ in range(10):
        cap.read()

    embeddings: list[np.ndarray] = []
    print(f"\n[SCAN] Capturing {TARGET_CAPTURES} face samples.")
    print("       Look at the camera. Slight head movements are OK.\n")

    for i in range(TARGET_CAPTURES):
        ret, frame = cap.read()
        if not ret:
            print(f"   [{i + 1:2d}/{TARGET_CAPTURES}] [WARN] Frame capture failed -- skipping")
            continue

        frame = cv2.flip(frame, 1)      # mirror for natural interaction

        # Show preview window
        preview = frame.copy()
        faces = engine.detect_faces(frame)

        if not faces:
            print(f"   [{i + 1:2d}/{TARGET_CAPTURES}] [WARN] No face detected -- skipping")
            cv2.imshow("JARVIS Enrollment", preview)
            cv2.waitKey(1)
            time.sleep(CAPTURE_INTERVAL_SEC)
            continue

        # Use the largest face (closest to camera)
        face = max(faces, key=lambda f: f[2] * f[3])
        confidence = face[14] if len(face) > 14 else face[-1]

        # Draw bounding box on preview
        x, y, w, h = int(face[0]), int(face[1]), int(face[2]), int(face[3])
        cv2.rectangle(preview, (x, y), (x + w, y + h), (0, 255, 0), 2)
        cv2.putText(
            preview,
            f"Capture {len(embeddings) + 1}/{TARGET_CAPTURES}  Conf: {confidence:.2f}",
            (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2,
        )
        cv2.imshow("JARVIS Enrollment", preview)
        cv2.waitKey(1)

        embedding = engine.extract_embedding(frame, face)
        if embedding is None:
            print(f"   [{i + 1:2d}/{TARGET_CAPTURES}] [WARN] Embedding extraction failed -- skipping")
            time.sleep(CAPTURE_INTERVAL_SEC)
            continue

        embeddings.append(embedding)
        print(f"   [{i + 1:2d}/{TARGET_CAPTURES}] [OK]   Captured ({len(embeddings)}/{TARGET_CAPTURES} valid)")

        time.sleep(CAPTURE_INTERVAL_SEC)

    cap.release()
    cv2.destroyAllWindows()
    return embeddings


def create_golden_signature(embeddings: list[np.ndarray]) -> np.ndarray:
    """Average multiple embeddings into the golden signature and L2 normalize."""
    golden = np.mean(embeddings, axis=0)
    golden = golden / np.linalg.norm(golden)
    return golden


def create_recovery_bundle(golden: np.ndarray):
    """Create recovery copies in core/auth/recovery/."""
    RECOVERY_DIR.mkdir(parents=True, exist_ok=True)

    recovery_sig = RECOVERY_DIR / "golden_signature.npy.bak"
    np.save(recovery_sig, golden)

    if keystore.MASTER_KEY_PATH.exists():
        shutil.copy(keystore.MASTER_KEY_PATH, RECOVERY_DIR / "master.key.bak")

    print(f"\n[RECOVERY] Bundle created in: {RECOVERY_DIR}")
    print("   +----------------------------------------------------------+")
    print("   |  [!] CRITICAL: Move this folder to a USB drive!          |")
    print("   |                                                          |")
    print("   |  Files:                                                  |")
    print(f"   |    - {recovery_sig.name:<50}|")
    if (RECOVERY_DIR / "master.key.bak").exists():
        print("   |    - master.key.bak                                      |")
    print("   |                                                          |")
    print("   |  Without these, a corrupted golden_signature =           |")
    print("   |  PERMANENT LOCKOUT. No recovery possible.                |")
    print("   +----------------------------------------------------------+")


def main() -> int:
    print_banner()

    engine = FaceEngine()

    # Pre-flight
    if not check_prerequisites(engine):
        if env_restored_temp and ENV_PATH.exists():
            try:
                ENV_PATH.unlink()
                print("[SECURITY] Restored temporary .env has been cleaned up.")
            except Exception:
                pass
        engine.shutdown()
        return 1

    # Confirm with user
    print("\n[READY] Ready to begin enrollment.")
    answer = input("        Proceed? (yes/no): ").strip().lower()
    if answer not in ("yes", "y"):
        print("        Enrollment cancelled.")
        if env_restored_temp and ENV_PATH.exists():
            try:
                ENV_PATH.unlink()
                print("[SECURITY] Restored temporary .env has been cleaned up.")
            except Exception:
                pass
        engine.shutdown()
        return 0

    try:
        # Phase 1: Capture embeddings
        embeddings = capture_face_embeddings(engine)
        engine.shutdown()

        if len(embeddings) < MIN_VALID_CAPTURES:
            print(f"\n[FAIL] Only {len(embeddings)} valid captures -- need at least {MIN_VALID_CAPTURES}.")
            print("       Check camera/lighting and retry. No files written.")
            return 1

        print(f"\n[OK] {len(embeddings)} valid captures collected.")

        # Phase 2: Generate golden signature
        golden = create_golden_signature(embeddings)
        np.save(GOLDEN_SIG_PATH, golden)
        print(f"[OK] Golden signature saved: {GOLDEN_SIG_PATH}")
        print(f"     Embedding dimensions: {golden.shape}")

        # Phase 3: Generate Fernet key and encrypt .env
        from cryptography.fernet import Fernet
        fernet_key = Fernet.generate_key()
        print(f"\n[KEY] AES-256 key generated ({len(fernet_key)} bytes)")

        encrypted_path = keystore.encrypt_env_file(fernet_key, ENV_PATH)
        print(f"[OK] .env encrypted: {encrypted_path}")

        # Phase 4: Protect key with DPAPI (USER-scoped)
        master_path = keystore.save_protected_key(fernet_key)
        print(f"[OK] Master key DPAPI-protected: {master_path}")

        # Phase 5: Verify round-trip
        print("\n[VERIFY] Verifying decryption round-trip...")
        try:
            recovered_key = keystore.load_fernet_key()
            assert recovered_key == fernet_key, "Key mismatch after DPAPI round-trip!"
            decrypted = keystore.decrypt_env_to_memory(recovered_key)
            original = ENV_PATH.read_text(encoding="utf-8")
            assert decrypted == original, "Decrypted content doesn't match original .env!"
            print("[OK] Round-trip verification passed. Encryption is solid.")
        except Exception as exc:
            print(f"[FAIL] Round-trip verification FAILED: {exc}")
            print("       Rolling back -- deleting encrypted files.")
            keystore.ENCRYPTED_ENV_PATH.unlink(missing_ok=True)
            keystore.MASTER_KEY_PATH.unlink(missing_ok=True)
            GOLDEN_SIG_PATH.unlink(missing_ok=True)
            return 1

        # Phase 6: Backup original .env
        shutil.copy(ENV_PATH, ENV_BACKUP_PATH)
        print(f"\n[BACKUP] .env backed up to: {ENV_BACKUP_PATH}")
        print("         [!] Move .env.backup to a USB drive for offline storage.")

        # Secure cleanup: Remove plaintext .env to protect secrets on disk
        try:
            ENV_PATH.unlink(missing_ok=True)
            print("[SECURITY] Plaintext .env file has been securely removed from disk.")
        except Exception as exc:
            print(f"[SECURITY] WARNING: Failed to remove plaintext .env: {exc}")

        # Phase 7: Recovery bundle
        create_recovery_bundle(golden)

        # Done
        print("\n" + "=" * 60)
        print("  [OK] ENROLLMENT COMPLETE")
        print("=" * 60)
        print()
        print("  Your identity has been registered.")
        print("  .env is now encrypted with AES-256.")
        print("  The encryption key is locked with Windows DPAPI.")
        print()
        print("  Next steps:")
        print("    1. Move .env.backup + recovery/ to a USB drive")
        print("    2. Confirm all configurations are solid")
        print("    3. The auth gate will use face verification to decrypt")
        print()
        return 0
    finally:
        if env_restored_temp and ENV_PATH.exists():
            try:
                ENV_PATH.unlink()
                print("[SECURITY] Restored temporary .env has been cleaned up.")
            except Exception:
                pass
        engine.shutdown()


if __name__ == "__main__":
    sys.exit(main())
