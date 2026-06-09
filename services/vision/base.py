"""비전 어댑터 Protocol — 제공자 교체 가능 계약 정의."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from core.schemas import SegmentAnalysisRequest, SegmentAnalysisResult


@runtime_checkable
class VisionAdapter(Protocol):
    """비전 LLM 제공자 교체 가능 인터페이스.

    구현체 예: MockVisionAdapter, GeminiVisionAdapter, OpenAIVisionAdapter.
    원본 영상은 이 인터페이스를 통해 외부로 전송하지 않는다.
    선별 프레임 경로(image_ref) + 필요한 텍스트만 전달한다.
    """

    def analyze_segment(self, request: SegmentAnalysisRequest) -> SegmentAnalysisResult:
        """구간 단위 프레임을 분석해 관찰 후보 목록을 반환한다."""
        ...
