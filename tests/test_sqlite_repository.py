"""SQLiteRepository CRUD 테스트. 모든 테스트는 tmp_path 를 사용하며 data/app.db 를 건드리지 않는다."""

from datetime import datetime
from pathlib import Path

import pytest

from core.schemas import (
    AuditLog,
    ChildMatch,
    FinalRecord,
    Frame,
    InteractionEvidence,
    KicceItemCandidate,
    NuriAreaCandidate,
    ObservationCandidate,
    Scene,
    ScaleMappingCandidate,
    Video,
)
from storage.sqlite_repository import SqliteRepository


# ---------------------------------------------------------------------------
# 공통 픽스처
# ---------------------------------------------------------------------------

@pytest.fixture
def repo(tmp_path: Path) -> SqliteRepository:
    db = SqliteRepository(str(tmp_path / "test.db"))
    db.init_schema()
    return db


def _video() -> Video:
    return Video(
        id="vid_001", filename="sample.mp4", stored_path="data/videos/sample.mp4",
        duration_sec=300.0, fps=30.0, width=1920, height=1080,
        status="uploaded", created_at=datetime(2026, 6, 9, 10, 0, 0),
    )


def _scene() -> Scene:
    return Scene(id="seg_001", video_id="vid_001", time_start=0.0, time_end=10.5)


def _frame() -> Frame:
    return Frame(
        id="f_001", scene_id="seg_001", t=2.5,
        image_path="data/frames/vid_001/seg_001/f_001.jpg",
        blur_score=150.0, kept=True,
    )


def _candidate() -> ObservationCandidate:
    return ObservationCandidate(
        id="cand_001", video_id="vid_001", scene_id="seg_001",
        time_start=2.0, time_end=8.5,
        temp_child_id="child_A",
        observed_behavior="블록을 쌓아 올리다 무너지자 다시 시도함",
        interaction=InteractionEvidence(
            with_peers="child_B와 번갈아 블록을 올림",
            with_teacher="교사 제안에 반응",
            with_materials="원목 블록 사용",
        ),
        activity_context="자유놀이 쌓기 영역",
        peer_relation="협력적 차례 주고받기",
        visual_evidence="f_001에서 두 손으로 블록 정렬",
        audio_support="(유아) 무너졌어! — 보조 근거",
        nuri_area_candidates=[
            NuriAreaCandidate(area="자연탐구", rationale="균형·인과 탐색", confidence=0.72),
        ],
        kicce_item_candidates=[
            KicceItemCandidate(item_id=34, item_text="문항 텍스트", rationale="근거", confidence=0.55),
        ],
        confidence=0.68,
        needs_teacher_review=True,
        created_at=datetime(2026, 6, 9, 10, 1, 0),
    )


def _mapping() -> ScaleMappingCandidate:
    return ScaleMappingCandidate(
        id="map_001", candidate_id="cand_001", scale="kicce",
        area="자연탐구", item_id=34, item_text="탐색 관련 문항",
        rationale="균형 탐색 행동 근거", confidence=0.60,
    )


def _final_record() -> FinalRecord:
    return FinalRecord(
        id="rec_001", candidate_id="cand_001", pseudonym_id="김OO",
        final_behavior="블록 쌓기를 반복하며 균형을 탐색함",
        confirmed_areas=["자연탐구"],
        confirmed_items=[
            KicceItemCandidate(item_id=34, item_text="문항 텍스트", rationale="근거", confidence=0.55),
        ],
        decision="edited", edited=True,
        confirmed_by="teacher_01",
        confirmed_at=datetime(2026, 6, 9, 11, 0, 0),
    )


# ---------------------------------------------------------------------------
# 테스트 1: init_schema() — 8개 테이블 생성 확인
# ---------------------------------------------------------------------------

def test_init_schema_creates_tables(tmp_path: Path) -> None:
    db = SqliteRepository(str(tmp_path / "new.db"))
    db.init_schema()

    import sqlite3
    conn = sqlite3.connect(str(tmp_path / "new.db"))
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    conn.close()

    expected = {
        "video", "scene", "frame", "audio_segment",
        "observation_candidate", "scale_mapping",
        "child_match", "final_record", "audit_log",
    }
    assert expected.issubset(tables)


