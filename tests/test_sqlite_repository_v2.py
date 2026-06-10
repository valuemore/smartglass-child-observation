"""V2 데이터 모델 확장 테스트.

대상: class_group / child / face_match_candidate / weekly_draft / ai_assistant_log
및 video·child_match·final_record 신규 컬럼, 마이그레이션.

모든 테스트는 tmp_path 를 사용하며 data/app.db 를 건드리지 않는다.
"""

from datetime import datetime
from pathlib import Path

import pytest

from core.schemas import (
    AiAssistantLog,
    Child,
    ChildMatch,
    ClassGroup,
    FaceMatchCandidate,
    FinalRecord,
    KicceItemCandidate,
    ObservationCandidate,
    Scene,
    Video,
    WeeklyDraft,
)
from storage.sqlite_repository import SqliteRepository


@pytest.fixture
def repo(tmp_path: Path) -> SqliteRepository:
    db = SqliteRepository(str(tmp_path / "v2.db"))
    db.init_schema()
    return db


def _class() -> ClassGroup:
    return ClassGroup(
        id="cls_01", name="햇빛반", teacher_owner="teacher_01",
        created_at=datetime(2026, 3, 1, 9, 0, 0),
    )


def _video(class_id="cls_01") -> Video:
    return Video(
        id="vid_001", filename="day1.mp4", stored_path="data/videos/day1.mp4",
        duration_sec=300.0, fps=30.0, width=1920, height=1080,
        status="uploaded", created_at=datetime(2026, 6, 9, 10, 0, 0),
        class_id=class_id, captured_date="2026-06-09",
    )


# ---------------------------------------------------------------------------
# 신규 테이블 생성
# ---------------------------------------------------------------------------

def test_init_schema_creates_v2_tables(tmp_path: Path) -> None:
    db = SqliteRepository(str(tmp_path / "new.db"))
    db.init_schema()

    import sqlite3
    conn = sqlite3.connect(str(tmp_path / "new.db"))
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    conn.close()

    expected = {
        "class_group", "child", "face_match_candidate",
        "weekly_draft", "ai_assistant_log",
    }
    assert expected.issubset(tables)


# ---------------------------------------------------------------------------
# ClassGroup
# ---------------------------------------------------------------------------

def test_class_save_get_list(repo: SqliteRepository) -> None:
    repo.save_class(_class())
    fetched = repo.get_class("cls_01")
    assert fetched is not None
    assert fetched.name == "햇빛반"
    assert fetched.face_match_enabled is False  # 기본 OFF

    assert len(repo.list_classes()) == 1
    assert len(repo.list_classes(teacher_owner="teacher_01")) == 1
    assert len(repo.list_classes(teacher_owner="other")) == 0


# ---------------------------------------------------------------------------
# Child — 실명 미저장, 동의 기반 얼굴 데이터
# ---------------------------------------------------------------------------

def test_child_without_consent_has_no_face_data(repo: SqliteRepository) -> None:
    repo.save_class(_class())
    c = Child(
        id="chd_01", class_id="cls_01", pseudonym_id="p_07",
        display_label="유아 7", created_at=datetime(2026, 3, 2, 9, 0, 0),
    )
    repo.add_child(c)
    got = repo.get_child("chd_01")
    assert got is not None
    assert got.pseudonym_id == "p_07"
    assert got.face_match_consent is False
    assert got.reference_photo_path is None
    assert got.face_embedding is None


def test_child_with_consent_stores_embedding(repo: SqliteRepository) -> None:
    repo.save_class(_class())
    c = Child(
        id="chd_02", class_id="cls_01", pseudonym_id="p_08",
        reference_photo_path="data/faces/cls_01/p_08.jpg",
        face_embedding=b"\x00\x01\x02\x03",
        face_match_consent=True,
        consent_at=datetime(2026, 3, 2, 9, 0, 0), consent_by="teacher_01",
        created_at=datetime(2026, 3, 2, 9, 0, 0),
    )
    repo.add_child(c)
    got = repo.get_child("chd_02")
    assert got.face_match_consent is True
    assert got.face_embedding == b"\x00\x01\x02\x03"
    assert got.reference_photo_path == "data/faces/cls_01/p_08.jpg"

    assert len(repo.list_children("cls_01")) == 1


def test_child_schema_rejects_face_data_without_consent() -> None:
    """보안 불변식: 동의 없는 유아는 참조사진·임베딩을 가질 수 없다."""
    with pytest.raises(ValueError):
        Child(
            id="chd_x", class_id="cls_01", pseudonym_id="p_x",
            reference_photo_path="data/faces/x.jpg",
            face_match_consent=False,
        )


