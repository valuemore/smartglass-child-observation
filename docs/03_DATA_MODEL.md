# 03. 데이터 모델

> 1차 저장소는 **SQLite** + JSON/CSV export. 스키마는 repository 인터페이스 뒤에 두어
> 향후 **PostgreSQL** 교체가 쉽도록 한다. 아래는 초안이며 구현 시 조정될 수 있다.

## 1. 엔티티 개요

```
class_group 1───* child                            (클래스·등록 유아: 가명 ID, 동의 시 얼굴 참조)
class_group 1───* video                            (클래스에 매일 업로드되는 영상)
video 1───* scene 1───* frame
video 1───* clip                                   (근거 클립)
video 1───* observation_candidate *───1 scene
observation_candidate 1───* scale_mapping          (누리/KICCE 후보)
observation_candidate *───? face_match_candidate   (검출 얼굴 → 등록 child 가명 후보)
observation_candidate *───? child_match            (temp_child_id ↔ 가명 ID, 교사 확정)
class_group 1───* weekly_draft *───* observation_candidate  (주간 초안 ← 누적 후보)
weekly_draft 1───* final_record                    (교사 확정, 주차 귀속)
audio_segment *───1 video
ai_assistant_log *───1 (actor)                     (AI비서 감사)
audit_log *───1 video
```

> **원천 저장소는 SQLite(향후 PostgreSQL).** 그래프 분석이 필요하면 SQLite에서
> **재생성 가능한 인메모리 투영(NetworkX)**으로만 다루며 영속 그래프 DB(Neo4j)는 사용하지 않는다(섹션 5).

## 2. 테이블 정의 (SQLite 초안)

### class_group — 학급(우리반)
| 컬럼 | 타입 | 설명 |
|------|------|------|
| id | TEXT PK | `cls_01` |
| name | TEXT | 클래스 표시명 |
| teacher_owner | TEXT | 담당 교사 식별자 |
| face_match_enabled | INTEGER | 클래스 단위 얼굴매칭 ON/OFF (**기본 0=OFF**) |
| created_at | TEXT | ISO8601 |

### child — 등록 유아 (실명 미저장)
| 컬럼 | 타입 | 설명 |
|------|------|------|
| id | TEXT PK | `chd_07` |
| class_id | TEXT FK | class_group |
| pseudonym_id | TEXT | 교사가 부여한 **가명 ID**(실명 아님) |
| display_label | TEXT | 화면 표시 라벨(가명 기반) |
| reference_photo_path | TEXT | `data/faces/...` (제한 접근, **동의 시에만 저장**, nullable) |
| face_embedding | BLOB | 얼굴 임베딩 벡터 (**로컬 전용**, **동의 시에만 저장**, nullable) |
| face_match_consent | INTEGER | 얼굴매칭 동의 여부 (**기본 0**) |
| consent_at / consent_by | TEXT | 동의 시각·기록자 |
| created_at | TEXT | |

> **실명 컬럼 없음.** `reference_photo_path`·`face_embedding`은 `face_match_consent=1`일 때만 채운다.
> 동의 철회 시 두 값을 즉시 삭제하고 `audit_log(face_consent_change)` 기록.

### video — 업로드된 교사 시점 영상
| 컬럼 | 타입 | 설명 |
|------|------|------|
| id | TEXT PK | `vid_001` |
| class_id | TEXT FK | 소속 클래스 |
| filename | TEXT | 원본 파일명 |
| stored_path | TEXT | `data/videos/...` (제한 접근) |
| duration_sec | REAL | 길이 |
| fps | REAL | 프레임레이트 |
| width / height | INTEGER | 해상도 |
| captured_date | TEXT | 촬영일(누적·주간 묶음 기준) |
| status | TEXT | uploaded / analyzing / analyzed / accumulated / reviewed |
| analysis_status | TEXT | queued / running / done / failed (자동 분석 상태머신) |
| progress | INTEGER | 0~100 (업로드·분석 진행률) |
| retry_count | INTEGER | 재시도 횟수 |
| last_error | TEXT | 마지막 실패 사유(nullable) |
| auto_analyzed | INTEGER | 업로드 즉시 자동 분석 완료 여부(0/1) |
| created_at | TEXT | ISO8601 |
| retention_until | TEXT | 삭제 정책 기준일 |

