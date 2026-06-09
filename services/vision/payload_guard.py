"""외부 비전 API 송신 직전 payload 안전성 검증 게이트.

원칙:
- 금지 키/값/패턴 발견 시 ValueError → 외부 호출 즉시 중단.
- security_service.assert_no_sensitive_paths를 재사용해 data/videos, data/frames 차단.
- 추가로 금지 키(stored_path 등), 영상 확장자, images 구조, API 키 패턴을 검사한다.
"""

from __future__ import annotations

import re
from typing import Any

from services.security_service import assert_no_sensitive_paths

# dict 키로 나타나면 차단
_FORBIDDEN_KEYS = frozenset({
    "stored_path", "video_path", "image_path", "image_ref", "path", "file",
    "score", "level", "rating", "observation_level", "development_score",
    "child_name", "real_name", "name",
})

# images[*]에서 허용되는 키만 이것
_IMAGES_ALLOWED_KEYS = frozenset({"frame_id", "mime", "data_b64"})

# 값 안에 포함되면 차단 (data/videos, data/frames는 assert_no_sensitive_paths가 처리)
# payload_builder._VIDEO_EXTENSIONS와 동일하게 유지한다.
_FORBIDDEN_VALUE_FRAGMENTS = (".mp4", ".mov", ".avi", ".mkv", ".m4v", ".webm")

# 스캔에서 제외할 키 — 머신 생성 base64 이미지 데이터(대용량, 오탐·성능 비용)
_SCAN_EXEMPT_KEYS = frozenset({"data_b64"})
_OMITTED_PLACEHOLDER = "<image-bytes-omitted>"

# API 키/시크릿 패턴 (간단한 휴리스틱)
_API_KEY_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_\-]{16,}"),          # OpenAI 스타일
    re.compile(r"AIza[A-Za-z0-9_\-]{35,}"),          # Google AI 스타일
    re.compile(r"Bearer\s+[A-Za-z0-9_\-\.]{16,}"),   # Bearer 토큰
]


def assert_safe_outbound_payload(payload: dict) -> None:
    """외부 API 전송 직전 payload 전체를 검사한다. 위반 시 ValueError로 전송을 차단한다.

    검사 순서:
    1. security_service.assert_no_sensitive_paths (data/videos, data/frames 경로 문자열)
    2. 금지 키 존재 여부 (stored_path, score 등)
    3. 금지 값 패턴 (영상 확장자 문자열)
    4. API 키 패턴
    5. images[*] 허용 키 외 키 존재 여부

    base64 이미지 데이터(images[*].data_b64)는 머신 생성 대용량 값이므로 문자열 스캔(1~4)에서
    제외한다(정규식 오탐·성능 비용 방지). 키 구조 검사(5)는 원본 기준으로 수행한다.
    """
    # base64 데이터를 플레이스홀더로 치환한 스캔 전용 사본
    scan_target = _strip_image_data(payload)

    # 1. 기존 민감 경로 검사 재사용 (data/videos, data/frames)
    assert_no_sensitive_paths(scan_target)

    # 2~4. 추가 재귀 검사
    _check_recursive(scan_target, _path="root")

    # 5. images 배열 구조 검사 (원본 기준)
    images = payload.get("images")
    if images is not None:
        if not isinstance(images, list):
            raise ValueError("payload.images는 리스트여야 합니다.")
        for i, img in enumerate(images):
            if not isinstance(img, dict):
                raise ValueError(f"payload.images[{i}]는 dict여야 합니다.")
            extra_keys = set(img.keys()) - _IMAGES_ALLOWED_KEYS
            if extra_keys:
                raise ValueError(
                    f"payload.images[{i}]에 허용되지 않은 키가 있습니다: {sorted(extra_keys)}. "
                    f"허용 키: {sorted(_IMAGES_ALLOWED_KEYS)}"
                )


def _strip_image_data(payload: dict) -> dict:
    """images[*].data_b64 값을 플레이스홀더로 치환한 얕은 사본을 반환한다.

    원본 payload는 변경하지 않는다. data_b64 키 자체는 보존되어 키 검사는 그대로 동작한다.
    """
    if not isinstance(payload, dict):
        return payload
    images = payload.get("images")
    if not isinstance(images, list):
        return payload

    new_images = []
    for img in images:
        if isinstance(img, dict) and any(k in img for k in _SCAN_EXEMPT_KEYS):
            stripped = dict(img)
            for k in _SCAN_EXEMPT_KEYS:
                if k in stripped:
                    stripped[k] = _OMITTED_PLACEHOLDER
            new_images.append(stripped)
        else:
            new_images.append(img)

    return {**payload, "images": new_images}


def _check_recursive(payload: Any, _path: str) -> None:
    if isinstance(payload, dict):
        for k, v in payload.items():
            key_str = str(k)
            if key_str in _FORBIDDEN_KEYS:
                raise ValueError(
                    f"금지된 키가 payload에 포함되었습니다 [{_path}]: {key_str!r}"
                )
            _check_recursive(v, _path=f"{_path}.{key_str}")
    elif isinstance(payload, (list, tuple)):
        for i, item in enumerate(payload):
            _check_recursive(item, _path=f"{_path}[{i}]")
    elif isinstance(payload, str):
        for fragment in _FORBIDDEN_VALUE_FRAGMENTS:
            if fragment in payload:
                raise ValueError(
                    f"금지된 문자열이 payload에 포함되었습니다 [{_path}]: {fragment!r}"
                )
        for pattern in _API_KEY_PATTERNS:
            if pattern.search(payload):
                raise ValueError(
                    f"API 키 패턴으로 보이는 문자열이 payload에 포함되었습니다 [{_path}]. "
                    "API 키는 payload에 포함해서는 안 됩니다."
                )
