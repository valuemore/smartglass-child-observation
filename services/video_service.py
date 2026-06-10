"""
영상 업로드 서비스.

원칙:
- 원본 영상은 로컬 videos_dir 에만 저장. 외부로 전송하지 않는다.
- 메타데이터 추출(OpenCV)에 실패해도 업로드 자체가 실패하지 않는다(0 처리).
- 업로드 시 audit_log(action="upload")를 반드시 기록한다.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from pathlib import Path

from core.config import DEFAULT_ACTOR, RETENTION_DAYS
from core.schemas import AuditLog, Video
from storage.sqlite_repository import SqliteRepository


def save_uploaded_video(
    file_bytes: bytes,
    filename: str,
    repo: SqliteRepository,
    videos_dir: str,
    actor: str = DEFAULT_ACTOR,
    institution: str = "",
    class_name: str = "",
    observation_context: str = "",
    notes: str = "",
    class_id: str | None = None,
    captured_date: str | None = None,
) -> Video:
    """
    업로드된 영상 바이트를 저장하고 Video 메타데이터를 DB에 기록한다.

    class_id: 소속 클래스(class_group.id, V2). captured_date: 촬영일(YYYY-MM-DD).
    반환값: 저장된 Video 객체 (status="uploaded", analysis_status="queued")

    """
    now = datetime.now()
    video_id = f"vid_{now.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"

    ext = Path(filename).suffix.lstrip(".").lower() or "mp4"
    dest_dir = Path(videos_dir) / video_id
    dest_dir.mkdir(parents=True, exist_ok=True)
    stored_path = str(dest_dir / f"original.{ext}")

    Path(stored_path).write_bytes(file_bytes)

    fps, width, height, duration_sec = _extract_metadata(stored_path)

    video = Video(
        id=video_id,
        filename=filename,
        stored_path=stored_path,
        duration_sec=duration_sec,
        fps=fps,
        width=width,
        height=height,
        status="uploaded",
        created_at=now,
        retention_until=now + timedelta(days=RETENTION_DAYS),
        owner=actor,
        institution=institution,
        class_name=class_name,
        observation_context=observation_context,
        notes=notes,
        class_id=class_id,
        captured_date=captured_date or now.strftime("%Y-%m-%d"),
    )
    repo.save_video(video)

    repo.write_audit(AuditLog(
        id=f"audit_{video_id}",
        video_id=video_id,
        actor=actor,
        action="upload",
        detail=f"filename={filename} stored={stored_path}",
        created_at=now,
    ))

    return video


def _extract_metadata(path: str) -> tuple[float, int, int, float]:
    """OpenCV로 fps·width·height·duration_sec 를 추출한다. 실패 시 모두 0 반환."""
    try:
        import cv2  # type: ignore
        cap = cv2.VideoCapture(path)
        if not cap.isOpened():
            return 0.0, 0, 0, 0.0
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        frame_count = float(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        duration_sec = frame_count / fps if fps > 0 else 0.0
        cap.release()
        return fps, width, height, duration_sec
    except Exception:
        return 0.0, 0, 0, 0.0
