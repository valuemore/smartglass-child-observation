"""연구자 리포트 서비스.

원칙:
- Repository 인터페이스만 사용한다(SQLite 직접 접근 금지).
- 관찰수준 점수(score/level/rating)는 산출하지 않는다.
- export에는 원본 영상 경로·프레임 이미지 경로를 포함하지 않는다.
- 유아 실명 없음. pseudonym_id 기준으로 리포트를 구성한다.
- "AI 정확도" 표현 금지. "AI 후보 대비 교사 검토 결과"로 표기.
"""

from __future__ import annotations

import csv
import io
import json
import uuid
from collections import defaultdict
from datetime import datetime
from typing import Optional

from core.config import DEFAULT_ACTOR
from core.schemas import AuditLog, FinalRecord, ObservationCandidate


# ---------------------------------------------------------------------------
# 핵심 리포트 구성
# ---------------------------------------------------------------------------

def build_video_report(video_id: str, repo) -> dict:
    """교사 확정 기록 기반 연구자 리포트 데이터를 구성한다.

    반환 키:
      video_id, filename, duration_sec,
      total_candidates, total_finals,
      accepted, edited, rejected, unreviewed,
      acceptance_rate, edit_rate, rejection_rate,
      area_distribution, kicce_coverage, by_pseudonym
    """
    video = repo.get_video(video_id)
    candidates: list[ObservationCandidate] = repo.list_candidates(video_id)
    finals: list[FinalRecord] = repo.list_final_records(video_id)

    cand_map: dict[str, ObservationCandidate] = {c.id: c for c in candidates}

    total_cands = len(candidates)
    total_finals = len(finals)
    accepted = sum(1 for f in finals if f.decision == "accepted")
    edited = sum(1 for f in finals if f.decision == "edited")
    rejected = sum(1 for f in finals if f.decision == "rejected")
    unreviewed = total_cands - total_finals

    base = total_cands if total_cands > 0 else 1
    return {
        "video_id": video_id,
        "filename": video.filename if video else "",
        "duration_sec": video.duration_sec if video else 0.0,
        "total_candidates": total_cands,
        "total_finals": total_finals,
        "accepted": accepted,
        "edited": edited,
        "rejected": rejected,
        "unreviewed": unreviewed,
        "acceptance_rate": round(accepted / base, 4),
        "edit_rate": round(edited / base, 4),
        "rejection_rate": round(rejected / base, 4),
        "area_distribution": calculate_area_distribution(finals),
        "kicce_coverage": calculate_kicce_coverage(finals),
        "by_pseudonym": _group_by_pseudonym(finals, cand_map),
    }


# ---------------------------------------------------------------------------
# 분석 함수
# ---------------------------------------------------------------------------

def calculate_area_distribution(final_records: list[FinalRecord]) -> dict[str, int]:
    """확정 기록의 confirmed_areas 기준으로 누리 영역별 카운트를 반환한다."""
    counts: dict[str, int] = defaultdict(int)
    for fr in final_records:
        for area in fr.confirmed_areas:
            if area:
                counts[area] += 1
    return dict(counts)


def calculate_kicce_coverage(final_records: list[FinalRecord]) -> list[dict]:
    """확정 기록의 confirmed_items 기준으로 KICCE 문항별 카운트를 반환한다.

    반환: [{"item_id": int|None, "item_text": str, "count": int}, ...]
    """
    item_counts: dict[tuple, dict] = {}
    for fr in final_records:
        for item in fr.confirmed_items:
            key = (item.item_id, item.item_text)
            if key not in item_counts:
                item_counts[key] = {
                    "item_id": item.item_id,
                    "item_text": item.item_text,
                    "count": 0,
                }
            item_counts[key]["count"] += 1
    return sorted(item_counts.values(), key=lambda x: -x["count"])


def calculate_ai_teacher_comparison(
    candidates: list[ObservationCandidate],
    finals: list[FinalRecord],
) -> dict:
    """AI 후보 대비 교사 검토 결과 통계를 반환한다.

    이 지표는 AI 성능 평가가 아닌 교사 검토 워크플로우 분석용이다.
    """
    total = len(candidates)
    accepted = sum(1 for f in finals if f.decision == "accepted")
    edited = sum(1 for f in finals if f.decision == "edited")
    rejected = sum(1 for f in finals if f.decision == "rejected")
    reviewed = len(finals)
    base = total if total > 0 else 1
    return {
        "total_candidates": total,
        "reviewed": reviewed,
        "accepted": accepted,
        "edited": edited,
        "rejected": rejected,
        "unreviewed": total - reviewed,
        "acceptance_rate": round(accepted / base, 4),
        "edit_rate": round(edited / base, 4),
        "rejection_rate": round(rejected / base, 4),
    }


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

