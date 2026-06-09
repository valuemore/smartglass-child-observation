"""외부 비전 API 어댑터 — 실호출 구현체.

원칙:
- dry_run=True(기본) 시 외부 네트워크 호출 없이 빈 결과 반환.
- API 키는 로그에 출력하지 않는다.
- _call_api()는 현재 NotImplementedError → 제공자별 서브클래스에서 구현.
- 각 analyze_segment 호출 후 _last_discarded 속성에 폐기 후보 수를 저장한다.
- payload 전송 전 payload_guard.assert_safe_outbound_payload 통과 필수.
"""

from __future__ import annotations

import logging

from core.schemas import SegmentAnalysisRequest, SegmentAnalysisResult
from services.vision.payload_builder import build_external_payload
from services.vision.payload_guard import assert_safe_outbound_payload
from services.vision.response_parser import parse_external_response

logger = logging.getLogger(__name__)


class ExternalVisionAdapter:
    """외부 비전 LLM API 호출 어댑터.

    제공자별 상세 구현은 이 클래스를 서브클래싱하거나 _call_api()를 재정의한다.
    """

    def __init__(
        self,
        model: str,
        api_key: str,
        dry_run: bool = True,
    ) -> None:
        if not model:
            raise ValueError("model은 비어 있을 수 없습니다.")
        if not api_key and not dry_run:
            raise ValueError("dry_run=False 시 api_key가 필요합니다.")

        self._model = model
        self._api_key = api_key      # 절대 로그에 출력하지 않음
        self._dry_run = dry_run
        self._last_discarded: int = 0

    # ------------------------------------------------------------------
    # VisionAdapter Protocol 구현
    # ------------------------------------------------------------------

    def analyze_segment(self, request: SegmentAnalysisRequest) -> SegmentAnalysisResult:
        """구간 분석. dry_run=True 시 네트워크 호출 없이 빈 결과를 반환한다."""
        self._last_discarded = 0

        if self._dry_run:
            logger.info(
                "dry_run=True: 외부 API 호출 없이 빈 결과 반환. "
                "segment_id=%s, model=%s",
                request.segment_id,
                self._model,
            )
            return SegmentAnalysisResult(
                segment_id=request.segment_id,
                time_start=request.time_start,
                time_end=request.time_end,
                observations=[],
            )

        # 1. payload 빌드
        payload = build_external_payload(request)

        # 2. 안전성 게이트 — 위반 시 ValueError로 호출 차단
        assert_safe_outbound_payload(payload)

        # 3. 실제 API 호출
        raw_response = self._call_api(payload)

        # 4. 응답 파싱 및 검증
        parse_result = parse_external_response(raw_response, request)
        self._last_discarded = parse_result.discarded_count

        if parse_result.warnings:
            for w in parse_result.warnings:
                logger.warning("응답 파서 경고 [%s]: %s", request.segment_id, w)

        return parse_result.result

    # ------------------------------------------------------------------
    # 내부 API 호출 — 서브클래스에서 구현
    # ------------------------------------------------------------------

    def _call_api(self, payload: dict) -> str | dict:
        """실제 외부 API를 호출하고 원시 응답(JSON 문자열 또는 dict)을 반환한다.

        이 클래스는 NotImplementedError를 발생시킨다.
        제공자별 구현: GeminiVisionAdapter, OpenAIVisionAdapter 등에서 재정의.
        """
        raise NotImplementedError(
            f"_call_api()가 구현되지 않았습니다. "
            f"model={self._model!r}에 대한 제공자 어댑터를 사용하세요."
        )

    # ------------------------------------------------------------------
    # 진단용 프로퍼티 (API 키 제외)
    # ------------------------------------------------------------------

    @property
    def model(self) -> str:
        return self._model

    @property
    def dry_run(self) -> bool:
        return self._dry_run

    def __repr__(self) -> str:
        return (
            f"ExternalVisionAdapter(model={self._model!r}, dry_run={self._dry_run})"
        )
