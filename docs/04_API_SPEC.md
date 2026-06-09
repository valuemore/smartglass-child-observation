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
    def save_video(self, video: Video) -> None: ...
    def add_scenes(self, scenes: list[Scene]) -> None: ...
    def add_frames(self, frames: list[Frame]) -> None: ...
    def add_candidates(self, items: list[ObservationCandidate]) -> None: ...
    def add_mappings(self, items: list[ScaleMappingCandidate]) -> None: ...
    def set_child_match(self, match: ChildMatch) -> None: ...
    def save_final_record(self, record: FinalRecord) -> None: ...
    def write_audit(self, entry: AuditLog) -> None: ...
    # 조회·export 메서드 포함
```

- 1차 구현: `SqliteRepository`. 향후 `PostgresRepository`로 교체(인터페이스 유지).

## 5. 향후 REST 윤곽 (2차, FastAPI)

| 메서드 | 경로 | 설명 |
|--------|------|------|
| POST | `/videos` | 영상 업로드(메타 등록) |
| POST | `/videos/{id}/analyze` | 배치 분석 트리거 |
| GET | `/videos/{id}/candidates` | 관찰 후보 + 매핑 조회 |
| POST | `/videos/{id}/child-matches` | temp_child_id ↔ 가명 ID 매칭 |
| POST | `/candidates/{id}/finalize` | 교사 확정(accept/edit/reject) |
| GET | `/reports/{video_id}` | 리포트 데이터 |
| DELETE | `/videos/{id}` | 삭제 정책에 따른 삭제(감사 기록) |

- 인증/권한·감사 로깅은 REST 전환 시 미들웨어로 강제.
- 모든 응답 스키마는 `core/schemas.py`와 일치.

## 6. 공통 규칙

- 모든 외부 호출은 어댑터 뒤에서 수행하고, 실패·재시도·비용 상한을 설정으로 관리.
- 모든 영상 접근/분석/삭제는 `write_audit`로 기록한다.
- AI 출력은 저장 전 Pydantic 검증을 통과해야 한다.