# ---------------------------------------------------------------------------
# 테스트 2: Video 저장 / 조회
# ---------------------------------------------------------------------------

def test_save_and_get_video(repo: SqliteRepository) -> None:
    v = _video()
    repo.save_video(v)

    fetched = repo.get_video("vid_001")
    assert fetched is not None
    assert fetched.id == "vid_001"
    assert fetched.filename == "sample.mp4"
    assert fetched.fps == 30.0
    assert fetched.status == "uploaded"


def test_list_videos(repo: SqliteRepository) -> None:
    repo.save_video(_video())
    videos = repo.list_videos()
    assert len(videos) == 1
    assert isinstance(videos[0], Video)


def test_get_video_not_found(repo: SqliteRepository) -> None:
    assert repo.get_video("nonexistent") is None


# ---------------------------------------------------------------------------
# 테스트 3: Scene / Frame 저장·조회
# ---------------------------------------------------------------------------

def test_add_and_list_scenes_and_frames(repo: SqliteRepository) -> None:
    repo.save_video(_video())
    repo.add_scenes([_scene()])
    repo.add_frames([_frame()])

    scenes = repo.list_scenes("vid_001")
    assert len(scenes) == 1
    assert scenes[0].id == "seg_001"
    assert scenes[0].time_end == 10.5

    frames = repo.list_frames("seg_001")
    assert len(frames) == 1
    assert frames[0].id == "f_001"
    assert frames[0].blur_score == 150.0
    assert frames[0].kept is True


# ---------------------------------------------------------------------------
# 테스트 4: ObservationCandidate 저장·조회 (JSON 필드 포함)
# ---------------------------------------------------------------------------

def test_add_and_list_candidates(repo: SqliteRepository) -> None:
    repo.save_video(_video())
    repo.add_scenes([_scene()])
    repo.add_candidates([_candidate()])

    candidates = repo.list_candidates("vid_001")
    assert len(candidates) == 1
    c = candidates[0]

    assert c.id == "cand_001"
    assert c.temp_child_id == "child_A"
    assert c.observed_behavior == "블록을 쌓아 올리다 무너지자 다시 시도함"
    assert c.visual_evidence == "f_001에서 두 손으로 블록 정렬"
    assert c.confidence == 0.68
    assert c.needs_teacher_review is True

    # JSON 필드 round-trip
    assert c.interaction is not None
    assert c.interaction.with_peers == "child_B와 번갈아 블록을 올림"
    assert len(c.nuri_area_candidates) == 1
    assert c.nuri_area_candidates[0].area == "자연탐구"
    assert len(c.kicce_item_candidates) == 1
    assert c.kicce_item_candidates[0].item_id == 34


# ---------------------------------------------------------------------------
# 테스트 5: ScaleMappingCandidate 저장·조회
# ---------------------------------------------------------------------------

def test_add_and_list_mappings(repo: SqliteRepository) -> None:
    repo.save_video(_video())
    repo.add_scenes([_scene()])
    repo.add_candidates([_candidate()])
    repo.add_mappings([_mapping()])

    mappings = repo.list_mappings("cand_001")
    assert len(mappings) == 1
    m = mappings[0]
    assert m.scale == "kicce"
    assert m.item_id == 34
    assert m.confidence == 0.60


# ---------------------------------------------------------------------------
# 테스트 6: ChildMatch 저장
# ---------------------------------------------------------------------------

def test_set_child_match(repo: SqliteRepository) -> None:
    repo.save_video(_video())
    match = ChildMatch(
        id="match_001", video_id="vid_001",
        temp_child_id="child_A", pseudonym_id="김OO",
        matched_by="teacher_01",
        matched_at=datetime(2026, 6, 9, 10, 30, 0),
    )
    repo.set_child_match(match)

    # 덮어쓰기(동일 video_id + temp_child_id)
    match2 = ChildMatch(
        id="match_001b", video_id="vid_001",
        temp_child_id="child_A", pseudonym_id="이OO",
        matched_by="teacher_01",
        matched_at=datetime(2026, 6, 9, 10, 31, 0),
    )
    repo.set_child_match(match2)

    import sqlite3
    conn = sqlite3.connect(repo.db_path)
    rows = conn.execute(
        "SELECT pseudonym_id FROM child_match WHERE video_id='vid_001' AND temp_child_id='child_A'"
    ).fetchall()
    conn.close()
    assert len(rows) == 1
    assert rows[0][0] == "이OO"


