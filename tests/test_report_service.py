"""report_service 단위 테스트.

원칙: tmp_path 기반 SQLite DB 만 사용. data/ 실제 경로 금지.
"""

import csv
import io
import sys
import uuid
from datetime import datetime, timedelta
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.schemas import (
    FinalRecord,
    KicceItemCandidate,
    ObservationCandidate,
    Scene,
    Video,
)
from services.report_service import (
    build_video_report,
    calculate_area_distribution,
    calculate_kicce_coverage,
    export_report_csv,
    export_report_json,
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


def _make_video(vid_id: str = None) -> Video:
    vid_id = vid_id or f"vid_{uuid.uuid4().hex[:6]}"
    return Video(
        id=vid_id, filename="test.mp4", stored_path="/tmp/test.mp4",
        duration_sec=30.0, fps=30.0, width=640, height=480,
        status="analyzed", created_at=datetime.now(),
        retention_until=datetime.now() + timedelta(days=180),
    )


def _make_scene(video_id: str, idx: int = 0) -> Scene:
    return Scene(
        id=f"scene_{video_id}_{idx:04d}",
        video_id=video_id,
        time_start=float(idx * 5),
        time_end=float(idx * 5 + 5),
        detector="fallback_fixed",
    )


def _make_candidate(video_id: str, scene_id: str,
                    temp_id: str = "child_A", idx: int = 0) -> ObservationCandidate:
    return ObservationCandidate(
        id=f"cand_{uuid.uuid4().hex[:8]}",
        video_id=video_id, scene_id=scene_id,
        time_start=float(idx * 5), time_end=float(idx * 5 + 5),
        temp_child_id=temp_id,
        observed_behavior=f"관찰 행동 {idx}",
        visual_evidence=f"프레임 {idx}에서 관찰됨",
        confidence=0.7, needs_teacher_review=True, created_at=datetime.now(),
    )


def _make_final(candidate_id: str, pseudonym_id: str, decision: str,
                areas: list = None, items: list = None) -> FinalRecord:
    return FinalRecord(
        id=f"final_{candidate_id}",
        candidate_id=candidate_id,
        pseudonym_id=pseudonym_id,
        final_behavior="최종 행동 서술",
        confirmed_areas=areas or [],
        confirmed_items=items or [],
        decision=decision,
        edited=(decision == "edited"),
        confirmed_by="teacher_demo",
        confirmed_at=datetime.now(),
    )


@pytest.fixture()
def seeded(repo: SqliteRepository):
    """video 4후보 4확정(accepted 2, edited 1, rejected 1)이 저장된 상태."""
    v = _make_video("vid_report_test")
    repo.save_video(v)
    scenes = [_make_scene(v.id, i) for i in range(3)]
    repo.add_scenes(scenes)

    cands = [_make_candidate(v.id, scenes[i].id, "child_A", i) for i in range(3)]
    cand_b = ObservationCandidate(
        id=f"cand_{uuid.uuid4().hex[:8]}",
        video_id=v.id, scene_id=scenes[0].id,
        time_start=0.0, time_end=5.0,
        temp_child_id="child_B",
        observed_behavior="관찰 행동 child_B",
        visual_evidence="프레임에서 관찰됨",
        confidence=0.6, needs_teacher_review=True, created_at=datetime.now(),
    )
    cands.append(cand_b)
    repo.add_candidates(cands)

    kicce_item = KicceItemCandidate(
        item_id=7, item_text="또래와 협력하며 놀이에 참여한다.",
        rationale="함께 놀이", confidence=0.75,
    )

    finals = [
        _make_final(cands[0].id, "child_001", "accepted",
                    ["사회관계"], [kicce_item]),
        _make_final(cands[1].id, "child_001", "edited",
                    ["자연탐구"]),
        _make_final(cands[2].id, "child_001", "accepted",
                    ["사회관계", "의사소통"]),
        _make_final(cand_b.id, "child_002", "rejected"),
    ]
    for f in finals:
        repo.save_final_record(f)

    return v, cands, finals


# ---------------------------------------------------------------------------
# 1. build_video_report 요약 반환 확인
# ---------------------------------------------------------------------------

def test_build_video_report_returns_summary(repo, seeded):
    v, _, _ = seeded
    report = build_video_report(v.id, repo)
    assert report["video_id"] == v.id
    assert report["filename"] == "test.mp4"
    assert report["total_candidates"] == 4
    assert report["total_finals"] == 4


# ---------------------------------------------------------------------------
# 2. accepted / edited / rejected 수 정확성
# ---------------------------------------------------------------------------

def test_decision_counts(repo, seeded):
    v, _, _ = seeded
    report = build_video_report(v.id, repo)
    assert report["accepted"] == 2
    assert report["edited"] == 1
    assert report["rejected"] == 1


# ---------------------------------------------------------------------------
# 3. 미검토 수 계산
# ---------------------------------------------------------------------------

def test_unreviewed_count(repo, seeded):
    v, _, _ = seeded
    report = build_video_report(v.id, repo)
    assert report["unreviewed"] == 0, "후보 4개 모두 확정 — 미검토 0"

    extra_scene = _make_scene(v.id, 99)
    repo.add_scenes([extra_scene])
    extra = _make_candidate(v.id, extra_scene.id, "child_A", 99)
    repo.add_candidates([extra])
    report2 = build_video_report(v.id, repo)
    assert report2["unreviewed"] == 1, "미확정 후보 1개 추가 → 미검토 1"


# ---------------------------------------------------------------------------
# 4. 누리 영역 분포 계산
# ---------------------------------------------------------------------------

def test_area_distribution(repo, seeded):
    v, _, _ = seeded
    report = build_video_report(v.id, repo)
    dist = report["area_distribution"]
    assert dist.get("사회관계") == 2   # cands[0] + cands[2]
    assert dist.get("자연탐구") == 1
    assert dist.get("의사소통") == 1


# ---------------------------------------------------------------------------
# 5. KICCE 문항 커버리지 계산
# ---------------------------------------------------------------------------

def test_kicce_coverage(repo, seeded):
    v, _, _ = seeded
    report = build_video_report(v.id, repo)
    cov = report["kicce_coverage"]
    assert len(cov) >= 1
    item_7 = next((c for c in cov if c["item_id"] == 7), None)
    assert item_7 is not None
    assert item_7["count"] == 1


# ---------------------------------------------------------------------------
# 6. pseudonym_id별 그룹화
# ---------------------------------------------------------------------------

def test_group_by_pseudonym(repo, seeded):
    v, _, _ = seeded
    report = build_video_report(v.id, repo)
    by_p = report["by_pseudonym"]
    assert "child_001" in by_p
    assert "child_002" in by_p
    assert len(by_p["child_001"]) == 3
    assert len(by_p["child_002"]) == 1


# ---------------------------------------------------------------------------
# 7. export JSON에 stored_path / image_path 미포함
# ---------------------------------------------------------------------------

def test_export_json_no_path_fields(repo, seeded):
    v, _, _ = seeded
    json_str = export_report_json(v.id, repo)
    assert "stored_path" not in json_str
    assert "image_path" not in json_str


# ---------------------------------------------------------------------------
# 8. export CSV 생성 가능 구조
# ---------------------------------------------------------------------------

def test_export_csv_structure(repo, seeded):
    v, _, _ = seeded
    csv_str = export_report_csv(v.id, repo)
    reader = csv.DictReader(io.StringIO(csv_str))
    rows = list(reader)
    assert len(rows) == 4
    required = {"pseudonym_id", "final_behavior", "decision", "confirmed_at"}
    assert required.issubset(set(reader.fieldnames))


# ---------------------------------------------------------------------------
# 9. audit_log export 기록
# ---------------------------------------------------------------------------

def test_audit_log_on_export(repo, seeded):
    v, _, _ = seeded
    export_report_json(v.id, repo)
    export_report_csv(v.id, repo)
    logs = repo.list_audit_logs(v.id)
    details = [(l.action, l.detail or "") for l in logs]
    assert any(a == "export" and "json" in d for a, d in details)
    assert any(a == "export" and "csv" in d for a, d in details)


# ---------------------------------------------------------------------------
# 10. score / level / rating 금지 필드 없음
# ---------------------------------------------------------------------------

def test_no_score_fields(repo, seeded):
    v, _, _ = seeded
    report = build_video_report(v.id, repo)
    json_str = export_report_json(v.id, repo)
    forbidden = {"score", "level", "rating", "eval_score", "dev_score"}
    for key in forbidden:
        assert key not in report, f"report dict에 금지된 필드: {key}"
        assert f'"{key}"' not in json_str, f"export JSON에 금지된 필드: {key}"
