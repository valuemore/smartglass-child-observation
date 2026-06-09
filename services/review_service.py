"""교사 검토·수정·확정 서비스.

원칙:
- AI 결과는 후보다. 확정은 교사 액션(이 서비스의 finalize_candidate)으로만 발생한다.
- Repository 인터페이스만 사용한다(SQLite 직접 접근 금지).
- 관찰수준 점수(score/level/rating)는 만들지 않는다.
- 유아 실명 없음. temp_child_id ↔ pseudonym_id(연구용 가명) 매칭만 다룬다.
- audit_log 없이 final_record 가 저장되는 경로는 없다.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from core.config import DEFAULT_ACTOR
from core.schemas import (
    AuditLog,
    ChildMatch,
    FinalRecord,
    KicceItemCandidate,
    ObservationCandidate,
    ScaleMappingCandidate,
)

DecisionType = str  # "accepted" | "edited" | "rejected"


# ---------------------------------------------------------------------------
# 조회
# ---------------------------------------------------------------------------

def list_review_candidates(video_id: str, repo) -> list[dict]:
    """검토 화면용 후보 묶음을 반환한다.

    각 항목:
      {
        "candidate": ObservationCandidate,
        "nuri":   list[ScaleMappingCandidate],   # scale == "nuri"
        "kicce":  list[ScaleMappingCandidate],   # scale == "kicce"
        "pseudonym_id": Optional[str],            # 매칭된 가명 ID (없으면 None)
        "final_record": Optional[FinalRecord],    # 이미 확정된 경우
      }
    """
    candidates = repo.list_candidates(video_id)
    matches = {m.temp_child_id: m.pseudonym_id for m in repo.list_child_matches(video_id)}
    finals = {fr.candidate_id: fr for fr in repo.list_final_records(video_id)}

    result: list[dict] = []
    for cand in candidates:
        mappings = repo.list_mappings(cand.id)
        result.append({
            "candidate": cand,
            "nuri": [m for m in mappings if m.scale == "nuri"],
            "kicce": [m for m in mappings if m.scale == "kicce"],
            "pseudonym_id": matches.get(cand.temp_child_id),
            "final_record": finals.get(cand.id),
        })
    return result


def list_final_records_for_video(video_id: str, repo) -> list[FinalRecord]:
    """영상의 확정 기록 목록을 반환한다."""
    return repo.list_final_records(video_id)


# ---------------------------------------------------------------------------
# child 매칭 (temp_child_id ↔ 연구용 가명 ID)
# ---------------------------------------------------------------------------

def set_child_match(
    video_id: str,
    temp_child_id: str,
    pseudonym_id: str,
    repo,
    actor: str = DEFAULT_ACTOR,
) -> ChildMatch:
    """임시 ID ↔ 가명 ID 매칭을 저장한다(동일 (video_id, temp_child_id) 는 덮어쓰기).

    pseudonym_id 는 실명이 아닌 연구용 가명 ID 여야 한다.
    """
    match = ChildMatch(
        id=f"cm_{video_id}_{temp_child_id}",
        video_id=video_id,
        temp_child_id=temp_child_id,
        pseudonym_id=pseudonym_id,
        matched_by=actor,
        matched_at=datetime.now(),
    )
    repo.set_child_match(match)

    repo.write_audit(AuditLog(
        id=f"audit_{video_id}_match_{uuid.uuid4().hex[:6]}",
        video_id=video_id,
        actor=actor,
        action="access",
        detail=f"set_child_match temp={temp_child_id} pseudonym={pseudonym_id}",
        created_at=datetime.now(),
    ))
    return match


# ---------------------------------------------------------------------------
# 영상 접근 로그 (플레이어 재생 시)
# ---------------------------------------------------------------------------

def log_video_access(video_id: str, repo, actor: str = DEFAULT_ACTOR) -> None:
    """원본 영상 접근(플레이어 재생)을 감사 로그에 기록한다."""
    repo.write_audit(AuditLog(
        id=f"audit_{video_id}_access_{uuid.uuid4().hex[:6]}",
        video_id=video_id,
        actor=actor,
        action="access",
        detail="video_player_access",
        created_at=datetime.now(),
    ))


# ---------------------------------------------------------------------------
# 후보 확정 (채택 / 수정 후 채택 / 기각)
# ---------------------------------------------------------------------------

def finalize_candidate(
    candidate_id: str,
    pseudonym_id: str,
    final_behavior: str,
    confirmed_areas: list[str],
    confirmed_items: list[KicceItemCandidate],
    decision: DecisionType,
    repo,
    actor: str = DEFAULT_ACTOR,
    video_id: Optional[str] = None,
    review_seconds: Optional[int] = None,
    evidence_adequacy: Optional[str] = None,
) -> FinalRecord:
    """관찰 후보를 교사 확정 기록(final_record)으로 저장한다.

    - decision: "accepted"(원문 채택) / "edited"(수정 채택) / "rejected"(기각).
    - edited 플래그는 decision == "edited" 일 때 True.
    - id 는 candidate_id 기반으로 고정해 같은 후보의 중복 확정을 방지한다(덮어쓰기).
    - review_seconds: 후보 열람~확정 경과(초, 선택). 워크플로우 분석용.
    - evidence_adequacy: AI 후보 시각적 근거 적절성(adequate|partial|inadequate, 선택).
      유아 발달/관찰수준 점수가 아님.
    - audit_log 를 반드시 함께 기록한다.
    """
    if decision not in ("accepted", "edited", "rejected"):
        raise ValueError(f"허용되지 않은 decision: {decision!r}")

    # audit_log 의 video_id 확보 (미지정 시 후보에서 역추적)
    if video_id is None:
        video_id = _resolve_video_id(candidate_id, repo)

    record = FinalRecord(
        id=f"final_{candidate_id}",
        candidate_id=candidate_id,
        pseudonym_id=pseudonym_id,
        final_behavior=final_behavior,
        confirmed_areas=confirmed_areas,
        confirmed_items=confirmed_items,
        decision=decision,
        edited=(decision == "edited"),
        confirmed_by=actor,
        confirmed_at=datetime.now(),
        review_seconds=review_seconds,
        evidence_adequacy=evidence_adequacy,
    )
    repo.save_final_record(record)

    repo.write_audit(AuditLog(
        id=f"audit_{video_id}_finalize_{uuid.uuid4().hex[:6]}",
        video_id=video_id,
        actor=actor,
        action="analyze",
        detail=(
            f"teacher_review_finalize decision={decision} candidate_id={candidate_id} "
            f"evidence={evidence_adequacy or '-'}"
        ),
        created_at=datetime.now(),
    ))
    return record


# ---------------------------------------------------------------------------
# 헬퍼
# ---------------------------------------------------------------------------

def mapping_to_kicce_item(m: ScaleMappingCandidate) -> KicceItemCandidate:
    """KICCE scale_mapping 후보를 KicceItemCandidate 로 변환한다(확정 저장용)."""
    return KicceItemCandidate(
        item_id=m.item_id,
        item_text=m.item_text,
        rationale=m.rationale,
        confidence=m.confidence,
    )


def _resolve_video_id(candidate_id: str, repo) -> str:
    """candidate_id 로부터 video_id 를 역추적한다(감사 로그용). 실패 시 candidate_id 사용."""
    # ObservationCandidate.id 규칙에 의존하지 않고 안전하게 후보를 직접 찾을 수는 없으므로,
    # 후보 자체는 video 단위로 조회되어야 하나 여기서는 보수적으로 빈 영상 추적을 시도한다.
    # candidate 는 list_candidates(video_id) 로만 조회 가능하므로,
    # 호출부에서 video_id 를 넘기는 것을 권장한다(미지정 시 candidate_id 를 식별자로 사용).
    return candidate_id
