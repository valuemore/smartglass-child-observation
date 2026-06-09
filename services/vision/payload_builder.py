"""외부 비전 API 전송용 payload 빌더.

원칙:
- 외부 payload에 원본 영상 경로, stored_path, image_ref(파일 경로) 미포함.
- kept=True 프레임의 base64 이미지 데이터만 포함.
- 프레임 수, 이미지 크기, 전체 payload 크기 상한 적용.
- 반환 payload의 images[*]는 frame_id / mime / data_b64 키만 포함.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path

from core.config import (
    VISION_MAX_FRAMES_PER_SEGMENT,
    VISION_MAX_IMAGE_BYTES,
    VISION_MAX_PAYLOAD_BYTES,
)
from core.schemas import SegmentAnalysisRequest

_VIDEO_EXTENSIONS = frozenset({".mp4", ".mov", ".avi", ".mkv", ".m4v", ".webm"})

_NURI_AREAS = ["신체운동·건강", "의사소통", "사회관계", "예술경험", "자연탐구"]

_INSTRUCTION_RULES = [
    "유아를 확정 식별하지 말 것. child_A, child_B, child_C 등 임시 ID만 부여할 것.",
    "각 관찰에 시각적 근거(어떤 이미지에서 무엇이 보였는지)를 반드시 포함할 것.",
    "신뢰도(confidence, 0~1)를 반드시 표기할 것.",
    "needs_teacher_review 필드를 반드시 포함하고 true로 설정할 것.",
    "관찰수준 점수, 발달 점수, 평정 점수(score/level/rating/observation_level/development_score)는 절대 포함하지 말 것.",
    "유아 실명(이름)을 절대 포함하지 말 것.",
    "오디오는 보조 증거로만 활용할 것.",
]


def build_external_payload(
    request: SegmentAnalysisRequest,
    max_frames: int = VISION_MAX_FRAMES_PER_SEGMENT,
    max_image_bytes: int = VISION_MAX_IMAGE_BYTES,
    max_payload_bytes: int = VISION_MAX_PAYLOAD_BYTES,
) -> dict:
    """SegmentAnalysisRequest → 외부 API 송신용 payload dict.

    - frame_ref.image_ref(파일 경로) → 파일 읽기 → base64 변환.
    - payload에는 경로 문자열이 포함되지 않는다.
    - 프레임 수 / 이미지 크기 / payload 총량 상한 초과 시 ValueError.
    - 영상 파일 확장자가 image_ref에 포함되면 ValueError (원본 영상 오삽입 방지).
    """
    frames = request.frames[:max_frames]

    images = []
    for frame_ref in frames:
        image_ref = frame_ref.image_ref
        suffix = Path(image_ref).suffix.lower()

        if suffix in _VIDEO_EXTENSIONS:
            raise ValueError(
                f"영상 파일 확장자({suffix})가 프레임 참조에 포함되었습니다: "
                f"frame_id={frame_ref.frame_id}. "
                "원본 영상이 아닌 프레임 이미지만 전달해야 합니다."
            )

        path = Path(image_ref)
        if not path.exists():
            raise FileNotFoundError(
                f"프레임 파일을 찾을 수 없습니다: frame_id={frame_ref.frame_id}, path={image_ref}"
            )

        raw_bytes = path.read_bytes()
        if len(raw_bytes) > max_image_bytes:
            raise ValueError(
                f"프레임 이미지 크기({len(raw_bytes):,} bytes)가 "
                f"상한({max_image_bytes:,} bytes)을 초과했습니다: "
                f"frame_id={frame_ref.frame_id}"
            )

        mime = _guess_mime(suffix)
        data_b64 = base64.b64encode(raw_bytes).decode("ascii")

        # images 항목에는 frame_id / mime / data_b64 키만 포함 (경로 미포함)
        images.append({"frame_id": frame_ref.frame_id, "mime": mime, "data_b64": data_b64})

    payload = {
        "segment_id": request.segment_id,
        "time_start": request.time_start,
        "time_end": request.time_end,
        "frame_layout": request.frame_layout,
        "images": images,
        "instruction_context": {
            "nuri_areas": _NURI_AREAS,
            "rules": _INSTRUCTION_RULES,
        },
    }

    payload_bytes = len(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    if payload_bytes > max_payload_bytes:
        raise ValueError(
            f"payload 총 크기({payload_bytes:,} bytes)가 "
            f"상한({max_payload_bytes:,} bytes)을 초과했습니다."
        )

    return payload


def _guess_mime(suffix: str) -> str:
    _MAP = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}
    return _MAP.get(suffix, "image/jpeg")
