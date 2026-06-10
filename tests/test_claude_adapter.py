"""ClaudeVisionAdapter 단위 테스트.

원칙:
- 실제 Anthropic API를 절대 호출하지 않는다.
- anthropic 패키지가 없어도 dry_run 테스트는 통과해야 한다.
- _call_api()는 mock으로 대체해 응답 파싱만 검증한다.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.schemas import FrameRef, SegmentAnalysisRequest
from services.vision.claude_adapter import ClaudeVisionAdapter, _extract_json

_DUMMY_FRAME = FrameRef(frame_id="f_001", t=1.0, image_ref="dummy/f_001.jpg")


# ---------------------------------------------------------------------------
# _extract_json 헬퍼 테스트
# ---------------------------------------------------------------------------

def test_extract_json_plain():
    raw = '{"observations": []}'
    assert _extract_json(raw) == '{"observations": []}'


def test_extract_json_strips_markdown_fence():
    raw = '```json\n{"observations": []}\n```'
    result = _extract_json(raw)
    assert result.startswith("{") and result.endswith("}")
    assert '"observations"' in result


def test_extract_json_strips_plain_fence():
    raw = '```\n{"observations": []}\n```'
    result = _extract_json(raw)
    assert result.startswith("{")


def test_extract_json_with_preamble():
    raw = '분석 결과입니다.\n{"observations": [{"temp_child_id": "child_A"}]}'
    result = _extract_json(raw)
    assert result.startswith("{")
    assert '"child_A"' in result


# ---------------------------------------------------------------------------
# dry_run — 실제 API 미호출 (anthropic 불필요)
# ---------------------------------------------------------------------------

def _make_request() -> SegmentAnalysisRequest:
    return SegmentAnalysisRequest(
        video_id="vid_test",
        segment_id="seg_001",
        time_start=0.0,
        time_end=5.0,
        frames=[_DUMMY_FRAME],
        frame_layout="sequence",
    )


# payload_builder 가 실제 파일을 읽지 않도록 빈 payload 로 mock
_EMPTY_PAYLOAD = {
    "segment_id": "seg_001", "time_start": 0.0, "time_end": 5.0,
    "frame_layout": "sequence", "images": [],
    "instruction_context": {"nuri_areas": [], "rules": []},
}

_BUILD_PATCH = "services.vision.external_adapter.build_external_payload"
_GUARD_PATCH = "services.vision.external_adapter.assert_safe_outbound_payload"


def test_dry_run_returns_empty_observations():
    adapter = ClaudeVisionAdapter(model="claude-sonnet-4-6", api_key="dummy", dry_run=True)
    with patch(_BUILD_PATCH, return_value=_EMPTY_PAYLOAD), \
         patch(_GUARD_PATCH):
        result = adapter.analyze_segment(_make_request())
    assert result.segment_id == "seg_001"
    assert result.observations == []


def test_dry_run_does_not_call_api():
    adapter = ClaudeVisionAdapter(model="claude-sonnet-4-6", api_key="dummy", dry_run=True)
    with patch(_BUILD_PATCH, return_value=_EMPTY_PAYLOAD), \
         patch(_GUARD_PATCH), \
         patch.object(adapter, "_call_api") as mock_call:
        adapter.analyze_segment(_make_request())
        mock_call.assert_not_called()


# ---------------------------------------------------------------------------
# _call_api 응답 파싱 (anthropic SDK mock)
# ---------------------------------------------------------------------------

_VALID_RESPONSE = """{
  "observations": [
    {
      "temp_child_id": "child_A",
      "observed_behavior": "블록을 쌓으며 균형을 탐색함",
      "visual_evidence": "1번 프레임에서 두 손으로 블록을 올리는 모습",
      "confidence": 0.78,
      "needs_teacher_review": true,
      "activity_context": "자유놀이 쌓기 영역",
      "nuri_area_candidates": [
        {"area": "자연탐구", "rationale": "균형·인과 탐색", "confidence": 0.72}
      ],
      "kicce_item_candidates": []
    }
  ]
}"""


def _mock_anthropic_response(text: str):
    """anthropic.Anthropic().messages.create() 가 반환하는 Mock 객체 생성."""
    content_block = MagicMock()
    content_block.text = text
    response = MagicMock()
    response.content = [content_block]
    return response


def test_call_api_parses_valid_response():
    """dry_run=False, _call_api mock → 유효한 후보 1개를 파싱한다."""
    adapter = ClaudeVisionAdapter(model="claude-sonnet-4-6", api_key="dummy", dry_run=False)

    with patch(_BUILD_PATCH, return_value=_EMPTY_PAYLOAD), \
         patch(_GUARD_PATCH), \
         patch.object(adapter, "_call_api", return_value=_VALID_RESPONSE):
        result = adapter.analyze_segment(_make_request())

    assert len(result.observations) == 1
    obs = result.observations[0]
    assert obs.temp_child_id == "child_A"
    assert obs.needs_teacher_review is True
    assert obs.confidence == pytest.approx(0.78)
    assert len(obs.nuri_area_candidates) == 1
    assert obs.nuri_area_candidates[0].area == "자연탐구"


def test_call_api_forces_needs_teacher_review():
    """API 응답에 needs_teacher_review=False 가 있어도 True 로 강제된다."""
    bad_response = _VALID_RESPONSE.replace('"needs_teacher_review": true', '"needs_teacher_review": false')
    adapter = ClaudeVisionAdapter(model="claude-sonnet-4-6", api_key="dummy", dry_run=False)

    with patch(_BUILD_PATCH, return_value=_EMPTY_PAYLOAD), \
         patch(_GUARD_PATCH), \
         patch.object(adapter, "_call_api", return_value=bad_response):
        result = adapter.analyze_segment(_make_request())

    assert all(o.needs_teacher_review is True for o in result.observations)


def test_call_api_normalizes_bad_child_id():
    """temp_child_id 가 패턴 미일치이면 child_unknown 으로 대체된다."""
    bad_response = _VALID_RESPONSE.replace('"child_A"', '"김철수"')
    adapter = ClaudeVisionAdapter(model="claude-sonnet-4-6", api_key="dummy", dry_run=False)

    with patch(_BUILD_PATCH, return_value=_EMPTY_PAYLOAD), \
         patch(_GUARD_PATCH), \
         patch.object(adapter, "_call_api", return_value=bad_response):
        result = adapter.analyze_segment(_make_request())

    assert result.observations[0].temp_child_id == "child_unknown"


def test_call_api_empty_observations_on_bad_json():
    """파싱 불가 응답이 오면 빈 관찰 목록을 반환하고 예외를 전파하지 않는다."""
    adapter = ClaudeVisionAdapter(model="claude-sonnet-4-6", api_key="dummy", dry_run=False)

    with patch(_BUILD_PATCH, return_value=_EMPTY_PAYLOAD), \
         patch(_GUARD_PATCH), \
         patch.object(adapter, "_call_api", return_value="이것은 JSON이 아닙니다"):
        result = adapter.analyze_segment(_make_request())

    assert result.observations == []


def test_call_api_rejects_score_field():
    """응답에 score 필드가 있으면 Pydantic extra=forbid 로 해당 후보가 폐기된다."""
    response_with_score = _VALID_RESPONSE.replace(
        '"kicce_item_candidates": []',
        '"kicce_item_candidates": [], "score": 4',
    )
    adapter = ClaudeVisionAdapter(model="claude-sonnet-4-6", api_key="dummy", dry_run=False)

    with patch(_BUILD_PATCH, return_value=_EMPTY_PAYLOAD), \
         patch(_GUARD_PATCH), \
         patch.object(adapter, "_call_api", return_value=response_with_score):
        result = adapter.analyze_segment(_make_request())

    # score 필드가 있는 후보는 Pydantic 검증 실패로 폐기된다
    assert result.observations == []


# ---------------------------------------------------------------------------
# provider_factory Claude 분기
# ---------------------------------------------------------------------------

def test_factory_claude_provider(monkeypatch):
    from services.vision import provider_factory
    monkeypatch.setattr(provider_factory, "VISION_PROVIDER", "claude")
    monkeypatch.setattr(provider_factory, "VISION_MODEL", "claude-sonnet-4-6")
    monkeypatch.setattr(provider_factory, "VISION_API_KEY", "dummy")
    monkeypatch.setattr(provider_factory, "VISION_DRY_RUN", True)

    adapter, info = provider_factory.get_vision_adapter()
    assert info["provider"] == "claude"
    assert info["model"] == "claude-sonnet-4-6"
    assert info["dry_run"] is True
    assert isinstance(adapter, ClaudeVisionAdapter)


def test_factory_external_with_claude_model_auto_detects(monkeypatch):
    """VISION_PROVIDER=external이어도 model이 claude-* 이면 ClaudeVisionAdapter 선택."""
    from services.vision import provider_factory
    monkeypatch.setattr(provider_factory, "VISION_PROVIDER", "external")
    monkeypatch.setattr(provider_factory, "VISION_MODEL", "claude-haiku-4-5-20251001")
    monkeypatch.setattr(provider_factory, "VISION_API_KEY", "dummy")
    monkeypatch.setattr(provider_factory, "VISION_DRY_RUN", True)

    adapter, info = provider_factory.get_vision_adapter()
    assert info["provider"] == "claude"
    assert isinstance(adapter, ClaudeVisionAdapter)


def test_factory_claude_no_key_dry_run_false_fallback_to_mock(monkeypatch):
    """api_key 없고 dry_run=False 이면 Mock 폴백."""
    from services.vision import provider_factory
    monkeypatch.setattr(provider_factory, "VISION_PROVIDER", "claude")
    monkeypatch.setattr(provider_factory, "VISION_MODEL", "claude-sonnet-4-6")
    monkeypatch.setattr(provider_factory, "VISION_API_KEY", "")
    monkeypatch.setattr(provider_factory, "VISION_DRY_RUN", False)

    from services.vision.mock_adapter import MockVisionAdapter
    adapter, info = provider_factory.get_vision_adapter()
    assert info["provider"] == "mock"
    assert isinstance(adapter, MockVisionAdapter)
    assert info["fallback_reason"] is not None


def test_factory_claude_default_model(monkeypatch):
    """VISION_MODEL 미설정 시 claude-sonnet-4-6 기본값을 사용한다."""
    from services.vision import provider_factory
    monkeypatch.setattr(provider_factory, "VISION_PROVIDER", "claude")
    monkeypatch.setattr(provider_factory, "VISION_MODEL", "")
    monkeypatch.setattr(provider_factory, "VISION_API_KEY", "dummy")
    monkeypatch.setattr(provider_factory, "VISION_DRY_RUN", True)

    _, info = provider_factory.get_vision_adapter()
    assert info["model"] == "claude-sonnet-4-6"
