"""업로드 즉시 자동 분석 오케스트레이터 (V2-3).

원칙:
- 업로드 후 전처리 → 근거 클립 → (안전 시) 비전 후보 → 누리/KICCE 매핑 → 후보 누적을
  한 번에 자동 실행한다. 교사는 매일 개별 후보를 검토하지 않는다.
- video.analysis_status 상태머신(queued/running/done/failed)·progress·retry_count로 진행을 추적한다.
- **비용·동의 안전장치**: 외부 제공자 실호출(provider!=mock 이고 dry_run=False)은
  allow_external_real=True 가 명시될 때만 수행한다. 자동 분석은 기본적으로 실호출하지 않는다.
- 멱등성: 이미 전처리·클립이 있으면 재사용해 재시도(retry)가 안전하다.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

from core import config as cfg
from core.config import CLIPS_DIR, DEFAULT_ACTOR, FRAMES_DIR
from services.clip_service import extract_clips, select_evidence_clips
from services.mapping.mapping_service import map_candidates_for_video
from services.observation_service import generate_observation_candidates_with_provider
from services.video_preprocess_service import preprocess_video
from storage.sqlite_repository import SqliteRepository

ProgressCb = Callable[[int, str], None]


def run_auto_analysis(
    video_id: str,
    repo: SqliteRepository,
    *,
    frames_dir: str = FRAMES_DIR,
    clips_dir: str = CLIPS_DIR,
    actor: str = DEFAULT_ACTOR,
    max_scenes: Optional[int] = None,
    progress_cb: Optional[ProgressCb] = None,
    allow_external_real: bool = False,
) -> dict:
    """단일 영상에 대해 전체 파이프라인을 자동 실행하고 상태머신을 갱신한다.

    반환 dict 키:
        status: "done" | "failed"
        scenes, frames, clips, candidates, mappings: 각 단계 산출 개수
        vision_skipped: 외부 실호출 안전장치로 비전 단계를 건너뛰었는지
        error: 실패 시 사유 문자열(성공 시 None)
    """
    video = repo.get_video(video_id)
    if video is None:
        raise ValueError(f"video_id={video_id} 를 찾을 수 없습니다.")

    def _emit(status: str, pct: int, label: str, err: Optional[str] = None) -> None:
        repo.update_analysis_status(video_id, status, pct, err)
        if progress_cb is not None:
            progress_cb(pct, label)

    result: dict = {
        "status": "failed", "scenes": 0, "frames": 0, "clips": 0,
        "candidates": 0, "mappings": 0, "face_matches": 0,
        "vision_skipped": False, "error": None,
    }

    try:
        _emit("running", 5, "분석 준비 중...")

        # 1) 전처리 (장면 분할 + 프레임 추출). 이미 있으면 재사용(재시도 안전).
        scenes = repo.list_scenes(video_id)
        if scenes:
            frames = [f for s in scenes for f in repo.list_frames(s.id)]
        else:
            _emit("running", 15, "장면 분할·프레임 추출 중...")
            scenes, frames = preprocess_video(
                video_id=video_id, repo=repo, frames_dir=frames_dir, actor=actor,
            )
        result["scenes"], result["frames"] = len(scenes), len(frames)
        _emit("running", 40, f"전처리 완료 (장면 {len(scenes)}·프레임 {len(frames)})")

        # 2) 근거 클립 추출. 이미 있으면 재사용.
        clips = repo.list_clips(video_id)
        if not clips and scenes:
            try:
                raw = select_evidence_clips(video=video, scenes=scenes, frames=frames)
                clips = extract_clips(video=video, clips=raw, clips_dir=clips_dir)
                repo.add_clips(clips)
            except Exception:
                # 클립 추출 실패는 치명적이지 않다 — 프레임 기반 분석으로 계속.
                clips = repo.list_clips(video_id)
        result["clips"] = len(clips)
        _emit("running", 60, f"근거 클립 {len(clips)}개")

        # 3) 비전 관찰 후보. 외부 실호출은 안전장치로 차단(기본).
        prov = (cfg.VISION_PROVIDER or "").lower()
        will_call_external_real = (prov != "mock") and (not cfg.VISION_DRY_RUN)
        if will_call_external_real and not allow_external_real:
            result["vision_skipped"] = True
            _emit("running", 90, "비전 후보 생성 보류 (외부 실호출 동의 필요)")
        else:
            # 외부 provider(gemini/external)는 근거 클립이 필수다. mock은 클립 불필요.
            if prov != "mock":
                _valid_clips = [
                    c for c in clips if c.local_clip_path and Path(c.local_clip_path).exists()
                ]
                if not _valid_clips:
                    if clips:
                        raise ValueError("클립 파일이 디스크에 존재하지 않습니다. 재시도 버튼을 눌러 재분석하세요.")
                    raise ValueError("클립 추출에 실패했습니다. ffmpeg 설치 여부와 영상 파일을 확인하세요.")
            _emit("running", 70, "AI 관찰 후보 생성 중...")
            candidates, _info = generate_observation_candidates_with_provider(
                video_id=video_id, repo=repo, actor=actor, max_scenes=max_scenes,
            )
            result["candidates"] = len(candidates)
            _emit("running", 90, f"관찰 후보 {len(candidates)}개")

            # 4) 누리/KICCE 매핑
            mappings = map_candidates_for_video(video_id, repo)
            result["mappings"] = len(mappings)

        # 4-1) 얼굴 매칭 후보 (동의 ON 시에만, 로컬 전용·후보만). 비치명적.
        try:
            from services.face.face_match_service import propose_matches
            fmcs = propose_matches(repo, video_id, actor=actor)
            result["face_matches"] = len(fmcs)
        except Exception:
            result["face_matches"] = 0

        # 5) 후보 누적 완료 → 상태 갱신
        latest = repo.get_video(video_id)
        if latest is not None:
            repo.save_video(latest.model_copy(update={"status": "accumulated"}))
        _emit("done", 100, "자동 분석 완료")
        result["status"] = "done"
        return result

    except Exception as e:  # noqa: BLE001 — 상태머신에 실패를 기록하고 결과로 반환
        msg = str(e)
        # 실패 시점의 진행률은 보존, retry_count 는 update_analysis_status 가 증가시킨다.
        current = repo.get_video(video_id)
        pct = current.progress if current is not None else 0
        repo.update_analysis_status(video_id, "failed", pct, msg)
        if progress_cb is not None:
            progress_cb(pct, f"실패: {msg}")
        result["error"] = msg
        return result
