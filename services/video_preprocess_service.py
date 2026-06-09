"""
영상 전처리 서비스 — 장면 분할 + 프레임 추출.

원칙:
- 원본 영상은 로컬에서만 읽는다. 외부로 전송하지 않는다.
- PySceneDetect 없거나 실패하면 고정 간격 fallback.
- OpenCV 없으면 프레임 추출 자체가 불가(ImportError 전파).
- 전처리 시작/완료를 video status 와 audit_log 에 기록한다.
- 각 scene에서 모든 프레임이 blur_threshold 미만이면
  blur_score 최고 프레임을 fallback으로 kept=True 처리한다.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from pathlib import Path

from core.config import (
    DEFAULT_ACTOR,
    FALLBACK_SCENE_INTERVAL_SEC,
    FRAME_BLUR_THRESHOLD,
    FRAMES_DIR,
)
from core.schemas import AuditLog, Frame, Scene, Video
from storage.sqlite_repository import SqliteRepository


# ---------------------------------------------------------------------------
# 공개 진입점
# ---------------------------------------------------------------------------

def preprocess_video(
    video_id: str,
    repo: SqliteRepository,
    frames_dir: str = FRAMES_DIR,
    blur_threshold: float = FRAME_BLUR_THRESHOLD,
    actor: str = DEFAULT_ACTOR,
) -> tuple[list[Scene], list[Frame]]:
    """
    장면 분할 → 프레임 추출 → DB 저장 → audit_log 기록.

    반환값: (저장된 Scene 목록, 저장된 Frame 목록)
    """
    video = repo.get_video(video_id)
    if video is None:
        raise ValueError(f"video_id={video_id} 를 찾을 수 없습니다.")

    repo.save_video(video.model_copy(update={"status": "analyzing"}))

    scenes = split_video_into_scenes(video)
    repo.add_scenes(scenes)

    frames = extract_representative_frames(video, scenes, frames_dir, blur_threshold)
    repo.add_frames(frames)

    repo.save_video(video.model_copy(update={"status": "analyzed"}))

    repo.write_audit(AuditLog(
        id=f"audit_{video_id}_preprocess_{uuid.uuid4().hex[:6]}",
        video_id=video_id,
        actor=actor,
        action="analyze",
        detail=(
            f"scene_split_and_frame_extraction "
            f"scenes={len(scenes)} frames={len(frames)} "
            f"kept={sum(1 for f in frames if f.kept)}"
        ),
        created_at=datetime.now(),
    ))

    return scenes, frames


# ---------------------------------------------------------------------------
# 장면 분할
# ---------------------------------------------------------------------------

def split_video_into_scenes(
    video: Video,
    fallback_interval_sec: float = FALLBACK_SCENE_INTERVAL_SEC,
) -> list[Scene]:
    """
    PySceneDetect ContentDetector로 장면 분할을 시도한다.
    실패하거나 결과가 없으면 고정 간격 fallback을 사용한다.
    영상이 fallback_interval_sec 보다 짧으면 전체를 1개 Scene으로 반환한다.
    """
    duration = _get_duration(video)
    if duration <= 0:
        return _fallback_scenes(video, fallback_interval_sec)

    try:
        scenes = _scenedetect_split(video, duration)
        if scenes:
            return scenes
    except Exception:
        pass

    return _fallback_scenes(video, fallback_interval_sec)


def _scenedetect_split(video: Video, duration: float) -> list[Scene]:
    """PySceneDetect ContentDetector 로 분할. 실패 시 빈 리스트 반환."""
    from scenedetect import ContentDetector, SceneManager, open_video  # type: ignore

    video_stream = open_video(video.stored_path)
    manager = SceneManager()
    manager.add_detector(ContentDetector())
    manager.detect_scenes(video=video_stream)
    raw = manager.get_scene_list()

    if not raw:
        return []

    scenes: list[Scene] = []
    for idx, (start_tc, end_tc) in enumerate(raw):
        t_start = start_tc.get_seconds()
        t_end = end_tc.get_seconds()
        if t_end <= t_start:
            continue
        scenes.append(Scene(
            id=f"scene_{video.id}_{idx:04d}",
            video_id=video.id,
            time_start=round(t_start, 3),
            time_end=round(min(t_end, duration), 3),
            detector="ContentDetector",
        ))
    return scenes


def _fallback_scenes(video: Video, interval_sec: float) -> list[Scene]:
    """고정 간격으로 Scene 을 생성한다. 영상이 짧으면 1개."""
    duration = _get_duration(video)
    if duration <= 0 or duration <= interval_sec:
        end = duration if duration > 0 else interval_sec
        return [Scene(
            id=f"scene_{video.id}_0000",
            video_id=video.id,
            time_start=0.0,
            time_end=round(end, 3),
            detector="fallback_fixed",
        )]

    scenes: list[Scene] = []
    t = 0.0
    idx = 0
    while t < duration:
        t_end = min(t + interval_sec, duration)
        if t_end - t < 0.5:
            if scenes:
                last = scenes[-1]
                scenes[-1] = Scene(
                    id=last.id,
                    video_id=last.video_id,
                    time_start=last.time_start,
                    time_end=round(t_end, 3),
                    detector=last.detector,
                )
            break
        scenes.append(Scene(
            id=f"scene_{video.id}_{idx:04d}",
            video_id=video.id,
            time_start=round(t, 3),
            time_end=round(t_end, 3),
            detector="fallback_fixed",
        ))
        t = t_end
        idx += 1
    return scenes


# ---------------------------------------------------------------------------
# 프레임 추출
# ---------------------------------------------------------------------------

def extract_representative_frames(
    video: Video,
    scenes: list[Scene],
    frames_dir: str = FRAMES_DIR,
    blur_threshold: float = FRAME_BLUR_THRESHOLD,
) -> list[Frame]:
    """
    각 scene 에서 대표 프레임을 추출하고 jpg 로 저장한다.
    - scene 길이 < 10s: 중간 1장
    - scene 길이 >= 10s: 시작/중간/끝 3장
    blur_score(Laplacian 분산) 계산 후 threshold 이상이면 kept=True.
    scene 내 모든 프레임이 threshold 미만이면 blur_score 최고 프레임을
    fallback으로 kept=True 처리한다. (blur_score 값 자체는 유지)
    """
    import cv2  # type: ignore

    cap = cv2.VideoCapture(video.stored_path)
    if not cap.isOpened():
        raise RuntimeError(f"영상을 열 수 없습니다: {video.stored_path}")

    frames: list[Frame] = []

    for scene in scenes:
        target_times = _sample_times(scene)
        scene_dir = Path(frames_dir) / video.id / scene.id
        scene_dir.mkdir(parents=True, exist_ok=True)

        scene_frames: list[Frame] = []
        for frm_idx, t in enumerate(target_times):
            frame_id = f"frm_{scene.id}_{frm_idx:03d}"
            image_path = str(scene_dir / f"frm_{frm_idx:03d}.jpg")

            cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
            ret, img = cap.read()
            if not ret or img is None:
                continue

            cv2.imwrite(image_path, img)
            blur_score = _laplacian_blur_score(img)
            kept = blur_score >= blur_threshold

            scene_frames.append(Frame(
                id=frame_id,
                scene_id=scene.id,
                t=round(t, 3),
                image_path=image_path,
                blur_score=round(blur_score, 2),
                kept=kept,
            ))

        # scene 내 모든 프레임이 threshold 미만이면 최고 blur_score 프레임을 fallback 선택
        if scene_frames and not any(f.kept for f in scene_frames):
            best = max(scene_frames, key=lambda f: f.blur_score)
            scene_frames = [
                f.model_copy(update={"kept": True}) if f.id == best.id else f
                for f in scene_frames
            ]

        frames.extend(scene_frames)

    cap.release()
    return frames


def _sample_times(scene: Scene) -> list[float]:
    """scene 길이에 따라 추출할 시각 목록을 반환한다."""
    duration = scene.time_end - scene.time_start
    mid = scene.time_start + duration / 2.0
    if duration >= 10.0:
        return [
            scene.time_start + 0.1,
            mid,
            scene.time_end - 0.1,
        ]
    return [mid]


def _laplacian_blur_score(img) -> float:
    """Laplacian 분산으로 선명도 점수를 계산한다. 클수록 선명."""
    import cv2  # type: ignore
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


# ---------------------------------------------------------------------------
# 헬퍼
# ---------------------------------------------------------------------------

def _get_duration(video: Video) -> float:
    """Video 객체에서 duration_sec 를 반환한다. 0이면 OpenCV 재시도."""
    if video.duration_sec > 0:
        return video.duration_sec
    try:
        import cv2  # type: ignore
        cap = cv2.VideoCapture(video.stored_path)
        fps = cap.get(cv2.CAP_PROP_FPS) or 0.0
        fc = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0.0
        cap.release()
        return fc / fps if fps > 0 else 0.0
    except Exception:
        return 0.0
