"""얼굴 매칭 후보 서비스 테스트 (V2-4). tmp_path 격리, Mock 임베더.

MockFaceEmbedder 는 같은 바이트 → 유사도 1.0, 다른 바이트 → 낮은 유사도.
따라서 프레임 바이트를 참조사진 바이트와 동일하게 두면 '매칭', 다르게 두면 '비매칭'을 만든다.
"""

from datetime import datetime
from pathlib import Path

import pytest

from core.schemas import (
    Child, ClassGroup, Frame, ObservationCandidate, Scene, Video,
)
from services.class_service import register_class, register_child
from services.face.base import decode_embedding
from services.face.face_match_service import propose_matches
from services.face.mock_embedder import MockFaceEmbedder
from storage.sqlite_repository import SqliteRepository

PHOTO_A = b"face-bytes-child-A-reference"
PHOTO_B = b"face-bytes-child-B-reference"


@pytest.fixture
def repo(tmp_path: Path) -> SqliteRepository:
    db = SqliteRepository(str(tmp_path / "fm.db"))
    db.init_schema()
    return db


def _seed_class(repo, faces_dir, *, face_enabled=True):
    g = register_class(repo, "햇빛반", "teacher_01", face_match_enabled=face_enabled)
    return g


def _add_consented_child(repo, class_id, faces_dir, pseudonym, photo_bytes):
    return register_child(
        repo, class_id, pseudonym_id=pseudonym, reference_photo=photo_bytes,
        consent=True, consent_by="teacher_01", faces_dir=faces_dir,
    )


def _seed_video_with_frame(repo, tmp_path, vid, class_id, frame_bytes, temp="child_A"):
    repo.save_video(Video(
        id=vid, filename=f"{vid}.mp4", stored_path=str(tmp_path / f"{vid}.mp4"),
        duration_sec=10.0, fps=30.0, width=640, height=480,
        status="accumulated", created_at=datetime(2026, 6, 9, 10, 0, 0),
        class_id=class_id, captured_date="2026-06-09",
    ))
    repo.add_scenes([Scene(id=f"{vid}_seg", video_id=vid, time_start=0.0, time_end=10.0)])
    img = tmp_path / f"{vid}_frame.jpg"
    img.write_bytes(frame_bytes)
    repo.add_frames([Frame(id=f"{vid}_f1", scene_id=f"{vid}_seg", t=2.0,
                           image_path=str(img), blur_score=200.0, kept=True)])
    repo.add_candidates([ObservationCandidate(
        id=f"{vid}_c1", video_id=vid, scene_id=f"{vid}_seg",
        time_start=1.0, time_end=5.0, temp_child_id=temp,
        observed_behavior="행동", visual_evidence="근거", confidence=0.6,
        created_at=datetime(2026, 6, 9, 10, 1, 0),
    )])


# ---------------------------------------------------------------------------
# 매칭 성공: 프레임 바이트 == 참조사진 바이트 → 높은 유사도 후보
# ---------------------------------------------------------------------------

def test_propose_match_when_frame_matches_reference(repo, tmp_path):
    faces = str(tmp_path / "faces")
    g = _seed_class(repo, faces)
    child = _add_consented_child(repo, g.id, faces, "p_07", PHOTO_A)
    _seed_video_with_frame(repo, tmp_path, "vid_1", g.id, frame_bytes=PHOTO_A)

    res = propose_matches(repo, "vid_1")
    assert len(res) == 1
    fmc = res[0]
    assert fmc.child_id == child.id
    assert fmc.temp_child_id == "child_A"
    assert fmc.status == "proposed"        # AI는 후보까지만
    assert fmc.confidence >= 0.99          # 동일 바이트 → 유사도 1.0

    # DB에도 저장됨
    assert len(repo.list_face_match_candidates("vid_1")) == 1


# ---------------------------------------------------------------------------
# 비매칭: 프레임 바이트 != 참조사진 → 임계값 미만 → 후보 없음
# ---------------------------------------------------------------------------

def test_no_match_when_frame_differs(repo, tmp_path):
    faces = str(tmp_path / "faces")
    g = _seed_class(repo, faces)
    _add_consented_child(repo, g.id, faces, "p_07", PHOTO_A)
    _seed_video_with_frame(repo, tmp_path, "vid_1", g.id, frame_bytes=b"completely-different")

    res = propose_matches(repo, "vid_1", min_confidence=0.5)
    assert res == []


