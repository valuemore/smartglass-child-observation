"""Gemini (Google) 비전 어댑터 — 동영상 클립 단위 분석.

원칙:
- 원본 영상 전체는 전송하지 않는다. 사전 추출된 근거 클립(clip_path)만 전송한다.
- Gemini Files API로 클립 업로드 → 분석 → 즉시 삭제.
- 업로드·삭제 이벤트를 _audit_events에 기록 (observation_service가 flush).
- 클립이 없으면 ValueError 발생 — 클립 추출을 먼저 실행해야 함.
- API 키는 절대 로그에 출력하지 않는다.
"""

from __future__ import annotations

import logging
import re
import time
from pathlib import Path

from core.schemas import SegmentAnalysisRequest, SegmentAnalysisResult
from services.vision.response_parser import parse_external_response

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "당신은 어린이집 교사 시점(1인칭) 스마트안경 영상에서 유아 행동을 관찰 기록하는 AI 보조 시스템입니다.\n\n"
    "역할:\n"
    "- 교사가 착용한 스마트안경 영상 클립을 분석해 유아의 행동 관찰 후보를 제시한다.\n"
    "- 관찰 후보는 교사가 검토·수정·확정하기 위한 초안이다. AI가 확정하지 않는다.\n"
    "- 관찰수준 점수, 발달 점수, 평정 점수를 절대 포함하지 않는다.\n"
    "- 유아를 child_A, child_B 등 임시 ID로만 식별한다. 실명·확정 신원을 포함하지 않는다.\n"
    "- 영상 클립의 시간 흐름에 따른 동적 행동(움직임·제스처·상호작용 순서·변화)을 구체적으로 묘사한다.\n\n"
    "출력 형식: 반드시 순수 JSON 형식으로만 출력한다. 마크다운 코드 블록, 설명 텍스트 없이 JSON만 출력한다."
)

_NURI_AREAS = ["신체운동·건강", "의사소통", "사회관계", "예술경험", "자연탐구"]

_JSON_SCHEMA_EXAMPLE = """{
  "observations": [
    {
      "temp_child_id": "child_A",
      "observed_behavior": "행동의 흐름과 변화 과정을 구체적으로 서술 (동적 묘사)",
      "visual_evidence": "영상 몇 초에서 어떤 움직임·동작이 관찰되었는지 구체적으로 서술",
      "confidence": 0.75,
      "needs_teacher_review": true,
      "activity_context": "활동 맥락 (선택, 없으면 생략)",
      "peer_relation": "또래 관계 (선택, 없으면 생략)",
      "interaction": {
        "with_peers": "또래와의 상호작용 순서와 내용 (없으면 null)",
        "with_teacher": "교사와의 상호작용 (없으면 null)",
        "with_materials": "교재·도구와의 상호작용 (없으면 null)"
      },
      "nuri_area_candidates": [
        {"area": "사회관계", "rationale": "누리과정 영역 해당 근거", "confidence": 0.7}
      ],
      "kicce_item_candidates": []
    }
  ]
}"""


