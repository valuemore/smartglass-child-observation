"""주간 관찰초안 서비스 테스트 (V2-6). tmp_path 격리."""

from datetime import datetime
from pathlib import Path

import pytest

from core.schemas import (
    ChildMatch, ClassGroup, Clip, NuriAreaCandidate, ObservationCandidate, Scene, Video,
)
from services.draft_service import build_interim_drafts, generate_weekly_draft
from storage.sqlite_repository import SqliteRepository


@pytest.fixture
def repo(tmp_path: Path) -> SqliteRepository:
    db = SqliteRepository(str(tmp_path / "draft.db"))
    db.init_schema()
    return db


def _class(repo):
    g = ClassGroup(id="cls_01", name="햇빛반", teacher_owner="teacher_01",
                   created_at=datetime(2026, 3, 1, 9, 0, 0))
    repo.save_class(g)
    return g


def _video(repo, vid, captured="2026-06-09", class_id="cls_01"):
    repo.save_video(Video(
        id=vid, filename=f"{vid}.mp4", stored_path=f"data/videos/{vid}.mp4",
        duration_sec=10.0, fps=30.0, width=640, height=480,
        status="accumulated", created_at=datetime(2026, 6, 9, 10, 0, 0),
        class_id=class_id, captured_date=captured,
    ))
    repo.add_scenes([Scene(id=f"{vid}_seg", video_id=vid, time_start=0.0, time_end=10.0)])


def _cand(repo, cid, vid, temp="child_A", area="자연탐구", behavior="블록을 쌓음", conf=0.7):
    repo.add_candidates([ObservationCandidate(
        id=cid, video_id=vid, scene_id=f"{vid}_seg", time_start=1.0, time_end=5.0,
        temp_child_id=temp, observed_behavior=behavior, visual_evidence="근거",
        nuri_area_candidates=[NuriAreaCandidate(area=area, rationale="r", confidence=0.6)],
        confidence=conf, created_at=datetime(2026, 6, 9, 10, 1, 0),
    )])


def _match(repo, vid, temp, pid):
    repo.set_child_match(ChildMatch(
        id=f"cm_{vid}_{temp}", video_id=vid, temp_child_id=temp, pseudonym_id=pid,
        matched_by="teacher_01", matched_at=datetime(2026, 6, 9, 11, 0, 0),
    ))


# ---------------------------------------------------------------------------
# 중간 관찰 초안(읽기 전용 미리보기)
# ---------------------------------------------------------------------------

def test_build_interim_drafts_matched(repo):
    """매칭된 후보가 유아·영역별 중간 초안으로 묶이고 DB에 저장되지 않는다."""
    from core.schemas import Child
    _class(repo)
    repo.add_child(Child(id="chd_1", class_id="cls_01", pseudonym_id="p_07",
                         display_label="A", created_at=datetime(2026, 3, 1, 9, 0, 0)))
    _video(repo, "vid_1")
    _cand(repo, "c1", "vid_1", temp="child_A", area="자연탐구", behavior="블록을 쌓음")
    _cand(repo, "c2", "vid_1", temp="child_A", area="사회관계", behavior="친구와 나눔")
    _match(repo, "vid_1", "child_A", "p_07")

    drafts = build_interim_drafts(repo, "cls_01", owner=None)
    assert len(drafts) == 1
    d = drafts[0]
    assert d["pseudonym_id"] == "p_07"
    assert d["label"] == "A"
    assert d["total"] == 2
    areas = {a["area"] for a in d["areas"]}
    assert areas == {"자연탐구", "사회관계"}
    # 읽기 전용: 주간 초안이 저장되지 않았다
    assert repo.list_weekly_drafts("cls_01") == []


def test_build_interim_drafts_excludes_unmatched(repo):
    """매칭되지 않은 후보는 중간 초안에서 제외된다."""
    _class(repo)
    _video(repo, "vid_1")
    _cand(repo, "c1", "vid_1", temp="child_A", area="자연탐구")
    # 매칭 없음
    assert build_interim_drafts(repo, "cls_01", owner=None) == []


# ---------------------------------------------------------------------------
# 매칭된 후보만 유아별 초안에 포함
# ---------------------------------------------------------------------------

def test_matched_candidate_produces_draft(repo):
    _class(repo)
    _video(repo, "vid_1")
    _cand(repo, "c1", "vid_1", temp="child_A", area="자연탐구")
    _match(repo, "vid_1", "child_A", "p_07")

    drafts = generate_weekly_draft(repo, "cls_01", "2026-06-01", "2026-06-14")
    assert len(drafts) == 1
    d = drafts[0]
    assert d.pseudonym_id == "p_07"
    assert d.area == "자연탐구"
    assert "c1" in d.source_candidate_ids
    assert d.status == "generated"
    assert "블록을 쌓음" in d.draft_text


