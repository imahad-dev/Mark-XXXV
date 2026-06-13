"""
core/auth/face_engine.py
========================
Lightweight face recognition engine using ONNX Runtime.

Architecture:
    - Detection:  OpenCV DNN (Yunet or Haar cascade fallback)
    - Embedding:  ArcFace ONNX model via onnxruntime (512-d vector)
    - Matching:   Cosine similarity against stored golden signature

Zero TensorFlow. Zero dlib. Zero C++ compilation.
Pure Python + onnxruntime + opencv-python.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Optional

import cv2
import numpy as np
import onnxruntime as ort

logger = logging.getLogger("jarvis.auth.face_engine")

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
AUTH_DIR = Path(__file__).resolve().parent
MODELS_DIR = AUTH_DIR / "models"

ARCFACE_MODEL_PATH = MODELS_DIR / "arcface_mobilefacenet.onnx"
YUNET_MODEL_PATH = MODELS_DIR / "face_detection_yunet_2023mar.onnx"

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
ARCFACE_INPUT_SIZE = (112, 112)
SIMILARITY_THRESHOLD = 0.45       # cosine similarity — ArcFace empirical sweet spot
MIN_FACE_CONFIDENCE = 0.7


class FaceEngine:
    """Detects faces and extracts 512-d ArcFace embeddings via ONNX Runtime."""

    def __init__(self):
        self._detector: Optional[cv2.FaceDetectorYN] = None
        self._recognizer: Optional[ort.InferenceSession] = None
        self._input_name: str = ""
        self._initialized = False

    def initialize(self) -> bool:
        """Load models. Returns True if successful, False otherwise."""
        if self._initialized:
            return True

        if not ARCFACE_MODEL_PATH.exists():
            logger.error("ArcFace model not found: %s", ARCFACE_MODEL_PATH)
            logger.error("Run: python core/auth/download_models.py")
            return False

        if not YUNET_MODEL_PATH.exists():
            logger.error("YuNet model not found: %s", YUNET_MODEL_PATH)
            logger.error("Run: python core/auth/download_models.py")
            return False

        try:
            self._detector = cv2.FaceDetectorYN.create(
                model=str(YUNET_MODEL_PATH),
                config="",
                input_size=(640, 480),
                score_threshold=MIN_FACE_CONFIDENCE,
                nms_threshold=0.3,
                top_k=5000,
            )
            logger.info("YuNet face detector loaded.")
        except Exception as exc:
            logger.error("Failed to load YuNet detector: %s", exc)
            return False

        try:
            so = ort.SessionOptions()
            so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
            so.intra_op_num_threads = 2
            so.log_severity_level = 3       # warnings only

            self._recognizer = ort.InferenceSession(
                str(ARCFACE_MODEL_PATH),
                sess_options=so,
                providers=["CPUExecutionProvider"],
            )
            self._input_name = self._recognizer.get_inputs()[0].name
            logger.info("ArcFace recognizer loaded (ONNX Runtime).")
        except Exception as exc:
            logger.error("Failed to load ArcFace model: %s", exc)
            return False

        self._initialized = True
        return True

    def detect_faces(self, frame: np.ndarray) -> list[np.ndarray]:
        """
        Detect faces in a BGR frame.

        Returns:
            List of face bounding boxes as [x, y, w, h, ...landmarks, confidence].
            Empty list if no faces detected.
        """
        if not self._initialized or self._detector is None:
            return []

        h, w = frame.shape[:2]
        self._detector.setInputSize((w, h))
        _, faces = self._detector.detect(frame)

        if faces is None:
            return []

        return [faces[i] for i in range(faces.shape[0])]

    def extract_embedding(self, frame: np.ndarray, face_box: np.ndarray) -> Optional[np.ndarray]:
        """
        Extract 512-d ArcFace embedding from a detected face region.

        Args:
            frame:    BGR image (full frame).
            face_box: Detection array from detect_faces().

        Returns:
            Normalized 512-d embedding vector, or None on failure.
        """
        if not self._initialized or self._recognizer is None:
            return None

        try:
            x, y, w, h = int(face_box[0]), int(face_box[1]), int(face_box[2]), int(face_box[3])

            # Expand crop by 20% for better alignment
            pad_w, pad_h = int(w * 0.2), int(h * 0.2)
            img_h, img_w = frame.shape[:2]
            x1 = max(0, x - pad_w)
            y1 = max(0, y - pad_h)
            x2 = min(img_w, x + w + pad_w)
            y2 = min(img_h, y + h + pad_h)

            face_crop = frame[y1:y2, x1:x2]
            if face_crop.size == 0:
                return None

            # Resize to ArcFace input dimensions
            face_resized = cv2.resize(face_crop, ARCFACE_INPUT_SIZE)

            # BGR → RGB, normalize to [-1, 1], transpose to NCHW
            face_rgb = cv2.cvtColor(face_resized, cv2.COLOR_BGR2RGB)
            face_norm = (face_rgb.astype(np.float32) / 127.5) - 1.0
            face_input = np.transpose(face_norm, (2, 0, 1))   # CHW
            face_input = np.expand_dims(face_input, axis=0)    # NCHW

            # Run inference
            outputs = self._recognizer.run(None, {self._input_name: face_input})
            embedding = outputs[0].flatten()

            # L2 normalize
            norm = np.linalg.norm(embedding)
            if norm < 1e-10:
                return None
            embedding = embedding / norm

            return embedding

        except Exception as exc:
            logger.warning("Embedding extraction failed: %s", exc)
            return None

    @staticmethod
    def compute_similarity(emb_a: np.ndarray, emb_b: np.ndarray) -> float:
        """Cosine similarity between two L2-normalized embeddings."""
        return float(np.dot(emb_a, emb_b))

    @staticmethod
    def is_match(emb_a: np.ndarray, emb_b: np.ndarray, threshold: float = SIMILARITY_THRESHOLD) -> bool:
        """Check if two embeddings belong to the same person."""
        similarity = FaceEngine.compute_similarity(emb_a, emb_b)
        return similarity >= threshold

    def shutdown(self) -> None:
        """Release resources."""
        self._detector = None
        self._recognizer = None
        self._initialized = False
        logger.info("FaceEngine shut down.")
