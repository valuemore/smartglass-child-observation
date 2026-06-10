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

from pydantic import BaseModel, ConfigDict, Field, model_validator


# ---------------------------------------------------------------------------
# 공통 열거형 상수
# ---------------------------------------------------------------------------

VideoStatus = Literal["uploaded", "analyzing", "analyzed", "accumulated", "reviewed"]
SpeakerHint = Literal["teacher", "child", "unknown"]
DecisionType = Literal["accepted", "edited", "rejected"]
AuditAction = Literal["upload", "access", "analyze", "export", "delete",
                       "gemini_clip_upload", "gemini_clip_delete",
                       # V2 추가: 얼굴·동의·주간초안·AI비서 감사
                       "face_match", "reference_photo_access",
                       "face_consent_change", "draft_generate", "ai_assist"]
FrameLayout = Literal["sequence", "grid_2x2", "grid_3x1"]
# AI 후보의 시각적 근거 적절성(교사 판단). 유아 발달/관찰수준 점수가 아님.
EvidenceAdequacy = Literal["adequate", "partial", "inadequate"]

# V2 — 업로드 즉시 자동 분석 상태머신
AnalysisStatus = Literal["queued", "running", "done", "failed"]
# V2 — 얼굴 매칭 후보 상태 (proposed=AI 제시, confirmed/rejected=교사 결정)
FaceMatchStatus = Literal["proposed", "confirmed", "rejected"]
# V2 — child_match 출처 (교사 직접 / 얼굴 후보를 교사가 확정)
ChildMatchSource = Literal["teacher", "face_candidate_confirmed"]
# V2 — 주간 초안 상태
DraftStatus = Literal["generated", "reviewing", "finalized"]
# V2 — AI비서 의도 (검색·초안 수정 보조·보완 촬영 제안으로 제한)
AssistantIntent = Literal["search", "edit_assist", "shoot_suggest"]


# ---------------------------------------------------------------------------
# 1. Video — 업로드된 교사 시점 영상
# ---------------------------------------------------------------------------

class Video(BaseModel):
    id: str
    filename: str
    stored_path: str
    duration_sec: float = Field(ge=0.0)
    fps: float = Field(ge=0.0)       # 0 = 메타 추출 실패(미지)
    width: int = Field(ge=0)         # 0 = 메타 추출 실패(미지)
    height: int = Field(ge=0)        # 0 = 메타 추출 실패(미지)
    status: VideoStatus = "uploaded"
    created_at: datetime
    retention_until: Optional[datetime] = None
    # 업로드 소유자 및 관찰 맥락 메타데이터
    owner: str = ""                  # 업로드 교사 username (get_current_actor() 자동 세팅)
    institution: str = ""            # 기관명
    class_name: str = ""             # 클래스 이름 (자유 입력, V2의 class_id와 별개)
    observation_context: str = ""    # 관찰 상황 (자유놀이·야외활동 등 고정 선택지)
    notes: str = ""                  # 선택적 메모
    # V2 — 클래스 연결·매일 업로드·업로드 즉시 자동 분석
    class_id: Optional[str] = None   # 소속 클래스(class_group.id). 미설정 영상 호환을 위해 nullable
    captured_date: Optional[str] = None  # 촬영일 (YYYY-MM-DD, 누적·주간 묶음 기준)
    analysis_status: AnalysisStatus = "queued"  # 자동 분석 상태머신
    progress: int = Field(default=0, ge=0, le=100)  # 업로드·분석 진행률
    retry_count: int = Field(default=0, ge=0)       # 분석 재시도 횟수
    last_error: Optional[str] = None                # 마지막 실패 사유
    auto_analyzed: bool = False                     # 업로드 즉시 자동 분석 완료 여부


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
    model_config = ConfigDict(extra="forbid")
    with_peers: Optional[str] = None
    with_teacher: Optional[str] = None
    with_materials: Optional[str] = None


# ---------------------------------------------------------------------------
# 6. NuriAreaCandidate — 누리과정 5영역 후보 (AI 제시, 확정 아님)
# ---------------------------------------------------------------------------

class NuriAreaCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    area: str
    rationale: str
    confidence: float = Field(ge=0.0, le=1.0)


