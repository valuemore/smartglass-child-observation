"""
services/video_preprocess_service.py 단위 테스트.

원칙:
- tmp_path 만 사용. data/ 아래 실제 경로에 절대 쓰지 않는다.
- cv2 미설치 시 전체 모듈을 skip.
- OpenCV VideoWriter 로 synthetic 영상(30fps, 3초)을 생성해 사용한다.
"""

import sys
import uuid
from pathlib import Path

import pytest

cv2 = pytest.importorskip("cv2")  # cv2 없으면 모듈 전체 skip

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from services.video_preprocess_service import (
    _fallback_scenes,
    extract_representative_frames,
    preprocess_video,
    split_video_into_scenes,
)
from core.config import FRAME_BLUR_THRESHOLD
from core.schemas import Video
from storage.sqlite_repository import SqliteRepository

import numpy as np
from datetime import datetime


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def repo(tmp_path: Path) -> SqliteRepository:
    r = SqliteRepository(str(tmp_path / "test.db"))
    r.init_schema()
    return r


@pytest.fixture()
def synthetic_video_path(tmp_path: Path) -> Path:
    """30fps, 640×480, 3초짜리 컬러 영상을 생성한다."""
    path = tmp_path / "test_video.mp4"
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(str(path), fourcc, 30, (640, 480))
    for frame_idx in range(90):  # 3초 × 30fps
        # 시간에 따라 색이 변하는 프레임 (blur_score 확보)
        color = int(frame_idx * 2.8) % 256
        img = np.full((480, 640, 3), color, dtype=np.uint8)
        # 텍스처 추가 — Laplacian 값이 0 이상이 되도록
        img[::10, ::10] = [255 - color, color, 128]
        writer.write(img)
    writer.release()
    return path


@pytest.fixture()
def video_obj(tmp_path: Path, synthetic_video_path: Path, repo: SqliteRepository) -> Video:
    """DB에 저장된 Video 객체를 반환한다."""
    from datetime import timedelta
    v = Video(
        id=f"vid_test_{uuid.uuid4().hex[:6]}",
        filename="test_video.mp4",
        stored_path=str(synthetic_video_path),
        duration_sec=3.0,
        fps=30.0,
        width=640,
        height=480,
        status="uploaded",
        created_at=datetime.now(),
        retention_until=datetime.now() + timedelta(days=180),
    )
    repo.save_video(v)
    return v


# ---------------------------------------------------------------------------
# 1. preprocess_video 실행 후 scene 이 저장되어야 한다
# ---------------------------------------------------------------------------

def test_preprocess_creates_scenes(tmp_path: Path, repo: SqliteRepository, video_obj: Video):
    frames_dir = str(tmp_path / "frames")
    scenes, _ = preprocess_video(
        video_id=video_obj.id,
        repo=repo,
        frames_dir=frames_dir,
        actor="test_actor",
    )
    assert len(scenes) >= 1, "scene 이 1개 이상 저장되어야 한다."
    saved = repo.list_scenes(video_obj.id)
    assert len(saved) == len(scenes)


# ---------------------------------------------------------------------------
# 2. preprocess_video 실행 후 frame 이 저장되어야 한다
# ---------------------------------------------------------------------------

def test_preprocess_creates_frames(tmp_path: Path, repo: SqliteRepository, video_obj: Video):
    frames_dir = str(tmp_path / "frames")
    scenes, frames = preprocess_video(
        video_id=video_obj.id,
        repo=repo,
        frames_dir=frames_dir,
        actor="test_actor",
    )
    assert len(frames) >= 1, "frame 이 1개 이상 저장되어야 한다."
    for scene in scenes:
        saved = repo.list_frames(scene.id)
        assert len(saved) >= 1


# ---------------------------------------------------------------------------
# 3. 추출된 프레임 이미지 파일이 디스크에 존재해야 한다
# ---------------------------------------------------------------------------

def test_frame_images_exist_on_disk(tmp_path: Path, repo: SqliteRepository, video_obj: Video):
    frames_dir = str(tmp_path / "frames")
    _, frames = preprocess_video(
        video_id=video_obj.id,
        repo=repo,
        frames_dir=frames_dir,
        actor="test_actor",
    )
    for frm in frames:
        assert Path(frm.image_path).exists(), f"이미지 파일 없음: {frm.image_path}"


# ---------------------------------------------------------------------------
# 4. blur_score 와 kept 값이 올바르게 저장되어야 한다
# ---------------------------------------------------------------------------

