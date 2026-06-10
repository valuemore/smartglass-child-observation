"""얼굴 임베더 인터페이스 + 임베딩 직렬화·유사도 유틸.

원칙:
- 임베딩은 로컬 전용. 외부로 전송하지 않는다.
- 유사도(cosine)는 0~1 로 클램프해 신뢰도로 쓴다. 발달/관찰수준 점수가 아니다.
"""

from __future__ import annotations

import json
import math
from typing import Protocol


class FaceEmbedder(Protocol):
    """얼굴 이미지(바이트) → 임베딩 벡터. 실제 모델로 교체 가능."""

    def embed(self, image_bytes: bytes) -> list[float]:
        ...


def encode_embedding(vec: list[float]) -> bytes:
    """임베딩 벡터를 BLOB(bytes)로 직렬화한다."""
    return json.dumps(vec).encode("utf-8")


def decode_embedding(blob: bytes | None) -> list[float] | None:
    if not blob:
        return None
    try:
        return json.loads(blob.decode("utf-8"))
    except Exception:
        return None


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """코사인 유사도를 0~1 로 클램프해 반환한다(음수는 0)."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    sim = dot / (na * nb)
    if sim < 0:
        return 0.0
    if sim > 1:
        return 1.0
    return sim
