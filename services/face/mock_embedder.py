"""결정적 Mock 얼굴 임베더 (무거운 CV 의존성 없음).

같은 이미지 바이트 → 같은 임베딩(유사도 1.0). 다른 바이트 → 거의 직교(낮은 유사도).
실제 얼굴 인식이 아니다 — 매칭 로직·동의 게이트·후보 생성 흐름을 검증하기 위한 대체물이며,
실제 얼굴 매칭은 FaceEmbedder 를 실제 모델 구현으로 교체해야 한다.
"""

from __future__ import annotations

import hashlib
import math
import struct

from core.config import FACE_EMBED_DIM


class MockFaceEmbedder:
    """이미지 바이트를 결정적 단위 벡터로 변환한다."""

    def __init__(self, dim: int = FACE_EMBED_DIM) -> None:
        self.dim = dim

    def embed(self, image_bytes: bytes) -> list[float]:
        vec: list[float] = []
        data = image_bytes if image_bytes else b"\x00"
        while len(vec) < self.dim:
            data = hashlib.sha256(data).digest()  # 결정적 체인
            for j in range(0, len(data), 4):
                if len(vec) >= self.dim:
                    break
                val = struct.unpack("<i", data[j:j + 4])[0]
                vec.append(val / 2_147_483_648.0)  # [-1, 1)
        norm = math.sqrt(sum(x * x for x in vec)) or 1.0
        return [x / norm for x in vec]
