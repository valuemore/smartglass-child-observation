"""generate_mock_observation_candidates 단위 테스트.

원칙:
- tmp_path 만 사용. data/ 아래 실제 경로에 절대 쓰지 않는다.
- cv2 미설치 시 모듈 전체 skip.
"""

import sys
import uuid
from datetime import datetime, timedelta
from pathlib import Path

import pytest

cv2 = pytest.importorskip("cv2")
import numpy as np

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.schemas import Frame, Scene, Video
from services.observation_service import generate_mock_observation_candidates
from storage.sqlite_repository import SqliteRepository


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def repo(tmp_path: Path) -> SqliteRepository:
    r = SqliteRepository(str(tmp_path / "test.db"))
    r.init_schema()
    return r


def _make_video(tmp_path: Path, suffix: str = "") -> Video:
    path = tmp_path / f"dummy{suffix}.mp4"
    path.write_bytes(b"\x00" * 16)
    return Video(
        id=f"vid_test_{uuid.uuid4().hex[:6]}",
        filename=path.name,
        stored_path=str(path),
        duration_sec=5.0,
        fps=30.0,
        width=640,
        height=480,
        status="analyzed",
        created_at=datetime.now(),
        retention_until=datetime.now() + timedelta(days=180),
    )


def _make_frame_image(frame_dir: Path, name: str = "frm_000.jpg") -> Path:
    frame_dir.mkdir(parents=True, exist_ok=True)
    path = frame_dir / name
    img = np.zeros((480, 640, 3), dtype=np.uint8)
    cv2.imwrite(str(path), img)
    return path


@pytest.fixture()
def video_with_kept_frame(repo: SqliteRepository, tmp_path: Path) -> Video:
    """video + scene + kept=True frame 1개가 저장된 상태."""
    v = _make_video(tmp_path)
    repo.save_video(v)

    scene = Scene(
        id=f"scene_{v.id}_0000",
        video_id=v.id,
        time_start=0.0,
        time_end=5.0,
        detector="fallback_fixed",
    )
    repo.add_scenes([scene])

    img_path = _make_frame_image(tmp_path / "frames" / v.id / scene.id)
    frame = Frame(
        id=f"frm_{scene.id}_000",
        scene_id=scene.id,
        t=2.5,
        image_path=str(img_path),
        blur_score=60.0,
        kept=True,
    )
    repo.add_frames([frame])
    return v


@pytest.fixture()
def video_no_kept_frames(repo: SqliteRepository, tmp_path: Path) -> Video:
    """video + scene + kept=False frame 만 있는 상태."""
    v = _make_video(tmp_path, suffix="_nokeep")
    repo.save_video(v)

    scene = Scene(
        id=f"scene_{v.id}_0000",
        video_id=v.id,
        time_start=0.0,
        time_end=5.0,
        detector="fallback_fixed",
    )
    repo.add_scenes([scene])

    img_path = _make_frame_image(tmp_path / "frames" / v.id / scene.id, "frm_001.jpg")
    frame = Frame(
        id=f"frm_{scene.id}_000",
        scene_id=scene.id,
        t=2.5,
        image_path=str(img_path),
        blur_score=10.0,
        kept=False,
    )
    repo.add_frames([frame])
    return v


# ---------------------------------------------------------------------------
# 1. 후보가 DB에 저장되어야 한다
# ---------------------------------------------------------------------------

def test_candidates_saved_to_db(repo: SqliteRepository, video_with_kept_frame: Video):
    candidates = generate_mock_observation_candidates(
        video_with_kept_frame.id, repo, actor="test_actor"
    )
    assert len(candidates) >= 1
    saved = repo.list_candidates(video_with_kept_frame.id)
    assert len(saved) == len(candidates)


# ---------------------------------------------------------------------------
# 2. audit_log 에 mock_vision_candidate_generation 이 기록되어야 한다
# ---------------------------------------------------------------------------

def test_audit_log_recorded(repo: SqliteRepository, video_with_kept_frame: Video):
    generate_mock_observation_candidates(
        video_with_kept_frame.id, repo, actor="test_actor"
    )
    logs = repo.list_audit_logs(video_with_kept_frame.id)
    analyze_logs = [l for l in logs if l.action == "analyze"]
    assert len(analyze_logs) >= 1
    mock_log = next(
        (l for l in analyze_logs if "mock_vision_candidate_generation" in (l.detail or "")),
        None,
    )
    assert mock_log is not None, "audit_log 에 mock_vision_candidate_generation 없음"


# ---------------------------------------------------------------------------
# 3. kept=False 프레임만 있으면 빈 리스트 반환 (오류 없음)
# ---------------------------------------------------------------------------

