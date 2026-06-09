"""Mock 비전 어댑터 — 실제 외부 API를 호출하지 않는 시연·테스트용 구현.

원칙:
- 외부 API 호출 없음.
- 유아 실명/확정 신원 없음. temp_child_id(child_A 등) 임시 ID만 사용.
- 관찰수준 점수 필드 없음.
- needs_teacher_review=True 강제.
- observed_behavior 는 "프레임에서 관찰 가능한 행동 후보" 문장으로 작성.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from core.schemas import (
    InteractionEvidence,
    ObservationCandidate,
    SegmentAnalysisRequest,
    SegmentAnalysisResult,
)

# 관찰 행동 템플릿 5종 — 장면 ID 해시로 순환 선택
_TEMPLATES = [
    {
        "temp_child_id": "child_A",
        "observed_behavior": "child_A가 놀이 자료를 손에 들고 조작하는 장면이 관찰됨",
        "activity_context": "자유놀이 시간 탐색 활동 영역",
        "peer_relation": "개별 놀이 진행 중",
        "interaction": InteractionEvidence(
            with_peers=None,
            with_teacher=None,
            with_materials="놀이 자료를 양손으로 탐색",
        ),
        "confidence": 0.72,
    },
    {
        "temp_child_id": "child_A",
        "observed_behavior": (
            "child_A와 child_B가 같은 놀이 공간에서 자료를 함께 바라보는 장면이 관찰됨"
        ),
        "activity_context": "자유놀이 시간 쌓기·구성 영역",
        "peer_relation": "또래와 공동 주의(joint attention) 공유",
        "interaction": InteractionEvidence(
            with_peers="child_B와 같은 자료에 시선 집중",
            with_teacher=None,
            with_materials="구성 놀이 자료 공동 탐색",
        ),
        "confidence": 0.65,
    },
    {
        "temp_child_id": "child_A",
        "observed_behavior": "유아가 신체를 움직이며 활동 공간을 이동하는 장면이 관찰됨",
        "activity_context": "실내 대근육 활동 시간",
        "peer_relation": "이동 중 또래와 병행 활동",
        "interaction": InteractionEvidence(
            with_peers="이동 경로에서 또래와 근접 위치",
            with_teacher=None,
            with_materials=None,
        ),
        "confidence": 0.58,
    },
    {
        "temp_child_id": "child_A",
        "observed_behavior": "child_A가 교사를 향해 얼굴을 돌리며 말하는 장면이 관찰됨",
        "activity_context": "이야기 나누기 또는 소집단 활동",
        "peer_relation": "교사와 일대일 언어적 상호작용",
        "interaction": InteractionEvidence(
            with_peers=None,
            with_teacher="교사 방향으로 시선과 발화 향함",
            with_materials=None,
        ),
        "confidence": 0.68,
    },
    {
        "temp_child_id": "child_A",
        "observed_behavior": "child_A가 자리에 앉아 눈앞의 자료를 집중하여 응시하는 장면이 관찰됨",
        "activity_context": "소집단 또는 개별 탐구 활동",
        "peer_relation": "집중 탐구 중 또래 상호작용 최소",
        "interaction": InteractionEvidence(
            with_peers=None,
            with_teacher=None,
            with_materials="자료에 시선 고정, 손으로 세부 탐색",
        ),
        "confidence": 0.75,
    },
]


class MockVisionAdapter:
    """실제 외부 API 없이 관찰 후보를 생성하는 Mock 어댑터.

    segment_id 해시 값으로 템플릿을 순환 선택해 장면마다 다른 후보를 생성한다.
    """

    def analyze_segment(self, request: SegmentAnalysisRequest) -> SegmentAnalysisResult:
        """구간 메타·프레임 ID를 활용해 관찰 후보 1개를 반환한다."""
        idx = abs(hash(request.segment_id)) % len(_TEMPLATES)
        tmpl = _TEMPLATES[idx]

        frame_ids = [f.frame_id for f in request.frames]
        if len(frame_ids) == 1:
            visual_evidence = f"{frame_ids[0]}에서 관찰 행동 확인"
        else:
            visual_evidence = f"{frame_ids[0]}~{frame_ids[-1]} 구간 프레임에서 관찰 행동 확인"

        candidate = ObservationCandidate(
            id=f"cand_{uuid.uuid4().hex[:10]}",
            video_id=request.video_id,
            scene_id=request.segment_id,
            time_start=request.time_start,
            time_end=request.time_end,
            temp_child_id=tmpl["temp_child_id"],
            observed_behavior=tmpl["observed_behavior"],
            interaction=tmpl["interaction"],
            activity_context=tmpl["activity_context"],
            peer_relation=tmpl["peer_relation"],
            visual_evidence=visual_evidence,
            audio_support=None,
            nuri_area_candidates=[],
            kicce_item_candidates=[],
            confidence=tmpl["confidence"],
            needs_teacher_review=True,
            created_at=datetime.now(),
        )

        return SegmentAnalysisResult(
            segment_id=request.segment_id,
            time_start=request.time_start,
            time_end=request.time_end,
            observations=[candidate],
        )
