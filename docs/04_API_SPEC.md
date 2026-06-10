# 04. API / 서비스 계약

> 1차(Streamlit)에서는 REST API 없이 **내부 Python 서비스 계약(인터페이스)** 으로 동작한다.
> 모든 외부 의존(비전/STT/저장소)은 인터페이스 뒤에 두어 교체·확장이 가능하게 한다.
> 2차에서 동일 계약을 **FastAPI REST**로 노출할 수 있다(섹션 5).
> 아래 시그니처는 설계 의도를 보여주는 초안이며 구현 시 조정될 수 있다.

## 1. 비전 어댑터 (`services/vision/`)

```python
class VisionAdapter(Protocol):
    """비전 LLM 제공자 교체 가능 인터페이스."""
    def analyze_segment(self, request: SegmentAnalysisRequest) -> SegmentAnalysisResult:
        ...
```

- 입력 `SegmentAnalysisRequest`: video_id, segment_id, time_start/end, frames(이미지 ref/바이트),
  frame_layout(`sequence`|`grid_2x2`), audio_support(optional), instruction_context(누리 영역·규칙).
- 출력 `SegmentAnalysisResult`: `observations: list[ObservationCandidate]`.
- 구현 예: `GeminiVisionAdapter`, `OpenAIVisionAdapter`, `ClaudeVisionAdapter` 중 1차 1개.
- **원본 영상 전체를 전송하지 않는다.** 선별 프레임/클립 + 필요한 텍스트만.

## 2. 매핑 서비스 (`services/mapping/`)

```python
class ScaleMapper(Protocol):
    """척도 플러그 인터페이스. 1차=KICCE, 향후 친사회성·놀이몰입·자기조절."""
    scale_name: str
    def map(self, candidate: ObservationCandidate) -> list[ScaleMappingCandidate]:
        ...
```

- `NuriClassifier`: 관찰 후보 → 누리 5영역 후보(근거·신뢰도).
- `KicceMapper`(핵심): 누리 영역 필터 + 키워드/규칙 + (권장) 임베딩 유사도 → 문항 후보.
- 결과는 **후보**(item_id·item_text·rationale·confidence). 확정·점수화 없음.
- 새 척도 추가 = `ScaleMapper` 구현 + `resources/`에 데이터 파일 추가.

## 2-1. 클래스·유아 등록 서비스 (`services/class/`)

```python
class ClassService(Protocol):
    def register_class(self, name: str, teacher_owner: str) -> ClassGroup: ...
    def register_child(self, class_id: str, pseudonym_id: str,
                       reference_photo: bytes | None = None,
                       consent: bool = False) -> Child: ...
    def set_face_consent(self, child_id: str, consent: bool, by: str) -> None: ...
    def list_children(self, class_id: str) -> list[Child]: ...
```

- **실명 인자 없음.** 가명 ID·표시 라벨만.
- `reference_photo`·임베딩은 `consent=True`일 때만 저장. `set_face_consent(False)` 시 **즉시 삭제** + 감사 기록.

## 2-2. 얼굴 매칭 서비스 (`services/face/`)

```python
class FaceMatchService(Protocol):
    def propose_matches(self, video_id: str) -> list[FaceMatchCandidate]:
        """검출 얼굴 ↔ 등록 참조사진 임베딩 유사도 → 가명 매칭 후보(+신뢰도). 후보만."""
        ...
```

- 클래스/유아 동의가 **OFF면 빈 목록** 반환(매칭 미수행).
- 임베딩 비교는 **로컬에서만**. 참조사진·임베딩을 외부로 전송하지 않는다.
- 산출물은 `status=proposed` 후보. 확정은 교사(자동 확정 금지).

## 2-3. 대시보드 서비스 (`services/dashboard/`)

```python
class DashboardService(Protocol):
    def collection_status(self, class_id: str) -> CollectionMatrix:
        """유아 × 누리 5영역 매트릭스: 후보 수·최근 관찰일·부족 플래그."""
        ...
```

## 2-4. 주간 초안 서비스 (`services/draft/`)

```python
class DraftService(Protocol):
    def generate_weekly_draft(self, class_id: str,
                              period_start: str, period_end: str,
                              pseudonym_ids: list[str] | None = None) -> list[WeeklyDraft]:
        """누적 후보를 유아·영역별로 묶어 초안 + 대표 근거 클립(1~3) 생성. 후보·점수 없음."""
        ...
```