# ---------------------------------------------------------------------------
# 테스트 7: FinalRecord 저장·조회
# ---------------------------------------------------------------------------

def test_save_and_list_final_records(repo: SqliteRepository) -> None:
    repo.save_video(_video())
    repo.add_scenes([_scene()])
    repo.add_candidates([_candidate()])
    repo.save_final_record(_final_record())

    records = repo.list_final_records(video_id="vid_001")
    assert len(records) == 1
    rec = records[0]

    assert rec.id == "rec_001"
    assert rec.pseudonym_id == "김OO"
    assert rec.decision == "edited"
    assert rec.edited is True
    assert rec.confirmed_areas == ["자연탐구"]
    assert len(rec.confirmed_items) == 1
    assert rec.confirmed_items[0].item_id == 34


def test_list_final_records_all(repo: SqliteRepository) -> None:
    repo.save_video(_video())
    repo.add_scenes([_scene()])
    repo.add_candidates([_candidate()])
    repo.save_final_record(_final_record())

    all_records = repo.list_final_records()
    assert len(all_records) == 1


# ---------------------------------------------------------------------------
# 테스트 8: AuditLog 저장·조회
# ---------------------------------------------------------------------------

def test_write_and_list_audit_logs(repo: SqliteRepository) -> None:
    repo.save_video(_video())

    entries = [
        AuditLog(id="log_001", video_id="vid_001", actor="teacher_01",
                 action="upload", created_at=datetime(2026, 6, 9, 10, 0, 0)),
        AuditLog(id="log_002", video_id="vid_001", actor="teacher_01",
                 action="analyze", detail="P1 파이프라인",
                 created_at=datetime(2026, 6, 9, 10, 5, 0)),
    ]
    for e in entries:
        repo.write_audit(e)

    logs = repo.list_audit_logs(video_id="vid_001")
    assert len(logs) == 2
    assert logs[0].action == "upload"
    assert logs[1].action == "analyze"
    assert logs[1].detail == "P1 파이프라인"


def test_audit_log_filter_by_video(repo: SqliteRepository) -> None:
    v2 = Video(
        id="vid_002", filename="other.mp4", stored_path="data/videos/other.mp4",
        duration_sec=60.0, fps=30.0, width=1280, height=720,
        status="uploaded", created_at=datetime(2026, 6, 9, 9, 0, 0),
    )
    repo.save_video(_video())
    repo.save_video(v2)
    repo.write_audit(AuditLog(id="log_a", video_id="vid_001", actor="t", action="access",
                              created_at=datetime.now()))
    repo.write_audit(AuditLog(id="log_b", video_id="vid_002", actor="t", action="access",
                              created_at=datetime.now()))

    assert len(repo.list_audit_logs(video_id="vid_001")) == 1
    assert len(repo.list_audit_logs(video_id="vid_002")) == 1
    assert len(repo.list_audit_logs()) == 2


# ---------------------------------------------------------------------------
# 테스트 9: FinalRecord 에 score 필드 없음
# ---------------------------------------------------------------------------

def test_final_record_has_no_score_field(repo: SqliteRepository) -> None:
    repo.save_video(_video())
    repo.add_scenes([_scene()])
    repo.add_candidates([_candidate()])
    repo.save_final_record(_final_record())

    rec = repo.list_final_records()[0]
    assert not hasattr(rec, "score")
    assert not hasattr(rec, "level")
    assert not hasattr(rec, "rating")


# ---------------------------------------------------------------------------
# 테스트 10: 테스트 DB 는 tmp_path 사용 — data/app.db 미생성
# ---------------------------------------------------------------------------

def test_uses_tmp_path_not_real_db(tmp_path: Path) -> None:
    db_file = tmp_path / "isolated.db"
    repo = SqliteRepository(str(db_file))
    repo.init_schema()

    assert db_file.exists(), "tmp_path DB 가 생성되어야 함"
    assert not Path("data/app.db").exists(), "data/app.db 는 테스트 중 생성되지 않아야 함"
