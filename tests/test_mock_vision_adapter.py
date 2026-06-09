"""MockVisionAdapter 단위 테스트."""

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.schemas import FrameRef, SegmentAnalysisRequest, SegmentAnalysisResult
from services.vision.mock_adapter import MockVisionAdapter


def _make_request(
    segment_id: str = "scene_vid_test_0000",
    n_frames: int = 1,
) -> SegmentAnalysisRequest:
    return SegmentAnalysisRequest(
        video_id="vid_test_001",
        segment_id=segment_id,
        time_start=0.0,
        time_end=5.0,
        frames=[
            FrameRef(frame_id=f"frm_{i:03d}", t=float(i), image_ref=f"data/frames/f{i:03d}.jpg")
            for i in range(n_frames)
        ],
    )


# ---------------------------------------------------------------------------
# 1. 반환 타입
# ---------------------------------------------------------------------------

def test_returns_segment_analysis_result():
    adapter = MockVisionAdapter()
    result = adapter.analyze_segment(_make_request())
    assert isinstance(result, SegmentAnalysisResult)


# ---------------------------------------------------------------------------
# 2. observations 1개 이상
# ---------------------------------------------------------------------------

def test_observations_at_least_one():
    adapter = MockVisionAdapter()
    result = adapter.analyze_segment(_make_request())
    assert len(result.observations) >= 1


# ---------------------------------------------------------------------------
# 3. confidence 범위 0~1
# ---------------------------------------------------------------------------

def test_confidence_in_range():
    adapter = MockVisionAdapter()
    result = adapter.analyze_segment(_make_request())
    for obs in result.observations:
        assert 0.0 <= obs.confidence <= 1.0


# ---------------------------------------------------------------------------
# 4. observed_behavior / visual_evidence 비어있지 않음
# ---------------------------------------------------------------------------

def test_required_fields_not_empty():
    adapter = MockVisionAdapter()
    result = adapter.analyze_segment(_make_request())
    for obs in result.observations:
        assert obs.observed_behavior.strip() != ""
        assert obs.visual_evidence.strip() != ""


# ---------------------------------------------------------------------------
# 5. 점수 필드 없음 (score / level / rating 등 금지)
# ---------------------------------------------------------------------------

def test_no_score_fields():
    """ObservationCandidate 스키마에 점수 필드가 없어야 한다."""
    from core.schemas import ObservationCandidate
    fields = set(ObservationCandidate.model_fields.keys())
    forbidden = {"score", "level", "rating", "eval_score", "dev_score"}
    assert forbidden.isdisjoint(fields), f"금지된 점수 필드 포함: {forbidden & fields}"


# ---------------------------------------------------------------------------
# 6. needs_teacher_review=True 강제
# ---------------------------------------------------------------------------

def test_needs_teacher_review_true():
    adapter = MockVisionAdapter()
    result = adapter.analyze_segment(_make_request())
    for obs in result.observations:
        assert obs.needs_teacher_review is True


# ---------------------------------------------------------------------------
# 7. temp_child_id 는 'child_' 패턴 (실명 금지)
# ---------------------------------------------------------------------------

def test_temp_child_id_pattern():
    adapter = MockVisionAdapter()
    result = adapter.analyze_segment(_make_request())
    for obs in result.observations:
        assert obs.temp_child_id.startswith("child_"), (
            f"실명 가능성 있는 ID: {obs.temp_child_id!r}"
        )


# ---------------------------------------------------------------------------
# 8. 여러 프레임이면 visual_evidence 에 범위 표현
# ---------------------------------------------------------------------------

def test_visual_evidence_references_frames():
    adapter = MockVisionAdapter()
    # 단일 프레임
    single = adapter.analyze_segment(_make_request(n_frames=1))
    assert "frm_000" in single.observations[0].visual_evidence

    # 복수 프레임
    multi = adapter.analyze_segment(_make_request(n_frames=3))
    obs = multi.observations[0]
    assert "frm_000" in obs.visual_evidence
    assert "frm_002" in obs.visual_evidence


# ---------------------------------------------------------------------------
# 9. segment_id 가 달라지면 다른 템플릿이 선택될 수 있다 (순환 검증)
# ---------------------------------------------------------------------------

def test_template_cycling():
    adapter = MockVisionAdapter()
    behaviors = set()
    for i in range(10):
        result = adapter.analyze_segment(_make_request(segment_id=f"scene_vid_{i:04d}"))
        behaviors.add(result.observations[0].observed_behavior)
    # 10개 segment 중 최소 2종 이상의 템플릿이 선택되어야 한다
    assert len(behaviors) >= 2
