"""누리과정 5영역 분류기 — 키워드/규칙 기반 1차 분류.

원칙:
- 결과는 후보다(확정 아님). 교사가 검토·확정한다.
- confidence 는 0~1. 점수(score/level/rating) 필드는 만들지 않는다.
- ObservationCandidate 의 텍스트(행동·상호작용·맥락·또래관계·시각근거)를
  종합해 영역별 키워드 매칭 수로 후보를 산출한다.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Optional

from core.schemas import ObservationCandidate, ScaleMappingCandidate

_DEFAULT_NURI_PATH = Path(__file__).resolve().parent.parent.parent / "resources" / "nuri_areas.json"

# confidence 정규화 기준: 이 개수 이상 키워드가 매칭되면 상한(0.9)에 도달
_CONF_SATURATION = 3
_CONF_BASE = 0.4   # 키워드 1개 매칭 시 기본 신뢰도
_CONF_MAX = 0.9    # 신뢰도 상한 (확정이 아님을 반영해 1.0 미만)


class NuriClassifier:
    """관찰 후보를 누리과정 5영역 후보로 분류한다."""

    scale_name = "nuri"

    def __init__(self, nuri_path: Optional[str] = None) -> None:
        path = Path(nuri_path) if nuri_path else _DEFAULT_NURI_PATH
        with open(path, encoding="utf-8") as f:
            self._areas: list[dict] = json.load(f)

    def map(self, candidate: ObservationCandidate) -> list[ScaleMappingCandidate]:
        """관찰 후보 → 누리 영역 후보 목록(매칭 신뢰도 내림차순)."""
        text = _candidate_text(candidate)

        results: list[tuple[dict, list[str]]] = []
        for area_def in self._areas:
            matched = [kw for kw in area_def["keywords"] if kw in text]
            if matched:
                results.append((area_def, matched))

        # 매칭 키워드 수 내림차순
        results.sort(key=lambda x: len(x[1]), reverse=True)

        mappings: list[ScaleMappingCandidate] = []
        for area_def, matched in results:
            confidence = _normalize_confidence(len(matched))
            rationale = (
                f"행동·맥락 서술에서 '{', '.join(matched[:5])}' 키워드가 "
                f"'{area_def['area']}' 영역과 연관됨 (후보)"
            )
            mappings.append(ScaleMappingCandidate(
                id=f"map_{candidate.id}_nuri_{uuid.uuid4().hex[:8]}",
                candidate_id=candidate.id,
                scale="nuri",
                area=area_def["area"],
                item_id=None,
                item_text=area_def["area"],
                rationale=rationale,
                confidence=confidence,
            ))
        return mappings


# ---------------------------------------------------------------------------
# 헬퍼
# ---------------------------------------------------------------------------

def _candidate_text(candidate: ObservationCandidate) -> str:
    """후보의 텍스트 필드를 하나의 검색 대상 문자열로 결합한다."""
    parts: list[str] = [
        candidate.observed_behavior,
        candidate.activity_context or "",
        candidate.peer_relation or "",
        candidate.visual_evidence,
    ]
    if candidate.interaction:
        parts.extend([
            candidate.interaction.with_peers or "",
            candidate.interaction.with_teacher or "",
            candidate.interaction.with_materials or "",
        ])
    return " ".join(parts)


def _normalize_confidence(match_count: int) -> float:
    """매칭 키워드 수를 0~1 신뢰도로 정규화한다(상한 _CONF_MAX)."""
    if match_count <= 0:
        return 0.0
    if match_count >= _CONF_SATURATION:
        return _CONF_MAX
    # 1개=base, saturation개=max 사이 선형 보간
    ratio = (match_count - 1) / (_CONF_SATURATION - 1)
    conf = _CONF_BASE + ratio * (_CONF_MAX - _CONF_BASE)
    return round(min(conf, _CONF_MAX), 2)
