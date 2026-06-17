"""OpenCV 기반 실제 얼굴 임베더 (로컬 전용).

- YuNet(FaceDetectorYN)으로 얼굴을 검출하고, SFace(FaceRecognizerSF)로 128-d 임베딩을 만든다.
- 무거운 새 의존성 없이 이미 설치된 opencv-python(>=4.8)의 내장 모델을 사용한다.
- 임베딩은 **로컬 전용**이며 외부로 전송하지 않는다(원칙 7).
- 얼굴 미검출 시 빈 리스트([])를 반환한다 — 매칭 불가 신호.

모델 파일(ONNX)은 최초 1회 opencv_zoo에서 FACE_MODEL_DIR로 내려받아 재사용한다.
"""

from __future__ import annotations

import logging
import urllib.request
from pathlib import Path
from typing import Optional

from core.config import FACE_MODEL_DIR

logger = logging.getLogger(__name__)

# opencv_zoo 모델 (소형).
# opencv_zoo는 Git LFS를 사용하므로 raw.githubusercontent.com이 아닌
# media.githubusercontent.com/media(LFS 실제 바이너리)에서 받아야 한다.
_YUNET_NAME = "face_detection_yunet_2023mar.onnx"
_SFACE_NAME = "face_recognition_sface_2021dec.onnx"
_ZOO_BASE = "https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models"
_YUNET_URL = f"{_ZOO_BASE}/face_detection_yunet/{_YUNET_NAME}"
_SFACE_URL = f"{_ZOO_BASE}/face_recognition_sface/{_SFACE_NAME}"

_MIN_MODEL_BYTES = 50 * 1024  # LFS 포인터(수백 바이트) 오다운로드 방지용 최소 크기
_MAX_DET_SIDE = 640  # 얼굴 검출 전 긴 변을 이 픽셀로 축소(초고해상도 사진 검출 안정화)


def _ensure_model(name: str, url: str) -> Path:
    """모델 파일이 없으면 다운로드하고 경로를 반환한다.

    Git LFS 포인터(수백 바이트)가 잘못 받아진 경우를 대비해 최소 크기를 검증하고,
    작으면 삭제 후 재다운로드한다.
    """
    model_dir = Path(FACE_MODEL_DIR)
    model_dir.mkdir(parents=True, exist_ok=True)
    dest = model_dir / name
    if dest.exists() and dest.stat().st_size >= _MIN_MODEL_BYTES:
        return dest
    if dest.exists():
        dest.unlink(missing_ok=True)  # 손상/포인터 파일 제거
    logger.info("얼굴 모델 다운로드: %s → %s", url, dest)
    urllib.request.urlretrieve(url, dest)  # noqa: S310 — 고정 opencv_zoo URL
    if dest.stat().st_size < _MIN_MODEL_BYTES:
        dest.unlink(missing_ok=True)
        raise RuntimeError(f"모델 다운로드 실패(크기 비정상): {name}")
    return dest


class OpenCVFaceEmbedder:
    """YuNet 검출 + SFace 인식으로 얼굴 임베딩을 생성한다."""

    def __init__(self) -> None:
        import cv2  # 지연 import (테스트/목업 환경 영향 최소화)

        self._cv2 = cv2
        yunet_path = str(_ensure_model(_YUNET_NAME, _YUNET_URL))
        sface_path = str(_ensure_model(_SFACE_NAME, _SFACE_URL))
        # 입력 크기는 detect 직전 setInputSize로 갱신한다.
        self._detector = cv2.FaceDetectorYN.create(yunet_path, "", (320, 320))
        self._recognizer = cv2.FaceRecognizerSF.create(sface_path, "")

    def embed(self, image_bytes: bytes) -> list[float]:
        cv2 = self._cv2
        import numpy as np

        if not image_bytes:
            return []
        arr = np.frombuffer(image_bytes, dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if img is None:
            return []

        # 스마트폰 사진은 3000x4000처럼 초고해상도라 원본에선 YuNet 검출이 불안정하다.
        # 긴 변을 _MAX_DET_SIDE로 축소하면 기본 임계값(0.9)에서 안정적으로 검출된다.
        h0, w0 = img.shape[:2]
        scale = _MAX_DET_SIDE / max(h0, w0)
        if scale < 1.0:
            img = cv2.resize(img, (int(w0 * scale), int(h0 * scale)))

        h, w = img.shape[:2]
        self._detector.setInputSize((w, h))
        _retval, faces = self._detector.detect(img)
        if faces is None or len(faces) == 0:
            return []

        # 가장 큰 얼굴 선택 (faces[:, 2], faces[:, 3] = w, h)
        best = max(faces, key=lambda f: float(f[2]) * float(f[3]))
        try:
            aligned = self._recognizer.alignCrop(img, best)
            feat = self._recognizer.feature(aligned)
        except Exception as exc:  # noqa: BLE001 — 정렬/추출 실패는 미검출로 처리
            logger.debug("SFace 임베딩 실패: %s", exc)
            return []
        # feat: shape (1, 128) → 평탄화
        return [float(x) for x in feat.flatten().tolist()]


_singleton: Optional[OpenCVFaceEmbedder] = None


def get_opencv_embedder() -> OpenCVFaceEmbedder:
    """프로세스 전역 싱글톤(모델 로딩 비용 절감)."""
    global _singleton
    if _singleton is None:
        _singleton = OpenCVFaceEmbedder()
    return _singleton
