"""payload_guard 단위 테스트.

각 금지 조건을 독립적으로 검증한다. 실제 외부 API 호출 없음.
"""
import pytest

from services.vision.payload_guard import assert_safe_outbound_payload


def _clean_payload() -> dict:
    """기본 통과 payload."""
    return {
        "segment_id": "seg_001",
        "time_start": 0.0,
        "time_end": 5.0,
        "images": [
            {"frame_id": "f_00", "mime": "image/jpeg", "data_b64": "AAAA"},
        ],
        "instruction_context": {"rules": ["유아를 확정 식별하지 말 것."]},
    }


def test_clean_payload_passes():
    """정상 payload는 예외 없이 통과해야 한다."""
    assert_safe_outbound_payload(_clean_payload())


def test_stored_path_key_rejected():
    """stored_path 키가 있으면 ValueError."""
    p = _clean_payload()
    p["stored_path"] = "/data/videos/foo.mp4"
    with pytest.raises(ValueError, match="stored_path"):
        assert_safe_outbound_payload(p)


def test_image_path_key_rejected():
    """image_path 키가 있으면 ValueError."""
    p = _clean_payload()
    p["image_path"] = "/data/frames/f_00.jpg"
    with pytest.raises(ValueError, match="image_path"):
        assert_safe_outbound_payload(p)


def test_image_ref_key_rejected():
    """images[*]에 image_ref 키가 있으면 ValueError."""
    p = _clean_payload()
    p["images"][0]["image_ref"] = "/data/frames/f_00.jpg"
    with pytest.raises(ValueError, match="image_ref|허용되지 않은 키"):
        assert_safe_outbound_payload(p)


def test_data_videos_path_rejected():
    """값에 data/videos 경로 문자열이 있으면 ValueError."""
    p = _clean_payload()
    p["instruction_context"]["extra"] = "/data/videos/raw.mp4"
    with pytest.raises(ValueError):
        assert_safe_outbound_payload(p)


def test_data_frames_path_rejected():
    """값에 data/frames 경로 문자열이 있으면 ValueError."""
    p = _clean_payload()
    p["instruction_context"]["note"] = "path: data/frames/f_00.jpg"
    with pytest.raises(ValueError):
        assert_safe_outbound_payload(p)


def test_images_extra_key_rejected():
    """images[*]에 허용 키 외 키가 있으면 ValueError."""
    p = _clean_payload()
    p["images"][0]["extra_field"] = "bad"
    with pytest.raises(ValueError, match="허용되지 않은 키"):
        assert_safe_outbound_payload(p)


def test_mp4_extension_in_value_rejected():
    """값에 .mp4 확장자 문자열이 있으면 ValueError."""
    p = _clean_payload()
    p["instruction_context"]["debug"] = "source.mp4"
    with pytest.raises(ValueError, match=r"\.mp4"):
        assert_safe_outbound_payload(p)


def test_mov_extension_in_value_rejected():
    """값에 .mov 확장자 문자열이 있으면 ValueError."""
    p = _clean_payload()
    p["instruction_context"]["debug"] = "clip.mov"
    with pytest.raises(ValueError, match=r"\.mov"):
        assert_safe_outbound_payload(p)


def test_score_key_rejected():
    """score 키가 있으면 ValueError."""
    p = _clean_payload()
    p["score"] = 0.9
    with pytest.raises(ValueError, match="score"):
        assert_safe_outbound_payload(p)


def test_api_key_pattern_rejected():
    """값에 OpenAI 스타일 API 키 패턴이 있으면 ValueError."""
    p = _clean_payload()
    p["instruction_context"]["debug"] = "sk-abcdefghijklmnopqrstuvwxyz123456"
    with pytest.raises(ValueError, match="API 키 패턴"):
        assert_safe_outbound_payload(p)


def test_webm_extension_in_value_rejected():
    """값에 .webm 확장자 문자열이 있으면 ValueError (F-3: builder와 동기화)."""
    p = _clean_payload()
    p["instruction_context"]["debug"] = "clip.webm"
    with pytest.raises(ValueError, match=r"\.webm"):
        assert_safe_outbound_payload(p)


def test_data_b64_value_exempt_from_scan():
    """data_b64 값은 스캔 대상에서 제외된다 (F-4: 오탐·성능 방지).

    base64 데이터에 API 키 패턴·민감 경로처럼 보이는 문자열이 우연히 포함돼도 통과해야 한다.
    """
    p = _clean_payload()
    # API 키 패턴 + 경로처럼 보이는 문자열을 base64 값에 넣어도 차단되지 않아야 함
    p["images"][0]["data_b64"] = "sk-abcdefghijklmnopqrstuvwxyz123456data/videos"
    assert_safe_outbound_payload(p)  # 예외 없이 통과


def test_forbidden_key_inside_image_still_rejected():
    """data_b64를 제외해도 image 내 금지 키(image_ref 등)는 여전히 차단된다."""
    p = _clean_payload()
    p["images"][0]["image_ref"] = "/some/path.jpg"
    with pytest.raises(ValueError):
        assert_safe_outbound_payload(p)
