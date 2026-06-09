"""KicceMapper 단위 테스트."""

import sys
import uuid
from datetime import datetime
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.schemas import ObservationCandidate, ScaleMappingCandidate
from services.mapping.kicce_mapper import KicceMapper


def _make_candidate(
    observed_behavior: str,
    activity_context: str = "",
    visual_evidence: str = "프레임에서 관찰됨",
) -> ObservationCandidate:
    return ObservationCandidate(
        id=f"cand_{uuid.uuid4().hex[:8]}",
        video_id="vid_test",
        scene_id="scene_test",
        time_start=0.0,
        time_end=5.0,
        temp_child_id="child_A",
        observed_behavior=observed_behavior,
        activity_context=activity_context,
        visual_evidence=visual_evidence,
        confidence=0.7,
        needs_teacher_review=True,
        created_at=datetime.now(),
    )


# ---------------------------------------------------------------------------
# 1. 문항 후보가 반환된다
# ---------------------------------------------------------------------------

def test_returns_item_candidates():
    mapper = KicceMapper()
    cand = _make_candidate(
        observed_behavior="블록을 쌓아 구조물을 만들며 균형을 살피는 모습",
        activity_context="쌓기놀이 영역",
    )
    results = mapper.map(cand)
    assert len(results) >= 1
    assert all(m.scale == "kicce" for m in results)


# ---------------------------------------------------------------------------
# 2. 누리 영역 필터가 동작한다
# ---------------------------------------------------------------------------

def test_nuri_area_filter():
    mapper = KicceMapper()
    cand = _make_candidate(
        observed_behavior="친구와 함께 차례를 지키며 블록을 나누어 쌓는 모습",
    )
    # 사회관계로만 필터 → 사회관계 문항만 반환되어야 한다
    results = mapper.map(cand, nuri_areas=["사회관계"])
    assert len(results) >= 1
    for m in results:
        assert m.area == "사회관계", f"필터 위반: {m.area}"


# ---------------------------------------------------------------------------
# 3. item_id, item_text, rationale, confidence 가 존재한다
# ---------------------------------------------------------------------------

def test_required_fields_present():
    mapper = KicceMapper()
    cand = _make_candidate(
        observed_behavior="블록을 쌓고 구조물의 균형을 탐구하는 모습",
    )
    results = mapper.map(cand)
    assert len(results) >= 1
    for m in results:
        assert m.item_id is not None
        assert m.item_text.strip() != ""
        assert m.rationale.strip() != ""
        assert 0.0 <= m.confidence <= 1.0


# ---------------------------------------------------------------------------
# 4. 매칭 없으면 빈 리스트 (오류 없음)
# ---------------------------------------------------------------------------

def test_no_match_returns_empty():
    mapper = KicceMapper()
    cand = _make_candidate(
        observed_behavior="zzz qqq xyz",
        visual_evidence="zzz",
    )
    results = mapper.map(cand)
    assert results == []


# ---------------------------------------------------------------------------
# 5. 최대 3개 문항으로 제한
# ---------------------------------------------------------------------------

def test_max_three_items():
    mapper = KicceMapper()
    cand = _make_candidate(
        observed_behavior=(
            "블록을 쌓고 친구와 함께 나누며 말하고 그림을 그리고 뛰어다니며 "
            "관찰하고 비교하고 도와주고 손씻기를 하는 모습"
        ),
        activity_context="자유놀이",
    )
    results = mapper.map(cand)
    assert len(results) <= 3


# ---------------------------------------------------------------------------
# 6. scale_name 속성
# ---------------------------------------------------------------------------

def test_scale_name():
    mapper = KicceMapper()
    assert mapper.scale_name == "kicce"


# ---------------------------------------------------------------------------
# 7. 누리 영역 필터 결과가 비면 전체로 폴백
# ---------------------------------------------------------------------------

def test_empty_area_filter_fallback():
    mapper = KicceMapper()
    cand = _make_candidate(
        observed_behavior="블록을 쌓는 모습",
    )
    # 존재하지 않는 영역으로 필터 → 폴백으로 키워드 매칭 시도
    results = mapper.map(cand, nuri_areas=["존재하지않는영역"])
    # 폴백되어 자연탐구 문항이 매칭될 수 있어야 한다
    assert len(results) >= 1