# ---------------------------------------------------------------------------
# 7. KicceItemCandidate — KICCE 문항 후보 (AI 제시, 확정 아님)
# ---------------------------------------------------------------------------

class KicceItemCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")
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
    model_config = ConfigDict(extra="forbid")  # 외부 응답의 점수·실명 필드 주입 즉시 거부
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
    clip_path: Optional[str] = Field(
        default=None,
        description="사전 추출된 근거 클립 경로 (Gemini 어댑터 전용). 원본 영상 경로 아님.",
    )

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
    source: ChildMatchSource = Field(
        default="teacher",
        description="매칭 출처. teacher=교사 직접, face_candidate_confirmed=얼굴 후보를 교사가 확정.",
    )
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
    # V2 — 주간 초안 귀속 (영상별 즉시 확정 V1과의 호환을 위해 nullable)
    weekly_draft_id: Optional[str] = Field(default=None, description="원본 주간 초안 ID (추적용)")
    period_start: Optional[str] = Field(default=None, description="확정 귀속 기간 시작 (YYYY-MM-DD)")
    period_end: Optional[str] = Field(default=None, description="확정 귀속 기간 종료 (YYYY-MM-DD)")
    final_behavior: str = Field(min_length=1, description="교사 검토 후 최종 행동 서술")
    confirmed_areas: list[str] = Field(default_factory=list, description="확정 누리 영역 목록")
    confirmed_items: list[KicceItemCandidate] = Field(default_factory=list, description="확정 KICCE 문항 목록")
    decision: DecisionType
    edited: bool = Field(description="후보 대비 수정 여부")
    confirmed_by: str
    confirmed_at: datetime = Field(default_factory=datetime.now)
    review_seconds: Optional[int] = Field(
        default=None, ge=0,
        description="후보 열람~확정 경과(초). AI 지원 워크플로우 분석용, 유아 평가 아님.",
    )
    evidence_adequacy: Optional[EvidenceAdequacy] = Field(
        default=None,
        description="AI 후보의 시각적 근거 적절성(교사 판단). 유아 발달/관찰수준 점수 아님.",
    )


# ---------------------------------------------------------------------------
# 14. Clip — 근거 클립 (선별 장면에서 ffmpeg로 추출한 짧은 동영상)
#
#     원본 영상은 보존하고 선별 구간만 추출한다.
#     외부 API(Gemini)에는 이 클립만 전송하고 원본 영상은 전송하지 않는다.
# ---------------------------------------------------------------------------

class Clip(BaseModel):
    id: str                                  # clip_{video_id}_{idx:03d}
    video_id: str
    source_scene_ids: list[str] = Field(default_factory=list)  # 이 클립의 원본 scene id 목록
    start_sec: float = Field(ge=0.0)
    end_sec: float = Field(ge=0.0)
    duration_sec: float = Field(ge=0.0)
    local_clip_path: str = ""               # data/clips/{video_id}/{id}.mp4
    selected_for_vision_analysis: bool = True
    selection_reason: str = ""
    created_at: datetime = Field(default_factory=datetime.now)

    @model_validator(mode="after")
    def _end_after_start(self) -> "Clip":
        if self.end_sec <= self.start_sec:
            raise ValueError(f"end_sec({self.end_sec}) 은 start_sec({self.start_sec}) 보다 커야 합니다.")
        return self


# ---------------------------------------------------------------------------
# 15. AuditLog — 영상 접근·분析·삭제 감사 로그
# ---------------------------------------------------------------------------

class AuditLog(BaseModel):
    id: str
    video_id: str
    actor: str
    action: AuditAction
    detail: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.now)


# ---------------------------------------------------------------------------
# 16. ClassGroup — 학급(우리반)
# ---------------------------------------------------------------------------

