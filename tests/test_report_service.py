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
    AuditLog,
    FinalRecord,
    Frame,
    KicceItemCandidate,
    ObservationCandidate,
    Scene,
    ScaleMappingCandidate,
    Video,
)
from services.report_service import (
    build_video_report,
    calculate_area_distribution,
    calculate_audit_completeness,
    calculate_candidate_retention,
    calculate_kicce_coverage,
    calculate_preprocessing_counts,
    calculate_review_effort,
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


# ---------------------------------------------------------------------------
# P-A. 전처리 카운트
# ---------------------------------------------------------------------------

def test_preprocessing_counts(repo):
    v = _make_video("vid_prep")
    repo.save_video(v)
    scenes = [_make_scene(v.id, i) for i in range(2)]
    repo.add_scenes(scenes)
    # scene0: 3프레임(kept 2), scene1: 1프레임(kept 1) → frame 4, kept 3
    frames = [
        Frame(id="f0", scene_id=scenes[0].id, t=0.1, image_path="frm0.jpg", blur_score=200.0, kept=True),
        Frame(id="f1", scene_id=scenes[0].id, t=0.2, image_path="frm1.jpg", blur_score=10.0, kept=False),
        Frame(id="f2", scene_id=scenes[0].id, t=0.3, image_path="frm2.jpg", blur_score=180.0, kept=True),
        Frame(id="f3", scene_id=scenes[1].id, t=5.1, image_path="frm3.jpg", blur_score=150.0, kept=True),
    ]
    repo.add_frames(frames)

    counts = calculate_preprocessing_counts(v.id, repo)
    assert counts["scene_count"] == 2
    assert counts["frame_count"] == 4
    assert counts["kept_frame_count"] == 3

    report = build_video_report(v.id, repo)
    assert report["scene_count"] == 2
    assert report["frame_count"] == 4
    assert report["kept_frame_count"] == 3


# ---------------------------------------------------------------------------
# P-A. AI 후보 유지율 (scale_mapping 기준)
# ---------------------------------------------------------------------------

def test_candidate_retention_with_mappings(repo):
    v = _make_video("vid_ret")
    repo.save_video(v)
    scene = _make_scene(v.id, 0)
    repo.add_scenes([scene])
    cand = _make_candidate(v.id, scene.id, "child_A", 0)
    repo.add_candidates([cand])

    # AI 제시: 누리 2개(사회관계, 자연탐구), KICCE 2개(item 7, 9)
    repo.add_mappings([
        ScaleMappingCandidate(id="m1", candidate_id=cand.id, scale="nuri",
                              area="사회관계", item_text="사회관계", rationale="r", confidence=0.7),
        ScaleMappingCandidate(id="m2", candidate_id=cand.id, scale="nuri",
                              area="자연탐구", item_text="자연탐구", rationale="r", confidence=0.6),
        ScaleMappingCandidate(id="m3", candidate_id=cand.id, scale="kicce",
                              item_id=7, item_text="문항7", rationale="r", confidence=0.7),
        ScaleMappingCandidate(id="m4", candidate_id=cand.id, scale="kicce",
                              item_id=9, item_text="문항9", rationale="r", confidence=0.5),
    ])

    # 교사 확정: 누리 1개(사회관계) 유지, KICCE 1개(item 7) 유지
    kept_item = KicceItemCandidate(item_id=7, item_text="문항7", rationale="r", confidence=0.7)
    final = _make_final(cand.id, "child_001", "edited", ["사회관계"], [kept_item])
    repo.save_final_record(final)

    ret = calculate_candidate_retention([cand], [final], repo)
    assert ret["reviewed_candidates"] == 1
    assert ret["nuri_suggested"] == 2
    assert ret["nuri_retained"] == 1
    assert ret["nuri_retention_rate"] == 0.5
    assert ret["kicce_suggested"] == 2
    assert ret["kicce_retained"] == 1
    assert ret["kicce_retention_rate"] == 0.5


def test_candidate_retention_zero_base(repo, seeded):
    """매핑이 없으면 분모 0 → 유지율 0.0 (ZeroDivision 없음)."""
    v, _, _ = seeded  # seeded는 scale_mapping을 저장하지 않음
    report = build_video_report(v.id, repo)
    ret = report["candidate_retention"]
    assert ret["nuri_suggested"] == 0
    assert ret["nuri_retention_rate"] == 0.0
    assert ret["kicce_retention_rate"] == 0.0


# ---------------------------------------------------------------------------
# P-A. 감사 완전성
# ---------------------------------------------------------------------------

def _audit(video_id: str, action: str) -> AuditLog:
    return AuditLog(
        id=f"audit_{video_id}_{action}_{uuid.uuid4().hex[:6]}",
        video_id=video_id, actor="teacher_demo", action=action,
        detail=action, created_at=datetime.now(),
    )


def test_audit_completeness_partial(repo):
    v = _make_video("vid_audit_p")
    repo.save_video(v)
    repo.write_audit(_audit(v.id, "upload"))
    repo.write_audit(_audit(v.id, "analyze"))

    comp = calculate_audit_completeness(v.id, repo)
    assert comp["upload"]["present"] is True
    assert comp["analyze"]["present"] is True
    assert comp["access"]["present"] is False
    assert set(comp["missing_actions"]) == {"access", "export", "delete"}


def test_audit_completeness_full(repo):
    v = _make_video("vid_audit_f")
    repo.save_video(v)
    for action in ("upload", "access", "analyze", "export", "delete"):
        repo.write_audit(_audit(v.id, action))

    comp = calculate_audit_completeness(v.id, repo)
    assert comp["missing_actions"] == []
    assert all(comp[a]["present"] for a in ("upload", "access", "analyze", "export", "delete"))


# ---------------------------------------------------------------------------
# P-A. export 안전성 (민감 경로 제외 + 신규 지표 + 가드)
# ---------------------------------------------------------------------------

def _assert_no_sensitive_substrings(text: str) -> None:
    for frag in ("stored_path", "image_path",
                 "data/videos", "data/frames",
                 "data\\videos", "data\\frames"):
        assert frag not in text, f"export에 민감 문자열 포함: {frag!r}"


def test_export_json_excludes_sensitive_paths(repo, seeded):
    v, _, _ = seeded
    _assert_no_sensitive_substrings(export_report_json(v.id, repo))


def test_export_csv_excludes_sensitive_paths(repo, seeded):
    v, _, _ = seeded
    _assert_no_sensitive_substrings(export_report_csv(v.id, repo))


def test_export_json_has_new_metrics(repo, seeded):
    import json as _json
    v, _, _ = seeded
    data = _json.loads(export_report_json(v.id, repo))
    summary = data["summary"]
    assert "scene_count" in summary
    assert "frame_count" in summary
    assert "kept_frame_count" in summary
    assert "candidate_retention" in data
    assert "audit_completeness" in data


def test_review_effort_aggregates_timing_and_adequacy():
    """(P-B.1) review_seconds 평균/합계와 근거 적절성 분포를 집계한다."""
    finals = [
        _make_final("c1", "child_001", "accepted"),
        _make_final("c2", "child_001", "edited"),
        _make_final("c3", "child_002", "rejected"),
        _make_final("c4", "child_002", "accepted"),
    ]
    finals[0].review_seconds = 30
    finals[0].evidence_adequacy = "adequate"
    finals[1].review_seconds = 90
    finals[1].evidence_adequacy = "partial"
    finals[2].evidence_adequacy = "inadequate"
    # finals[3]: 타이밍·적절성 미기록 → unrated, 타이밍 집계 제외

    effort = calculate_review_effort(finals)
    assert effort["review_seconds_total"] == 120
    assert effort["review_seconds_avg"] == 60.0
    assert effort["reviewed_with_timing"] == 2
    dist = effort["evidence_adequacy_distribution"]
    assert dist == {"adequate": 1, "partial": 1, "inadequate": 1, "unrated": 1}


def test_review_effort_empty_when_no_timing():
    """타이밍이 전혀 없으면 avg 는 None, total 0, 전부 unrated."""
    finals = [_make_final("c1", "child_001", "accepted")]
    effort = calculate_review_effort(finals)
    assert effort["review_seconds_total"] == 0
    assert effort["review_seconds_avg"] is None
    assert effort["reviewed_with_timing"] == 0
    assert effort["evidence_adequacy_distribution"]["unrated"] == 1


def test_export_json_includes_review_effort(repo, seeded):
    import json as _json
    v, _, _ = seeded
    data = _json.loads(export_report_json(v.id, repo))
    assert "review_effort" in data
    re = data["review_effort"]
    assert "review_seconds_avg" in re
    assert "evidence_adequacy_distribution" in re


def test_export_json_guard_raises_on_injected_path(repo):
    """확정 행동 서술에 민감 경로가 섞이면 export 가드가 차단한다(defense-in-depth)."""
    v = _make_video("vid_guard")
    repo.save_video(v)
    scene = _make_scene(v.id, 0)
    repo.add_scenes([scene])
    cand = _make_candidate(v.id, scene.id, "child_A", 0)
    repo.add_candidates([cand])
    bad = FinalRecord(
        id=f"final_{cand.id}", candidate_id=cand.id, pseudonym_id="child_001",
        final_behavior="원본 위치 data/videos/leak.mp4 참조",  # 민감 경로 주입
        confirmed_areas=[], confirmed_items=[], decision="accepted",
        edited=False, confirmed_by="teacher_demo", confirmed_at=datetime.now(),
    )
    repo.save_final_record(bad)

    with pytest.raises(ValueError, match="민감 경로"):
        export_report_json(v.id, repo)