def test_empty_when_no_kept_frames(repo: SqliteRepository, video_no_kept_frames: Video):
    candidates = generate_mock_observation_candidates(
        video_no_kept_frames.id, repo, actor="test_actor"
    )
    assert candidates == [], "kept 프레임 없으면 빈 리스트여야 한다"


# ---------------------------------------------------------------------------
# 4. scene 이 없으면 빈 리스트 반환 (오류 없음)
# ---------------------------------------------------------------------------

def test_empty_when_no_scenes(repo: SqliteRepository, tmp_path: Path):
    v = _make_video(tmp_path, suffix="_noscene")
    repo.save_video(v)
    candidates = generate_mock_observation_candidates(v.id, repo, actor="test_actor")
    assert candidates == [], "scene 없으면 빈 리스트여야 한다"


# ---------------------------------------------------------------------------
# 5. 없는 video_id 는 ValueError
# ---------------------------------------------------------------------------

def test_invalid_video_raises(repo: SqliteRepository):
    with pytest.raises(ValueError, match="video_id="):
        generate_mock_observation_candidates("nonexistent_vid_xxx", repo)


# ---------------------------------------------------------------------------
# 6. 저장된 후보가 ObservationCandidate 타입으로 복원되어야 한다
# ---------------------------------------------------------------------------

def test_candidate_restored_as_schema(repo: SqliteRepository, video_with_kept_frame: Video):
    from core.schemas import ObservationCandidate
    generate_mock_observation_candidates(
        video_with_kept_frame.id, repo, actor="test_actor"
    )
    saved = repo.list_candidates(video_with_kept_frame.id)
    for c in saved:
        assert isinstance(c, ObservationCandidate)
        assert c.video_id == video_with_kept_frame.id
        assert c.needs_teacher_review is True
        assert 0.0 <= c.confidence <= 1.0


# ---------------------------------------------------------------------------
# 7. 커스텀 어댑터를 주입할 수 있어야 한다
# ---------------------------------------------------------------------------

def test_custom_adapter_injection(repo: SqliteRepository, video_with_kept_frame: Video):
    """adapter 파라미터로 커스텀 Mock을 주입할 수 있어야 한다."""
    from services.vision.mock_adapter import MockVisionAdapter

    class CountingAdapter(MockVisionAdapter):
        call_count: int = 0

        def analyze_segment(self, request):
            self.call_count += 1
            return super().analyze_segment(request)

    custom = CountingAdapter()
    generate_mock_observation_candidates(
        video_with_kept_frame.id, repo, adapter=custom, actor="test_actor"
    )
    assert custom.call_count >= 1, "커스텀 어댑터가 호출되어야 한다"


# ---------------------------------------------------------------------------
# 8. DB kept 값 roundtrip: int 1로 저장 → list_frames에서 True로 복원
# ---------------------------------------------------------------------------

def test_list_frames_kept_roundtrip(repo: SqliteRepository, tmp_path: Path):
    """add_frames(kept=True) → list_frames() → frame.kept is True."""
    v = _make_video(tmp_path, suffix="_roundtrip")
    repo.save_video(v)

    scene = Scene(
        id=f"scene_{v.id}_0000",
        video_id=v.id,
        time_start=0.0,
        time_end=5.0,
        detector="fallback_fixed",
    )
    repo.add_scenes([scene])

    img_path = _make_frame_image(tmp_path / "frames" / v.id / scene.id, "frm_rt.jpg")
    frame = Frame(
        id=f"frm_{scene.id}_rt",
        scene_id=scene.id,
        t=2.5,
        image_path=str(img_path),
        blur_score=60.0,
        kept=True,
    )
    repo.add_frames([frame])

    loaded = repo.list_frames(scene.id)
    assert len(loaded) == 1
    assert loaded[0].kept is True, f"kept 복원 실패: {loaded[0].kept!r} (type={type(loaded[0].kept).__name__})"
    assert isinstance(loaded[0].kept, bool)


# ---------------------------------------------------------------------------
# 9. kept=False → kept=True 재저장 시 DB 값이 업데이트되어야 한다 (upsert)
# ---------------------------------------------------------------------------

def test_add_frames_upsert_updates_kept(repo: SqliteRepository, tmp_path: Path):
    """INSERT OR IGNORE 대신 upsert: kept=False 후 kept=True 재저장 → DB에 True."""
    v = _make_video(tmp_path, suffix="_upsert")
    repo.save_video(v)

    scene = Scene(
        id=f"scene_{v.id}_0000",
        video_id=v.id,
        time_start=0.0,
        time_end=5.0,
        detector="fallback_fixed",
    )
    repo.add_scenes([scene])

    img_path = _make_frame_image(tmp_path / "frames" / v.id / scene.id, "frm_up.jpg")
    frame_id = f"frm_{scene.id}_up"

    # 1차 저장: kept=False
    repo.add_frames([Frame(
        id=frame_id,
        scene_id=scene.id,
        t=2.5,
        image_path=str(img_path),
        blur_score=10.0,
        kept=False,
    )])
    assert repo.list_frames(scene.id)[0].kept is False

    # 2차 저장: kept=True (fallback 결과)
    repo.add_frames([Frame(
        id=frame_id,
        scene_id=scene.id,
        t=2.5,
        image_path=str(img_path),
        blur_score=10.0,
        kept=True,
    )])
    updated = repo.list_frames(scene.id)
    assert updated[0].kept is True, "재전처리 후 kept=True 가 DB에 반영되어야 한다"


