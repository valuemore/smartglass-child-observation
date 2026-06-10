"""클래스·유아 등록 서비스 테스트 (V2-2). tmp_path 격리 — data/faces 미사용."""

from pathlib import Path

import pytest

from services.class_service import (
    register_class, register_child, set_child_face_consent, delete_child, list_children,
)
from storage.sqlite_repository import SqliteRepository


@pytest.fixture
def repo(tmp_path: Path) -> SqliteRepository:
    db = SqliteRepository(str(tmp_path / "cls.db"))
    db.init_schema()
    return db


@pytest.fixture
def faces_dir(tmp_path: Path) -> str:
    return str(tmp_path / "faces")


# ---------------------------------------------------------------------------
# 클래스 생성
# ---------------------------------------------------------------------------

def test_register_class_defaults_face_off(repo):
    g = register_class(repo, name="햇빛반", teacher_owner="teacher_01")
    assert g.id.startswith("cls_")
    assert g.face_match_enabled is False
    assert repo.get_class(g.id).name == "햇빛반"


def test_register_class_empty_name_raises(repo):
    with pytest.raises(ValueError):
        register_class(repo, name="  ", teacher_owner="teacher_01")


# ---------------------------------------------------------------------------
# 유아 등록 — 동의 없으면 사진 미저장
# ---------------------------------------------------------------------------

def test_register_child_without_consent_no_photo(repo, faces_dir):
    g = register_class(repo, "햇빛반", "teacher_01")
    c = register_child(repo, g.id, pseudonym_id="p_07", display_label="유아7",
                       consent=False, faces_dir=faces_dir)
    assert c.face_match_consent is False
    assert c.reference_photo_path is None
    assert c.face_embedding is None
    # 디렉토리 자체가 생성되지 않아야 함
    assert not Path(faces_dir).exists()


def test_register_child_photo_without_consent_raises(repo, faces_dir):
    g = register_class(repo, "햇빛반", "teacher_01")
    with pytest.raises(ValueError):
        register_child(repo, g.id, pseudonym_id="p_08",
                       reference_photo=b"\xff\xd8\xff\xd9", consent=False,
                       faces_dir=faces_dir)


def test_register_child_with_consent_saves_photo(repo, faces_dir):
    g = register_class(repo, "햇빛반", "teacher_01")
    c = register_child(repo, g.id, pseudonym_id="p_09", display_label="유아9",
                       reference_photo=b"\xff\xd8\xff\xd9", photo_ext="jpg",
                       consent=True, consent_by="teacher_01", faces_dir=faces_dir)
    assert c.face_match_consent is True
    assert c.reference_photo_path is not None
    assert Path(c.reference_photo_path).exists()
    assert Path(c.reference_photo_path).read_bytes() == b"\xff\xd8\xff\xd9"
    # data/faces 가 아닌 tmp faces_dir 하위에 저장됐는지
    assert faces_dir in c.reference_photo_path


def test_register_child_unknown_class_raises(repo, faces_dir):
    with pytest.raises(ValueError):
        register_child(repo, "no_class", pseudonym_id="p_x", faces_dir=faces_dir)


# ---------------------------------------------------------------------------
# 동의 철회 → 사진 파일·임베딩 즉시 삭제
# ---------------------------------------------------------------------------

def test_revoke_consent_deletes_photo_and_embedding(repo, faces_dir):
    g = register_class(repo, "햇빛반", "teacher_01")
    c = register_child(repo, g.id, pseudonym_id="p_10",
                       reference_photo=b"\xaa\xbb", consent=True,
                       consent_by="teacher_01", faces_dir=faces_dir)
    photo_path = c.reference_photo_path
    assert Path(photo_path).exists()

    updated = set_child_face_consent(repo, c.id, consent=False, by="teacher_01",
                                     faces_dir=faces_dir)
    assert updated.face_match_consent is False
    assert updated.reference_photo_path is None
    assert updated.face_embedding is None
    assert not Path(photo_path).exists()  # 파일 즉시 삭제


def test_consent_change_writes_audit(repo, faces_dir):
    g = register_class(repo, "햇빛반", "teacher_01")
    c = register_child(repo, g.id, pseudonym_id="p_11", consent=False, faces_dir=faces_dir)
    set_child_face_consent(repo, c.id, consent=True, by="teacher_01", faces_dir=faces_dir)
    logs = repo.list_audit_logs(video_id=c.id)
    assert any(l.action == "face_consent_change" for l in logs)


# ---------------------------------------------------------------------------
# 유아 삭제 → 사진 파일 + cascade
# ---------------------------------------------------------------------------

def test_delete_child_removes_photo_and_rows(repo, faces_dir):
    g = register_class(repo, "햇빛반", "teacher_01")
    c = register_child(repo, g.id, pseudonym_id="p_12",
                       reference_photo=b"\x01", consent=True, faces_dir=faces_dir)
    photo_path = c.reference_photo_path
    assert Path(photo_path).exists()

    delete_child(repo, c.id, faces_dir=faces_dir)
    assert repo.get_child(c.id) is None
    assert not Path(photo_path).exists()
    # 삭제 감사 로그는 보존
    assert any(l.action == "delete" for l in repo.list_audit_logs(video_id=c.id))


def test_delete_unknown_child_raises(repo, faces_dir):
    with pytest.raises(ValueError):
        delete_child(repo, "no_child", faces_dir=faces_dir)


# ---------------------------------------------------------------------------
# 실명 미저장 (불변식) — Child 에 real_name 등 주입 거부
# ---------------------------------------------------------------------------

def test_no_real_name_field(repo, faces_dir):
    g = register_class(repo, "햇빛반", "teacher_01")
    c = register_child(repo, g.id, pseudonym_id="p_13", display_label="유아13",
                       consent=False, faces_dir=faces_dir)
    assert not hasattr(c, "real_name")
    assert not hasattr(c, "name")
    children = list_children(repo, g.id)
    assert len(children) == 1
    assert children[0].pseudonym_id == "p_13"
