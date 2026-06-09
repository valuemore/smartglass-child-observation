"""보안·삭제·개인정보 보호 서비스.

원칙:
- 원본 영상 삭제 전에 반드시 audit_log에 delete 기록을 남긴다.
- audit_log는 삭제하지 않는다(영구 보존).
- export payload에 원본 영상 경로·프레임 경로를 포함하지 않는다.
"""

from __future__ import annotations

import shutil
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from core.config import DEFAULT_ACTOR, FRAMES_DIR, VIDEOS_DIR
from core.schemas import AuditLog

_SENSITIVE_KEYS = frozenset({"stored_path", "image_path"})
_SENSITIVE_FRAGMENTS = ("data/videos", "data/frames")


def delete_video_and_related_data(
    video_id: str,
    repo,
    actor: str = DEFAULT_ACTOR,
    videos_dir: str = VIDEOS_DIR,
    frames_dir: str = FRAMES_DIR,
) -> dict:
    """원본 영상과 파생 데이터를 삭제하고 감사 로그를 기록한다.

    순서:
    1. video 조회 (없으면 ValueError)
    2. audit_log action="delete" 기록 — DB 삭제 이전 필수
    3. 원본 영상 파일 삭제 (없어도 graceful)
    4. data/frames/{video_id}/ 폴더 삭제 (없어도 graceful)
    5. repo.delete_video_cascade(video_id) — DB 행 cascade 삭제
       audit_log는 FK 없음 → 고아 행으로 영구 보존

    반환: {"video_id", "file_deleted", "frames_dir_deleted", "db_rows_deleted"}
    """
    video = repo.get_video(video_id)
    if video is None:
        raise ValueError(f"video_id={video_id} 를 찾을 수 없습니다.")

    # 2. audit_log 기록 — video 행이 아직 DB에 존재하는 시점
    repo.write_audit(AuditLog(
        id=f"audit_{video_id}_delete_{uuid.uuid4().hex[:6]}",
        video_id=video_id,
        actor=actor,
        action="delete",
        detail=f"delete_video_and_related_data filename={video.filename}",
        created_at=datetime.now(),
    ))

    # 3. 원본 영상 파일 삭제
    file_deleted = False
    video_path = Path(video.stored_path)
    if video_path.exists():
        video_path.unlink()
        file_deleted = True

    # 4. 프레임 폴더 삭제
    frames_dir_deleted = False
    frames_path = Path(frames_dir) / video_id
    if frames_path.exists():
        shutil.rmtree(frames_path)
        frames_dir_deleted = True

    # 5. DB cascade 삭제 (audit_log 제외)
    db_rows_deleted = repo.delete_video_cascade(video_id)

    return {
        "video_id": video_id,
        "file_deleted": file_deleted,
        "frames_dir_deleted": frames_dir_deleted,
        "db_rows_deleted": db_rows_deleted,
    }


def sanitize_report_export(report_data: dict) -> dict:
    """export dict에서 민감 경로 필드를 제거한다.

    - 최상위 키 stored_path, image_path 제거
    - 문자열 값에 'data/videos' 또는 'data/frames' 포함 시 해당 키 제거
    원본 dict는 변경하지 않고 새 dict를 반환한다.
    """
    return {
        k: v
        for k, v in report_data.items()
        if k not in _SENSITIVE_KEYS
        and not (isinstance(v, str) and _has_sensitive_fragment(v))
    }


def assert_no_sensitive_paths(payload: Any, _path: str = "root") -> None:
    """payload 전체를 재귀 순회해 민감 경로가 포함되면 ValueError를 발생시킨다.

    외부 API 전송 직전 사전 검증용.
    Windows 백슬래시 경로(data\\videos 등)도 탐지하기 위해 구분자를 정규화한다.
    """
    if isinstance(payload, str):
        normalized = _normalize_separators(payload)
        for fragment in _SENSITIVE_FRAGMENTS:
            if fragment in normalized:
                raise ValueError(
                    f"민감 경로가 payload에 포함되었습니다 [{_path}]: {fragment!r}"
                )
    elif isinstance(payload, dict):
        for k, v in payload.items():
            assert_no_sensitive_paths(v, _path=f"{_path}.{k}")
    elif isinstance(payload, (list, tuple)):
        for i, item in enumerate(payload):
            assert_no_sensitive_paths(item, _path=f"{_path}[{i}]")


def _normalize_separators(value: str) -> str:
    """경로 구분자를 슬래시로 통일한다(Windows 백슬래시 → 슬래시)."""
    return value.replace("\\", "/")


def _has_sensitive_fragment(value: str) -> bool:
    normalized = _normalize_separators(value)
    return any(fragment in normalized for fragment in _SENSITIVE_FRAGMENTS)
