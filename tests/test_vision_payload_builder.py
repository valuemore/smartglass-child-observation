"""payload_builder 단위 테스트.

실제 외부 API 호출 없음. 프레임 파일은 tmp_path 픽스처로 임시 생성.
"""
import base64
from pathlib import Path

import pytest

from core.schemas import FrameRef, SegmentAnalysisRequest
from services.vision.payload_builder import build_external_payload


def _make_request(tmp_path: Path, n_frames: int = 2, suffix: str = ".jpg") -> SegmentAnalysisRequest:
    """임시 프레임 파일을 생성하고 SegmentAnalysisRequest를 반환한다."""
    frames = []
    for i in range(n_frames):
        img_file = tmp_path / f"f_{i:02d}{suffix}"
        img_file.write_bytes(b"\xff\xd8\xff" + b"X" * 100)  # 최소 JPEG 바이트
        frames.append(FrameRef(frame_id=f"f_{i:02d}", t=float(i), image_ref=str(img_file)))
    return SegmentAnalysisRequest(
        video_id="vid_t",
        segment_id="seg_t",
        time_start=0.0,
        time_end=5.0,
        frames=frames,
    )


def test_images_contain_only_allowed_keys(tmp_path):
    """images[*]에는 frame_id/mime/data_b64 키만 포함되어야 한다."""
    req = _make_request(tmp_path)
    payload = build_external_payload(req)
    allowed = {"frame_id", "mime", "data_b64"}
    for img in payload["images"]:
        assert set(img.keys()) == allowed


def test_no_path_strings_in_payload(tmp_path):
    """payload에는 파일 경로 문자열이 없어야 한다."""
    req = _make_request(tmp_path)
    payload = build_external_payload(req)
    import json
    payload_str = json.dumps(payload)
    # data/ 또는 C:\ 등 경로 문자열 미포함 확인
    assert str(tmp_path).replace("\\", "/") not in payload_str
    assert "image_ref" not in payload_str
    assert "stored_path" not in payload_str


def test_frame_count_limit(tmp_path):
    """max_frames 상한 초과 프레임은 잘려야 한다."""
    req = _make_request(tmp_path, n_frames=5)
    payload = build_external_payload(req, max_frames=3)
    assert len(payload["images"]) == 3


def test_per_image_size_limit_raises(tmp_path):
    """이미지 크기가 max_image_bytes를 초과하면 ValueError."""
    req = _make_request(tmp_path, n_frames=1)
    with pytest.raises(ValueError, match="이미지 크기"):
        build_external_payload(req, max_image_bytes=10)  # 10 bytes 상한


def test_total_payload_size_limit_raises(tmp_path):
    """payload 총 크기가 max_payload_bytes를 초과하면 ValueError."""
    req = _make_request(tmp_path, n_frames=2)
    with pytest.raises(ValueError, match="payload 총 크기"):
        build_external_payload(req, max_payload_bytes=100)  # 100 bytes 상한


def test_video_extension_rejected(tmp_path):
    """프레임 image_ref에 영상 확장자(.mp4)가 있으면 ValueError."""
    mp4_file = tmp_path / "clip.mp4"
    mp4_file.write_bytes(b"\x00\x00\x00\x18ftyp" + b"X" * 100)
    req = SegmentAnalysisRequest(
        video_id="vid_t",
        segment_id="seg_t",
        time_start=0.0,
        time_end=5.0,
        frames=[FrameRef(frame_id="f_00", t=0.0, image_ref=str(mp4_file))],
    )
    with pytest.raises(ValueError, match="영상 파일 확장자"):
        build_external_payload(req)


def test_base64_decodes_correctly(tmp_path):
    """data_b64는 원본 파일 바이트로 디코딩되어야 한다."""
    req = _make_request(tmp_path, n_frames=1)
    original_bytes = Path(req.frames[0].image_ref).read_bytes()
    payload = build_external_payload(req)
    decoded = base64.b64decode(payload["images"][0]["data_b64"])
    assert decoded == original_bytes
