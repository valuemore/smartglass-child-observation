"""AI비서 — 제한형 (V2-7).

허용 기능 3가지로 **엄격히 제한**한다:
  1. search       : 확정·후보 관찰기록 검색
  2. edit_assist  : 초안 문구 수정 보조(내용 생성 아님, 정리·다듬기만)
  3. shoot_suggest: 보완 촬영 제안(수집 균형 기반)

금지(거부): 새 관찰기록 자동 생성/작성, 자동 확정, 점수·발달·평정 산출, 유아 실명·신원 식별.
모든 상호작용은 ai_assistant_log 에 기록한다.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime

from core.config import DEFAULT_ACTOR
from core.schemas import AiAssistantLog
from storage.sqlite_repository import SqliteRepository

_SAFETY_NOTE = (
    "ℹ️ AI비서는 검색·초안 수정 보조·보완 촬영 제안만 합니다. "
    "기록을 새로 만들거나 확정하지 않으며, 점수·신원을 산출하지 않습니다."
)

# 금지 요청 패턴 (사유, 정규식)
_BANNED = [
    ("점수·평정·발달 산출은 하지 않습니다.", r"점수|평정|등급|몇\s*점|발달\s*점수|레벨"),
    ("기록 확정은 교사만 할 수 있습니다.", r"확정\s*해|대신\s*확정|자동\s*확정"),
    ("유아 실명·신원 식별은 하지 않습니다.", r"실명|본명|진짜\s*이름|누구(인지|야|예요)"),
    ("새 관찰기록 작성·생성은 하지 않습니다(초안은 ‘주간 관찰초안’에서 생성).",
     r"기록.*(작성|생성|만들)|관찰기록.*(써|작성)|새.*기록"),
]

_INTENT_RULES = [
    ("shoot_suggest", r"촬영|보완|부족|다음.*찍|무엇을\s*찍|뭘\s*찍|찍어야"),
    ("edit_assist", r"수정|다듬|문장|고쳐|정리|초안.*(고|수정)|표현"),
    ("search", r"찾|검색|조회|보여|어디|기록.*(있|보)|확정.*기록"),
]


def classify_intent(query: str) -> str:
    """질의를 허용 intent 중 하나로 분류한다. 모호하면 search(가장 안전)."""
    for intent, pat in _INTENT_RULES:
        if re.search(pat, query):
            return intent
    return "search"


def check_banned(query: str) -> str | None:
    """금지 요청이면 사유를 반환, 아니면 None."""
    for reason, pat in _BANNED:
        if re.search(pat, query):
            return reason
    return None


def _search_records(repo: SqliteRepository, query: str, owner: str | None) -> str:
    """확정 기록·관찰 후보에서 키워드/가명/영역으로 검색한다."""
    finals = repo.list_final_records()
    q = query.strip()
    # 키워드: 한글/영문 토큰
    tokens = [t for t in re.split(r"[\s,]+", q) if len(t) >= 2]
    hits = []
    for f in finals:
        hay = " ".join([f.pseudonym_id, f.final_behavior or "", " ".join(f.confirmed_areas)])
        if any(t in hay for t in tokens) or not tokens:
            hits.append(f)
    hits = hits[:10]
    if not hits:
        return "검색 결과가 없습니다. 가명 ID·영역·행동 키워드로 다시 시도해보세요."
    lines = [f"확정 기록 {len(hits)}건 (최대 10건 표시):"]
    for f in hits:
        areas = ", ".join(f.confirmed_areas) or "-"
        period = f"{f.period_start}~{f.period_end}" if f.period_start else ""
        lines.append(f"- [{f.pseudonym_id}] {areas} {period}: {(f.final_behavior or '')[:60]}")
    return "\n".join(lines)


def _edit_assist(query: str) -> str:
    """초안 문구 정리 보조. 내용을 생성하지 않고 정리·구조 제안만 한다."""
    # 질의에서 따옴표로 감싼 텍스트가 있으면 그 텍스트를 정리 대상으로 본다.
    m = re.search(r"[\"'“”'‘](.+?)[\"'“”'’]", query, re.S)
    target = m.group(1) if m else query
    cleaned = re.sub(r"\s+", " ", target).strip()
    cleaned = cleaned.rstrip(" .") + "."
    return (
        "초안 정리 제안 (내용은 추가하지 않았습니다):\n"
        f"  {cleaned}\n"
        "권장 구조: ‘관찰 행동 → 상호작용/맥락 → 시각적 근거’ 순으로 다듬어 보세요. "
        "최종 문구는 교사가 확정합니다."
    )


def _shoot_suggest(repo: SqliteRepository, owner: str | None) -> str:
    """수집 균형을 바탕으로 보완 촬영을 제안한다."""
    from services.dashboard_service import collection_status
    status = collection_status(repo, class_id=None, owner=owner)
    if status["total_candidates"] == 0:
        return "아직 누적된 관찰 후보가 없습니다. 먼저 ‘일일 영상 기록’에서 영상을 업로드하세요."
    notes = status["shortage_notes"]
    if not notes:
        return "현재 기준에서 부족한 영역·유아가 없습니다. 균형 있게 수집되고 있습니다."
    return "보완 촬영 제안:\n" + "\n".join(f"- {n}" for n in notes[:6])


def handle_query(
    repo: SqliteRepository,
    actor: str,
    query: str,
    owner: str | None = None,
) -> dict:
    """질의를 처리한다. 반환: {intent, response, refused}. 항상 감사 로그를 남긴다."""
    query = (query or "").strip()
    intent = classify_intent(query)
    refused = False

    banned_reason = check_banned(query)
    if banned_reason or not query:
        refused = True
        response = (banned_reason or "질문을 입력해주세요.") + "\n" + _SAFETY_NOTE
    elif intent == "search":
        response = _search_records(repo, query, owner) + "\n\n" + _SAFETY_NOTE
    elif intent == "edit_assist":
        response = _edit_assist(query) + "\n\n" + _SAFETY_NOTE
    else:  # shoot_suggest
        response = _shoot_suggest(repo, owner) + "\n\n" + _SAFETY_NOTE

    repo.write_assistant_log(AiAssistantLog(
        id=f"ai_{uuid.uuid4().hex[:10]}",
        actor=actor or DEFAULT_ACTOR,
        query=query[:500],
        intent=intent,
        response_summary=("refused: " + (banned_reason or "empty")) if refused else response[:200],
        created_at=datetime.now(),
    ))
    return {"intent": intent, "response": response, "refused": refused}
