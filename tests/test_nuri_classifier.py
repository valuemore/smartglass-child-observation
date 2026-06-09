"""NuriClassifier 단위 테스트."""

import sys
import uuid
from datetime import datetime
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.schemas import InteractionEvidence, ObservationCandidate, ScaleMappingCandidate
from services.mapping.nuri_classifier import NuriClassifier


def _make_candidate(
    observed_behavior: str,
    activity_context: str = "",
    peer_relation: str = "",
    visual_evidence: str = "프레임에서 관찰됨",
    interaction: InteractionEvidence = None,
) -> ObservationCandidate:
    return ObservationCandidate(
        id=f"cand_{uuid.uuid4().hex[:8]}",
        video_id="vid_test",
        scene_id="scene_test",
        time_start=0.0,
        time_end=5.0,
        temp_child_id="child_A",
        observed_behavior=observed_behavior,
        interaction=interaction,
        activity_context=activity_context,
        peer_relation=peer_relation,
        visual_evidence=visual_evidence,
        confidence=0.7,
        needs_teacher_review=True,
        created_at=datetime.now(),
    )


# ---------------------------------------------------------------------------
# 1. 블록·쌓기·비교·탐색 → 자연탐구
# ---------------------------------------------------------------------------

def test_block_stacking_maps_to_natural_inquiry():
    clf = NuriClassifier()
    cand = _make_candidate(
        observed_behavior="블록을 쌓아 구조물을 만들며 크기를 비교하고 탐색하는 모습",
        activity_context="쌓기놀이 영역",
    )
    results = clf.map(cand)
    areas = [m.area for m in results]
    assert "자연탐구" in areas
    # 자연탐구가 가장 강하게 매칭되어야 한다(상위)
    assert results[0].area == "자연탐구"


# ---------------------------------------------------------------------------
# 2. 친구·함께·도와 → 사회관계
# ---------------------------------------------------------------------------

def test_peer_cooperation_maps_to_social():
    clf = NuriClassifier()
    cand = _make_candidate(
        observed_behavior="친구와 함께 자료를 나누어 사용하며 도와주는 모습",
        peer_relation="또래와 협력",
    )
    results = clf.map(cand)
    areas = [m.area for m in results]
    assert "사회관계" in areas


# ---------------------------------------------------------------------------
# 3. 말하다·이야기 → 의사소통
# ---------------------------------------------------------------------------

def test_speaking_maps_to_communication():
    clf = NuriClassifier()
    cand = _make_candidate(
        observed_behavior="교사에게 자신의 생각을 말하고 이야기하며 질문하는 모습",
    )
    results = clf.map(cand)
    areas = [m.area for m in results]
    assert "의사소통" in areas


# ---------------------------------------------------------------------------
# 4. confidence 0~1
# ---------------------------------------------------------------------------

def test_confidence_in_range():
    clf = NuriClassifier()
    cand = _make_candidate(
        observed_behavior="블록을 쌓고 친구와 함께 이야기하며 노래하는 모습",
    )
    results = clf.map(cand)
    assert len(results) >= 1
    for m in results:
        assert 0.0 <= m.confidence <= 1.0


# ---------------------------------------------------------------------------
# 5. 점수 필드 없음
# ---------------------------------------------------------------------------

def test_no_score_fields():
    fields = set(ScaleMappingCandidate.model_fields.keys())
    forbidden = {"score", "level", "rating", "eval_score", "dev_score"}
    assert forbidden.isdisjoint(fields), f"금지된 점수 필드: {forbidden & fields}"


# ---------------------------------------------------------------------------
# 6. 매칭 키워드가 없으면 빈 리스트 (오류 없음)
# ---------------------------------------------------------------------------

def test_no_match_returns_empty():
    clf = NuriClassifier()
    cand = _make_candidate(
        observed_behavior="zzz qqq xyz",
        visual_evidence="zzz",
    )
    results = clf.map(cand)
    assert results == []


# ---------------------------------------------------------------------------
# 7. scale 은 항상 'nuri', item_id 는 None
# ---------------------------------------------------------------------------

def test_scale_is_nuri():
    clf = NuriClassifier()
    assert clf.scale_name == "nuri"
    cand = _make_candidate(observed_behavior="블록을 쌓는 모습")
    for m in clf.map(cand):
        assert m.scale == "nuri"
        assert m.item_id is None


# ---------------------------------------------------------------------------
# 8. interaction 텍스트도 분류에 반영
# ---------------------------------------------------------------------------

def test_interaction_text_considered():
    clf = NuriClassifier()
    cand = _make_candidate(
        observed_behavior="자리에 앉아 있는 모습",
        interaction=InteractionEvidence(
            with_peers="친구와 함께 차례를 지키며 나누어 사용",
            with_teacher=None,
            with_materials=None,
        ),
    )
    results = clf.map(cand)
    areas = [m.area for m in results]
    assert "사회관계" in areas
