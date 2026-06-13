"""
core/auth/download_models.py
=============================
Downloads the required ONNX models for the face recognition engine.

Models:
    1. YuNet face detector    (~220 KB)  — OpenCV's recommended DNN face detector
    2. ArcFace MobileFaceNet  (~4.7 MB)  — Lightweight face embedding model

Both models are served from OpenCV's official GitHub and ONNX Model Zoo.
Run once: python core/auth/download_models.py
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import requests

MODELS_DIR = Path(__file__).resolve().parent / "models"

MODELS = [
    {
        "name": "face_detection_yunet_2023mar.onnx",
        "url": "https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx",
        "sha256": None,                 # skip hash check — official source
        "description": "YuNet face detector (OpenCV Zoo)",
    },
    {
        "name": "arcface_mobilefacenet.onnx",
        "url": "https://github.com/onnx/models/raw/main/validated/vision/body_analysis/arcface/model/arcfaceresnet100-11-int8.onnx",
        "sha256": None,
        "description": "ArcFace ResNet100 INT8 (ONNX Model Zoo)",
    },
]


def download_file(url: str, dest: Path, description: str) -> bool:
    """Download a file with progress indication."""
    print(f"\n[Download] {description}")
    print(f"           {url}")
    print(f"        -> {dest}")

    try:
        resp = requests.get(url, stream=True, timeout=60, allow_redirects=True)
        resp.raise_for_status()
    except requests.RequestException as exc:
        print(f"  ❌ Download failed: {exc}")
        return False

    total = int(resp.headers.get("content-length", 0))
    downloaded = 0

    with open(dest, "wb") as f:
        for chunk in resp.iter_content(chunk_size=8192):
            f.write(chunk)
            downloaded += len(chunk)
            if total > 0:
                pct = int(downloaded / total * 100)
                bar = "#" * (pct // 5) + "." * (20 - pct // 5)
                print(f"\r  [{bar}] {pct}%  ({downloaded:,}/{total:,} bytes)", end="", flush=True)

    print(f"\n  ✅ Saved ({dest.stat().st_size:,} bytes)")
    return True


def verify_hash(filepath: Path, expected_sha256: str | None) -> bool:
    """Verify SHA-256 hash if provided."""
    if expected_sha256 is None:
        return True

    sha = hashlib.sha256()
    with open(filepath, "rb") as f:
        for block in iter(lambda: f.read(8192), b""):
            sha.update(block)

    actual = sha.hexdigest()
    if actual != expected_sha256:
        print(f"  ❌ Hash mismatch! Expected: {expected_sha256[:16]}...")
        print(f"                    Got:      {actual[:16]}...")
        return False

    print(f"  ✅ Hash verified: {actual[:16]}...")
    return True


def main() -> int:
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    print("=" * 60)
    print("  JARVIS — Face Recognition Model Downloader")
    print("=" * 60)

    all_ok = True
    for model in MODELS:
        dest = MODELS_DIR / model["name"]

        if dest.exists():
            print(f"\n[Skip] {model['name']} already exists ({dest.stat().st_size:,} bytes)")
            continue

        ok = download_file(model["url"], dest, model["description"])
        if not ok:
            all_ok = False
            continue

        if not verify_hash(dest, model["sha256"]):
            dest.unlink(missing_ok=True)
            all_ok = False

    print("\n" + "=" * 60)
    if all_ok:
        print("  All models ready. You can now run: python core/auth/enroll_face.py")
    else:
        print("  ⚠️  Some downloads failed. Please retry or download manually.")
    print("=" * 60)

    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
