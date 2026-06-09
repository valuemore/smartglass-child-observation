"""core/schemas.py 검증 테스트."""

from datetime import datetime

import pytest
from pydantic import ValidationError

from core.schemas import (
    AuditLog,
    FinalRecord,
    FrameRef,
    InteractionEvidence,
    KicceItemCandidate,
    NuriAreaCandidate,
    ObservationCandidate,
    SegmentAnalysisResult,
)


# ---------------------------------------------------------------------------
# 공통 픽스처
# ---------------------------------------------------------------------------

def _valid_candidate(**overrides) -> dict:
    base = dict(
        id="cand_001",
        video_id="vid_001",
        scene_id="seg_007",
        time_start=132.4,
        time_end=138.9,
        temp_child_id="child_A",
        observed_behavior="블록을 수직으로 쌓아 올리다가 무너지자 다시 시도함",
        visual_evidence="f_021에서 두 손으로 블록을 정렬, f_022에서 무너진 블록을 응시",
        confidence=0.68,
        needs_teacher_review=True,
        created_at=datetime.now(),
    )
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# 테스트 1: 유효한 ObservationCandidate 생성 성공
# ---------------------------------------------------------------------------

def test_valid_observation_candidate():
    candidate = ObservationCandidate(
        **_valid_candidate(
            interaction=InteractionEvidence(
                with_peers="child_B와 번갈아 블록을 올림",
                with_teacher="교사의 제안에 반응",
                with_materials="원목 블록 사용",
            ),
            activity_context="자유놀이 쌓기놀이 영역",
            peer_relation="협력적 차례 주고받기",
            audio_support="(유아) 무너졌어! — 보조 근거",
            nuri_area_candidates=[
                NuriAreaCandidate(area="자연탐구", rationale="구조물 균형·인과 탐색", confidence=0.72),
                NuriAreaCandidate(area="사회관계", rationale="또래와 협력", confidence=0.60),
            ],
            kicce_item_candidates=[
                KicceItemCandidate(item_id=34, item_text="적절한 문항 텍스트", rationale="근거", confidence=0.55),
            ],
        )
    )

    assert candidate.id == "cand_001"
    assert candidate.temp_child_id == "child_A"
    assert candidate.confidence == 0.68
    assert candidate.needs_teacher_review is True
    assert len(candidate.nuri_area_candidates) == 2
    assert len(candidate.kicce_item_candidates) == 1


# ---------------------------------------------------------------------------
# 테스트 2: confidence 가 1 을 초과하면 ValidationError
# ---------------------------------------------------------------------------

def test_confidence_exceeds_max():
    with pytest.raises(ValidationError) as exc_info:
        ObservationCandidate(**_valid_candidate(confidence=1.1))
    errors = exc_info.value.errors()
    assert any("confidence" in str(e["loc"]) for e in errors)


# ---------------------------------------------------------------------------
# 테스트 3: time_end 가 time_start 보다 작거나 같으면 ValidationError
# ---------------------------------------------------------------------------

def test_time_end_not_after_start_raises():
    with pytest.raises(ValidationError):
        ObservationCandidate(**_valid_candidate(time_start=100.0, time_end=100.0))

    with pytest.raises(ValidationError):
        ObservationCandidate(**_valid_candidate(time_start=100.0, time_end=90.0))


# ---------------------------------------------------------------------------
# 테스트 4: FinalRecord 에 score 필드가 없어야 함
# ---------------------------------------------------------------------------

def test_final_record_has_no_score_field():
    record = FinalRecord(
        id="rec_001",
        candidate_id="cand_001",
        pseudonym_id="김OO",
        final_behavior="블록 쌓기 시도를 반복하며 인과관계를 탐색함",
        confirmed_areas=["자연탐구"],
        confirmed_items=[
            KicceItemCandidate(item_id=34, item_text="문항 텍스트", rationale="근거", confidence=0.55)
        ],
        decision="edited",
        edited=True,
        confirmed_by="teacher_01",
        confirmed_at=datetime.now(),
    )

    assert not hasattr(record, "score")
    assert not hasattr(record, "level")
    assert not hasattr(record, "rating")
    assert record.decision == "edited"


# ---------------------------------------------------------------------------
# 테스트 5: AuditLog action 이 허용값이 아니면 ValidationError
# ---------------------------------------------------------------------------

def test_audit_log_invalid_action():
    with pytest.raises(ValidationError) as exc_info:
        AuditLog(
            id="log_001",
            video_id="vid_001",
            actor="teacher_01",
            action="view",  # 허용되지 않는 값
            created_at=datetime.now(),
        )
    errors = exc_info.value.errors()
    assert any("action" in str(e["loc"]) for e in errors)


def test_audit_log_valid_actions():
    for action in ("upload", "access", "analyze", "export", "delete"):
        log = AuditLog(
            id=f"log_{action}",
            video_id="vid_001",
            actor="teacher_01",
            action=action,
            created_at=datetime.now(),
        )
        assert log.action == action


# ---------------------------------------------------------------------------
# 테스트 6: SegmentAnalysisResult 가 observations 리스트를 정상 검증
# ---------------------------------------------------------------------------

def test_segment_analysis_result_observations():
    result = SegmentAnalysisResult(
        segment_id="seg_007",
        time_start=132.4,
        time_end=138.9,
        observations=[
            ObservationCandidate(**_valid_candidate()),
            ObservationCandidate(
                **_valid_candidate(
                    id="cand_002",
                    temp_child_id="child_B",
                    observed_behavior="옆에서 블록을 건네주며 협력함",
                    visual_evidence="f_022에서 child_B가 블록을 손으로 밀어주는 동작",
                    confidence=0.55,
                )
            ),
        ],
    )

    assert result.segment_id == "seg_007"
    assert len(result.observations) == 2
    assert result.observations[0].temp_child_id == "child_A"
    assert result.observations[1].temp_child_id == "child_B"


def test_segment_analysis_result_empty_observations():
    result = SegmentAnalysisResult(
        segment_id="seg_008",
        time_start=140.0,
        time_end=145.0,
    )
    assert result.observations == []