### clip — 근거 클립
| 컬럼 | 타입 | 설명 |
|------|------|------|
| id | TEXT PK | `clip_vid_001_003` |
| video_id | TEXT FK | |
| source_scene_ids | TEXT(JSON) | 포함 scene id 목록 |
| start_sec / end_sec / duration_sec | REAL | 구간 |
| local_clip_path | TEXT | `data/clips/...` (제한 접근) |
| selected_for_vision_analysis | INTEGER | 0/1 |
| selection_reason | TEXT | 선정 근거 |
| created_at | TEXT | |

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

### face_match_candidate — 얼굴 참조 매칭 후보 (AI 제시, 교사 확정)
| 컬럼 | 타입 | 설명 |
|------|------|------|
| id | TEXT PK | |
| video_id | TEXT FK | |
| scene_id / clip_id | TEXT | 근거 구간(nullable) |
| temp_child_id | TEXT | `child_A` 등 임시 ID |
| child_id | TEXT FK | 매칭 후보 등록 유아(child) |
| confidence | REAL | 0~1 (유사도 기반) |
| status | TEXT | proposed / confirmed / rejected (**기본 proposed**) |
| decided_by / decided_at | TEXT | 교사 확정 정보(nullable) |

> AI는 **후보(proposed)** 까지만 만든다. `confirmed`는 교사 액션으로만 전환된다(자동 확정 금지).

### child_match — 임시 ID ↔ 가명 ID 매칭 (교사 확정)
| 컬럼 | 타입 | 설명 |
|------|------|------|
| id | TEXT PK | |
| video_id | TEXT FK | |
| temp_child_id | TEXT | `child_A` |
| pseudonym_id | TEXT | 확정된 가명 ID |
| source | TEXT | teacher / face_candidate_confirmed (얼굴 후보를 교사가 확정) |
| matched_by | TEXT | 교사 식별자 |
| matched_at | TEXT | |

### weekly_draft — 주간/격주 관찰기록 초안 (누적 후보 기반)
| 컬럼 | 타입 | 설명 |
|------|------|------|
| id | TEXT PK | |
| class_id | TEXT FK | |
| pseudonym_id | TEXT | 대상 유아(가명) |
| period_start / period_end | TEXT | 1주/2주 기간 |
| area | TEXT | 누리 영역 |
| draft_text | TEXT | AI 생성 초안(후보, 점수 없음) |
| source_candidate_ids_json | TEXT(JSON) | 근거가 된 observation_candidate id 목록 |
| representative_clip_ids_json | TEXT(JSON) | **대표 근거 클립 1~3개** id |
| status | TEXT | generated / reviewing / finalized |
| created_at | TEXT | |

### final_record — 교사 확정 관찰기록 (주차 귀속)
| 컬럼 | 타입 | 설명 |
|------|------|------|
| id | TEXT PK | |
| weekly_draft_id | TEXT FK | 원 주간 초안(nullable, 추적용) |
| candidate_id | TEXT FK | 원 후보(추적용, nullable) |
| pseudonym_id | TEXT | 확정 대상 유아 |
| period_start / period_end | TEXT | 확정 귀속 기간 |
| final_behavior | TEXT | 교사 수정 후 행동 서술 |
| confirmed_areas_json | TEXT(JSON) | 확정 누리 영역 |
| confirmed_items_json | TEXT(JSON) | 확정 KICCE 문항 |
| decision | TEXT | accepted / edited / (rejected는 미저장 또는 플래그) |
| edited | INTEGER | 후보 대비 수정 여부(0/1) |
| confirmed_by | TEXT | 교사 식별자 |
| confirmed_at | TEXT | |