def test_child_schema_forbids_extra_fields() -> None:
    """실명 등 예기치 않은 필드 주입을 거부한다."""
    with pytest.raises(Exception):
        Child(
            id="chd_y", class_id="cls_01", pseudonym_id="p_y",
            real_name="홍길동",  # type: ignore[call-arg]
        )


def test_set_face_consent_revoke_clears_face_data(repo: SqliteRepository) -> None:
    repo.save_class(_class())
    repo.add_child(Child(
        id="chd_03", class_id="cls_01", pseudonym_id="p_09",
        reference_photo_path="data/faces/cls_01/p_09.jpg",
        face_embedding=b"\xaa\xbb", face_match_consent=True,
        created_at=datetime(2026, 3, 2, 9, 0, 0),
    ))
    # 철회
    repo.set_face_consent("chd_03", consent=False, by="teacher_01")
    got = repo.get_child("chd_03")
    assert got.face_match_consent is False
    assert got.reference_photo_path is None
    assert got.face_embedding is None


def test_delete_child_cascade_removes_face_candidates(repo: SqliteRepository) -> None:
    repo.save_class(_class())
    repo.save_video(_video())
    repo.add_child(Child(id="chd_04", class_id="cls_01", pseudonym_id="p_10",
                         created_at=datetime(2026, 3, 2, 9, 0, 0)))
    repo.add_face_match_candidates([FaceMatchCandidate(
        id="fmc_01", video_id="vid_001", temp_child_id="child_A",
        child_id="chd_04", confidence=0.6,
        created_at=datetime(2026, 6, 9, 10, 1, 0),
    )])
    deleted = repo.delete_child_cascade("chd_04")
    assert deleted >= 2  # child + face_match_candidate
    assert repo.get_child("chd_04") is None
    assert repo.list_face_match_candidates("vid_001") == []


# ---------------------------------------------------------------------------
# FaceMatchCandidate — AI 후보(proposed), 교사 확정
# ---------------------------------------------------------------------------

def test_face_match_candidate_proposed_then_confirmed(repo: SqliteRepository) -> None:
    repo.save_class(_class())
    repo.save_video(_video())
    repo.add_child(Child(id="chd_05", class_id="cls_01", pseudonym_id="p_11",
                         created_at=datetime(2026, 3, 2, 9, 0, 0)))
    repo.add_face_match_candidates([FaceMatchCandidate(
        id="fmc_02", video_id="vid_001", temp_child_id="child_A",
        child_id="chd_05", confidence=0.71,
        created_at=datetime(2026, 6, 9, 10, 1, 0),
    )])
    cands = repo.list_face_match_candidates("vid_001")
    assert len(cands) == 1
    assert cands[0].status == "proposed"  # AI는 후보까지만

    # 교사 확정
    repo.decide_face_match("fmc_02", status="confirmed", decided_by="teacher_01")
    assert repo.list_face_match_candidates("vid_001")[0].status == "confirmed"


# ---------------------------------------------------------------------------
# WeeklyDraft
# ---------------------------------------------------------------------------

def test_weekly_draft_roundtrip(repo: SqliteRepository) -> None:
    repo.save_class(_class())
    d = WeeklyDraft(
        id="wd_01", class_id="cls_01", pseudonym_id="p_07",
        period_start="2026-06-01", period_end="2026-06-14", area="자연탐구",
        draft_text="블록 구조의 균형을 반복 탐색함",
        source_candidate_ids=["cand_001", "cand_002"],
        representative_clip_ids=["clip_a", "clip_b", "clip_c"],
        created_at=datetime(2026, 6, 14, 17, 0, 0),
    )
    repo.save_weekly_drafts([d])
    got = repo.get_weekly_draft("wd_01")
    assert got is not None
    assert got.area == "자연탐구"
    assert got.source_candidate_ids == ["cand_001", "cand_002"]
    assert len(got.representative_clip_ids) == 3
    assert got.status == "generated"

    assert len(repo.list_weekly_drafts("cls_01")) == 1
    assert len(repo.list_weekly_drafts("cls_01", pseudonym_id="p_07")) == 1
    assert len(repo.list_weekly_drafts("cls_01", pseudonym_id="없음")) == 0

    repo.update_draft_status("wd_01", "finalized")
    assert repo.get_weekly_draft("wd_01").status == "finalized"


