"""백그라운드 분석 큐 테스트 (V2-5).

tmp_path 기반 격리. 워커 스레드의 실제 분석은 호출하지 않고
submit 중복 차단·고아 정리 로직만 검증한다(run_auto_analysis는 monkeypatch).
"""

import time
from datetime import datetime
from pathlib import Path

import pytest

from core.schemas import Video
from storage.sqlite_repository import SqliteRepository
import services.analysis_queue as aq
from services.analysis_queue import AnalysisQueue


@pytest.fixture
def db_path(tmp_path: Path) -> str:
    db = SqliteRepository(str(tmp_path / "queue.db"))
    db.init_schema()
    return str(tmp_path / "queue.db")


def _seed_video(repo: SqliteRepository, vid: str, status: str, progress: int) -> None:
    repo.save_video(Video(
        id=vid, filename=f"{vid}.mp4", stored_path="/nope.mp4",
        duration_sec=30.0, fps=30.0, width=1280, height=720,
        status="uploaded", created_at=datetime(2026, 6, 17, 10, 0, 0),
        captured_date="2026-06-17",
    ))
    repo.update_analysis_status(vid, status, progress, None)


def test_reset_orphans_marks_running_as_failed(db_path, monkeypatch):
    """프로세스 시작 시 'running'으로 멈춘 영상을 실패로 정리한다."""
    monkeypatch.setattr(aq, "DB_PATH", db_path)
    repo = SqliteRepository(db_path)
    _seed_video(repo, "vid_stuck", "running", 70)
    _seed_video(repo, "vid_done", "done", 100)

    AnalysisQueue()  # __init__에서 _reset_orphans 실행

    assert repo.get_video("vid_stuck").analysis_status == "failed"
    assert repo.get_video("vid_done").analysis_status == "done"  # 변경 없음


def test_submit_dedup(db_path, monkeypatch):
    """동일 video_id 중복 제출은 무시(False)한다."""
    monkeypatch.setattr(aq, "DB_PATH", db_path)

    processed: list[str] = []
    gate = {"block": True}

    def _fake_run(video_id, repo, **kwargs):
        # 첫 작업을 잠시 붙잡아 inflight 상태를 유지시킨다
        while gate["block"]:
            time.sleep(0.01)
        processed.append(video_id)

    monkeypatch.setattr(aq, "run_auto_analysis", _fake_run)

    q = AnalysisQueue()
    assert q.submit("vid_A", "teacher") is True
    # 워커가 vid_A를 잡고 멈춘 동안 같은 ID 재제출은 거부
    time.sleep(0.05)
    assert q.submit("vid_A", "teacher") is False
    assert q.submit("vid_B", "teacher") is True

    gate["block"] = False
    time.sleep(0.2)
    assert "vid_A" in processed
    assert "vid_B" in processed
