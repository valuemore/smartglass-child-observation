"""ExternalVisionAdapter 단위 테스트.

VISION_DRY_RUN=true(기본) 환경을 전제하며, 실제 외부 API 호출은 없다.
"""
from pathlib import Path
from unittest.mock import patch

import pytest

from core.schemas import FrameRef, SegmentAnalysisRequest
from services.vision.external_adapter import ExternalVisionAdapter


def _make_request(tmp_path: Path) -> SegmentAnalysisRequest:
    img = tmp_path / "f_00.jpg"
    img.write_bytes(b"\xff\xd8\xff" + b"X" * 200)
    return SegmentAnalysisRequest(
        video_id="vid_t",
        segment_id="seg_t",
        time_start=0.0,
        time_end=5.0,
        frames=[FrameRef(frame_id="f_00", t=0.5, image_ref=str(img))],
    )


def test_dry_run_returns_empty_without_network(tmp_path):
    """dry_run=True 시 외부 네트워크 호출 없이 빈 결과를 반환한다."""
    adapter = ExternalVisionAdapter(model="gpt-4o", api_key="dummy", dry_run=True)
    req = _make_request(tmp_path)

    with patch.object(adapter, "_call_api", side_effect=AssertionError("API 호출 금지")) as mock_call:
        result = adapter.analyze_segment(req)
        mock_call.assert_not_called()

    assert result.observations == []
    assert result.segment_id == "seg_t"


def test_payload_guard_runs_before_api_call(tmp_path):
    """dry_run=False 시 payload_guard가 실행된 후 _call_api가 호출된다."""
    adapter = ExternalVisionAdapter(model="gpt-4o", api_key="test-key-xyz", dry_run=False)
    req = _make_request(tmp_path)

    # _call_api가 NotImplementedError를 발생시키는 것을 확인
    with pytest.raises(NotImplementedError):
        adapter.analyze_segment(req)


def test_empty_result_on_dry_run(tmp_path):
    """dry_run=True 시 결과의 observations 목록이 비어 있어야 한다."""
    adapter = ExternalVisionAdapter(model="gemini-1.5-pro", api_key="x", dry_run=True)
    result = adapter.analyze_segment(_make_request(tmp_path))
    assert len(result.observations) == 0
    assert adapter._last_discarded == 0


def test_dry_run_still_validates_payload(tmp_path):
    """dry_run=True 여도 payload 빌드·안전성 검증을 수행한다(안전 리허설).

    프레임 image_ref가 영상 확장자(.mp4)이면 dry_run에서도 ValueError가 발생해야 한다.
    """
    mp4 = tmp_path / "clip.mp4"
    mp4.write_bytes(b"\x00\x00\x00\x18ftyp" + b"X" * 100)
    req = SegmentAnalysisRequest(
        video_id="vid_t",
        segment_id="seg_t",
        time_start=0.0,
        time_end=5.0,
        frames=[FrameRef(frame_id="f_00", t=0.0, image_ref=str(mp4))],
    )
    adapter = ExternalVisionAdapter(model="gpt-4o", api_key="dummy", dry_run=True)

    # dry_run 이어도 build_external_payload 단계에서 영상 확장자를 거부해야 한다
    with patch.object(adapter, "_call_api", side_effect=AssertionError("API 호출 금지")):
        with pytest.raises(ValueError, match="영상 파일 확장자"):
            adapter.analyze_segment(req)


def test_missing_api_key_dry_run_false_raises():
    """dry_run=False이고 api_key가 없으면 ValueError."""
    with pytest.raises(ValueError, match="api_key"):
        ExternalVisionAdapter(model="gpt-4o", api_key="", dry_run=False)


def test_api_key_not_in_repr():
    """repr에 API 키 값이 포함되지 않아야 한다."""
    adapter = ExternalVisionAdapter(model="gpt-4o", api_key="sk-supersecret", dry_run=True)
    r = repr(adapter)
    assert "sk-supersecret" not in r
    assert "gpt-4o" in r
