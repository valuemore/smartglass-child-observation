"""백그라운드 분석 큐 (V2-5).

원칙:
- 업로드 흐름을 블로킹하지 않기 위해 자동 분석을 **프로세스 전역 단일 워커 스레드**에서
  순차 처리한다. 업로드/재시도는 큐에 넣고 즉시 반환한다.
- 워커는 Streamlit 컨텍스트(st.*)를 절대 호출하지 않는다. 진행률은 `run_auto_analysis`가
  `repo.update_analysis_status()`로 DB에 기록하고, UI는 DB 폴링(@st.fragment)으로 표시한다.
- SQLite 단일 라이터 경합을 피하려 워커는 1개로 직렬 처리한다.
- `SqliteRepository`는 호출마다 새 연결을 열므로 워커 스레드에서 자체 인스턴스를 만들어 안전하다.
"""

from __future__ import annotations

import logging
import queue
import threading

from core.config import CLIPS_DIR, DB_PATH, FRAMES_DIR, VISION_PROVIDER
from services.auto_analysis_service import run_auto_analysis
from storage.sqlite_repository import SqliteRepository

logger = logging.getLogger(__name__)


class AnalysisQueue:
    """업로드 즉시 자동 분석을 백그라운드에서 순차 실행하는 큐."""

    def __init__(self) -> None:
        self._q: "queue.Queue[tuple[str, str]]" = queue.Queue()
        self._inflight: set[str] = set()
        self._lock = threading.Lock()
        self._reset_orphans()  # 프로세스 시작 시 중단된 running 정리
        self._thread = threading.Thread(
            target=self._worker, name="analysis-worker", daemon=True
        )
        self._thread.start()

    def _reset_orphans(self) -> None:
        """이전 프로세스에서 멈춘 '분석 중' 영상을 실패로 정리해 재시도 가능하게 한다."""
        try:
            repo = SqliteRepository(DB_PATH)
            for v in repo.list_videos(owner=None):
                if v.analysis_status == "running":
                    repo.update_analysis_status(
                        v.id, "failed", v.progress or 0,
                        "이전 분석이 중단되었습니다. 재시도를 눌러주세요.",
                    )
        except Exception:
            logger.exception("고아 분석 상태 정리 실패")

    def submit(self, video_id: str, actor: str) -> bool:
        """분석 작업을 큐에 넣는다. 이미 대기/처리 중이면 False(중복 무시)."""
        with self._lock:
            if video_id in self._inflight:
                return False
            self._inflight.add(video_id)
        self._q.put((video_id, actor))
        return True

    def pending(self) -> int:
        """대기/처리 중인 작업 수."""
        with self._lock:
            return len(self._inflight)

    def _worker(self) -> None:
        while True:
            video_id, actor = self._q.get()
            try:
                repo = SqliteRepository(DB_PATH)  # 스레드 전용(호출마다 새 연결)
                allow_real = (VISION_PROVIDER or "").lower() != "mock"
                run_auto_analysis(
                    video_id, repo,
                    frames_dir=FRAMES_DIR, clips_dir=CLIPS_DIR,
                    actor=actor, allow_external_real=allow_real,
                )  # progress_cb 없음 → DB 갱신만
            except Exception:
                logger.exception("백그라운드 분석 실패: video_id=%s", video_id)
            finally:
                with self._lock:
                    self._inflight.discard(video_id)
                self._q.task_done()
