"""업로드 즉시 자동 분석 오케스트레이터 테스트 (V2-3).

tmp_path 기반 격리. 외부 API 미호출(mock provider / 안전장치).
preprocess 는 사전 시드된 scene/frame 로 건너뛰어 OpenCV·ffmpeg 의존을 피한다.
"""

from datetime import datetime
from pathlib import Path

import pytest

from core import config as cfg
from core.schemas import Frame, Scene, Video
from storage.sqlite_repository import SqliteRepository
import services.auto_analysis_service as aas
from services.auto_analysis_service import run_auto_analysis


@pytest.fixture
def repo(tmp_path: Path) -> SqliteRepository:
    db = SqliteRepository(str(tmp_path / "auto.db"))
    db.init_schema()
    return db


def _seed_video_with_scene(repo: SqliteRepository, tmp_path: Path) -> str:
    """video + scene + kept frame 을 시드해 preprocess 단계를 건너뛰게 한다."""
    vid = "vid_auto_001"
    # stored_path 는 존재하지 않아도 됨(클립 추출은 실패해도 비치명적, mock은 이미지 미읽음)
    repo.save_video(Video(
        id=vid, filename="day1.mp4", stored_path=str(tmp_path / "nope.mp4"),
        duration_sec=30.0, fps=30.0, width=1280, height=720,
        status="uploaded", created_at=datetime(2026, 6, 9, 10, 0, 0),
        captured_date="2026-06-09",
    ))
    repo.add_scenes([Scene(id="seg_001", video_id=vid, time_start=0.0, time_end=10.0)])
    img = tmp_path / "f1.jpg"
    img.write_bytes(b"\xff\xd8\xff\xd9")  # 더미 JPG
    repo.add_frames([Frame(
        id="f_001", scene_id="seg_001", t=2.0,
        image_path=str(img), blur_score=200.0, kept=True,
    )])
    return vid


# ---------------------------------------------------------------------------
# 정상 흐름 (mock provider): 상태머신 done + 후보 누적
# ---------------------------------------------------------------------------

def test_auto_analysis_mock_completes(repo, tmp_path, monkeypatch):
    monkeypatch.setattr(cfg, "VISION_PROVIDER", "mock")
    monkeypatch.setattr(cfg, "VISION_DRY_RUN", True)
    vid = _seed_video_with_scene(repo, tmp_path)

    progress_log: list[tuple[int, str]] = []
    result = run_auto_analysis(
        vid, repo, frames_dir=str(tmp_path / "frames"),
        clips_dir=str(tmp_path / "clips"),
        progress_cb=lambda pct, label: progress_log.append((pct, label)),
    )

    assert result["status"] == "done"
    assert result["vision_skipped"] is False
    assert result["candidates"] >= 1

    v = repo.get_video(vid)
    assert v.analysis_status == "done"
    assert v.progress == 100
    assert v.auto_analyzed is True
    assert v.status == "accumulated"

    # 진행률은 단조 증가하며 100에서 끝난다
    pcts = [p for p, _ in progress_log]
    assert pcts[-1] == 100
    assert pcts == sorted(pcts)

    # 후보가 실제로 누적되었다
    assert len(repo.list_candidates(vid)) >= 1


# ---------------------------------------------------------------------------
# 외부 실호출 안전장치: provider=external + dry_run=False + allow_external_real=False
# ---------------------------------------------------------------------------

def test_auto_analysis_blocks_external_real_call(repo, tmp_path, monkeypatch):
    monkeypatch.setattr(cfg, "VISION_PROVIDER", "external")
    monkeypatch.setattr(cfg, "VISION_DRY_RUN", False)

    called = {"vision": False}

    def _boom(*a, **k):
        called["vision"] = True
        raise AssertionError("외부 실호출이 자동 분석에서 발생하면 안 된다")

    monkeypatch.setattr(aas, "generate_observation_candidates_with_provider", _boom)
    vid = _seed_video_with_scene(repo, tmp_path)

    result = run_auto_analysis(
        vid, repo, frames_dir=str(tmp_path / "frames"),
        clips_dir=str(tmp_path / "clips"),
        allow_external_real=False,
    )

    assert called["vision"] is False
    assert result["vision_skipped"] is True
    assert result["status"] == "done"
    assert result["candidates"] == 0
    assert repo.get_video(vid).analysis_status == "done"


# ---------------------------------------------------------------------------
# 실패 → failed 상태 + retry_count 증가, 재시도 시 복구
# ---------------------------------------------------------------------------

def test_auto_analysis_failure_sets_failed_and_increments_retry(repo, tmp_path, monkeypatch):
    monkeypatch.setattr(cfg, "VISION_PROVIDER", "mock")
    monkeypatch.setattr(cfg, "VISION_DRY_RUN", True)
    vid = _seed_video_with_scene(repo, tmp_path)

    def _fail(*a, **k):
        raise RuntimeError("비전 단계 강제 실패")

    monkeypatch.setattr(aas, "generate_observation_candidates_with_provider", _fail)
    result = run_auto_analysis(
        vid, repo, frames_dir=str(tmp_path / "frames"), clips_dir=str(tmp_path / "clips"),
    )

    assert result["status"] == "failed"
    assert "비전 단계 강제 실패" in result["error"]
    v = repo.get_video(vid)
    assert v.analysis_status == "failed"
    assert v.retry_count == 1
    assert v.last_error is not None

    # 재시도: 정상 함수로 복구하면 done 으로 전이
    import services.observation_service as obs
    monkeypatch.setattr(
        aas, "generate_observation_candidates_with_provider",
        obs.generate_observation_candidates_with_provider,
    )
    result2 = run_auto_analysis(
        vid, repo, frames_dir=str(tmp_path / "frames"), clips_dir=str(tmp_path / "clips"),
    )
    assert result2["status"] == "done"
    v2 = repo.get_video(vid)
    assert v2.analysis_status == "done"
    assert v2.retry_count == 1  # 성공 경로는 retry_count 를 더 늘리지 않는다


# ---------------------------------------------------------------------------
# 없는 video_id 는 ValueError
# ---------------------------------------------------------------------------

def test_auto_analysis_unknown_video_raises(repo, tmp_path):
    with pytest.raises(ValueError):
        run_auto_analysis(
            "no_such_video", repo,
            frames_dir=str(tmp_path / "frames"), clips_dir=str(tmp_path / "clips"),
        )