class ClassGroup(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    name: str = Field(min_length=1, description="클래스 표시명")
    teacher_owner: str = Field(description="담당 교사 식별자")
    face_match_enabled: bool = Field(
        default=False,
        description="클래스 단위 얼굴매칭 ON/OFF. 기본 OFF, 연구 동의 시에만 ON.",
    )
    created_at: datetime = Field(default_factory=datetime.now)


# ---------------------------------------------------------------------------
# 17. Child — 등록 유아 (실명 미저장)
#
#     실명 필드 없음. 가명 ID(pseudonym_id)와 표시 라벨만 사용한다.
#     reference_photo_path / face_embedding 은 face_match_consent=True 일 때만 채운다.
#     extra="forbid" 로 실명 등 예기치 않은 필드 주입을 거부한다.
# ---------------------------------------------------------------------------

class Child(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    class_id: str = Field(description="소속 class_group.id")
    pseudonym_id: str = Field(description="교사가 부여한 가명 ID. 실명 아님.")
    display_label: str = Field(default="", description="화면 표시 라벨(가명 기반)")
    reference_photo_path: Optional[str] = Field(
        default=None, description="data/faces/... (제한 접근, 동의 시에만 저장)")
    face_embedding: Optional[bytes] = Field(
        default=None, description="얼굴 임베딩 벡터 (로컬 전용, 동의 시에만 저장). 외부 전송 금지.")
    face_match_consent: bool = Field(default=False, description="얼굴매칭 동의 여부. 기본 False.")
    consent_at: Optional[datetime] = None
    consent_by: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.now)

    @model_validator(mode="after")
    def _consent_gate(self) -> "Child":
        # 동의가 없으면 참조사진·임베딩을 보유할 수 없다(보안 불변식).
        if not self.face_match_consent and (self.reference_photo_path or self.face_embedding):
            raise ValueError("face_match_consent=False 인 유아는 reference_photo_path/face_embedding 을 가질 수 없습니다.")
        return self


# ---------------------------------------------------------------------------
# 18. FaceMatchCandidate — 얼굴 참조 매칭 후보 (AI 제시, 교사 확정)
#
#     AI 는 status="proposed" 후보까지만 만든다. confirmed/rejected 는 교사 액션.
#     confidence 는 유사도 기반 신뢰도이며 관찰수준/발달 점수가 아니다.
# ---------------------------------------------------------------------------

class FaceMatchCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    video_id: str
    scene_id: Optional[str] = None
    clip_id: Optional[str] = None
    temp_child_id: str = Field(description="임시 유아 ID (예: child_A)")
    child_id: str = Field(description="매칭 후보 등록 유아(child.id)")
    confidence: float = Field(ge=0.0, le=1.0, description="얼굴 유사도 기반 신뢰도. 발달 점수 아님.")
    status: FaceMatchStatus = "proposed"
    decided_by: Optional[str] = None
    decided_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=datetime.now)


# ---------------------------------------------------------------------------
# 19. WeeklyDraft — 주간/격주 관찰기록 초안 (누적 후보 기반, 확정 아님)
#
#     점수 필드 없음. draft_text 는 AI 초안이며 교사 확정 전까지 기록이 아니다.
# ---------------------------------------------------------------------------

class WeeklyDraft(BaseModel):
    id: str
    class_id: str
    pseudonym_id: str = Field(description="대상 유아 가명 ID")
    period_start: str = Field(description="기간 시작 (YYYY-MM-DD)")
    period_end: str = Field(description="기간 종료 (YYYY-MM-DD)")
    area: str = Field(description="누리 영역")
    draft_text: str = Field(default="", description="AI 생성 초안(후보, 점수 없음)")
    source_candidate_ids: list[str] = Field(
        default_factory=list, description="근거 observation_candidate id 목록")
    representative_clip_ids: list[str] = Field(
        default_factory=list, description="대표 근거 클립 id 1~3개")
    status: DraftStatus = "generated"
    created_at: datetime = Field(default_factory=datetime.now)


# ---------------------------------------------------------------------------
# 20. AiAssistantLog — AI비서 감사 (검색·초안 수정 보조·보완 촬영 제안으로 제한)
# ---------------------------------------------------------------------------

class AiAssistantLog(BaseModel):
    id: str
    actor: str
    query: str
    intent: AssistantIntent = Field(description="search / edit_assist / shoot_suggest 로 제한")
    response_summary: str = ""
    created_at: datetime = Field(default_factory=datetime.now)
