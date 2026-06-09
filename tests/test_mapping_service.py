"""map_candidates_for_video / map_single_candidate 단위 테스트.

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
    ObservationCandidate,
    Scene,
    ScaleMappingCandidate,
    Video,
)
from services.mapping.kicce_mapper import KicceMapper
from services.mapping.mapping_service import (
    map_candidates_for_video,
    map_single_candidate,
)
from services.mapping.nuri_classifier import NuriClassifier
from storage.sqlite_repository import SqliteRepository


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def repo(tmp_path: Path) -> SqliteRepository:
    r = SqliteRepository(str(tmp_path / "test.db"))
    r.init_schema()
    return r


def _make_video(repo: SqliteRepository) -> Video:
    v = Video(
        id=f"vid_test_{uuid.uuid4().hex[:6]}",
        filename="dummy.mp4",
        stored_path="/tmp/dummy.mp4",
        duration_sec=5.0,
        fps=30.0,
        width=640,
        height=480,
        status="analyzed",
        created_at=datetime.now(),
        retention_until=datetime.now() + timedelta(days=180),
    )
    repo.save_video(v)
    scene = Scene(
        id=f"scene_{v.id}_0000",
        video_id=v.id,
        time_start=0.0,
        time_end=5.0,
        detector="fallback_fixed",
    )
    repo.add_scenes([scene])
    return v


def _add_candidate(repo: SqliteRepository, video: Video, behavior: str) -> ObservationCandidate:
    cand = ObservationCandidate(
        id=f"cand_{uuid.uuid4().hex[:8]}",
        video_id=video.id,
        scene_id=f"scene_{video.id}_0000",
        time_start=0.0,
        time_end=5.0,
        temp_child_id="child_A",
        observed_behavior=behavior,
        interaction=InteractionEvidence(with_peers="친구와 함께", with_teacher=None, with_materials="블록"),
        activity_context="쌓기놀이 영역",
        peer_relation="또래와 협력",
        visual_evidence="프레임에서 관찰됨",
        confidence=0.7,
        needs_teacher_review=True,
        created_at=datetime.now(),
    )
    repo.add_candidates([cand])
    return cand


# ---------------------------------------------------------------------------
# 1. scale_mapping 이 DB에 저장된다
# ---------------------------------------------------------------------------

def test_mappings_saved_to_db(repo: SqliteRepository):
    v = _make_video(repo)
    cand = _add_candidate(repo, v, "블록을 쌓으며 친구와 함께 나누는 모습")

    mappings = map_candidates_for_video(v.id, repo, actor="test_actor")
    assert len(mappings) >= 1

    saved = repo.list_mappings(cand.id)
    assert len(saved) == len(mappings)
    for m in saved:
        assert isinstance(m, ScaleMappingCandidate)


# ---------------------------------------------------------------------------
# 2. nuri 와 kicce scale 이 모두 저장된다
# ---------------------------------------------------------------------------

def test_both_nuri_and_kicce_saved(repo: SqliteRepository):
    v = _make_video(repo)
    cand = _add_candidate(repo, v, "블록을 쌓아 구조물을 만들며 친구와 함께 나누어 사용하는 모습")

    map_candidates_for_video(v.id, repo, actor="test_actor")

    saved = repo.list_mappings(cand.id)
    scales = {m.scale for m in saved}
    assert "nuri" in scales, "누리 매핑이 저장되어야 한다"
    assert "kicce" in scales, "KICCE 매핑이 저장되어야 한다"


# ---------------------------------------------------------------------------
# 3. audit_log action=analyze, detail=nuri_kicce_mapping 기록
# ---------------------------------------------------------------------------

def test_audit_log_recorded(repo: SqliteRepository):
    v = _make_video(repo)
    _add_candidate(repo, v, "블록을 쌓는 모습")

    map_candidates_for_video(v.id, repo, actor="test_actor")

    logs = repo.list_audit_logs(v.id)
    analyze = [l for l in logs if l.action == "analyze"]
    assert any("nuri_kicce_mapping" in (l.detail or "") for l in analyze)


# ---------------------------------------------------------------------------
# 4. 관찰수준 점수 필드가 없다
# ---------------------------------------------------------------------------

def test_no_score_fields(repo: SqliteRepository):
    fields = set(ScaleMappingCandidate.model_fields.keys())
    forbidden = {"score", "level", "rating", "eval_score", "dev_score"}
    assert forbidden.isdisjoint(fields)


# ---------------------------------------------------------------------------
# 5. 재실행해도 중복 저장되지 않는다
# ---------------------------------------------------------------------------

def test_no_duplicate_on_rerun(repo: SqliteRepository):
    v = _make_video(repo)
    cand = _add_candidate(repo, v, "블록을 쌓으며 친구와 함께 나누는 모습")

    first = map_candidates_for_video(v.id, repo, actor="test_actor")
    first_count = len(repo.list_mappings(cand.id))
    assert first_count == len(first)

    # 재실행 — 기존 매핑 삭제 후 재삽입되므로 개수가 누적되지 않아야 한다
    map_candidates_for_video(v.id, repo, actor="test_actor")
    second_count = len(repo.list_mappings(cand.id))
    assert second_count == first_count, "재실행 시 매핑이 중복 저장되면 안 된다"


# ---------------------------------------------------------------------------
# 6. 후보가 없으면 빈 리스트 (오류 없음)
# ---------------------------------------------------------------------------

def test_empty_when_no_candidates(repo: SqliteRepository):
    v = _make_video(repo)
    mappings = map_candidates_for_video(v.id, repo, actor="test_actor")
    assert mappings == []


# ---------------------------------------------------------------------------
# 7. 없는 video_id 는 ValueError
# ---------------------------------------------------------------------------

def test_invalid_video_raises(repo: SqliteRepository):
    with pytest.raises(ValueError, match="video_id="):
        map_candidates_for_video("nonexistent_xxx", repo)


# ---------------------------------------------------------------------------
# 8. map_single_candidate 는 누리+KICCE 후보를 함께 반환
# ---------------------------------------------------------------------------

def test_map_single_candidate(repo: SqliteRepository):
    v = _make_video(repo)
    cand = _add_candidate(repo, v, "블록을 쌓으며 친구와 함께 나누는 모습")

    nuri = NuriClassifier()
    kicce = KicceMapper()
    results = map_single_candidate(cand, nuri, kicce)

    scales = {m.scale for m in results}
    assert "nuri" in scales
    assert "kicce" in scales
    for m in results:
        assert m.candidate_id == cand.id
        assert 0.0 <= m.confidence <= 1.0
