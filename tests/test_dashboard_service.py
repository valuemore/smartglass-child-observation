"""수집 균형 대시보드 서비스 테스트 (V2-5). tmp_path 격리."""

from datetime import datetime
from pathlib import Path

import pytest

from core.schemas import (
    Child, ChildMatch, ClassGroup, NuriAreaCandidate,
    ObservationCandidate, Scene, ScaleMappingCandidate, Video,
)
from services.dashboard_service import collection_status, NURI_AREAS
from storage.sqlite_repository import SqliteRepository


@pytest.fixture
def repo(tmp_path: Path) -> SqliteRepository:
    db = SqliteRepository(str(tmp_path / "dash.db"))
    db.init_schema()
    return db


def _video(vid, class_id="cls_01", captured="2026-06-09", owner="teacher_01") -> Video:
    return Video(
        id=vid, filename=f"{vid}.mp4", stored_path=f"data/videos/{vid}.mp4",
        duration_sec=10.0, fps=30.0, width=640, height=480,
        status="accumulated", created_at=datetime(2026, 6, 9, 10, 0, 0),
        class_id=class_id, captured_date=captured, owner=owner,
    )


def _cand(cid, vid, temp="child_A", area="자연탐구") -> ObservationCandidate:
    return ObservationCandidate(
        id=cid, video_id=vid, scene_id=f"{vid}_seg", time_start=1.0, time_end=5.0,
        temp_child_id=temp, observed_behavior="행동", visual_evidence="근거",
        nuri_area_candidates=[NuriAreaCandidate(area=area, rationale="r", confidence=0.6)],
        confidence=0.6, created_at=datetime(2026, 6, 9, 10, 1, 0),
    )


def _seed_video_with_candidate(repo, vid, temp, area, class_id="cls_01"):
    repo.save_video(_video(vid, class_id=class_id))
    repo.add_scenes([Scene(id=f"{vid}_seg", video_id=vid, time_start=0.0, time_end=10.0)])
    repo.add_candidates([_cand(f"{vid}_c1", vid, temp=temp, area=area)])


# ---------------------------------------------------------------------------
# 기본 구조
# ---------------------------------------------------------------------------

def test_empty_class_returns_five_areas(repo):
    repo.save_class(ClassGroup(id="cls_01", name="햇빛반", teacher_owner="teacher_01",
                               created_at=datetime(2026, 3, 1, 9, 0, 0)))
    res = collection_status(repo, class_id="cls_01")
    assert res["areas"] == NURI_AREAS
    assert res["total_candidates"] == 0
    assert res["children"] == []
    # 등록 유아 없음 → 행 없음, 전 영역 부족
    assert set(res["area_shortage"]) == set(NURI_AREAS)


def test_registered_child_with_zero_data_is_shown(repo):
    repo.save_class(ClassGroup(id="cls_01", name="햇빛반", teacher_owner="teacher_01",
                               created_at=datetime(2026, 3, 1, 9, 0, 0)))
    repo.add_child(Child(id="chd_01", class_id="cls_01", pseudonym_id="p_07",
                         display_label="유아7", created_at=datetime(2026, 3, 2, 9, 0, 0)))
    res = collection_status(repo, class_id="cls_01")
    assert len(res["children"]) == 1
    row = res["children"][0]
    assert row["label"] == "유아7"
    assert row["total"] == 0
    assert set(row["shortage_areas"]) == set(NURI_AREAS)  # 자료 0 → 전 영역 부족


# ---------------------------------------------------------------------------
# 미매칭 후보는 unmatched 버킷에 집계
# ---------------------------------------------------------------------------

def test_unmatched_candidate_goes_to_unmatched_bucket(repo):
    _seed_video_with_candidate(repo, "vid_1", temp="child_A", area="자연탐구")
    res = collection_status(repo, class_id="cls_01")
    assert res["unmatched_candidates"] == 1
    assert res["matched_candidates"] == 0
    assert res["unmatched"]["cells"]["자연탐구"]["count"] == 1
    assert res["area_totals"]["자연탐구"] == 1
    assert res["children"] == []  # 매칭·등록 없음