def test_blur_score_and_kept_stored(tmp_path: Path, repo: SqliteRepository, video_obj: Video):
    frames_dir = str(tmp_path / "frames")
    _, frames = preprocess_video(
        video_id=video_obj.id,
        repo=repo,
        frames_dir=frames_dir,
        blur_threshold=0.0,   # 모든 프레임이 kept=True 가 되도록
        actor="test_actor",
    )
    for frm in frames:
        assert frm.blur_score >= 0.0
        assert isinstance(frm.kept, bool)
    # threshold=0 이면 모두 kept=True
    assert all(f.kept for f in frames), "blur_threshold=0 이면 모든 프레임이 kept=True 이어야 한다."


# ---------------------------------------------------------------------------
# 5. audit_log action=analyze 가 기록되어야 한다
# ---------------------------------------------------------------------------

def test_audit_log_analyze_recorded(tmp_path: Path, repo: SqliteRepository, video_obj: Video):
    frames_dir = str(tmp_path / "frames")
    preprocess_video(
        video_id=video_obj.id,
        repo=repo,
        frames_dir=frames_dir,
        actor="test_actor",
    )
    logs = repo.list_audit_logs(video_obj.id)
    analyze_logs = [l for l in logs if l.action == "analyze"]
    assert len(analyze_logs) >= 1, "audit_log에 action=analyze 가 1건 이상 있어야 한다."
    log = analyze_logs[0]
    assert "scene_split_and_frame_extraction" in (log.detail or "")
    assert "scenes=" in (log.detail or "")
    assert "frames=" in (log.detail or "")


# ---------------------------------------------------------------------------
# 6. 전처리 후 video status 가 analyzed 로 변경되어야 한다
# ---------------------------------------------------------------------------

def test_video_status_updated_to_analyzed(tmp_path: Path, repo: SqliteRepository, video_obj: Video):
    frames_dir = str(tmp_path / "frames")
    preprocess_video(
        video_id=video_obj.id,
        repo=repo,
        frames_dir=frames_dir,
        actor="test_actor",
    )
    updated = repo.get_video(video_obj.id)
    assert updated is not None
    assert updated.status == "analyzed", f"status 가 analyzed 이어야 하는데 {updated.status!r} 입니다."


# ---------------------------------------------------------------------------
# 7. scenedetect 없이 fallback 장면 분할이 작동해야 한다
# ---------------------------------------------------------------------------

def test_fallback_scene_split(video_obj: Video):
    """_fallback_scenes 직접 호출 — scenedetect 의존 없음."""
    scenes = _fallback_scenes(video_obj, interval_sec=1.0)
    assert len(scenes) >= 1
    for sc in scenes:
        assert sc.time_end > sc.time_start
        assert sc.video_id == video_obj.id
        assert sc.detector == "fallback_fixed"


# ---------------------------------------------------------------------------
# 8. 모든 프레임이 threshold 미만이어도 scene당 최소 1개 kept=True 보장
# ---------------------------------------------------------------------------

def test_fallback_kept_when_all_below_threshold(tmp_path: Path, repo: SqliteRepository, video_obj: Video):
    """blur_threshold 를 극단값으로 올려도 scene당 최소 1개 kept=True 여야 한다."""
    frames_dir = str(tmp_path / "frames")
    _, frames = preprocess_video(
        video_id=video_obj.id,
        repo=repo,
        frames_dir=frames_dir,
        blur_threshold=999_999.0,  # 어떤 프레임도 통과하지 못하는 값
        actor="test_actor",
    )
    # 전체에서 kept=True 가 1개 이상이어야 한다
    kept_frames = [f for f in frames if f.kept]
    assert len(kept_frames) >= 1, "fallback: 전체에 최소 1개 이상 kept=True 이어야 한다"

    # 각 scene에서 최소 1개 kept=True
    scenes = repo.list_scenes(video_obj.id)
    for scene in scenes:
        scene_frames = [f for f in frames if f.scene_id == scene.id]
        if scene_frames:  # 프레임이 추출된 scene에만 적용
            assert any(f.kept for f in scene_frames), (
                f"scene {scene.id}: 최소 1개 kept=True 이어야 한다 (fallback)"
            )

    # blur_score 값은 그대로 유지되어야 한다 (fallback이 score를 바꾸지 않음)
    for f in kept_frames:
        assert f.blur_score >= 0.0