# ---------------------------------------------------------------------------
# 동의 게이트: 클래스 face_match_enabled=False → 매칭 안 함
# ---------------------------------------------------------------------------

def test_no_match_when_class_face_disabled(repo, tmp_path):
    faces = str(tmp_path / "faces")
    g = _seed_class(repo, faces, face_enabled=False)
    _add_consented_child(repo, g.id, faces, "p_07", PHOTO_A)
    _seed_video_with_frame(repo, tmp_path, "vid_1", g.id, frame_bytes=PHOTO_A)

    assert propose_matches(repo, "vid_1") == []


# ---------------------------------------------------------------------------
# 동의 게이트: 동의·참조사진 없는 유아만 있으면 매칭 안 함
# ---------------------------------------------------------------------------

def test_no_match_when_no_consented_children(repo, tmp_path):
    faces = str(tmp_path / "faces")
    g = _seed_class(repo, faces)
    register_child(repo, g.id, pseudonym_id="p_07", consent=False, faces_dir=faces)
    _seed_video_with_frame(repo, tmp_path, "vid_1", g.id, frame_bytes=PHOTO_A)

    assert propose_matches(repo, "vid_1") == []


# ---------------------------------------------------------------------------
# class_id 없는 영상 → 매칭 안 함
# ---------------------------------------------------------------------------

def test_no_match_when_video_has_no_class(repo, tmp_path):
    faces = str(tmp_path / "faces")
    g = _seed_class(repo, faces)
    _add_consented_child(repo, g.id, faces, "p_07", PHOTO_A)
    # class_id=None 영상
    repo.save_video(Video(
        id="vid_x", filename="x.mp4", stored_path=str(tmp_path / "x.mp4"),
        duration_sec=5.0, fps=30.0, width=320, height=240,
        status="accumulated", created_at=datetime(2026, 6, 9, 10, 0, 0),
    ))
    assert propose_matches(repo, "vid_x") == []


# ---------------------------------------------------------------------------
# 임베딩 저장은 동의 유아만 (보안 가드)
# ---------------------------------------------------------------------------

def test_embedding_stored_only_for_consented(repo, tmp_path):
    faces = str(tmp_path / "faces")
    g = _seed_class(repo, faces)
    child = _add_consented_child(repo, g.id, faces, "p_07", PHOTO_A)
    _seed_video_with_frame(repo, tmp_path, "vid_1", g.id, frame_bytes=PHOTO_A)
    propose_matches(repo, "vid_1")
    # 동의 유아 임베딩이 계산·저장됨
    stored = decode_embedding(repo.get_child(child.id).face_embedding)
    assert stored is not None and len(stored) > 0


# ---------------------------------------------------------------------------
# 멱등성 + 교사 확정 보존
# ---------------------------------------------------------------------------

def test_rerun_preserves_teacher_decision(repo, tmp_path):
    faces = str(tmp_path / "faces")
    g = _seed_class(repo, faces)
    _add_consented_child(repo, g.id, faces, "p_07", PHOTO_A)
    _seed_video_with_frame(repo, tmp_path, "vid_1", g.id, frame_bytes=PHOTO_A)

    res = propose_matches(repo, "vid_1")
    # 교사 확정
    repo.decide_face_match(res[0].id, status="confirmed", decided_by="teacher_01")
    # 재실행: confirmed 는 보존되고 proposed 중복 생성 안 함
    propose_matches(repo, "vid_1")
    all_fmc = repo.list_face_match_candidates("vid_1")
    confirmed = [f for f in all_fmc if f.status == "confirmed"]
    assert len(confirmed) == 1


# ---------------------------------------------------------------------------
# 감사 로그: face_match + reference_photo_access
# ---------------------------------------------------------------------------

def test_audit_logged(repo, tmp_path):
    faces = str(tmp_path / "faces")
    g = _seed_class(repo, faces)
    _add_consented_child(repo, g.id, faces, "p_07", PHOTO_A)
    _seed_video_with_frame(repo, tmp_path, "vid_1", g.id, frame_bytes=PHOTO_A)
    propose_matches(repo, "vid_1")
    actions = {l.action for l in repo.list_audit_logs(video_id="vid_1")}
    assert "face_match" in actions
    assert "reference_photo_access" in actions


# ---------------------------------------------------------------------------
# 없는 영상 → ValueError
# ---------------------------------------------------------------------------

def test_unknown_video_raises(repo):
    with pytest.raises(ValueError):
        propose_matches(repo, "no_such_video")
