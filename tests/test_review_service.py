"""review_service 단위 테스트.

원칙: tmp_path 기반 SQLite DB 만 사용. data/ 실제 경로 금지.
"""

import sys
import uuid
from datetime import datetime, timedelta
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.schemas import (
    InteractionEvidence,
    KicceItemCandidate,
    ObservationCandidate,
    Scene,
    ScaleMappingCandidate,
    Video,
)
from services.review_service import (
    finalize_candidate,
    list_final_records_for_video,
    list_review_candidates,
    mapping_to_kicce_item,
    set_child_match,
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


@pytest.fixture()
def seeded(repo: SqliteRepository):
    """video + scene + candidate(child_A) + nuri/kicce 매핑이 저장된 상태."""
    v = Video(
        id=f"vid_test_{uuid.uuid4().hex[:6]}",
        filename="dummy.mp4",
        stored_path="/tmp/dummy.mp4",
        duration_sec=5.0, fps=30.0, width=640, height=480,
        status="analyzed", created_at=datetime.now(),
        retention_until=datetime.now() + timedelta(days=180),
    )
    repo.save_video(v)
    scene = Scene(id=f"scene_{v.id}_0000", video_id=v.id,
                  time_start=0.0, time_end=5.0, detector="fallback_fixed")
    repo.add_scenes([scene])

    cand = ObservationCandidate(
        id=f"cand_{uuid.uuid4().hex[:8]}",
        video_id=v.id, scene_id=scene.id,
        time_start=0.0, time_end=5.0,
        temp_child_id="child_A",
        observed_behavior="블록을 쌓으며 친구와 함께 나누는 모습",
        interaction=InteractionEvidence(with_peers="친구와 함께", with_teacher=None, with_materials="블록"),
        activity_context="쌓기놀이 영역",
        peer_relation="또래와 협력",
        visual_evidence="프레임에서 관찰됨",
        confidence=0.7, needs_teacher_review=True, created_at=datetime.now(),
    )
    repo.add_candidates([cand])

    repo.add_mappings([
        ScaleMappingCandidate(
            id=f"map_{cand.id}_nuri_1", candidate_id=cand.id, scale="nuri",
            area="사회관계", item_id=None, item_text="사회관계",
            rationale="또래 협력", confidence=0.8,
        ),
        ScaleMappingCandidate(
            id=f"map_{cand.id}_kicce_1", candidate_id=cand.id, scale="kicce",
            area="사회관계", item_id=7, item_text="또래와 협력하며 놀이에 참여한다.",
            rationale="함께 나눔", confidence=0.75,
        ),
    ])
    return v, cand


# ---------------------------------------------------------------------------
# 1. set_child_match 저장
# ---------------------------------------------------------------------------

def test_set_child_match_saves(repo: SqliteRepository, seeded):
    v, _ = seeded
    set_child_match(v.id, "child_A", "child_001", repo, actor="teacher_demo")
    matches = repo.list_child_matches(v.id)
    assert len(matches) == 1
    assert matches[0].temp_child_id == "child_A"
    assert matches[0].pseudonym_id == "child_001"
    assert matches[0].matched_by == "teacher_demo"


# ---------------------------------------------------------------------------
# 2. 동일 temp_child_id 재매칭 시 중복 없이 덮어쓰기
# ---------------------------------------------------------------------------

def test_set_child_match_overwrite(repo: SqliteRepository, seeded):
    v, _ = seeded
    set_child_match(v.id, "child_A", "child_001", repo)
    set_child_match(v.id, "child_A", "child_999", repo)
    matches = repo.list_child_matches(v.id)
    assert len(matches) == 1, "동일 temp_child_id 는 중복 저장되면 안 된다"
    assert matches[0].pseudonym_id == "child_999"


# ---------------------------------------------------------------------------
# 3. finalize accepted → final_record 저장
# ---------------------------------------------------------------------------

def test_finalize_accepted(repo: SqliteRepository, seeded):
    v, cand = seeded
    rec = finalize_candidate(
        candidate_id=cand.id, pseudonym_id="child_001",
        final_behavior=cand.observed_behavior,
        confirmed_areas=["사회관계"], confirmed_items=[],
        decision="accepted", repo=repo, video_id=v.id,
    )
    assert rec.decision == "accepted"
    assert rec.edited is False
    saved = repo.list_final_records(v.id)
    assert len(saved) == 1
    assert saved[0].candidate_id == cand.id


# ---------------------------------------------------------------------------
# 4. finalize edited → edited=1
# ---------------------------------------------------------------------------

def test_finalize_edited(repo: SqliteRepository, seeded):
    v, cand = seeded
    rec = finalize_candidate(
        candidate_id=cand.id, pseudonym_id="child_001",
        final_behavior="교사가 수정한 행동 서술",
        confirmed_areas=["사회관계"], confirmed_items=[],
        decision="edited", repo=repo, video_id=v.id,
    )
    assert rec.edited is True
    saved = repo.list_final_records(v.id)[0]
    assert saved.decision == "edited"
    assert saved.edited is True
    assert saved.final_behavior == "교사가 수정한 행동 서술"


# ---------------------------------------------------------------------------
# 5. finalize rejected → decision="rejected" 저장
# ---------------------------------------------------------------------------

def test_finalize_rejected(repo: SqliteRepository, seeded):
    v, cand = seeded
    rec = finalize_candidate(
        candidate_id=cand.id, pseudonym_id="child_001",
        final_behavior=cand.observed_behavior,
        confirmed_areas=[], confirmed_items=[],
        decision="rejected", repo=repo, video_id=v.id,
    )
    assert rec.decision == "rejected"
    saved = repo.list_final_records(v.id)
    assert len(saved) == 1
    assert saved[0].decision == "rejected"


# ---------------------------------------------------------------------------
# 6. confirmed_areas / confirmed_items 저장
# ---------------------------------------------------------------------------

def test_confirmed_areas_and_items_saved(repo: SqliteRepository, seeded):
    v, cand = seeded
    kicce = repo.list_mappings(cand.id)
    items = [mapping_to_kicce_item(m) for m in kicce if m.scale == "kicce"]
    finalize_candidate(
        candidate_id=cand.id, pseudonym_id="child_001",
        final_behavior=cand.observed_behavior,
        confirmed_areas=["사회관계", "자연탐구"], confirmed_items=items,
        decision="accepted", repo=repo, video_id=v.id,
    )
    saved = repo.list_final_records(v.id)[0]
    assert saved.confirmed_areas == ["사회관계", "자연탐구"]
    assert len(saved.confirmed_items) == 1
    assert saved.confirmed_items[0].item_id == 7


# ---------------------------------------------------------------------------
# 7. audit_log 기록 (매칭 + 확정)
# ---------------------------------------------------------------------------

def test_audit_log_recorded(repo: SqliteRepository, seeded):
    v, cand = seeded
    set_child_match(v.id, "child_A", "child_001", repo)
    finalize_candidate(
        candidate_id=cand.id, pseudonym_id="child_001",
        final_behavior=cand.observed_behavior,
        confirmed_areas=[], confirmed_items=[],
        decision="accepted", repo=repo, video_id=v.id,
    )
    logs = repo.list_audit_logs(v.id)
    details = [l.detail or "" for l in logs]
    assert any("set_child_match" in d for d in details)
    assert any("teacher_review_finalize" in d and "accepted" in d for d in details)


# ---------------------------------------------------------------------------
# 8. 점수 필드 없음
# ---------------------------------------------------------------------------

def test_no_score_fields():
    from core.schemas import FinalRecord
    fields = set(FinalRecord.model_fields.keys())
    forbidden = {"score", "level", "rating", "eval_score", "dev_score"}
    assert forbidden.isdisjoint(fields), f"금지된 점수 필드: {forbidden & fields}"


# ---------------------------------------------------------------------------
# 9. 중복 확정 방지 — 같은 candidate 재확정 시 record 1건 유지
# ---------------------------------------------------------------------------

def test_no_duplicate_finalize(repo: SqliteRepository, seeded):
    v, cand = seeded
    finalize_candidate(
        candidate_id=cand.id, pseudonym_id="child_001",
        final_behavior="첫 확정", confirmed_areas=[], confirmed_items=[],
        decision="accepted", repo=repo, video_id=v.id,
    )
    # 재확정(수정 재저장) — 같은 candidate_id 이므로 덮어쓰기
    finalize_candidate(
        candidate_id=cand.id, pseudonym_id="child_001",
        final_behavior="수정 재확정", confirmed_areas=[], confirmed_items=[],
        decision="edited", repo=repo, video_id=v.id,
    )
    saved = repo.list_final_records(v.id)
    assert len(saved) == 1, "같은 후보의 확정 기록은 1건만 유지되어야 한다"
    assert saved[0].final_behavior == "수정 재확정"
    assert saved[0].decision == "edited"


# ---------------------------------------------------------------------------
# 10. list_review_candidates 묶음 반환
# ---------------------------------------------------------------------------

def test_list_review_candidates(repo: SqliteRepository, seeded):
    v, cand = seeded
    set_child_match(v.id, "child_A", "child_001", repo)
    items = list_review_candidates(v.id, repo)
    assert len(items) == 1
    item = items[0]
    assert item["candidate"].id == cand.id
    assert item["pseudonym_id"] == "child_001"
    assert len(item["nuri"]) == 1
    assert len(item["kicce"]) == 1
    assert item["final_record"] is None  # 아직 미확정

    # 확정 후 final_record 가 채워져야 한다
    finalize_candidate(
        candidate_id=cand.id, pseudonym_id="child_001",
        final_behavior=cand.observed_behavior,
        confirmed_areas=[], confirmed_items=[],
        decision="accepted", repo=repo, video_id=v.id,
    )
    items2 = list_review_candidates(v.id, repo)
    assert items2[0]["final_record"] is not None
    assert items2[0]["final_record"].decision == "accepted"


# ---------------------------------------------------------------------------
# 11. 허용되지 않은 decision 은 ValueError
# ---------------------------------------------------------------------------

def test_invalid_decision_raises(repo: SqliteRepository, seeded):
    v, cand = seeded
    with pytest.raises(ValueError, match="decision"):
        finalize_candidate(
            candidate_id=cand.id, pseudonym_id="child_001",
            final_behavior="x", confirmed_areas=[], confirmed_items=[],
            decision="approved", repo=repo, video_id=v.id,
        )


# ---------------------------------------------------------------------------
# 12. list_final_records_for_video
# ---------------------------------------------------------------------------

def test_list_final_records_for_video(repo: SqliteRepository, seeded):
    v, cand = seeded
    assert list_final_records_for_video(v.id, repo) == []
    finalize_candidate(
        candidate_id=cand.id, pseudonym_id="child_001",
        final_behavior=cand.observed_behavior,
        confirmed_areas=[], confirmed_items=[],
        decision="accepted", repo=repo, video_id=v.id,
    )
    assert len(list_final_records_for_video(v.id, repo)) == 1
