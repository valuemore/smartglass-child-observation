"""관찰 후보 생성 서비스 — Mock 및 외부 비전 어댑터 기반 배치 처리."""

from __future__ import annotations

import uuid
from datetime import datetime

from core.config import (
    DEFAULT_ACTOR,
    VISION_MAX_SCENES_PER_VIDEO,
    VISION_MIN_SCENE_DURATION_SEC,
    VISION_SCENE_SELECTION_ENABLED,
)
from core.schemas import AuditLog, FrameRef, ObservationCandidate, SegmentAnalysisRequest
from services.video_preprocess_service import select_scenes_for_analysis
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
    - 재실행 시 중복 누적을 막기 위해 기존 후보(+연결 매핑)를 먼저 삭제한다.
    """
    if adapter is None:
        adapter = MockVisionAdapter()

    video = repo.get_video(video_id)
    if video is None:
        raise ValueError(f"video_id={video_id} 를 찾을 수 없습니다.")

    # 중복 방지: 이 영상의 기존 관찰 후보와 연결된 매핑을 먼저 삭제한다.
    repo.delete_candidates_for_video(video_id)

    all_scenes = repo.list_scenes(video_id)
    all_frames_flat = [f for s in all_scenes for f in repo.list_frames(s.id)]

    if VISION_SCENE_SELECTION_ENABLED:
        scenes = select_scenes_for_analysis(
            all_scenes, all_frames_flat,
            max_scenes=VISION_MAX_SCENES_PER_VIDEO,
            min_scene_duration=VISION_MIN_SCENE_DURATION_SEC,
        )
    else:
        scenes = all_scenes

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
            f"total_scenes={len(all_scenes)} "
            f"selected_scenes={len(scenes)} "
            f"candidates={len(all_candidates)}"
        ),
        created_at=datetime.now(),
    ))

    return all_candidates


def generate_observation_candidates_with_provider(
    video_id: str,
    repo: SqliteRepository,
    actor: str = DEFAULT_ACTOR,
) -> tuple[list[ObservationCandidate], dict]:
    """팩토리가 선택한 비전 어댑터(mock 또는 external)로 관찰 후보를 생성해 DB에 저장한다.

    - 어댑터 선택은 core/config.py VISION_* 설정에 따라 provider_factory가 결정한다.
    - VISION_DRY_RUN=true(기본) 시 외부 API를 호출하지 않는다.
    - 없는 video_id 는 ValueError.
    - 재실행 시 중복 누적을 막기 위해 기존 후보(+연결 매핑)를 먼저 삭제한다.
    - 반환: (candidates, provider_info)
      provider_info 키: provider, model, dry_run, fallback_reason, segments, frames, discarded
    """
    from services.vision.provider_factory import get_vision_adapter

    adapter, provider_info = get_vision_adapter()

    video = repo.get_video(video_id)
    if video is None:
        raise ValueError(f"video_id={video_id} 를 찾을 수 없습니다.")

    repo.delete_candidates_for_video(video_id)

    all_scenes = repo.list_scenes(video_id)
    all_frames_flat = [f for s in all_scenes for f in repo.list_frames(s.id)]

    if VISION_SCENE_SELECTION_ENABLED:
        scenes = select_scenes_for_analysis(
            all_scenes, all_frames_flat,
            max_scenes=VISION_MAX_SCENES_PER_VIDEO,
            min_scene_duration=VISION_MIN_SCENE_DURATION_SEC,
        )
    else:
        scenes = all_scenes

    all_candidates: list[ObservationCandidate] = []
    total_frames = 0
    total_discarded = 0

    for scene in scenes:
        kept_frames = [f for f in repo.list_frames(scene.id) if f.kept]
        if not kept_frames:
            continue

        total_frames += len(kept_frames)
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
        total_discarded += getattr(adapter, "_last_discarded", 0)

    if all_candidates:
        repo.add_candidates(all_candidates)

    provider_info = {
        **provider_info,
        "total_scenes": len(all_scenes),
        "segments": len(scenes),
        "frames": total_frames,
        "discarded": total_discarded,
        "stored": len(all_candidates),
    }

    repo.write_audit(AuditLog(
        id=f"audit_{video_id}_vision_{uuid.uuid4().hex[:6]}",
        video_id=video_id,
        actor=actor,
        action="analyze",
        detail=(
            f"vision_candidate_generation "
            f"provider={provider_info['provider']} "
            f"dry_run={provider_info['dry_run']} "
            f"total_scenes={provider_info['total_scenes']} "
            f"selected_scenes={provider_info['segments']} "
            f"frames={provider_info['frames']} "
            f"stored={provider_info['stored']} "
            f"discarded={provider_info['discarded']}"
        ),
        created_at=datetime.now(),
    ))

    return all_candidates, provider_info