def test_weekly_draft_has_no_score_field() -> None:
    d = WeeklyDraft(
        id="wd_x", class_id="cls_01", pseudonym_id="p_07",
        period_start="2026-06-01", period_end="2026-06-14", area="자연탐구",
    )
    assert not hasattr(d, "score")
    assert not hasattr(d, "level")
    assert not hasattr(d, "rating")


# ---------------------------------------------------------------------------
# AiAssistantLog — 제한된 intent
# ---------------------------------------------------------------------------

def test_assistant_log_roundtrip(repo: SqliteRepository) -> None:
    repo.write_assistant_log(AiAssistantLog(
        id="ai_01", actor="teacher_01", query="p_07 자연탐구 기록 찾아줘",
        intent="search", response_summary="3건 검색됨",
        created_at=datetime(2026, 6, 14, 17, 5, 0),
    ))
    logs = repo.list_assistant_logs(actor="teacher_01")
    assert len(logs) == 1
    assert logs[0].intent == "search"
    assert len(repo.list_assistant_logs()) == 1


def test_assistant_log_rejects_invalid_intent() -> None:
    with pytest.raises(Exception):
        AiAssistantLog(
            id="ai_x", actor="t", query="q",
            intent="generate_record",  # type: ignore[arg-type]
        )


# ---------------------------------------------------------------------------
# Video V2 필드 + 분석 상태머신
# ---------------------------------------------------------------------------

def test_video_v2_fields_roundtrip(repo: SqliteRepository) -> None:
    repo.save_class(_class())
    repo.save_video(_video())
    v = repo.get_video("vid_001")
    assert v.class_id == "cls_01"
    assert v.captured_date == "2026-06-09"
    assert v.analysis_status == "queued"
    assert v.progress == 0
    assert v.auto_analyzed is False


def test_update_analysis_status_done(repo: SqliteRepository) -> None:
    repo.save_class(_class())
    repo.save_video(_video())
    repo.update_analysis_status("vid_001", status="running", progress=40)
    assert repo.get_video("vid_001").analysis_status == "running"

    repo.update_analysis_status("vid_001", status="done", progress=100)
    v = repo.get_video("vid_001")
    assert v.analysis_status == "done"
    assert v.progress == 100
    assert v.auto_analyzed is True


def test_update_analysis_status_failed_increments_retry(repo: SqliteRepository) -> None:
    repo.save_class(_class())
    repo.save_video(_video())
    repo.update_analysis_status("vid_001", status="failed", progress=20, last_error="ffmpeg 오류")
    v = repo.get_video("vid_001")
    assert v.analysis_status == "failed"
    assert v.retry_count == 1
    assert v.last_error == "ffmpeg 오류"

    repo.update_analysis_status("vid_001", status="failed", progress=20, last_error="재시도 실패")
    assert repo.get_video("vid_001").retry_count == 2


# ---------------------------------------------------------------------------
# ChildMatch.source / FinalRecord 주간 필드
# ---------------------------------------------------------------------------

def test_child_match_source(repo: SqliteRepository) -> None:
    repo.save_class(_class())
    repo.save_video(_video())
    repo.set_child_match(ChildMatch(
        id="cm_01", video_id="vid_001", temp_child_id="child_A",
        pseudonym_id="p_07", source="face_candidate_confirmed",
        matched_by="teacher_01", matched_at=datetime(2026, 6, 14, 17, 0, 0),
    ))
    matches = repo.list_child_matches("vid_001")
    assert len(matches) == 1
    assert matches[0].source == "face_candidate_confirmed"


def test_final_record_weekly_fields(repo: SqliteRepository) -> None:
    repo.save_class(_class())
    repo.save_video(_video())
    repo.add_scenes([Scene(id="seg_001", video_id="vid_001", time_start=0.0, time_end=10.0)])
    repo.add_candidates([ObservationCandidate(
        id="cand_001", video_id="vid_001", scene_id="seg_001",
        time_start=1.0, time_end=8.0, temp_child_id="child_A",
        observed_behavior="행동", visual_evidence="근거", confidence=0.6,
        created_at=datetime(2026, 6, 9, 10, 1, 0),
    )])
    repo.save_weekly_drafts([WeeklyDraft(
        id="wd_01", class_id="cls_01", pseudonym_id="p_07",
        period_start="2026-06-01", period_end="2026-06-14", area="자연탐구",
    )])
    repo.save_final_record(FinalRecord(
        id="rec_01", candidate_id="cand_001", pseudonym_id="p_07",
        weekly_draft_id="wd_01", period_start="2026-06-01", period_end="2026-06-14",
        final_behavior="확정 서술", decision="accepted", edited=False,
        confirmed_by="teacher_01", confirmed_at=datetime(2026, 6, 14, 17, 0, 0),
    ))
    rec = repo.list_final_records(video_id="vid_001")[0]
    assert rec.weekly_draft_id == "wd_01"
    assert rec.period_start == "2026-06-01"
    assert rec.period_end == "2026-06-14"


