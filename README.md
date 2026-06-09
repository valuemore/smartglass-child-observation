# 스마트안경 유아 행동 관찰기록 보조 시스템

어린이집 교사가 착용한 **스마트안경(교사 1인칭 시점)** 영상에서 유아의 행동·상호작용·활동 맥락을
AI가 분석해 **관찰기록 초안(후보)** 을 생성하는 연구용 웹 시스템입니다.
AI는 기록을 확정하지 않으며, **교사가 최종 검토·수정·확정**합니다.

## 목적

- **1차 목표**: 학회 발표용 시연 시스템 (샘플 영상 업로드 → 배치 분석 → 교사 검토 → 확정)
- **2차 목표**: 논문용 프로토타입 (AI 후보 vs 교사 확정 비교 등 유효성 지표 산출)

## 핵심 원칙

- AI 분석의 중심 데이터는 **영상**이며, 오디오는 **보조 증거**다.
- AI 분석 방식은 **프레임 추출 + 비전 모델**이다.
- AI는 관찰기록을 **확정하지 않고 후보만 제시**한다.
- AI는 유아를 자동 식별하지 않는다. `child_A`, `child_B` 임시 ID만 부여하고 **교사가 실제 가명 ID와 매칭**한다.
- 1차 분류 기준은 **누리과정 5개 영역**, **KICCE 유아관찰척도 60문항 매핑은 핵심 기능**이다(후보 제시).
- **관찰수준 점수는 산출하지 않는다.**
- 원본 영상은 **제한 접근 · 접근 로그 · 삭제 정책**을 따른다.

## 기술 스택 (1차 시연)

Python 3.11+ · Streamlit · OpenCV/ffmpeg · PySceneDetect · 클라우드 멀티모달 비전 LLM API ·
faster-whisper(보조 STT) · SQLite. **배포는 로컬 앱 + 클라우드 AI API**, **입력은 녹화 영상 업로드 후 배치 분석**.
Neo4j는 사용하지 않습니다. 향후 PostgreSQL/FastAPI/Next.js로 확장 가능하도록 모듈을 분리합니다.

## 문서 인덱스

| 문서 | 내용 |
|------|------|
| [docs/00_DEVELOPMENT_PLAN.md](docs/00_DEVELOPMENT_PLAN.md) | 전체 개발 계획 (기술 스택·파이프라인·개발 단계) |
| [docs/01_PROJECT_BRIEF.md](docs/01_PROJECT_BRIEF.md) | 연구 배경·목표·범위·용어 |
| [docs/02_SYSTEM_ARCHITECTURE.md](docs/02_SYSTEM_ARCHITECTURE.md) | 시스템 아키텍처·모듈 구조·확장 경로 |
| [docs/03_DATA_MODEL.md](docs/03_DATA_MODEL.md) | 데이터 모델·SQLite/JSON 스키마 |
| [docs/04_API_SPEC.md](docs/04_API_SPEC.md) | 내부 서비스 계약·향후 REST 윤곽 |
| [docs/05_AI_PIPELINE.md](docs/05_AI_PIPELINE.md) | AI 분석 파이프라인 상세 |
| [docs/06_SECURITY_PRIVACY_RULES.md](docs/06_SECURITY_PRIVACY_RULES.md) | 보안·개인정보 보호 규칙 |
| [docs/07_UI_FLOW.md](docs/07_UI_FLOW.md) | Streamlit 화면 흐름 |
| [docs/08_TEST_PLAN.md](docs/08_TEST_PLAN.md) | 테스트·검증 계획 |

## 현재 상태

문서/개발 기준 정의 단계입니다. **아직 애플리케이션 코드는 작성되지 않았습니다.**

## 디렉토리 개요 (예정)

```
app/         Streamlit UI (진입점, 도메인 로직 없음)
core/        도메인 모델·스키마·상수
services/    분석 파이프라인 (video / vision / audio / mapping)
storage/     저장소 추상화 (SQLite 구현)
resources/   누리과정·KICCE 기준 데이터
data/        영상·프레임·DB (git 제외, 제한 접근)
security/    접근 제어·감사 로그·삭제 정책
docs/        프로젝트 문서
```
