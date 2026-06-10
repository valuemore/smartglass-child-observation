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
    photo_ext: str = "jpg",
    consent: bool = False,
    consent_by: Optional[str] = None,
    faces_dir: str = FACES_DIR,
    actor: str = DEFAULT_ACTOR,
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
        Path(reference_photo_path).write_bytes(reference_photo)

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
    )
    repo.add_child(child)

    repo.write_audit(AuditLog(
        id=f"audit_{child_id}_register_{uuid.uuid4().hex[:6]}",
        video_id=child_id,  # 유아 스코프 감사(영상 없음)
        actor=actor, action="access",
        detail=(
            f"child_register class={class_id} pseudonym={child.pseudonym_id} "
            f"consent={consent} has_photo={reference_photo_path is not None}"
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
        if child.reference_photo_path:
            p = Path(child.reference_photo_path)
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
    if child.reference_photo_path:
        p = Path(child.reference_photo_path)
        if p.exists() and "faces" in p.parts:
            p.unlink(missing_ok=True)
    return repo.delete_child_cascade(child_id)


def list_children(repo: SqliteRepository, class_id: str) -> list[Child]:
    return repo.list_children(class_id)
