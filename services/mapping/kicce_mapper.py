"""KICCE 유아관찰척도 문항 매핑기 (핵심 기능) — 키워드/규칙 기반 1차.

원칙:
- 결과는 문항 후보다(확정·점수화 아님). 교사가 검토·확정한다.
- confidence 는 0~1. 점수(score/level/rating) 필드는 만들지 않는다.
- 1차 흐름:
  1. NuriClassifier 가 제시한 상위 영역으로 문항을 필터링(nuri_areas 주어진 경우).
  2. 후보 텍스트와 문항 keywords/example_cases 를 키워드 매칭.
  3. 매칭 점수 상위 1~3개 문항 후보 반환.
- 후보가 없으면 빈 리스트(오류 없음).
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Optional

from core.schemas import ObservationCandidate, ScaleMappingCandidate

_DEFAULT_KICCE_PATH = Path(__file__).resolve().parent.parent.parent / "resources" / "kicce_items.json"

_MAX_ITEMS = 3       # 후보 문항 최대 개수
_CONF_SATURATION = 3
_CONF_BASE = 0.35
_CONF_MAX = 0.85     # 확정이 아님을 반영해 상한 1.0 미만


class KicceMapper:
    """관찰 후보를 KICCE 문항 후보로 매핑한다."""

    scale_name = "kicce"

    def __init__(self, kicce_path: Optional[str] = None) -> None:
        path = Path(kicce_path) if kicce_path else _DEFAULT_KICCE_PATH
        with open(path, encoding="utf-8") as f:
            self._items: list[dict] = json.load(f)

    def map(
        self,
        candidate: ObservationCandidate,
        nuri_areas: Optional[list[str]] = None,
    ) -> list[ScaleMappingCandidate]:
        """관찰 후보 → KICCE 문항 후보 목록(매칭 점수 내림차순, 최대 _MAX_ITEMS).

        nuri_areas 가 주어지면 해당 영역의 문항만 1차 필터링한다.
        주어지지 않으면(Protocol 호출) 전체 문항에서 키워드 매칭한다.
        """
        text = _candidate_text(candidate)

        # 1차: 누리 영역 필터
        if nuri_areas:
            area_set = set(nuri_areas)
            items = [it for it in self._items if it["area"] in area_set]
            # 영역 필터 결과가 비면 전체로 폴백(키워드 매칭에 맡김)
            if not items:
                items = self._items
        else:
            items = self._items

        # 2차: 키워드 + example_cases 매칭
        scored: list[tuple[dict, list[str]]] = []
        for item in items:
            matched = [kw for kw in item["keywords"] if kw in text]
            # example_cases 의 단어 일부도 보조 매칭(부분 포함)
            example_hit = any(
                _example_overlap(ex, text)
                for ex in item.get("example_cases", []) + item.get("level_descriptions", [])
            )
            if matched or example_hit:
                scored.append((item, matched))

        # 매칭 키워드 수 내림차순, 동점이면 item_id 오름차순
        scored.sort(key=lambda x: (-len(x[1]), x[0]["item_id"]))

        mappings: list[ScaleMappingCandidate] = []
        for item, matched in scored[:_MAX_ITEMS]:
            confidence = _normalize_confidence(len(matched))
            if matched:
                rationale = (
                    f"'{', '.join(matched[:5])}' 키워드가 문항 내용과 연관됨 (후보)"
                )
            else:
                rationale = "관찰사례 예시와 맥락이 유사함 (후보)"
            mappings.append(ScaleMappingCandidate(
                id=f"map_{candidate.id}_kicce_{uuid.uuid4().hex[:8]}",
                candidate_id=candidate.id,
                scale="kicce",
                area=item["area"],
                item_id=item["item_id"],
                item_text=item["item_text"],
                rationale=rationale,
                confidence=confidence,
            ))
        return mappings


# ---------------------------------------------------------------------------
# 헬퍼
# ---------------------------------------------------------------------------

def _candidate_text(candidate: ObservationCandidate) -> str:
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


def _example_overlap(example: str, text: str) -> bool:
    """관찰사례 예시의 핵심 어절이 후보 텍스트에 일부 포함되는지(보조 매칭)."""
    tokens = [t for t in example.replace(",", " ").split() if len(t) >= 2]
    hits = sum(1 for t in tokens if t in text)
    return hits >= 2


def _normalize_confidence(match_count: int) -> float:
    """매칭 키워드 수를 0~1 신뢰도로 정규화한다(상한 _CONF_MAX)."""
    if match_count <= 0:
        return _CONF_BASE  # example_cases 보조 매칭만 된 경우의 기본값
    if match_count >= _CONF_SATURATION:
        return _CONF_MAX
    ratio = (match_count - 1) / (_CONF_SATURATION - 1)
    conf = _CONF_BASE + ratio * (_CONF_MAX - _CONF_BASE)
    return round(min(conf, _CONF_MAX), 2)