class GeminiVisionAdapter:
    """Gemini Files API를 통해 동영상 클립 구간을 분석한다.

    SegmentAnalysisRequest.clip_path에 사전 추출된 클립 경로가 있어야 한다.
    """

    def __init__(
        self,
        api_key: str,
        model: str = "gemini-1.5-flash",
        request_timeout_sec: float = 120.0,
        max_retries: int = 2,
    ) -> None:
        self._api_key = api_key
        self._model_name = model
        self._request_timeout_sec = request_timeout_sec
        self._max_retries = max_retries
        self._audit_events: list[dict] = []

    def analyze_segment(self, request: SegmentAnalysisRequest) -> SegmentAnalysisResult:
        if not request.clip_path or not Path(request.clip_path).exists():
            raise ValueError(
                f"clip_path가 없거나 파일이 존재하지 않습니다: {request.clip_path!r}\n"
                "클립 추출(clip_service.extract_clips)을 먼저 실행하세요."
            )

        last_exc: Exception | None = None
        for attempt in range(self._max_retries + 1):
            try:
                return self._analyze_segment_once(request)
            except (OSError, ConnectionError, TimeoutError) as exc:
                last_exc = exc
                msg = str(exc)
                if "110" in msg or "timed out" in msg.lower() or "timeout" in msg.lower():
                    logger.warning(
                        "Gemini API 연결 시간 초과 (시도 %d/%d): %s",
                        attempt + 1, self._max_retries + 1, exc,
                    )
                    if attempt < self._max_retries:
                        time.sleep(2 ** attempt)
                        continue
                    raise ConnectionError(
                        f"Gemini API 연결 시간 초과 — {self._max_retries + 1}회 시도 후 실패.\n"
                        "서버에서 generativelanguage.googleapis.com:443 아웃바운드가 허용되어야 합니다.\n"
                        f"원인: {exc}"
                    ) from exc
                raise
        assert last_exc is not None
        raise last_exc

    def _analyze_segment_once(self, request: SegmentAnalysisRequest) -> SegmentAnalysisResult:
        try:
            import google.generativeai as genai  # type: ignore
        except ImportError as exc:
            raise ImportError(
                "google-generativeai 패키지가 설치되지 않았습니다. "
                "`pip install google-generativeai` 를 실행하세요."
            ) from exc

        genai.configure(api_key=self._api_key)
        model = genai.GenerativeModel(self._model_name)
        _req_opts = {"timeout": self._request_timeout_sec}

        # 1. 클립 업로드
        video_file = genai.upload_file(
            path=request.clip_path, mime_type="video/mp4",
            request_options=_req_opts,
        )
        self._audit_events.append({
            "action": "gemini_clip_upload",
            "detail": f"clip_path={request.clip_path} file_name={video_file.name}",
            "video_id": request.video_id,
        })
        logger.info(
            "Gemini Files API 업로드 완료: %s → %s",
            request.clip_path, video_file.name,
        )

        # 2. ACTIVE 상태 대기 (최대 120초)
        self._wait_active(genai, video_file, timeout_sec=120)

        try:
            # 3. 관찰 후보 생성
            prompt = self._build_prompt(request)
            response = model.generate_content(
                [video_file, prompt],
                request_options=_req_opts,
            )
            raw_text: str = response.text

            logger.debug("Gemini 원시 응답 (앞 200자): %.200s", raw_text)

            # 4. 즉시 삭제
            video_file.delete()
            self._audit_events.append({
                "action": "gemini_clip_delete",
                "detail": f"file_name={video_file.name}",
                "video_id": request.video_id,
            })
            logger.info("Gemini Files API 삭제 완료: %s", video_file.name)

        except Exception:
            # 실패해도 업로드 파일 삭제 시도
            try:
                video_file.delete()
                self._audit_events.append({
                    "action": "gemini_clip_delete",
                    "detail": f"file_name={video_file.name} reason=error_cleanup",
                    "video_id": request.video_id,
                })
            except Exception as del_err:
                logger.warning("Gemini 파일 삭제 실패: %s", del_err)
            raise

        # 5. 응답 파싱 (기존 response_parser 재사용)
        cleaned = _extract_json(raw_text)
        return parse_external_response(cleaned, request)

    def _build_prompt(self, request: SegmentAnalysisRequest) -> str:
        return (
            f"{_SYSTEM_PROMPT}\n\n"
            f"이 영상 클립은 어린이집 교사 시점 스마트안경 영상의 "
            f"{request.time_start:.1f}초 ~ {request.time_end:.1f}초 구간입니다.\n"
            "음성이 없거나 불분명할 수 있습니다. 시각 정보(움직임·동작·상호작용)를 중심으로 관찰하세요.\n\n"
            "## 관찰 규칙\n"
            "- 행동의 흐름과 변화 과정(시작→중간→끝)을 동적으로 묘사하세요.\n"
            "- 영상 내 특정 시간대(예: 0:03~0:07)에서 관찰된 내용을 visual_evidence에 포함하세요.\n"
            "- 관찰수준 점수를 포함하지 마세요.\n"
            "- 유아를 child_A, child_B 임시 ID로만 식별하세요.\n\n"
            f"## 누리과정 5개 영역 (1차 분류 기준)\n{', '.join(_NURI_AREAS)}\n\n"
            f"## 출력 JSON 형식 (이 형식만 허용)\n{_JSON_SCHEMA_EXAMPLE}\n\n"
            "관찰 가능한 유아 행동이 없으면 `{\"observations\": []}` 를 반환하세요."
        )

    @staticmethod
    def _wait_active(genai_module, video_file, timeout_sec: float = 120) -> None:
        """파일이 ACTIVE 상태가 될 때까지 poll한다."""
        elapsed = 0.0
        interval = 3.0
        while video_file.state.name == "PROCESSING":
            if elapsed >= timeout_sec:
                raise TimeoutError(
                    f"Gemini 파일 처리 대기 시간 초과 ({timeout_sec}초): {video_file.name}"
                )
            time.sleep(interval)
            elapsed += interval
            video_file = genai_module.get_file(video_file.name)
        if video_file.state.name == "FAILED":
            raise RuntimeError(f"Gemini 파일 처리 실패: {video_file.name}")


def _extract_json(text: str) -> str:
    """응답 텍스트에서 JSON 부분만 추출한다."""
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*\n?", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\n?```$", "", text.strip())
    text = text.strip()
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return text[start:end + 1]
    return text
