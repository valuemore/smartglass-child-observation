"""외부 비전 API 응답 파서 — Pydantic 검증 + 안전성 보정.

원칙:
- needs_teacher_review=True 강제 (API 응답값 무시).
- temp_child_id는 ^child_[A-Za-z0-9_]+$ 형태만 허용. 미일치 시 child_unknown 대체.
- 개별 후보 파싱 실패 시 해당 후보만 버리고 나머지는 유지 (부분 폐기 정책).
- score/level/rating 등 금지 필드는 ConfigDict(extra="forbid")로 Pydantic 단계에서 차단.
- 원시 응답 JSON 파싱 실패 → 빈 관찰 목록 반환 (예외 전파 없음).
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from pydantic import ValidationError

from core.schemas import (
    ObservationCandidate,
    SegmentAnalysisRequest,
    SegmentAnalysisResult,
)

logger = logging.getLogger(__name__)

_CHILD_ID_PATTERN = re.compile(r"^child_[A-Za-z0-9_]+$")


@dataclass
class ParseResult:
    """외부 API 응답 파싱 결과."""
    result: SegmentAnalysisResult
    discarded_count: int = 0
    warnings: list[str] = field(default_factory=list)


def parse_external_response(
    raw: str | dict,
    request: SegmentAnalysisRequest,
) -> ParseResult:
    """외부 비전 API 원시 응답을 파싱해 안전한 ParseResult로 반환한다.

    - raw: JSON 문자열 또는 이미 dict로 파싱된 응답.
    - 파싱 실패 또는 최상위 구조 이상 → 빈 SegmentAnalysisResult 반환.
    - 후보별 검증 실패 → 해당 후보 폐기, discarded_count 증가.
    - needs_teacher_review는 항상 True로 강제.
    - temp_child_id가 패턴에 맞지 않으면 child_unknown으로 대체.
    """
    warnings: list[str] = []
    discarded_count = 0

    # 1. JSON 파싱
    if isinstance(raw, str):
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            warnings.append(f"JSON 파싱 실패: {e}")
            return ParseResult(
                result=_empty_result(request),
                discarded_count=0,
                warnings=warnings,
            )
    else:
        data = raw

    if not isinstance(data, dict):
        warnings.append("응답이 dict 형태가 아닙니다. 빈 결과를 반환합니다.")
        return ParseResult(result=_empty_result(request), discarded_count=0, warnings=warnings)

    # 2. observations 목록 추출
    raw_obs: list[Any] = data.get("observations", [])
    if not isinstance(raw_obs, list):
        warnings.append("'observations' 필드가 리스트가 아닙니다.")
        raw_obs = []

    # 3. 후보별 검증 및 보정
    candidates: list[ObservationCandidate] = []
    for i, obs in enumerate(raw_obs):
        if not isinstance(obs, dict):
            warnings.append(f"observations[{i}]: dict가 아닙니다. 폐기.")
            discarded_count += 1
            continue

        obs = dict(obs)  # 원본 변조 방지용 복사

        # 필수 id 보정: 없으면 자동 생성
        if not obs.get("id"):
            obs["id"] = f"cand_{uuid.uuid4().hex[:10]}"

        # video_id / scene_id는 request에서 채운다 (외부 응답 신뢰 안 함)
        obs["video_id"] = request.video_id
        obs["scene_id"] = request.segment_id

        # needs_teacher_review 강제
        obs["needs_teacher_review"] = True
        if data.get("needs_teacher_review") is False:
            warnings.append(
                f"observations[{i}]: needs_teacher_review=False → True로 강제 보정."
            )

        # temp_child_id 정규화
        raw_cid = obs.get("temp_child_id", "")
        if not _CHILD_ID_PATTERN.match(str(raw_cid)):
            warnings.append(
                f"observations[{i}]: temp_child_id={raw_cid!r}가 패턴에 맞지 않습니다. "
                "'child_unknown'으로 대체합니다."
            )
            obs["temp_child_id"] = "child_unknown"

        # created_at 기본값 보정
        if "created_at" not in obs:
            obs["created_at"] = datetime.now()

        # Pydantic 검증 (extra="forbid"로 score 등 금지 필드 차단)
        try:
            candidate = ObservationCandidate.model_validate(obs)
        except ValidationError as e:
            warnings.append(
                f"observations[{i}]: Pydantic 검증 실패, 폐기. 오류: {e.error_count()}건"
            )
            logger.warning("observations[%d] 폐기: %s", i, e)
            discarded_count += 1
            continue

        candidates.append(candidate)

    result = SegmentAnalysisResult(
        segment_id=request.segment_id,
        time_start=request.time_start,
        time_end=request.time_end,
        observations=candidates,
    )

    return ParseResult(result=result, discarded_count=discarded_count, warnings=warnings)


def _empty_result(request: SegmentAnalysisRequest) -> SegmentAnalysisResult:
    return SegmentAnalysisResult(
        segment_id=request.segment_id,
        time_start=request.time_start,
        time_end=request.time_end,
        observations=[],
    )
