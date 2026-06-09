# 03. 데이터 모델

> 1차 저장소는 **SQLite** + JSON/CSV export. 스키마는 repository 인터페이스 뒤에 두어
> 향후 **PostgreSQL** 교체가 쉽도록 한다. 아래는 초안이며 구현 시 조정될 수 있다.

## 1. 엔티티 개요

```
video 1───* scene 1───* frame
video 1───* observation_candidate *───1 scene
observation_candidate 1───* scale_mapping        (누리/KICCE 후보)
observation_candidate *───? child_match           (temp_child_id ↔ 가명 ID)
observation_candidate 1───? final_record          (교사 확정)
audio_segment *───1 video
audit_log *───1 video
```

## 2. 테이블 정의 (SQLite 초안)

### video — 업로드된 교사 시점 영상
| 컬럼 | 타입 | 설명 |
|------|------|------|
| id | TEXT PK | `vid_001` |
| filename | TEXT | 원본 파일명 |
| stored_path | TEXT | `data/videos/...` (제한 접근) |
| duration_sec | REAL | 길이 |
| fps | REAL | 프레임레이트 |
| width / height | INTEGER | 해상도 |
| status | TEXT | uploaded / analyzing / analyzed / reviewed |
| created_at | TEXT | ISO8601 |
| retention_until | TEXT | 삭제 정책 기준일 |

### scene — 장면 분할 구간
| 컬럼 | 타입 | 설명 |
|------|------|------|
| id | TEXT PK | `seg_007` |
| video_id | TEXT FK | |
| time_start / time_end | REAL | 초 단위 |
| detector | TEXT | 예: ContentDetector |

### frame — 추출 프레임
| 컬럼 | 타입 | 설명 |
|------|------|------|
| id | TEXT PK | `f_021` |
| scene_id | TEXT FK | |
| t | REAL | 프레임 시각(초) |
| image_path | TEXT | `data/frames/{video}/{scene}/...` |
| blur_score | REAL | Laplacian 분산 |
| kept | INTEGER | 품질 필터 통과 여부(0/1) |

### audio_segment — 보조 STT 결과
| 컬럼 | 타입 | 설명 |
|------|------|------|
| id | TEXT PK | |
| video_id | TEXT FK | |
| time_start / time_end | REAL | |
| transcript | TEXT | 전사 텍스트 |
| speaker_hint | TEXT | teacher / child / unknown |
| is_clear | INTEGER | 명확 발화 여부(0/1) |

### observation_candidate — AI 관찰 후보 (확정 아님)
| 컬럼 | 타입 | 설명 |
|------|------|------|
| id | TEXT PK | |
| video_id / scene_id | TEXT FK | |
| time_start / time_end | REAL | |
| temp_child_id | TEXT | `child_A` 등 임시 ID |
| observed_behavior | TEXT | 행동 서술 |
| interaction_json | TEXT(JSON) | peers/teacher/materials |
| activity_context | TEXT | |
| peer_relation | TEXT | |
| visual_evidence | TEXT | 시각적 근거 |
| audio_support | TEXT | 보조 STT 근거(nullable) |
| confidence | REAL | 0~1 |
| needs_teacher_review | INTEGER | 0/1 |
| created_at | TEXT | |

> **점수 컬럼 없음.** 관찰수준 점수는 저장·산출하지 않는다.

### scale_mapping — 누리/KICCE(및 향후 척도) 후보
| 컬럼 | 타입 | 설명 |
|------|------|------|
| id | TEXT PK | |
| candidate_id | TEXT FK | observation_candidate |
| scale | TEXT | `nuri` / `kicce` / (향후) `prosocial` 등 |
| area | TEXT | 누리 영역(해당 시) |
| item_id | INTEGER | 문항 ID(해당 시, nullable) |
| item_text | TEXT | 문항 텍스트 |
| rationale | TEXT | 매핑 근거 |
| confidence | REAL | 0~1 |

### child_match — 임시 ID ↔ 가명 ID 매칭 (교사 수행)
| 컬럼 | 타입 | 설명 |
|------|------|------|
| id | TEXT PK | |
| video_id | TEXT FK | |
| temp_child_id | TEXT | `child_A` |
| pseudonym_id | TEXT | 교사가 지정한 가명 ID |
| matched_by | TEXT | 교사 식별자 |
| matched_at | TEXT | |

### final_record — 교사 확정 관찰기록
| 컬럼 | 타입 | 설명 |
|------|------|------|
| id | TEXT PK | |
| candidate_id | TEXT FK | 원 후보(추적용) |
| pseudonym_id | TEXT | 확정 대상 유아 |
| final_behavior | TEXT | 교사 수정 후 행동 서술 |
| confirmed_areas_json | TEXT(JSON) | 확정 누리 영역 |
| confirmed_items_json | TEXT(JSON) | 확정 KICCE 문항 |
| decision | TEXT | accepted / edited / (rejected는 미저장 또는 플래그) |
| edited | INTEGER | 후보 대비 수정 여부(0/1) |
| confirmed_by | TEXT | 교사 식별자 |
| confirmed_at | TEXT | |

### audit_log — 영상 접근·분석·삭제 감사
| 컬럼 | 타입 | 설명 |
|------|------|------|
| id | TEXT PK | |
| video_id | TEXT FK | |
| actor | TEXT | 행위자 |
| action | TEXT | upload / access / analyze / export / delete |
| detail | TEXT | 부가 정보 |
| created_at | TEXT | |

## 3. 핵심 JSON 스키마 (core/schemas.py 대응)

비전 모델 입력/출력 JSON 구조는 [05_AI_PIPELINE.md](05_AI_PIPELINE.md)의
섹션 4·5와 일치시키며, `core/schemas.py`의 Pydantic 모델로 **검증**한다.

- `ObservationCandidate`: 필수 `time_start/end`, `observed_behavior`, `visual_evidence`, `confidence`, `needs_teacher_review`.
- `ScaleMappingCandidate`: `scale`, `area?`, `item_id?`, `item_text`, `rationale`, `confidence`.
- 점수 필드 없음. `temp_child_id`는 교사 매칭 전 임시값.

## 4. 인덱스·제약 (권장)

- `frame(scene_id)`, `observation_candidate(video_id)`, `scale_mapping(candidate_id)`, `audit_log(video_id)` 인덱스.
- `child_match(video_id, temp_child_id)` 유니크.
- 외래키 ON DELETE는 삭제 정책(영상 삭제 시 파생 데이터 처리)과 연동([06_SECURITY_PRIVACY_RULES.md](06_SECURITY_PRIVACY_RULES.md)).