# ---------------------------------------------------------------------------
# 10. kept=False 후 kept=True 재저장 → observation_service 가 후보 생성
# ---------------------------------------------------------------------------

def test_kept_updated_via_upsert_generates_candidates(repo: SqliteRepository, tmp_path: Path):
    """DB에 kept=False로 먼저 저장된 뒤 재전처리로 kept=True가 되면 후보가 생성되어야 한다."""
    v = _make_video(tmp_path, suffix="_fixed")
    repo.save_video(v)

    scene = Scene(
        id=f"scene_{v.id}_0000",
        video_id=v.id,
        time_start=0.0,
        time_end=5.0,
        detector="fallback_fixed",
    )
    repo.add_scenes([scene])

    img_path = _make_frame_image(tmp_path / "frames" / v.id / scene.id, "frm_fix.jpg")
    frame_id = f"frm_{scene.id}_fix"

    # 1차: kept=False (이전 전처리 결과 시뮬레이션)
    repo.add_frames([Frame(
        id=frame_id,
        scene_id=scene.id,
        t=2.5,
        image_path=str(img_path),
        blur_score=10.0,
        kept=False,
    )])

    # 이 상태에서는 후보 0개
    assert generate_mock_observation_candidates(v.id, repo, actor="test") == []

    # 2차: kept=True (fallback 적용 재전처리 시뮬레이션)
    repo.add_frames([Frame(
        id=frame_id,
        scene_id=scene.id,
        t=2.5,
        image_path=str(img_path),
        blur_score=10.0,
        kept=True,
    )])

    # 이제 후보가 생성되어야 한다
    candidates = generate_mock_observation_candidates(v.id, repo, actor="test")
    assert len(candidates) >= 1, "upsert 후 kept=True 프레임이 있으면 후보가 생성되어야 한다"


# ---------------------------------------------------------------------------
# 11. 재실행해도 관찰 후보가 중복 누적되지 않는다
# ---------------------------------------------------------------------------

def test_no_duplicate_candidates_on_rerun(repo: SqliteRepository, video_with_kept_frame: Video):
    """generate_mock_observation_candidates 를 두 번 실행해도 후보가 누적되지 않아야 한다."""
    first = generate_mock_observation_candidates(
        video_with_kept_frame.id, repo, actor="test_actor"
    )
    first_saved = repo.list_candidates(video_with_kept_frame.id)
    assert len(first_saved) == len(first)

    # 재실행 — 기존 후보 삭제 후 재생성되므로 개수가 누적되지 않아야 한다
    second = generate_mock_observation_candidates(
        video_with_kept_frame.id, repo, actor="test_actor"
    )
    second_saved = repo.list_candidates(video_with_kept_frame.id)
    assert len(second_saved) == len(second), "재실행 시 후보가 중복 저장되면 안 된다"
    assert len(second_saved) == len(first_saved), "재실행 후 후보 개수가 동일해야 한다"


# ---------------------------------------------------------------------------
# 12. 후보 재생성 시 연결된 매핑도 함께 삭제된다 (고아 매핑 방지)
# ---------------------------------------------------------------------------

def test_rerun_clears_linked_mappings(repo: SqliteRepository, video_with_kept_frame: Video):
    """후보에 매핑이 있는 상태에서 후보를 재생성하면 기존 매핑이 정리되어야 한다."""
    from core.schemas import ScaleMappingCandidate

    cands = generate_mock_observation_candidates(
        video_with_kept_frame.id, repo, actor="test_actor"
    )
    assert len(cands) >= 1
    old_id = cands[0].id

    # 임의 매핑 1건 저장
    repo.add_mappings([ScaleMappingCandidate(
        id="map_test_0001",
        candidate_id=old_id,
        scale="nuri",
        area="자연탐구",
        item_id=None,
        item_text="자연탐구",
        rationale="테스트",
        confidence=0.7,
    )])
    assert len(repo.list_mappings(old_id)) == 1

    # 후보 재생성 — 기존 후보 삭제 시 연결 매핑도 삭제되어야 한다
    generate_mock_observation_candidates(
        video_with_kept_frame.id, repo, actor="test_actor"
    )
    assert repo.list_mappings(old_id) == [], "고아 매핑이 남으면 안 된다"
