"""
공통 Pydantic 스키마 정의.

원칙:
- 관찰수준 점수 필드 없음 (score, level, rating 등 금지)
- temp_child_id 는 임시 ID (child_A 등). 실명/확정 신원 필드 없음.
- ObservationCandidate 는 AI 후보이며 확정 기록이 아님.
- confidence 는 0 이상 1 이하.
- time_end 는 time_start 보다 커야 함.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator


# ---------------------------------------------------------------------------
# 공통 열거형 상수
# ---------------------------------------------------------------------------

VideoStatus = Literal["uploaded", "analyzing", "analyzed", "reviewed"]
SpeakerHint = Literal["teacher", "child", "unknown"]
DecisionType = Literal["accepted", "edited", "rejected"]
AuditAction = Literal["upload", "access", "analyze", "export", "delete"]
FrameLayout = Literal["sequence", "grid_2x2", "grid_3x1"]


# ---------------------------------------------------------------------------
# 1. Video — 업로드된 교사 시점 영상
# ---------------------------------------------------------------------------

class Video(BaseModel):
    id: str
    filename: str
    stored_path: str
    duration_sec: float = Field(ge=0.0)
    fps: float = Field(gt=0.0)
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    status: VideoStatus = "uploaded"
    created_at: datetime
    retention_until: Optional[datetime] = None


# ---------------------------------------------------------------------------
# 2. Scene — 장면 분할 구간
# ---------------------------------------------------------------------------

class Scene(BaseModel):
    id: str
    video_id: str
    time_start: float = Field(ge=0.0)
    time_end: float = Field(ge=0.0)
    detector: str = "ContentDetector"

    @model_validator(mode="after")
    def _end_after_start(self) -> "Scene":
        if self.time_end <= self.time_start:
            raise ValueError(f"time_end({self.time_end}) 은 time_start({self.time_start}) 보다 커야 합니다.")
        return self


# ---------------------------------------------------------------------------
# 3. Frame — 추출 프레임
# ---------------------------------------------------------------------------

class Frame(BaseModel):
    id: str
    scene_id: str
    t: float = Field(ge=0.0, description="프레임 시각(초)")
    image_path: str
    blur_score: float = Field(ge=0.0, description="Laplacian 분산 기반 흐림 점수")
    kept: bool = True


# ---------------------------------------------------------------------------
# 4. AudioSegment — 보조 STT 결과 (중심 데이터가 아닌 보조 증거)
# ---------------------------------------------------------------------------

class AudioSegment(BaseModel):
    id: str
    video_id: str
    time_start: float = Field(ge=0.0)
    time_end: float = Field(ge=0.0)
    transcript: str
    speaker_hint: SpeakerHint = "unknown"
    is_clear: bool = False

    @model_validator(mode="after")
    def _end_after_start(self) -> "AudioSegment":
        if self.time_end <= self.time_start:
            raise ValueError(f"time_end({self.time_end}) 은 time_start({self.time_start}) 보다 커야 합니다.")
        return self


# ---------------------------------------------------------------------------
# 5. InteractionEvidence — 관찰 후보의 상호작용 맥락
# ---------------------------------------------------------------------------

class InteractionEvidence(BaseModel):
    with_peers: Optional[str] = None
    with_teacher: Optional[str] = None
    with_materials: Optional[str] = None


# ---------------------------------------------------------------------------
# 6. NuriAreaCandidate — 누리과정 5영역 후보 (AI 제시, 확정 아님)
# ---------------------------------------------------------------------------

class NuriAreaCandidate(BaseModel):
    area: str
    rationale: str
    confidence: float = Field(ge=0.0, le=1.0)


# ---------------------------------------------------------------------------
# 7. KicceItemCandidate — KICCE 문항 후보 (AI 제시, 확정 아님)
# ---------------------------------------------------------------------------

class KicceItemCandidate(BaseModel):
    item_id: Optional[int] = None
    item_text: str
    rationale: str
    confidence: float = Field(ge=0.0, le=1.0)


# ---------------------------------------------------------------------------
# 8. ScaleMappingCandidate — 척도 매핑 후보 (nuri / kicce / 향후 확장)
# ---------------------------------------------------------------------------

class ScaleMappingCandidate(BaseModel):
    id: str
    candidate_id: str
    scale: str = Field(description="척도 식별자 예: nuri, kicce, prosocial")
    area: Optional[str] = None
    item_id: Optional[int] = None
    item_text: str
    rationale: str
    confidence: float = Field(ge=0.0, le=1.0)


# ---------------------------------------------------------------------------
# 9. ObservationCandidate — AI 관찰 후보 (확정 기록이 아님)
#
#    필수: time_start, time_end, observed_behavior, visual_evidence,
#          confidence, needs_teacher_review
#    금지: 관찰수준 점수 필드 (score, level, rating 등)
#    temp_child_id: "child_A" 형태의 임시 ID, 교사가 가명 ID로 매칭
# ---------------------------------------------------------------------------

class ObservationCandidate(BaseModel):
    id: str
    video_id: str
    scene_id: str
    time_start: float = Field(ge=0.0)
    time_end: float = Field(ge=0.0)
    temp_child_id: str = Field(description="임시 유아 ID (예: child_A). 교사가 가명 ID와 매칭.")
    observed_behavior: str = Field(min_length=1)
    interaction: Optional[InteractionEvidence] = None
    activity_context: Optional[str] = None
    peer_relation: Optional[str] = None
    visual_evidence: str = Field(min_length=1, description="시각적 근거 (어떤 프레임에서 무엇이 보였는지)")
    audio_support: Optional[str] = Field(default=None, description="보조 STT 근거 (있을 경우에만)")
    nuri_area_candidates: list[NuriAreaCandidate] = Field(default_factory=list)
    kicce_item_candidates: list[KicceItemCandidate] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    needs_teacher_review: bool = True
    created_at: datetime = Field(default_factory=datetime.now)

    @model_validator(mode="after")
    def _end_after_start(self) -> "ObservationCandidate":
        if self.time_end <= self.time_start:
            raise ValueError(f"time_end({self.time_end}) 은 time_start({self.time_start}) 보다 커야 합니다.")
        return self


# ---------------------------------------------------------------------------
# 10 & 11. SegmentAnalysisRequest / Result — 비전 LLM 입출력
# ---------------------------------------------------------------------------

class FrameRef(BaseModel):
    """비전 입력용 프레임 참조."""
    frame_id: str
    t: float = Field(ge=0.0)
    image_ref: str = Field(description="파일 경로 또는 base64 데이터 참조")


class AudioSupportContext(BaseModel):
    """비전 입력용 보조 오디오 컨텍스트."""
    available: bool
    transcript: Optional[str] = None
    speaker_hint: Optional[SpeakerHint] = None


class SegmentAnalysisRequest(BaseModel):
    """비전 LLM에 전달하는 구간 분석 요청. 원본 영상 경로는 포함하지 않는다."""
    video_id: str
    segment_id: str
    time_start: float = Field(ge=0.0)
    time_end: float = Field(ge=0.0)
    frames: list[FrameRef] = Field(min_length=1)
    frame_layout: FrameLayout = "sequence"
    audio_support: Optional[AudioSupportContext] = None

    @model_validator(mode="after")
    def _end_after_start(self) -> "SegmentAnalysisRequest":
        if self.time_end <= self.time_start:
            raise ValueError(f"time_end({self.time_end}) 은 time_start({self.time_start}) 보다 커야 합니다.")
        return self


class SegmentAnalysisResult(BaseModel):
    """비전 LLM 분석 결과. observations 는 후보이며 자동 확정되지 않는다."""
    segment_id: str
    time_start: float = Field(ge=0.0)
    time_end: float = Field(ge=0.0)
    observations: list[ObservationCandidate] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# 12. ChildMatch — 임시 ID ↔ 가명 ID 매칭 (교사 수행)
# ---------------------------------------------------------------------------

class ChildMatch(BaseModel):
    id: str
    video_id: str
    temp_child_id: str = Field(description="AI 부여 임시 ID (예: child_A)")
    pseudonym_id: str = Field(description="교사가 지정한 가명 ID. 실명 아님.")
    matched_by: str
    matched_at: datetime = Field(default_factory=datetime.now)


# ---------------------------------------------------------------------------
# 13. FinalRecord — 교사 확정 관찰기록
#
#     decision: accepted(원문 채택) / edited(수정 채택) / rejected(기각)
#     금지: score 등 관찰수준 점수 필드
# ---------------------------------------------------------------------------

class FinalRecord(BaseModel):
    id: str
    candidate_id: str = Field(description="원본 ObservationCandidate ID (추적용)")
    pseudonym_id: str = Field(description="확정 대상 유아 가명 ID")
    final_behavior: str = Field(min_length=1, description="교사 검토 후 최종 행동 서술")
    confirmed_areas: list[str] = Field(default_factory=list, description="확정 누리 영역 목록")
    confirmed_items: list[KicceItemCandidate] = Field(default_factory=list, description="확정 KICCE 문항 목록")
    decision: DecisionType
    edited: bool = Field(description="후보 대비 수정 여부")
    confirmed_by: str
    confirmed_at: datetime = Field(default_factory=datetime.now)


# ---------------------------------------------------------------------------
# 14. AuditLog — 영상 접근·분석·삭제 감사 로그
# ---------------------------------------------------------------------------

class AuditLog(BaseModel):
    id: str
    video_id: str
    actor: str
    action: AuditAction
    detail: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.now)