# ---------------------------------------------------------------------------
# 매칭된 후보는 해당 유아 행으로 집계
# ---------------------------------------------------------------------------

def test_matched_candidate_aggregates_to_child_row(repo):
    repo.save_class(ClassGroup(id="cls_01", name="햇빛반", teacher_owner="teacher_01",
                               created_at=datetime(2026, 3, 1, 9, 0, 0)))
    _seed_video_with_candidate(repo, "vid_1", temp="child_A", area="자연탐구")
    repo.set_child_match(ChildMatch(
        id="cm_1", video_id="vid_1", temp_child_id="child_A", pseudonym_id="p_07",
        matched_by="teacher_01", matched_at=datetime(2026, 6, 9, 11, 0, 0),
    ))
    res = collection_status(repo, class_id="cls_01")
    assert res["matched_candidates"] == 1
    assert res["unmatched_candidates"] == 0
    row = next(c for c in res["children"] if c["child_key"] == "p_07")
    assert row["cells"]["자연탐구"]["count"] == 1
    assert row["cells"]["자연탐구"]["last_observed"] == "2026-06-09"
    assert "자연탐구" not in row["shortage_areas"] or res["min_per_area"] > 1


# ---------------------------------------------------------------------------
# 부족 임계값·보완 안내
# ---------------------------------------------------------------------------

def test_shortage_threshold_and_notes(repo):
    # 자연탐구 1건만 → min_per_area=2 면 부족
    _seed_video_with_candidate(repo, "vid_1", temp="child_A", area="자연탐구")
    res = collection_status(repo, class_id="cls_01", min_per_area=2)
    assert "자연탐구" in res["area_shortage"]
    assert any("자연탐구" in n for n in res["shortage_notes"])
    # 의사소통은 0건 → 당연히 부족
    assert "의사소통" in res["area_shortage"]


def test_two_candidates_meet_threshold(repo):
    repo.save_video(_video("vid_1"))
    repo.add_scenes([Scene(id="vid_1_seg", video_id="vid_1", time_start=0.0, time_end=10.0)])
    repo.add_candidates([
        _cand("c1", "vid_1", temp="child_A", area="사회관계"),
        _cand("c2", "vid_1", temp="child_B", area="사회관계"),
    ])
    res = collection_status(repo, class_id="cls_01", min_per_area=2)
    assert res["area_totals"]["사회관계"] == 2
    assert "사회관계" not in res["area_shortage"]


# ---------------------------------------------------------------------------
# class_id 필터 / owner 필터
# ---------------------------------------------------------------------------

def test_class_id_filter_excludes_other_classes(repo):
    _seed_video_with_candidate(repo, "vid_1", temp="child_A", area="자연탐구", class_id="cls_01")
    _seed_video_with_candidate(repo, "vid_2", temp="child_A", area="자연탐구", class_id="cls_02")
    res = collection_status(repo, class_id="cls_01")
    assert res["total_videos"] == 1
    assert res["area_totals"]["자연탐구"] == 1


def test_class_id_none_aggregates_all(repo):
    _seed_video_with_candidate(repo, "vid_1", temp="child_A", area="자연탐구", class_id="cls_01")
    _seed_video_with_candidate(repo, "vid_2", temp="child_B", area="사회관계", class_id="cls_02")
    res = collection_status(repo, class_id=None)
    assert res["total_videos"] == 2
    assert res["area_totals"]["자연탐구"] == 1
    assert res["area_totals"]["사회관계"] == 1


# ---------------------------------------------------------------------------
# 점수 필드 없음 (불변식)
# ---------------------------------------------------------------------------

def test_no_score_fields_in_output(repo):
    _seed_video_with_candidate(repo, "vid_1", temp="child_A", area="자연탐구")
    res = collection_status(repo, class_id="cls_01")
    flat = str(res)
    for banned in ("score", "level", "rating", "발달점수", "평정"):
        assert banned not in flat
