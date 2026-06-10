"""V2 보안·개인정보 회귀 테스트 (V2-9).

교차 불변식을 한곳에서 게이트화한다(개별 기능 테스트와 일부 중복은 의도적):
  1) export(영상·클래스)에 미디어 경로·키 미포함
  2) 유아 실명 미저장(Child extra 금지)
  3) 얼굴 임베딩·참조사진은 동의 시에만, 철회 시 삭제
  4) 얼굴 매칭은 동의 게이트
  5) 민감 액션 감사 로그 기록
  6) 점수/발달/평정 필드 부재
  7) assert_no_sensitive_paths 가 클립·얼굴 경로까지 차단
  8) AI비서 금지 요청 거부
"""

import json
from datetime import datetime
from pathlib import Path

import pytest

from core.schemas import (
    Child, ClassGroup, FinalRecord, Frame, KicceItemCandidate,
    NuriAreaCandidate, ObservationCandidate, Scene, Video,
)
from services.security_service import assert_no_sensitive_paths
from services.class_service import register_class, register_child, set_child_face_consent
from services.face.face_match_service import propose_matches
from services.assistant_service import handle_query
from services.report_service import export_report_json, export_class_report_json
from storage.sqlite_repository import SqliteRepository

_MEDIA_FRAGMENTS = ("data/videos", "data/frames", "data/clips", "data/faces")
_MEDIA_KEYS = ("stored_path", "image_path", "local_clip_path", "reference_photo_path")


@pytest.fixture
def repo(tmp_path: Path) -> SqliteRepository:
    db = SqliteRepository(str(tmp_path / "sec.db"))
    db.init_schema()
    return db


# ---------------------------------------------------------------------------
# 1) assert_no_sensitive_paths — 클립·얼굴 경로까지 차단
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("frag", _MEDIA_FRAGMENTS)
def test_guard_blocks_all_media_fragments(frag):
    with pytest.raises(ValueError):
        assert_no_sensitive_paths({"x": f"{frag}/abc/def.mp4"})


@pytest.mark.parametrize("frag", ["data\\clips", "data\\faces"])
def test_guard_blocks_windows_backslash_media(frag):
    with pytest.raises(ValueError):
        assert_no_sensitive_paths({"x": f"{frag}\\abc.jpg"})


def test_guard_passes_clean_payload():
    assert_no_sensitive_paths({"pseudonym_id": "p_07", "area": "자연탐구", "count": 3})


# ---------------------------------------------------------------------------
# 2) 유아 실명 미저장
# ---------------------------------------------------------------------------

def test_child_rejects_real_name_field():
    with pytest.raises(Exception):
        Child(id="c", class_id="cls", pseudonym_id="p", real_name="홍길동")  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# 3) 얼굴 임베딩·참조사진은 동의 기반, 철회 시 삭제
# ---------------------------------------------------------------------------

def test_face_data_consent_gate_and_revoke(repo, tmp_path):
    faces = str(tmp_path / "faces")
    g = register_class(repo, "반", "teacher_01")
    # 동의 없이 사진 저장 시도 → 거부
    with pytest.raises(ValueError):
        register_child(repo, g.id, pseudonym_id="p_x",
                       reference_photo=b"img", consent=False, faces_dir=faces)
    # 동의 시 저장
    c = register_child(repo, g.id, pseudonym_id="p_y",
                       reference_photo=b"img", consent=True, faces_dir=faces)
    assert Path(c.reference_photo_path).exists()
    # 철회 → 파일·임베딩 삭제
    upd = set_child_face_consent(repo, c.id, consent=False, faces_dir=faces)
    assert upd.reference_photo_path is None
    assert upd.face_embedding is None
    assert not Path(c.reference_photo_path).exists()


def test_repo_embedding_guard_only_consented(repo):
    """동의 안 한 유아에는 임베딩이 저장되지 않는다(DB 가드)."""
    g = register_class(repo, "반", "teacher_01")
    c = register_child(repo, g.id, pseudonym_id="p_z", consent=False)
    repo.set_child_embedding(c.id, b"\x01\x02")
    assert repo.get_child(c.id).face_embedding is None


# ---------------------------------------------------------------------------
# 4) 얼굴 매칭은 동의 게이트 (동의/활성화 없으면 후보 없음)
# ---------------------------------------------------------------------------

