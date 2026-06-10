"""클래스 단위 지원도 리포트·export 테스트 (V2-8). tmp_path 격리."""

import json
from datetime import datetime
from pathlib import Path

import pytest

from core.schemas import (
    ClassGroup, FinalRecord, KicceItemCandidate, NuriAreaCandidate,
    ObservationCandidate, Scene, Video,
)
from services.report_service import (
    build_class_report, calculate_support_metrics, calculate_by_period,
    export_class_report_json,
)
from storage.sqlite_repository import SqliteRepository


@pytest.fixture
def repo(tmp_path: Path) -> SqliteRepository:
    db = SqliteRepository(str(tmp_path / "rep.db"))
    db.init_schema()
    return db


def _setup(repo):
    repo.save_class(ClassGroup(id="cls_01", name="햇빛반", teacher_owner="teacher_01",
                               created_at=datetime(2026, 3, 1, 9, 0, 0)))
    repo.save_video(Video(
        id="vid_1", filename="d1.mp4", stored_path="data/videos/vid_1/original.mp4",
        duration_sec=10, fps=30, width=640, height=480, status="reviewed",
        created_at=datetime(2026, 6, 9, 10, 0, 0), class_id="cls_01", captured_date="2026-06-09",
    ))
    repo.add_scenes([Scene(id="s1", video_id="vid_1", time_start=0, time_end=10)])
    # 3 candidates with varying confidence
    for cid, conf in [("c1", 0.8), ("c2", 0.5), ("c3", 0.3)]:
        repo.add_candidates([ObservationCandidate(
            id=cid, video_id="vid_1", scene_id="s1", time_start=1, time_end=5,
            temp_child_id="child_A", observed_behavior=f"행동 {cid}", visual_evidence="근거",
            nuri_area_candidates=[NuriAreaCandidate(area="자연탐구", rationale="r", confidence=0.6)],
            kicce_item_candidates=[KicceItemCandidate(item_id=34, item_text="문항", rationale="r", confidence=0.5)],
            confidence=conf, created_at=datetime(2026, 6, 9, 10, 1, 0),
        )])


def _final(cid, decision, pid="p_07", areas=("자연탐구",), items=True,
           ps="2026-06-04", pe="2026-06-10"):
    # 실제 화면 동작과 동일: 기각은 영역·문항을 기여하지 않는다.
    is_rej = decision == "rejected"
    return FinalRecord(
        id=f"rec_{cid}", candidate_id=cid, pseudonym_id=pid,
        weekly_draft_id=f"wd_{cid}", period_start=ps, period_end=pe,
        final_behavior="확정 서술",
        confirmed_areas=([] if is_rej else list(areas)),
        confirmed_items=([] if (is_rej or not items)
                         else [KicceItemCandidate(item_id=34, item_text="문항", rationale="r", confidence=0.5)]),
        decision=decision, edited=(decision == "edited"),
        confirmed_by="teacher_01", confirmed_at=datetime(2026, 6, 10, 17, 0, 0),
    )


# ---------------------------------------------------------------------------
# 기본 집계
# ---------------------------------------------------------------------------

def test_build_class_report_basic(repo):
    _setup(repo)
    repo.save_final_record(_final("c1", "accepted"))
    repo.save_final_record(_final("c2", "edited"))
    repo.save_final_record(_final("c3", "rejected"))

    rep = build_class_report(repo=repo, class_id="cls_01")
    assert rep["class_name"] == "햇빛반"
    assert rep["total_videos"] == 1
    assert rep["total_candidates"] == 3
    assert rep["total_finals"] == 3
    assert rep["accepted"] == 1
    assert rep["edited"] == 1
    assert rep["rejected"] == 1
    assert rep["area_distribution"]["자연탐구"] == 2  # accepted + edited (rejected has areas too -> 3? )


# ---------------------------------------------------------------------------
# 지원도 지표
# ---------------------------------------------------------------------------

def test_support_metrics_ratio_and_bands(repo):
    _setup(repo)
    finals = [_final("c1", "accepted"), _final("c2", "edited"), _final("c3", "rejected")]
    cands = repo.list_candidates("vid_1")
    sm = calculate_support_metrics(cands, finals)
    # 3 검토 중 2 활용(accepted+edited)
    assert sm["reviewed"] == 3
    assert sm["used"] == 2
    assert sm["ai_support_ratio"] == round(2 / 3, 4)
    # 신뢰도 구간: c1(0.8)=high used, c2(0.5)=mid used, c3(0.3)=low rejected
    assert sm["confidence_band_usage"]["high"]["usage_rate"] == 1.0
    assert sm["confidence_band_usage"]["mid"]["usage_rate"] == 1.0
    assert sm["confidence_band_usage"]["low"]["usage_rate"] == 0.0


# ---------------------------------------------------------------------------
# 주차별 집계
# ---------------------------------------------------------------------------

def test_by_period_grouping(repo):
    finals = [
        _final("c1", "accepted", ps="2026-06-04", pe="2026-06-10"),
        _final("c2", "edited", ps="2026-06-04", pe="2026-06-10"),
        _final("c3", "accepted", ps="2026-06-11", pe="2026-06-17"),
    ]
    by_p = calculate_by_period(finals)
    assert len(by_p) == 2
    wk1 = next(b for b in by_p if b["period_start"] == "2026-06-04")
    assert wk1["total"] == 2
    assert wk1["accepted"] == 1
    assert wk1["edited"] == 1


# ---------------------------------------------------------------------------
# export: 미디어 경로 미포함
# ---------------------------------------------------------------------------

def test_export_class_report_excludes_media_paths(repo):
    _setup(repo)
    repo.save_final_record(_final("c1", "accepted"))
    out = export_class_report_json("cls_01", repo)
    # 경로 조각이 직렬화 결과에 없어야 함
    for frag in ("data/videos", "data/frames", "data/clips", "data/faces",
                 "stored_path", "image_path", "local_clip_path", "reference_photo_path"):
        assert frag not in out
    data = json.loads(out)
    assert data["class_id"] == "cls_01"
    assert "support_metrics" in data
    # export 감사 로그 기록
    assert any(l.action == "export" for l in repo.list_audit_logs(video_id="cls_01"))


def test_export_class_report_has_no_score(repo):
    _setup(repo)
    repo.save_final_record(_final("c1", "accepted"))
    out = export_class_report_json("cls_01", repo)
    for banned in ("\"score\"", "\"level\"", "\"rating\"", "발달점수", "평정"):
        assert banned not in out
