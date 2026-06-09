"""response_parser 단위 테스트.

실제 외부 API 호출 없음. 모든 입력은 합성 데이터.
"""
from datetime import datetime

import pytest

from core.schemas import SegmentAnalysisRequest, FrameRef
from services.vision.response_parser import parse_external_response


def _req() -> SegmentAnalysisRequest:
    return SegmentAnalysisRequest(
        video_id="vid_test",
        segment_id="seg_test",
        time_start=0.0,
        time_end=5.0,
        frames=[FrameRef(frame_id="f_00", t=0.5, image_ref="f_00.jpg")],
    )


def _valid_obs(**kwargs) -> dict:
    base = {
        "id": "cand_aaa",
        "temp_child_id": "child_A",
        "observed_behavior": "블록을 쌓는다",
        "visual_evidence": "f_00에서 블록 쌓기 확인",
        "confidence": 0.7,
        "needs_teacher_review": True,
        "time_start": 0.0,
        "time_end": 5.0,
    }
    base.update(kwargs)
    return base


def test_valid_response_parsed():
    """유효한 응답은 1개 candidate로 파싱되어야 한다."""
    raw = {"observations": [_valid_obs()]}
    result = parse_external_response(raw, _req())
    assert result.discarded_count == 0
    assert len(result.result.observations) == 1


def test_needs_teacher_review_forced_true():
    """needs_teacher_review=False 이더라도 True로 강제되어야 한다."""
    raw = {"observations": [_valid_obs(needs_teacher_review=False)]}
    result = parse_external_response(raw, _req())
    cand = result.result.observations[0]
    assert cand.needs_teacher_review is True


def test_score_field_rejected():
    """score 필드가 있으면 ConfigDict(extra='forbid')로 ValidationError → 후보 폐기."""
    obs = _valid_obs()
    obs["score"] = 0.9
    raw = {"observations": [obs]}
    result = parse_external_response(raw, _req())
    assert result.discarded_count == 1
    assert len(result.result.observations) == 0


def test_temp_child_id_normalized():
    """패턴에 맞지 않는 temp_child_id는 child_unknown으로 대체된다."""
    obs = _valid_obs(temp_child_id="김철수")  # 실명 형태
    raw = {"observations": [obs]}
    result = parse_external_response(raw, _req())
    assert result.result.observations[0].temp_child_id == "child_unknown"
    assert any("child_unknown" in w for w in result.warnings)


def test_broken_json_returns_empty():
    """JSON 파싱 실패 시 빈 결과를 반환한다. 예외 전파 없음."""
    result = parse_external_response("{broken json }", _req())
    assert result.discarded_count == 0
    assert result.result.observations == []
    assert len(result.warnings) > 0


def test_missing_required_field_discards():
    """필수 필드(observed_behavior) 누락 시 해당 후보를 폐기한다."""
    obs = _valid_obs()
    del obs["observed_behavior"]
    raw = {"observations": [obs]}
    result = parse_external_response(raw, _req())
    assert result.discarded_count == 1


def test_confidence_out_of_range_discards():
    """confidence가 범위(0~1)를 벗어나면 해당 후보를 폐기한다."""
    obs = _valid_obs(confidence=1.5)
    raw = {"observations": [obs]}
    result = parse_external_response(raw, _req())
    assert result.discarded_count == 1


def test_time_end_before_start_discards():
    """time_end <= time_start이면 해당 후보를 폐기한다."""
    obs = _valid_obs(time_start=5.0, time_end=0.0)
    raw = {"observations": [obs]}
    result = parse_external_response(raw, _req())
    assert result.discarded_count == 1


def test_partial_discard_keeps_valid():
    """일부 후보가 무효여도 나머지 유효 후보는 유지된다."""
    bad = _valid_obs(confidence=2.0)
    good = _valid_obs(id="cand_bbb", temp_child_id="child_B")
    raw = {"observations": [bad, good]}
    result = parse_external_response(raw, _req())
    assert result.discarded_count == 1
    assert len(result.result.observations) == 1
    assert result.result.observations[0].temp_child_id == "child_B"