def export_report_json(video_id: str, repo, actor: str = DEFAULT_ACTOR) -> str:
    """리포트 데이터를 JSON 문자열로 직렬화한다.

    - 원본 영상 경로·프레임 이미지 경로는 포함하지 않는다.
    - export 실행 시 audit_log에 기록한다.
    """
    report = build_video_report(video_id, repo)

    export_data = {
        "export_at": datetime.now().isoformat(),
        "video_id": report["video_id"],
        "filename": report["filename"],
        "duration_sec": report["duration_sec"],
        "summary": {
            "total_candidates": report["total_candidates"],
            "total_finals": report["total_finals"],
            "accepted": report["accepted"],
            "edited": report["edited"],
            "rejected": report["rejected"],
            "unreviewed": report["unreviewed"],
            "acceptance_rate": report["acceptance_rate"],
            "edit_rate": report["edit_rate"],
            "rejection_rate": report["rejection_rate"],
        },
        "area_distribution": report["area_distribution"],
        "kicce_coverage": report["kicce_coverage"],
        "by_pseudonym": {
            pid: [
                {
                    k: (v.isoformat() if isinstance(v, datetime) else v)
                    for k, v in rec.items()
                }
                for rec in recs
            ]
            for pid, recs in report["by_pseudonym"].items()
        },
    }

    repo.write_audit(AuditLog(
        id=f"audit_{video_id}_export_{uuid.uuid4().hex[:6]}",
        video_id=video_id,
        actor=actor,
        action="export",
        detail="research_report_export_json",
        created_at=datetime.now(),
    ))
    return json.dumps(export_data, ensure_ascii=False, indent=2)


def export_report_csv(video_id: str, repo, actor: str = DEFAULT_ACTOR) -> str:
    """확정 기록을 CSV 문자열로 직렬화한다.

    - 원본 영상 경로·프레임 이미지 경로는 포함하지 않는다.
    - export 실행 시 audit_log에 기록한다.
    """
    report = build_video_report(video_id, repo)

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "pseudonym_id", "time_start", "time_end",
        "final_behavior", "confirmed_areas",
        "confirmed_item_ids", "confirmed_item_texts",
        "decision", "edited", "confirmed_at",
    ])

    for pid, recs in report["by_pseudonym"].items():
        for rec in recs:
            areas_str = "|".join(rec.get("confirmed_areas") or [])
            items = rec.get("confirmed_items") or []
            item_ids = "|".join(
                str(i.get("item_id", "") if isinstance(i, dict) else getattr(i, "item_id", ""))
                for i in items
            )
            item_texts = "|".join(
                (i.get("item_text", "") if isinstance(i, dict) else getattr(i, "item_text", ""))
                for i in items
            )
            confirmed_at = rec.get("confirmed_at", "")
            if isinstance(confirmed_at, datetime):
                confirmed_at = confirmed_at.isoformat()
            writer.writerow([
                pid,
                rec.get("time_start", ""),
                rec.get("time_end", ""),
                rec.get("final_behavior", ""),
                areas_str,
                item_ids,
                item_texts,
                rec.get("decision", ""),
                rec.get("edited", False),
                confirmed_at,
            ])

    repo.write_audit(AuditLog(
        id=f"audit_{video_id}_export_{uuid.uuid4().hex[:6]}",
        video_id=video_id,
        actor=actor,
        action="export",
        detail="research_report_export_csv",
        created_at=datetime.now(),
    ))
    return output.getvalue()


# ---------------------------------------------------------------------------
# 헬퍼
# ---------------------------------------------------------------------------

def _group_by_pseudonym(
    finals: list[FinalRecord],
    cand_map: dict[str, ObservationCandidate],
) -> dict[str, list[dict]]:
    """pseudonym_id별로 확정 기록을 그룹화한다."""
    groups: dict[str, list[dict]] = defaultdict(list)
    for fr in finals:
        cand = cand_map.get(fr.candidate_id)
        groups[fr.pseudonym_id].append({
            "candidate_id": fr.candidate_id,
            "time_start": cand.time_start if cand else None,
            "time_end": cand.time_end if cand else None,
            "final_behavior": fr.final_behavior,
            "confirmed_areas": fr.confirmed_areas,
            "confirmed_items": [
                {
                    "item_id": i.item_id,
                    "item_text": i.item_text,
                    "rationale": i.rationale,
                    "confidence": i.confidence,
                }
                for i in fr.confirmed_items
            ],
            "decision": fr.decision,
            "edited": fr.edited,
            "confirmed_at": fr.confirmed_at,
        })
    return dict(groups)