def test_face_match_requires_consent_and_enabled(repo, tmp_path):
    faces = str(tmp_path / "faces")
    # 클래스 face_match_enabled=False
    g = register_class(repo, "반", "teacher_01", face_match_enabled=False)
    register_child(repo, g.id, pseudonym_id="p_07", reference_photo=b"img",
                   consent=True, faces_dir=faces)
    repo.save_video(Video(id="v1", filename="f", stored_path=str(tmp_path / "f.mp4"),
                          duration_sec=5, fps=30, width=320, height=240, status="accumulated",
                          created_at=datetime.now(), class_id=g.id, captured_date="2026-06-09"))
    repo.add_scenes([Scene(id="s1", video_id="v1", time_start=0, time_end=5)])
    img = tmp_path / "fr.jpg"; img.write_bytes(b"img")
    repo.add_frames([Frame(id="f1", scene_id="s1", t=1, image_path=str(img), blur_score=200, kept=True)])
    repo.add_candidates([ObservationCandidate(
        id="c1", video_id="v1", scene_id="s1", time_start=1, time_end=3,
        temp_child_id="child_A", observed_behavior="b", visual_evidence="e",
        confidence=0.6, created_at=datetime.now())])
    assert propose_matches(repo, "v1") == []  # 비활성 → 후보 없음


# ---------------------------------------------------------------------------
# 5) 민감 액션 감사 로그
# ---------------------------------------------------------------------------

def test_consent_change_and_export_audited(repo, tmp_path):
    faces = str(tmp_path / "faces")
    g = register_class(repo, "반", "teacher_01")
    c = register_child(repo, g.id, pseudonym_id="p_07", consent=False)
    set_child_face_consent(repo, c.id, consent=True, faces_dir=faces)
    actions = {l.action for l in repo.list_audit_logs(video_id=c.id)}
    assert "face_consent_change" in actions

    export_class_report_json(g.id, repo)
    assert any(l.action == "export" for l in repo.list_audit_logs(video_id=g.id))


def test_assistant_logged(repo):
    handle_query(repo, actor="teacher_01", query="자연탐구 찾아줘")
    assert len(repo.list_assistant_logs(actor="teacher_01")) == 1


# ---------------------------------------------------------------------------
# 6) 점수/발달/평정 필드 부재
# ---------------------------------------------------------------------------

def test_core_schemas_have_no_score_fields():
    for model in (ObservationCandidate, FinalRecord, KicceItemCandidate, NuriAreaCandidate):
        fields = set(model.model_fields.keys())
        for banned in ("score", "level", "rating", "grade"):
            assert banned not in fields, f"{model.__name__}.{banned} 금지"


# ---------------------------------------------------------------------------
# 7) export(영상·클래스) 미디어 경로·키 미포함
# ---------------------------------------------------------------------------

def _seed_video_with_final(repo):
    g = register_class(repo, "반", "teacher_01")
    repo.save_video(Video(id="v1", filename="f.mp4",
                          stored_path="data/videos/v1/original.mp4",
                          duration_sec=5, fps=30, width=320, height=240, status="reviewed",
                          created_at=datetime.now(), class_id=g.id, captured_date="2026-06-09"))
    repo.add_scenes([Scene(id="s1", video_id="v1", time_start=0, time_end=5)])
    repo.add_candidates([ObservationCandidate(
        id="c1", video_id="v1", scene_id="s1", time_start=1, time_end=3,
        temp_child_id="child_A", observed_behavior="b", visual_evidence="e",
        confidence=0.6, created_at=datetime.now())])
    repo.save_final_record(FinalRecord(
        id="r1", candidate_id="c1", pseudonym_id="p_07",
        period_start="2026-06-04", period_end="2026-06-10",
        final_behavior="확정", confirmed_areas=["자연탐구"],
        decision="accepted", edited=False, confirmed_by="teacher_01",
        confirmed_at=datetime.now()))
    return g


def test_video_export_excludes_media(repo):
    _seed_video_with_final(repo)
    out = export_report_json("v1", repo)
    for tok in _MEDIA_FRAGMENTS + _MEDIA_KEYS:
        assert tok not in out


def test_class_export_excludes_media(repo):
    g = _seed_video_with_final(repo)
    out = export_class_report_json(g.id, repo)
    for tok in _MEDIA_FRAGMENTS + _MEDIA_KEYS:
        assert tok not in out


# ---------------------------------------------------------------------------
# 8) AI비서 금지 요청 거부
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("q", [
    "발달 점수 매겨줘", "관찰기록 대신 확정해줘", "child_A 실명 알려줘", "새 관찰기록 작성해줘",
])
def test_assistant_refuses_banned(repo, q):
    res = handle_query(repo, actor="teacher_01", query=q)
    assert res["refused"] is True