### ai_assistant_log — AI비서 감사 (제한형)
| 컬럼 | 타입 | 설명 |
|------|------|------|
| id | TEXT PK | |
| actor | TEXT | 교사 식별자 |
| query | TEXT | 질의 |
| intent | TEXT | search / edit_assist / shoot_suggest (3종으로 제한) |
| response_summary | TEXT | 응답 요약 |
| created_at | TEXT | |

> AI비서는 **검색·초안 수정 보조·보완 촬영 제안**만 수행. 신규 후보 생성·확정·점수화 불가.

### audit_log — 영상·얼굴·분석·삭제 감사
| 컬럼 | 타입 | 설명 |
|------|------|------|
| id | TEXT PK | |
| video_id | TEXT FK | (얼굴/비서 관련은 nullable) |
| actor | TEXT | 행위자 |
| action | TEXT | upload / access / analyze / export / delete / **face_match / reference_photo_access / face_consent_change / draft_generate / ai_assist** |
| detail | TEXT | 부가 정보 |
| created_at | TEXT | |

## 3. 핵심 JSON 스키마 (core/schemas.py 대응)

비전 모델 입력/출력 JSON 구조는 [05_AI_PIPELINE.md](05_AI_PIPELINE.md)의
섹션 4·5와 일치시키며, `core/schemas.py`의 Pydantic 모델로 **검증**한다.

- `ObservationCandidate`: 필수 `time_start/end`, `observed_behavior`, `visual_evidence`, `confidence`, `needs_teacher_review`.
- `ScaleMappingCandidate`: `scale`, `area?`, `item_id?`, `item_text`, `rationale`, `confidence`.
- `FaceMatchCandidate`: `temp_child_id`, `child_id`, `confidence`, `status`(기본 proposed). 점수 아님(유사도).
- `WeeklyDraft`: `pseudonym_id`, `period_start/end`, `area`, `draft_text`, `source_candidate_ids`, `representative_clip_ids`.
- 점수 필드 없음(관찰수준·발달·평정 모두). `temp_child_id`는 교사 매칭 전 임시값.

## 4. 인덱스·제약 (권장)

- `child(class_id)`, `video(class_id)`, `video(class_id, captured_date)` 인덱스.
- `frame(scene_id)`, `clip(video_id)`, `observation_candidate(video_id)`, `scale_mapping(candidate_id)`, `audit_log(video_id)` 인덱스.
- `face_match_candidate(video_id)`, `weekly_draft(class_id, pseudonym_id, period_start)` 인덱스.
- `child_match(video_id, temp_child_id)` 유니크.
- 외래키 ON DELETE는 삭제 정책(영상·유아·클래스 삭제 시 파생 데이터·참조사진·임베딩·매칭 후보 연쇄 처리)과 연동([06_SECURITY_PRIVACY_RULES.md](06_SECURITY_PRIVACY_RULES.md)).

## 5. 그래프 투영 (분석 전용, 비영속)

주간 초안·균형 분석에 그래프 관점이 필요하면 **SQLite에서 재생성 가능한 인메모리 투영(NetworkX)**으로 다룬다.
**Neo4j 등 영속 그래프 DB는 사용하지 않는다.**

- **노드**: `Child(pseudonym_id)` · `NuriArea` · `KicceItem` · `ObservationCandidate` · `Clip(id만)` · `Period`.
- **관계**: `ABOUT`(후보→유아) · `IN_AREA` · `SUGGESTS`(→KICCE) · `EVIDENCED_BY`(→클립 id) · `DURING`(→기간) · `BELONGS_TO`.
- **투영 금지 데이터**: 실명·얼굴 사진·얼굴 임베딩·원본 영상 경로·클립 파일 경로. 클립은 **id만** 두고 경로는 렌더 시 SQLite 조회(접근 감사).
- 투영은 원천(SQLite)에서 재생성되므로 git 제외, 원천과 동일한 보관·삭제 정책을 따른다.