- 교사 트리거(수동). 기간(1주/2주)·대상 선택. 초안은 후보 → 교사 확정 시 `final_record`.

## 3. 오디오(보조) 서비스 (`services/audio/`)

```python
class Transcriber(Protocol):
    def transcribe(self, video_path: str) -> list[AudioSegment]:
        ...
```

- ffmpeg로 오디오 분리 → faster-whisper STT(타임스탬프).
- 명확 발화만 `is_clear=1`. 화자 힌트(teacher/child/unknown) 라벨, 확정 아님.
- 비전 분석 구간에 시간 정렬해 `audio_support`로 주입.

## 4. 저장소 (`storage/repository.py`)

```python
class Repository(Protocol):
    def save_class(self, group: ClassGroup) -> None: ...
    def add_child(self, child: Child) -> None: ...
    def save_video(self, video: Video) -> None: ...
    def update_analysis_status(self, video_id: str, status: str,
                               progress: int, last_error: str | None = None) -> None: ...
    def add_scenes(self, scenes: list[Scene]) -> None: ...
    def add_frames(self, frames: list[Frame]) -> None: ...
    def add_clips(self, clips: list[Clip]) -> None: ...
    def add_candidates(self, items: list[ObservationCandidate]) -> None: ...
    def add_mappings(self, items: list[ScaleMappingCandidate]) -> None: ...
    def add_face_match_candidates(self, items: list[FaceMatchCandidate]) -> None: ...
    def set_child_match(self, match: ChildMatch) -> None: ...
    def save_weekly_drafts(self, drafts: list[WeeklyDraft]) -> None: ...
    def save_final_record(self, record: FinalRecord) -> None: ...
    def write_audit(self, entry: AuditLog) -> None: ...
    def write_assistant_log(self, entry: AiAssistantLog) -> None: ...
    # 조회·집계·export 메서드 포함
```

- 1차 구현: `SqliteRepository`. 향후 `PostgresRepository`로 교체(인터페이스 유지).
- **export 직렬화는 미디어 경로(영상/프레임/클립/참조사진)를 제외**하는 화이트리스트 방식.

## 5. 향후 REST 윤곽 (2차, FastAPI)

| 메서드 | 경로 | 설명 |
|--------|------|------|
| POST | `/classes` | 클래스 등록 |
| POST | `/classes/{id}/children` | 유아 등록(가명·동의 기반 참조사진) |
| POST | `/videos` | 영상 업로드 → **즉시 자동 분석·누적 시작** |
| GET | `/videos/{id}/status` | 분석 상태·진행률 조회 |
| POST | `/videos/{id}/retry` | 실패 분석 재시도 |
| GET | `/dashboard/{class_id}` | 유아×누리영역 수집 현황 |
| POST | `/drafts` | 주간 초안 생성(class_id·기간·대상) |
| POST | `/face-matches/{id}/confirm` | 얼굴 매칭 후보 확정/기각(교사) |
| POST | `/drafts/{id}/finalize` | 주간 초안 확정(accept/edit/reject) |
| GET | `/reports/{class_id}` | 리포트 데이터(지원도 지표, 미디어 경로 제외) |
| DELETE | `/videos/{id}` · `/children/{id}` | 삭제 정책에 따른 연쇄 삭제(감사 기록) |

- 인증/권한·감사 로깅은 REST 전환 시 미들웨어로 강제.
- 모든 응답 스키마는 `core/schemas.py`와 일치.

## 6. 공통 규칙

- 모든 외부 호출은 어댑터 뒤에서 수행하고, 실패·재시도·비용 상한을 설정으로 관리.
- **얼굴 검출·임베딩·매칭은 로컬 전용**. 얼굴 참조사진·임베딩·원본 영상은 외부로 전송하지 않는다.
- 모든 영상·얼굴 참조사진 접근/분석/삭제/동의 변경/초안 생성/AI비서 호출은 `write_audit`(또는 `write_assistant_log`)로 기록한다.
- AI 출력은 저장 전 Pydantic 검증을 통과해야 한다.
- 점수·발달·평정 산출 인터페이스를 두지 않는다. KICCE는 후보·근거 연결용.
- export 응답·파일에는 미디어 경로를 포함하지 않는다.
