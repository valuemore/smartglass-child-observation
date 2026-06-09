"""관찰 후보 생성 서비스 — Mock 비전 어댑터 기반 배치 처리."""

from __future__ import annotations

import uuid
from datetime import datetime

from core.config import DEFAULT_ACTOR
from core.schemas import AuditLog, FrameRef, ObservationCandidate, SegmentAnalysisRequest
from services.vision.mock_adapter import MockVisionAdapter
from storage.sqlite_repository import SqliteRepository


def generate_mock_observation_candidates(
    video_id: str,
    repo: SqliteRepository,
    adapter=None,
    actor: str = DEFAULT_ACTOR,
) -> list[ObservationCandidate]:
    """전처리된 scene/frame 데이터로 Mock 관찰 후보를 생성해 DB에 저장한다.

    - adapter 기본값: MockVisionAdapter().
    - kept=True 프레임이 없는 scene 은 건너뜀(오류 없음).
    - 전체 후보가 0개이면 빈 리스트 반환(오류 없음).
    - 없는 video_id 는 ValueError.
    """
    if adapter is None:
        adapter = MockVisionAdapter()

    video = repo.get_video(video_id)
    if video is None:
        raise ValueError(f"video_id={video_id} 를 찾을 수 없습니다.")

    scenes = repo.list_scenes(video_id)
    all_candidates: list[ObservationCandidate] = []

    for scene in scenes:
        kept_frames = [f for f in repo.list_frames(scene.id) if f.kept]
        if not kept_frames:
            continue

        frame_refs = [
            FrameRef(frame_id=f.id, t=f.t, image_ref=f.image_path)
            for f in kept_frames
        ]
        request = SegmentAnalysisRequest(
            video_id=video_id,
            segment_id=scene.id,
            time_start=scene.time_start,
            time_end=scene.time_end,
            frames=frame_refs,
        )
        result = adapter.analyze_segment(request)
        all_candidates.extend(result.observations)

    if all_candidates:
        repo.add_candidates(all_candidates)

    repo.write_audit(AuditLog(
        id=f"audit_{video_id}_mock_{uuid.uuid4().hex[:6]}",
        video_id=video_id,
        actor=actor,
        action="analyze",
        detail=(
            f"mock_vision_candidate_generation "
            f"scenes={len(scenes)} "
            f"candidates={len(all_candidates)}"
        ),
        created_at=datetime.now(),
    ))

    return all_candidates
