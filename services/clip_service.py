"""
근거 클립(evidence clip) 서비스.

원칙:
- 원본 영상은 보존하고 선별 구간만 ffmpeg로 추출한다.
- 외부 API에는 이 클립만 전송하고 원본 영상은 전송하지 않는다.
- 클립 수·총 길이 상한을 설정에서 관리한다.
- selected_for_vision_analysis=True 인 클립이 0개가 되지 않도록 fallback을 둔다.
"""

from __future__ import annotations

import json
import math
import subprocess
import uuid
from datetime import datetime
from pathlib import Path

from core.config import (
    CLIP_MAX_CLIPS_PER_VIDEO,
    CLIP_MAX_DURATION_SEC,
    CLIP_MAX_TOTAL_DURATION_SEC,
    CLIP_MIN_DURATION_SEC,
    CLIP_PADDING_SEC,
    CLIPS_DIR,
)
from core.schemas import Clip, Frame, Scene, Video


# ---------------------------------------------------------------------------
# 공개 진입점
# ---------------------------------------------------------------------------

def select_evidence_clips(
    video: Video,
    scenes: list[Scene],
    frames: list[Frame],
    max_clips: int = CLIP_MAX_CLIPS_PER_VIDEO,
    padding_sec: float = CLIP_PADDING_SEC,
    min_duration: float = CLIP_MIN_DURATION_SEC,
    max_duration: float = CLIP_MAX_DURATION_SEC,
    max_total_duration: float = CLIP_MAX_TOTAL_DURATION_SEC,
) -> list[Clip]:
    """장면 목록에서 근거 클립 후보를 선별한다.

    선별 전략:
    1. kept 프레임이 있는 장면만 후보로 올린다.
    2. 장면별 점수 = max(blur_score) × log(1 + duration)
    3. 후보 수 ≤ max_clips이면 전체 사용.
    4. 후보 수 > max_clips이면 영상을 max_clips 구간으로 균등 분할 후
       각 구간에서 최고 점수 장면 1개 선택.
    5. 선별 장면 앞뒤에 padding_sec 여유 추가 후 [min_dur, max_dur] 클램프.
    6. 총 길이 초과 시 짧은 클립부터 제거.
    7. Fallback: 최종 0개이면 점수가 가장 높은 1개 강제 포함.
    """
    if not scenes:
        return []

    kept_scene_ids = {f.scene_id for f in frames if f.kept}
    candidates = [s for s in scenes if s.id in kept_scene_ids]

    if not candidates:
        candidates = list(scenes)  # fallback: 모든 장면

    def _score(scene: Scene) -> float:
        blur_scores = [f.blur_score for f in frames if f.scene_id == scene.id and f.kept]
        max_blur = max(blur_scores) if blur_scores else 0.0
        duration = scene.time_end - scene.time_start
        return max_blur * math.log1p(duration)

    if len(candidates) <= max_clips:
        selected_scenes = sorted(candidates, key=lambda s: s.time_start)
    else:
        t_min = candidates[0].time_start
        t_max = candidates[-1].time_end
        interval = (t_max - t_min) / max_clips
        selected_scenes = []
        for i in range(max_clips):
            bucket_start = t_min + i * interval
            bucket_end = bucket_start + interval
            in_bucket = [
                s for s in candidates
                if s.time_start < bucket_end and s.time_end > bucket_start
            ]
            if in_bucket:
                best = max(in_bucket, key=_score)
                if best not in selected_scenes:
                    selected_scenes.append(best)
        selected_scenes.sort(key=lambda s: s.time_start)

    # 구간 클램프 후 Clip 객체 생성
    clips: list[Clip] = []
    video_duration = video.duration_sec if video.duration_sec > 0 else float("inf")
    for idx, scene in enumerate(selected_scenes):
        raw_start = max(0.0, scene.time_start - padding_sec)
        raw_end = min(video_duration, scene.time_end + padding_sec)
        duration = raw_end - raw_start
        # 최소 길이 보장
        if duration < min_duration:
            mid = (raw_start + raw_end) / 2
            raw_start = max(0.0, mid - min_duration / 2)
            raw_end = min(video_duration, raw_start + min_duration)
            raw_start = max(0.0, raw_end - min_duration)
            duration = raw_end - raw_start
        # 최대 길이 클램프
        if duration > max_duration:
            mid = scene.time_start + (scene.time_end - scene.time_start) / 2
            raw_start = max(0.0, mid - max_duration / 2)
            raw_end = min(video_duration, raw_start + max_duration)
            raw_start = max(0.0, raw_end - max_duration)
            duration = raw_end - raw_start

        clip_id = f"clip_{video.id}_{idx:03d}"
        clips.append(Clip(
            id=clip_id,
            video_id=video.id,
            source_scene_ids=[scene.id],
            start_sec=round(raw_start, 3),
            end_sec=round(raw_end, 3),
            duration_sec=round(duration, 3),
            local_clip_path="",
            selected_for_vision_analysis=True,
            selection_reason=f"scene={scene.id} score={_score(scene):.1f}",
            created_at=datetime.now(),
        ))

    # 총 길이 상한 초과 시 가장 짧은 클립부터 제거 (최소 1개 보존)
    while len(clips) > 1:
        total = sum(c.duration_sec for c in clips)
        if total <= max_total_duration:
            break
        shortest = min(clips, key=lambda c: c.duration_sec)
        clips.remove(shortest)

    # Fallback: 아무것도 없으면 점수 최고 1개 강제
    if not clips and candidates:
        best = max(candidates, key=_score)
        raw_start = max(0.0, best.time_start - padding_sec)
        raw_end = min(video_duration, best.time_end + padding_sec)
        duration = max(min_duration, min(raw_end - raw_start, max_duration))
        clips.append(Clip(
            id=f"clip_{video.id}_000",
            video_id=video.id,
            source_scene_ids=[best.id],
            start_sec=round(raw_start, 3),
            end_sec=round(raw_start + duration, 3),
            duration_sec=round(duration, 3),
            local_clip_path="",
            selected_for_vision_analysis=True,
            selection_reason="fallback_best_scene",
            created_at=datetime.now(),
        ))

    return clips


def extract_clips(
    video: Video,
    clips: list[Clip],
    clips_dir: str = CLIPS_DIR,
) -> list[Clip]:
    """ffmpeg로 클립 파일을 추출하고 local_clip_path를 채운 새 목록을 반환한다.

    ffmpeg가 없거나 실패하면 RuntimeError를 발생시킨다.
    원본 영상 파일은 수정하지 않는다.
    """
    result: list[Clip] = []
    for clip in clips:
        out_dir = Path(clips_dir) / clip.video_id
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = str(out_dir / f"{clip.id}.mp4")

        try:
            subprocess.run(
                [
                    "ffmpeg", "-y",
                    "-i", video.stored_path,
                    "-ss", str(clip.start_sec),
                    "-to", str(clip.end_sec),
                    "-c", "copy",
                    out_path,
                ],
                check=True,
                capture_output=True,
            )
        except FileNotFoundError as e:
            raise RuntimeError("ffmpeg를 찾을 수 없습니다. PATH를 확인해주세요.") from e
        except subprocess.CalledProcessError as e:
            stderr = e.stderr.decode("utf-8", errors="replace") if e.stderr else ""
            raise RuntimeError(f"ffmpeg 클립 추출 실패: {stderr[:300]}") from e

        result.append(clip.model_copy(update={"local_clip_path": out_path}))

    return result