def test_unmatched_candidate_excluded(repo):
    _class(repo)
    _video(repo, "vid_1")
    _cand(repo, "c1", "vid_1", temp="child_A", area="자연탐구")
    # 매칭 없음
    drafts = generate_weekly_draft(repo, "cls_01", "2026-06-01", "2026-06-14")
    assert drafts == []


# ---------------------------------------------------------------------------
# 기간 필터
# ---------------------------------------------------------------------------

def test_period_filter_excludes_outside(repo):
    _class(repo)
    _video(repo, "vid_in", captured="2026-06-09")
    _video(repo, "vid_out", captured="2026-05-01")
    _cand(repo, "c_in", "vid_in", temp="child_A", area="사회관계")
    _cand(repo, "c_out", "vid_out", temp="child_A", area="사회관계")
    _match(repo, "vid_in", "child_A", "p_07")
    _match(repo, "vid_out", "child_A", "p_07")

    drafts = generate_weekly_draft(repo, "cls_01", "2026-06-01", "2026-06-14")
    assert len(drafts) == 1
    assert drafts[0].source_candidate_ids == ["c_in"]


# ---------------------------------------------------------------------------
# 대표 근거 클립 최대 3개, 신뢰도 순
# ---------------------------------------------------------------------------

def test_representative_clips_capped_at_three(repo):
    _class(repo)
    _video(repo, "vid_1")
    # 한 scene 에 클립 5개
    for i in range(5):
        repo.add_clips([Clip(
            id=f"clip_{i}", video_id="vid_1", source_scene_ids=["vid_1_seg"],
            start_sec=float(i), end_sec=float(i) + 4.0, duration_sec=4.0,
            local_clip_path=f"data/clips/vid_1/clip_{i}.mp4",
            created_at=datetime(2026, 6, 9, 10, 0, 0),
        )])
    _cand(repo, "c1", "vid_1", temp="child_A", area="예술경험")
    _match(repo, "vid_1", "child_A", "p_07")

    drafts = generate_weekly_draft(repo, "cls_01", "2026-06-01", "2026-06-14")
    assert len(drafts[0].representative_clip_ids) == 3


# ---------------------------------------------------------------------------
# pseudonym 필터
# ---------------------------------------------------------------------------

def test_pseudonym_filter(repo):
    _class(repo)
    _video(repo, "vid_1")
    _cand(repo, "c1", "vid_1", temp="child_A", area="자연탐구")
    _cand(repo, "c2", "vid_1", temp="child_B", area="자연탐구")
    _match(repo, "vid_1", "child_A", "p_07")
    _match(repo, "vid_1", "child_B", "p_08")

    drafts = generate_weekly_draft(repo, "cls_01", "2026-06-01", "2026-06-14",
                                   pseudonym_ids=["p_07"])
    assert len(drafts) == 1
    assert drafts[0].pseudonym_id == "p_07"


# ---------------------------------------------------------------------------
# 멱등성 + finalized 보존
# ---------------------------------------------------------------------------

def test_regenerate_preserves_finalized(repo):
    _class(repo)
    _video(repo, "vid_1")
    _cand(repo, "c1", "vid_1", temp="child_A", area="자연탐구")
    _match(repo, "vid_1", "child_A", "p_07")

    drafts = generate_weekly_draft(repo, "cls_01", "2026-06-01", "2026-06-14")
    draft_id = drafts[0].id
    repo.update_draft_status(draft_id, "finalized")

    # 재생성 → finalized 는 건너뜀
    drafts2 = generate_weekly_draft(repo, "cls_01", "2026-06-01", "2026-06-14")
    assert all(d.id != draft_id for d in drafts2)
    assert repo.get_weekly_draft(draft_id).status == "finalized"


# ---------------------------------------------------------------------------
# 점수 필드 없음
# ---------------------------------------------------------------------------

def test_draft_has_no_score(repo):
    _class(repo)
    _video(repo, "vid_1")
    _cand(repo, "c1", "vid_1", temp="child_A", area="자연탐구")
    _match(repo, "vid_1", "child_A", "p_07")
    d = generate_weekly_draft(repo, "cls_01", "2026-06-01", "2026-06-14")[0]
    assert not hasattr(d, "score")
    for banned in ("score", "level", "rating", "평정", "발달점수"):
        assert banned not in d.draft_text


def test_unknown_class_raises(repo):
    with pytest.raises(ValueError):
        generate_weekly_draft(repo, "no_class", "2026-06-01", "2026-06-14")
