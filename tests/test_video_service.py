"""
services/video_service.py 단위 테스트.

원칙:
- tmp_path 만 사용. data/app.db 와 data/videos/ 에 절대 쓰지 않는다.
- cv2 없이도 메타 0-처리가 작동함을 검증한다.
- 이중 저장 방지는 repo.save_video (INSERT OR REPLACE) 로 위임.
"""

import importlib
import sys
import uuid
from pathlib import Path

import pytest

# --- sys.path 조정 (프로젝트 루트를 import 경로에 추가) ---
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from services.video_service import _extract_metadata, save_uploaded_video
from storage.sqlite_repository import SqliteRepository


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def repo(tmp_path: Path) -> SqliteRepository:
    db_file = tmp_path / "test.db"
    r = SqliteRepository(str(db_file))
    r.init_schema()
    return r


@pytest.fixture()
def videos_dir(tmp_path: Path) -> str:
    d = tmp_path / "videos"
    d.mkdir()
    return str(d)


@pytest.fixture()
def sample_bytes() -> bytes:
    """최소 mp4 헤더처럼 보이는 더미 바이트."""
    return b"\x00" * 128


# ---------------------------------------------------------------------------
# 1. 파일 저장 확인
# ---------------------------------------------------------------------------

def test_file_written_to_disk(repo, videos_dir, sample_bytes):
    video = save_uploaded_video(
        file_bytes=sample_bytes,
        filename="test.mp4",
        repo=repo,
        videos_dir=videos_dir,
    )
    assert Path(video.stored_path).exists(), "저장 경로에 파일이 없다."
    assert Path(video.stored_path).read_bytes() == sample_bytes


# ---------------------------------------------------------------------------
# 2. video_id 형식 검증
# ---------------------------------------------------------------------------

def test_video_id_format(repo, videos_dir, sample_bytes):
    video = save_uploaded_video(
        file_bytes=sample_bytes,
        filename="sample.mov",
        repo=repo,
        videos_dir=videos_dir,
    )
    parts = video.id.split("_")
    assert parts[0] == "vid"
    assert len(parts[1]) == 8   # YYYYMMDD
    assert len(parts[2]) == 6   # HHMMSS
    assert len(parts[3]) == 6   # uuid hex slice


# ---------------------------------------------------------------------------
# 3. DB 저장 확인 — repo.get_video 로 조회 가능해야 함
# ---------------------------------------------------------------------------

def test_video_saved_to_repo(repo, videos_dir, sample_bytes):
    video = save_uploaded_video(
        file_bytes=sample_bytes,
        filename="obs.mp4",
        repo=repo,
        videos_dir=videos_dir,
    )
    fetched = repo.get_video(video.id)
    assert fetched is not None
    assert fetched.id == video.id
    assert fetched.filename == "obs.mp4"
    assert fetched.status == "uploaded"


# ---------------------------------------------------------------------------
# 4. 감사 로그 기록 확인
# ---------------------------------------------------------------------------

def test_audit_log_written(repo, videos_dir, sample_bytes):
    video = save_uploaded_video(
        file_bytes=sample_bytes,
        filename="audit_test.mp4",
        repo=repo,
        videos_dir=videos_dir,
    )
    logs = repo.list_audit_logs(video.id)
    assert len(logs) == 1
    assert logs[0].action == "upload"
    assert logs[0].video_id == video.id


# ---------------------------------------------------------------------------
# 5. OpenCV 없을 때 메타데이터 0-처리 (graceful fallback)
# ---------------------------------------------------------------------------

def test_metadata_fallback_without_opencv(tmp_path: Path):
    """cv2 가 import 되지 않는 상황을 시뮬레이션: 더미 파일에서 추출 실패 → 모두 0."""
    dummy = tmp_path / "dummy.mp4"
    dummy.write_bytes(b"\x00" * 64)

    # cv2 를 일시적으로 숨기기
    original_cv2 = sys.modules.get("cv2")
    sys.modules["cv2"] = None  # type: ignore

    try:
        fps, width, height, duration = _extract_metadata(str(dummy))
    finally:
        if original_cv2 is None:
            sys.modules.pop("cv2", None)
        else:
            sys.modules["cv2"] = original_cv2

    assert fps == 0.0
    assert width == 0
    assert height == 0
    assert duration == 0.0


# ---------------------------------------------------------------------------
# 6. 확장자 보존 — .mov 파일이 original.mov 로 저장되어야 함
# ---------------------------------------------------------------------------

def test_extension_preserved(repo, videos_dir, sample_bytes):
    video = save_uploaded_video(
        file_bytes=sample_bytes,
        filename="classroom.mov",
        repo=repo,
        videos_dir=videos_dir,
    )
    assert video.stored_path.endswith("original.mov"), (
        f"확장자가 보존되어야 한다: {video.stored_path}"
    )
