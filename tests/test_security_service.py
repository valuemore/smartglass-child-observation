"""security_service 단위 테스트.

원칙: tmp_path 기반 SQLite DB와 임시 파일만 사용. data/ 실제 경로 금지.
"""

import sys
import uuid
from datetime import datetime
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.schemas import Scene, Video
from services.security_service import (
    assert_no_sensitive_paths,
    delete_video_and_related_data,
    sanitize_report_export,
)
from storage.sqlite_repository import SqliteRepository


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def repo(tmp_path: Path) -> SqliteRepository:
    r = SqliteRepository(str(tmp_path / "test.db"))
    r.init_schema()
    return r


def _make_video(tmp_path: Path, vid_id: str = "vid_test") -> tuple[Video, Path]:
    """임시 영상 파일과 Video 객체를 생성한다."""
    video_file = tmp_path / f"{vid_id}.mp4"
    video_file.write_bytes(b"fake video content")
    video = Video(
        id=vid_id,
        filename=f"{vid_id}.mp4",
        stored_path=str(video_file),
        duration_sec=30.0,
        fps=30.0,
        width=640,
        height=480,
        status="analyzed",
        created_at=datetime.now(),
    )
    return video, video_file


# ---------------------------------------------------------------------------
# T1. 삭제 후 원본 영상 파일이 존재하지 않음
# ---------------------------------------------------------------------------

def test_delete_removes_video_file(tmp_path: Path, repo: SqliteRepository) -> None:
    video, video_file = _make_video(tmp_path)
    repo.save_video(video)
    assert video_file.exists()

    result = delete_video_and_related_data(
        video.id, repo,
        videos_dir=str(tmp_path),
        frames_dir=str(tmp_path / "frames"),
    )

    assert result["file_deleted"] is True
    assert not video_file.exists()


# ---------------------------------------------------------------------------
# T2. 삭제 후 data/frames/{video_id}/ 폴더가 존재하지 않음
# ---------------------------------------------------------------------------

def test_delete_removes_frames_dir(tmp_path: Path, repo: SqliteRepository) -> None:
    video, _ = _make_video(tmp_path)
    repo.save_video(video)

    frames_dir = tmp_path / "frames"
    video_frames = frames_dir / video.id
    video_frames.mkdir(parents=True)
    (video_frames / "frame_001.jpg").write_bytes(b"fake frame")

    result = delete_video_and_related_data(
        video.id, repo,
        videos_dir=str(tmp_path),
        frames_dir=str(frames_dir),
    )

    assert result["frames_dir_deleted"] is True
    assert not video_frames.exists()


# ---------------------------------------------------------------------------
# T3. audit_log에 action="delete" 기록이 남음
# ---------------------------------------------------------------------------

def test_delete_writes_audit_log(tmp_path: Path, repo: SqliteRepository) -> None:
    video, _ = _make_video(tmp_path)
    repo.save_video(video)

    delete_video_and_related_data(
        video.id, repo,
        videos_dir=str(tmp_path),
        frames_dir=str(tmp_path / "frames"),
    )

    logs = repo.list_audit_logs(video.id)
    delete_logs = [lg for lg in logs if lg.action == "delete"]
    assert len(delete_logs) >= 1
    assert video.id in delete_logs[0].video_id


# ---------------------------------------------------------------------------
# T4. audit_log는 video 행 삭제 후에도 조회 가능 (고아 행 영구 보존)
# ---------------------------------------------------------------------------

def test_audit_log_survives_video_deletion(tmp_path: Path, repo: SqliteRepository) -> None:
    video, _ = _make_video(tmp_path)
    repo.save_video(video)

    delete_video_and_related_data(
        video.id, repo,
        videos_dir=str(tmp_path),
        frames_dir=str(tmp_path / "frames"),
    )

    # video 행은 삭제됨
    assert repo.get_video(video.id) is None

    # audit_log는 FK 없음 → 고아 행으로 영구 보존
    logs = repo.list_audit_logs(video.id)
    assert any(lg.action == "delete" for lg in logs)


# ---------------------------------------------------------------------------
# T5. sanitize_report_export가 stored_path, image_path를 제거함
# ---------------------------------------------------------------------------

def test_sanitize_removes_sensitive_keys() -> None:
    data = {
        "video_id": "vid_001",
        "filename": "test.mp4",
        "stored_path": "data/videos/vid_001/test.mp4",
        "image_path": "data/frames/vid_001/frame_001.jpg",
        "duration_sec": 60.0,
        "summary": {"total_candidates": 5},
    }
    result = sanitize_report_export(data)

    assert "stored_path" not in result
    assert "image_path" not in result
    assert result["video_id"] == "vid_001"
    assert result["filename"] == "test.mp4"
    assert result["duration_sec"] == 60.0
    assert "summary" in result


def test_sanitize_removes_value_with_sensitive_fragment() -> None:
    data = {
        "video_id": "vid_001",
        "video_path": "data/videos/vid_001/test.mp4",
        "export_at": "2026-06-09T10:00:00",
    }
    result = sanitize_report_export(data)

    assert "video_path" not in result
    assert result["video_id"] == "vid_001"
    assert result["export_at"] == "2026-06-09T10:00:00"


# ---------------------------------------------------------------------------
# T6. assert_no_sensitive_paths가 data/videos 경로 발견 시 ValueError 발생
# ---------------------------------------------------------------------------

def test_assert_raises_on_data_videos_path() -> None:
    payload = {"filename": "test.mp4", "stored_path": "data/videos/vid_001/test.mp4"}
    with pytest.raises(ValueError, match="민감 경로"):
        assert_no_sensitive_paths(payload)


def test_assert_raises_on_data_frames_path() -> None:
    payload = {"frames": ["data/frames/vid_001/frame_001.jpg"]}
    with pytest.raises(ValueError, match="민감 경로"):
        assert_no_sensitive_paths(payload)


def test_assert_passes_clean_payload() -> None:
    payload = {
        "video_id": "vid_001",
        "filename": "test.mp4",
        "summary": {"total_candidates": 3, "accepted": 2},
    }
    assert_no_sensitive_paths(payload)  # ValueError 없이 통과
