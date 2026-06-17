"""클래스·유아 등록 서비스 (V2-2).

원칙(보안·개인정보):
- 유아 **실명 미저장**. 가명 ID(pseudonym_id)와 표시 라벨만 사용한다.
- 얼굴 참조사진은 **동의(face_match_consent=True) 시에만** `data/faces/`(제한 접근, git 제외)에 저장.
- 동의 철회 시 참조사진 파일·임베딩을 **즉시 삭제**한다.
- 얼굴 참조사진·임베딩은 로컬 전용이며 외부로 전송하지 않는다(이 서비스는 전송 코드 없음).
- 모든 등록·동의 변경·삭제·사진 접근은 audit_log 에 기록한다.

얼굴 임베딩 계산은 V2-4(FaceMatchService)에서 수행한다. 본 서비스는 참조사진 저장까지만 담당하며
face_embedding 은 None 으로 둔다.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from core.config import DEFAULT_ACTOR, FACES_DIR
from core.schemas import AuditLog, Child, ClassGroup
from storage.sqlite_repository import SqliteRepository

_ALLOWED_PHOTO_EXT = {"jpg", "jpeg", "png", "webp"}


def _auto_rotate_photo(img_bytes: bytes) -> bytes:
    """EXIF orientation을 적용해 사진을 올바른 방향으로 저장한다.

    스마트폰 사진은 회전 정보를 EXIF에만 기록하고 픽셀은 눕혀서 저장하는 경우가 많다.
    PIL ImageOps.exif_transpose()로 픽셀을 실제 방향으로 회전한 뒤 반환한다.
    """
    try:
        import io
        from PIL import Image, ImageOps
        img = Image.open(io.BytesIO(img_bytes))
        img = ImageOps.exif_transpose(img)
        buf = io.BytesIO()
        save_fmt = (img.format or "JPEG").upper()
        if save_fmt not in ("JPEG", "PNG", "WEBP"):
            save_fmt = "JPEG"
        if save_fmt == "JPEG":
            img.save(buf, format=save_fmt, quality=90, exif=b"")
        else:
            img.save(buf, format=save_fmt)
        return buf.getvalue()
    except Exception:
        return img_bytes


def register_class(
    repo: SqliteRepository,
    name: str,
    teacher_owner: str,
    face_match_enabled: bool = False,
    actor: str = DEFAULT_ACTOR,
) -> ClassGroup:
    """클래스(우리반)를 생성한다. 얼굴매칭은 기본 OFF."""
    if not name.strip():
        raise ValueError("클래스 이름은 비어 있을 수 없습니다.")
    now = datetime.now()
    group = ClassGroup(
        id=f"cls_{now.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}",
        name=name.strip(),
        teacher_owner=teacher_owner,
        face_match_enabled=face_match_enabled,
        created_at=now,
    )
    repo.save_class(group)
    repo.write_audit(AuditLog(
        id=f"audit_{group.id}_create_{uuid.uuid4().hex[:6]}",
        video_id=group.id,  # 클래스 스코프 감사(영상 없음)
        actor=actor, action="access",
        detail=f"class_create name={group.name} face_match_enabled={face_match_enabled}",
        created_at=now,
    ))
    return group


def register_child(
    repo: SqliteRepository,
    class_id: str,
    pseudonym_id: str,
    display_label: str = "",
    reference_photo: Optional[bytes] = None,
    photo_right: Optional[bytes] = None,      # 우측면 사진 바이트 (신규)
    photo_left: Optional[bytes] = None,       # 좌측면 사진 바이트 (신규)
    photo_ext: str = "jpg",
    consent: bool = False,
    consent_by: Optional[str] = None,
    faces_dir: str = FACES_DIR,
    actor: str = DEFAULT_ACTOR,
    birth_date: Optional[str] = None,         # 신규
    gender: Optional[str] = None,             # 신규
    notes: str = "",                          # 신규
) -> Child:
    """유아를 가명 ID로 등록한다. 실명은 받지 않는다.

    reference_photo 가 주어지면 **consent=True 일 때만** data/faces/{class_id}/ 에 저장한다.
    동의 없이 사진을 저장하려 하면 ValueError.
    """
    if not pseudonym_id.strip():
        raise ValueError("가명 ID는 비어 있을 수 없습니다.")
    if repo.get_class(class_id) is None:
        raise ValueError(f"class_id={class_id} 클래스를 찾을 수 없습니다.")
    if reference_photo is not None and not consent:
        raise ValueError("얼굴 매칭 동의 없이 참조사진을 저장할 수 없습니다.")

    now = datetime.now()
    child_id = f"chd_{now.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"

    reference_photo_path: Optional[str] = None
    if reference_photo is not None and consent:
        ext = (photo_ext or "jpg").lower().lstrip(".")
        if ext not in _ALLOWED_PHOTO_EXT:
            ext = "jpg"
        dest_dir = Path(faces_dir) / class_id
        dest_dir.mkdir(parents=True, exist_ok=True)
        reference_photo_path = str(dest_dir / f"{child_id}.{ext}")
        Path(reference_photo_path).write_bytes(_auto_rotate_photo(reference_photo))

    photo_right_path: Optional[str] = None
    if photo_right is not None and consent:
        ext_r = (photo_ext or "jpg").lower().lstrip(".")
        if ext_r not in _ALLOWED_PHOTO_EXT:
            ext_r = "jpg"
        dest_dir = Path(faces_dir) / class_id
        dest_dir.mkdir(parents=True, exist_ok=True)
        photo_right_path = str(dest_dir / f"{child_id}_right.{ext_r}")
        Path(photo_right_path).write_bytes(_auto_rotate_photo(photo_right))

    photo_left_path: Optional[str] = None
    if photo_left is not None and consent:
        ext_l = (photo_ext or "jpg").lower().lstrip(".")
        if ext_l not in _ALLOWED_PHOTO_EXT:
            ext_l = "jpg"
        dest_dir = Path(faces_dir) / class_id
        dest_dir.mkdir(parents=True, exist_ok=True)
        photo_left_path = str(dest_dir / f"{child_id}_left.{ext_l}")
        Path(photo_left_path).write_bytes(_auto_rotate_photo(photo_left))

    child = Child(
        id=child_id,
        class_id=class_id,
        pseudonym_id=pseudonym_id.strip(),
        display_label=display_label.strip() or pseudonym_id.strip(),
        reference_photo_path=reference_photo_path,
        face_embedding=None,  # 임베딩은 V2-4에서 계산
        face_match_consent=consent,
        consent_at=now if consent else None,
        consent_by=consent_by if consent else None,
        created_at=now,
        birth_date=birth_date,
        gender=gender,
        notes=notes,
        photo_right_path=photo_right_path,
        photo_left_path=photo_left_path,
    )
    repo.add_child(child)

    repo.write_audit(AuditLog(
        id=f"audit_{child_id}_register_{uuid.uuid4().hex[:6]}",
        video_id=child_id,  # 유아 스코프 감사(영상 없음)
        actor=actor, action="access",
        detail=(
            f"child_register class={class_id} pseudonym={child.pseudonym_id} "
            f"consent={consent} has_photo={reference_photo_path is not None} "
            f"has_right_photo={photo_right_path is not None} "
            f"has_left_photo={photo_left_path is not None}"
        ),
        created_at=now,
    ))
    if reference_photo_path is not None:
        repo.write_audit(AuditLog(
            id=f"audit_{child_id}_photo_{uuid.uuid4().hex[:6]}",
            video_id=child_id, actor=actor, action="reference_photo_access",
            detail="reference_photo_saved", created_at=now,
        ))
    return child


def set_child_face_consent(
    repo: SqliteRepository,
    child_id: str,
    consent: bool,
    by: Optional[str] = None,
    faces_dir: str = FACES_DIR,
    actor: str = DEFAULT_ACTOR,
) -> Child:
    """얼굴매칭 동의 상태를 변경한다.

    철회(consent=False) 시: DB의 참조사진 경로·임베딩을 비우고(repo.set_face_consent),
    실제 참조사진 파일도 즉시 삭제한다. 감사 로그(face_consent_change) 기록.
    """
    child = repo.get_child(child_id)
    if child is None:
        raise ValueError(f"child_id={child_id} 유아를 찾을 수 없습니다.")

    now = datetime.now()
    if not consent:
        # 철회: 파일 먼저 삭제 → DB 비우기 순서(파일 누락 방지)
        for path_str in [child.reference_photo_path, child.photo_right_path, child.photo_left_path]:
            if path_str:
                p = Path(path_str)
                if p.exists() and "faces" in p.parts:
                    p.unlink(missing_ok=True)
        repo.set_face_consent(child_id, consent=False, by=by, consent_at=now)
        detail = "consent_revoked reference_photo_and_embedding_deleted"
    else:
        repo.set_face_consent(child_id, consent=True, by=by, consent_at=now)
        detail = "consent_granted"

    repo.write_audit(AuditLog(
        id=f"audit_{child_id}_consent_{uuid.uuid4().hex[:6]}",
        video_id=child_id, actor=actor, action="face_consent_change",
        detail=detail, created_at=now,
    ))
    return repo.get_child(child_id)


def delete_child(
    repo: SqliteRepository,
    child_id: str,
    faces_dir: str = FACES_DIR,
    actor: str = DEFAULT_ACTOR,
) -> int:
    """유아와 연관 데이터(참조사진 파일·임베딩·얼굴 매칭 후보)를 삭제한다."""
    child = repo.get_child(child_id)
    if child is None:
        raise ValueError(f"child_id={child_id} 유아를 찾을 수 없습니다.")
    now = datetime.now()
    # 감사 로그는 행 삭제 이전에 기록
    repo.write_audit(AuditLog(
        id=f"audit_{child_id}_delete_{uuid.uuid4().hex[:6]}",
        video_id=child_id, actor=actor, action="delete",
        detail=f"child_delete pseudonym={child.pseudonym_id}", created_at=now,
    ))
    for path_str in [child.reference_photo_path, child.photo_right_path, child.photo_left_path]:
        if path_str:
            p = Path(path_str)
            if p.exists() and "faces" in p.parts:
                p.unlink(missing_ok=True)
    return repo.delete_child_cascade(child_id)


def list_children(repo: SqliteRepository, class_id: str) -> list[Child]:
    return repo.list_children(class_id)


def update_child_meta(
    repo: SqliteRepository,
    child_id: str,
    display_label: str,
    birth_date: Optional[str],
    gender: Optional[str],
    notes: str,
    actor: str = DEFAULT_ACTOR,
) -> Child:
    """display_label, birth_date, gender, notes를 갱신한다."""
    child = repo.get_child(child_id)
    if child is None:
        raise ValueError(f"child_id={child_id} 유아를 찾을 수 없습니다.")
    now = datetime.now()
    repo.update_child_meta(child_id, display_label.strip(), birth_date, gender, notes)
    repo.write_audit(AuditLog(
        id=f"audit_{child_id}_meta_{uuid.uuid4().hex[:6]}",
        video_id=child_id, actor=actor, action="access",
        detail=f"child_meta_update pseudonym={child.pseudonym_id}",
        created_at=now,
    ))
    return repo.get_child(child_id)


def update_child_photos(
    repo: SqliteRepository,
    child_id: str,
    photo_right: Optional[bytes] = None,
    photo_left: Optional[bytes] = None,
    photo_ext: str = "jpg",
    faces_dir: str = FACES_DIR,
    actor: str = DEFAULT_ACTOR,
) -> Child:
    """우측면·좌측면 사진을 교체하고 다각도 임베딩을 무효화한다.

    사진 변경 시 기존 평균 임베딩이 유효하지 않으므로 face_embedding=None으로 초기화.
    다음 propose_matches() 호출 시 새 3각도 평균 임베딩이 재계산된다.
    """
    child = repo.get_child(child_id)
    if child is None:
        raise ValueError(f"child_id={child_id} 유아를 찾을 수 없습니다.")
    if not child.face_match_consent:
        raise ValueError("얼굴 매칭 동의 없이 사진을 저장할 수 없습니다.")

    now = datetime.now()
    ext = (photo_ext or "jpg").lower().lstrip(".")
    if ext not in _ALLOWED_PHOTO_EXT:
        ext = "jpg"
    dest_dir = Path(faces_dir) / child.class_id
    dest_dir.mkdir(parents=True, exist_ok=True)

    photo_right_path: Optional[str] = child.photo_right_path
    if photo_right is not None:
        photo_right_path = str(dest_dir / f"{child_id}_right.{ext}")
        Path(photo_right_path).write_bytes(_auto_rotate_photo(photo_right))

    photo_left_path: Optional[str] = child.photo_left_path
    if photo_left is not None:
        photo_left_path = str(dest_dir / f"{child_id}_left.{ext}")
        Path(photo_left_path).write_bytes(_auto_rotate_photo(photo_left))

    # 임베딩 무효화: 다음 propose_matches()에서 다각도 평균으로 재계산
    repo.update_child_photos(child_id, photo_right_path, photo_left_path)

    repo.write_audit(AuditLog(
        id=f"audit_{child_id}_photos_{uuid.uuid4().hex[:6]}",
        video_id=child_id, actor=actor, action="reference_photo_access",
        detail=(
            f"child_photos_update pseudonym={child.pseudonym_id} "
            f"right={photo_right is not None} left={photo_left is not None} "
            f"embedding_invalidated=True"
        ),
        created_at=now,
    ))
    return repo.get_child(child_id)