# ---------------------------------------------------------------------------
# 마이그레이션: 구버전 video/child_match/final_record 무손실 보강
# ---------------------------------------------------------------------------

def test_migrates_legacy_video_table(tmp_path: Path) -> None:
    import sqlite3
    db_file = tmp_path / "legacy_v1.db"
    conn = sqlite3.connect(str(db_file))
    # V1 video 테이블 (V2 컬럼 없음)
    conn.execute(
        """
        CREATE TABLE video (
            id TEXT PRIMARY KEY, filename TEXT NOT NULL, stored_path TEXT NOT NULL,
            duration_sec REAL NOT NULL DEFAULT 0, fps REAL NOT NULL DEFAULT 0,
            width INTEGER NOT NULL DEFAULT 0, height INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'uploaded', created_at TEXT NOT NULL,
            retention_until TEXT
        )
        """
    )
    conn.execute(
        "INSERT INTO video (id, filename, stored_path, duration_sec, fps, width, height, status, created_at) "
        "VALUES ('vid_legacy','old.mp4','data/videos/old.mp4',100,30,1280,720,'analyzed','2026-05-01T10:00:00')"
    )
    conn.commit()
    conn.close()

    repo = SqliteRepository(str(db_file))
    repo.init_schema()  # ALTER TABLE 로 V2 컬럼 보강

    cols = {row["name"] for row in repo._connect().execute("PRAGMA table_info(video)")}
    for c in ["class_id", "captured_date", "analysis_status", "progress",
              "retry_count", "last_error", "auto_analyzed"]:
        assert c in cols

    # 기존 행 보존 + 신규 컬럼 기본값
    v = repo.get_video("vid_legacy")
    assert v is not None
    assert v.filename == "old.mp4"
    assert v.class_id is None
    assert v.analysis_status == "queued"
    assert v.auto_analyzed is False

    repo.init_schema()  # idempotent


def test_migrates_legacy_child_match_and_final_record(tmp_path: Path) -> None:
    import sqlite3
    db_file = tmp_path / "legacy_v1b.db"
    conn = sqlite3.connect(str(db_file))
    conn.execute(
        """
        CREATE TABLE child_match (
            id TEXT PRIMARY KEY, video_id TEXT NOT NULL, temp_child_id TEXT NOT NULL,
            pseudonym_id TEXT NOT NULL, matched_by TEXT NOT NULL, matched_at TEXT NOT NULL,
            UNIQUE(video_id, temp_child_id)
        )
        """
    )
    conn.execute(
        "INSERT INTO child_match VALUES ('cm_legacy','vid_x','child_A','p_01','teacher','2026-05-01T10:00:00')"
    )
    conn.execute(
        """
        CREATE TABLE final_record (
            id TEXT PRIMARY KEY, candidate_id TEXT NOT NULL, pseudonym_id TEXT NOT NULL,
            final_behavior TEXT NOT NULL, confirmed_areas_json TEXT NOT NULL DEFAULT '[]',
            confirmed_items_json TEXT NOT NULL DEFAULT '[]', decision TEXT NOT NULL,
            edited INTEGER NOT NULL, confirmed_by TEXT NOT NULL, confirmed_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "INSERT INTO final_record VALUES ('rec_legacy','cand_x','p_01','서술','[]','[]','accepted',0,'teacher','2026-05-01T10:00:00')"
    )
    conn.commit()
    conn.close()

    repo = SqliteRepository(str(db_file))
    repo.init_schema()

    cm_cols = {row["name"] for row in repo._connect().execute("PRAGMA table_info(child_match)")}
    assert "source" in cm_cols
    matches = repo.list_child_matches("vid_x")
    assert matches[0].source == "teacher"  # 기본값 보강

    fr_cols = {row["name"] for row in repo._connect().execute("PRAGMA table_info(final_record)")}
    for c in ["weekly_draft_id", "period_start", "period_end"]:
        assert c in fr_cols
    rec = repo.list_final_records()[0]
    assert rec.weekly_draft_id is None
