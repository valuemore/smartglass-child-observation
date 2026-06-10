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
from services.security_service import assert_no_sensitive_paths

# 감사 로그 완전성 검사 대상 액션 (core.schemas.AuditAction과 동일)
_AUDIT_ACTIONS = ("upload", "access", "analyze", "export", "delete")


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
    report = {
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

    # P-A: 전처리 카운트 · AI 후보 유지율 · 감사 완전성 (기존 데이터만으로 산출)
    report.update(calculate_preprocessing_counts(video_id, repo))
    report["candidate_retention"] = calculate_candidate_retention(candidates, finals, repo)
    report["review_effort"] = calculate_review_effort(finals)
    report["audit_completeness"] = calculate_audit_completeness(video_id, repo)
    return report


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


def calculate_preprocessing_counts(video_id: str, repo) -> dict:
    """영상 전처리 카운트를 반환한다.

    반환: scene_count, frame_count, kept_frame_count
    """
    scenes = repo.list_scenes(video_id)
    frame_count = 0
    kept_frame_count = 0
    for sc in scenes:
        frames = repo.list_frames(sc.id)
        frame_count += len(frames)
        kept_frame_count += sum(1 for f in frames if f.kept)
    return {
        "scene_count": len(scenes),
        "frame_count": frame_count,
        "kept_frame_count": kept_frame_count,
    }


def calculate_candidate_retention(
    candidates: list[ObservationCandidate],
    finals: list[FinalRecord],
    repo,
) -> dict:
    """AI가 제시한 누리 영역·KICCE 문항 후보 중 교사가 확정에 유지한 비율을 반환한다.

    - AI 제시 후보 출처: scale_mapping(repo.list_mappings) ∪ 후보 자체 필드.
      (Mock+매핑 경로는 scale_mapping에, 외부 API 경로는 후보 필드에 담길 수 있어 합집합 사용)
    - 분모: 검토된 후보(final_record 존재)의 AI 제시 후보 수. 기각 후보는 유지 0으로 포함.
    - AI 성능 점수가 아니라 교사 판단 기여도(워크플로우) 지표다.
    """
    cand_map = {c.id: c for c in candidates}
    nuri_sug = nuri_ret = kicce_sug = kicce_ret = 0
    reviewed = 0

    def _item_key(item) -> object:
        item_id = getattr(item, "item_id", None)
        return item_id if item_id is not None else getattr(item, "item_text", "")

    for fr in finals:
        cand = cand_map.get(fr.candidate_id)
        if cand is None:
            continue
        reviewed += 1

        mappings = repo.list_mappings(fr.candidate_id)

        # AI 제시 누리 영역 (scale_mapping ∪ 후보 필드)
        ai_areas = {m.area for m in mappings if m.scale == "nuri" and m.area}
        ai_areas |= {n.area for n in cand.nuri_area_candidates if n.area}
        conf_areas = set(fr.confirmed_areas or [])

        # AI 제시 KICCE 문항 (item_id 우선, 없으면 item_text)
        ai_items = {_item_key(m) for m in mappings if m.scale == "kicce"}
        ai_items |= {_item_key(k) for k in cand.kicce_item_candidates}
        conf_items = {_item_key(i) for i in fr.confirmed_items}

        nuri_sug += len(ai_areas)
        nuri_ret += len(ai_areas & conf_areas)
        kicce_sug += len(ai_items)
        kicce_ret += len(ai_items & conf_items)

    return {
        "reviewed_candidates": reviewed,
        "nuri_suggested": nuri_sug,
        "nuri_retained": nuri_ret,
        "nuri_retention_rate": round(nuri_ret / nuri_sug, 4) if nuri_sug else 0.0,
        "kicce_suggested": kicce_sug,
        "kicce_retained": kicce_ret,
        "kicce_retention_rate": round(kicce_ret / kicce_sug, 4) if kicce_sug else 0.0,
    }


def calculate_review_effort(finals: list[FinalRecord]) -> dict:
    """교사 검토 워크플로우 지표를 반환한다(유아 평가 아님).

    - review_seconds_avg / review_seconds_total: 값이 기록된 확정만 집계.
    - evidence_adequacy_distribution: adequate/partial/inadequate/unrated 카운트.
    """
    seconds = [f.review_seconds for f in finals if f.review_seconds is not None]
    total = sum(seconds)
    avg = round(total / len(seconds), 1) if seconds else None

    dist = {"adequate": 0, "partial": 0, "inadequate": 0, "unrated": 0}
    for f in finals:
        key = f.evidence_adequacy if f.evidence_adequacy in dist else "unrated"
        dist[key] += 1

    return {
        "review_seconds_total": total,
        "review_seconds_avg": avg,
        "reviewed_with_timing": len(seconds),
        "evidence_adequacy_distribution": dist,
    }


def calculate_audit_completeness(video_id: str, repo) -> dict:
    """영상 감사 로그에 5종 액션이 모두 기록됐는지 점검한다(진단용).

    반환: {action: {"present": bool, "count": int}, ..., "missing_actions": [..]}
    주의: access 등 미계측 액션은 missing으로 표시되며 이는 계측 공백을 드러내는 정상 결과다.
    """
    logs = repo.list_audit_logs(video_id)
    result: dict = {}
    for action in _AUDIT_ACTIONS:
        count = sum(1 for lg in logs if lg.action == action)
        result[action] = {"present": count > 0, "count": count}
    result["missing_actions"] = [a for a in _AUDIT_ACTIONS if result[a]["count"] == 0]
    return result


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
# 클래스 단위 지원도 리포트 (V2-8)
# ---------------------------------------------------------------------------

def build_class_report(class_id: str, repo) -> dict:
    """클래스 단위 AI 지원도 리포트를 구성한다(주차별 누적 확정 기반).

    '지원도'는 AI 성능 평가가 아니라, AI 후보가 교사의 기록 작성을 얼마나 도왔는지를
    보는 워크플로우 지표다(채택·수정 활용률, 후보 유지율, 신뢰도 구간별 활용률).
    """
    group = repo.get_class(class_id)
    videos = [v for v in repo.list_videos() if v.class_id == class_id]

    all_candidates: list[ObservationCandidate] = []
    all_finals: list[FinalRecord] = []
    for v in videos:
        all_candidates.extend(repo.list_candidates(v.id))
        all_finals.extend(repo.list_final_records(v.id))
    cand_map = {c.id: c for c in all_candidates}

    total_cands = len(all_candidates)
    total_finals = len(all_finals)
    accepted = sum(1 for f in all_finals if f.decision == "accepted")
    edited = sum(1 for f in all_finals if f.decision == "edited")
    rejected = sum(1 for f in all_finals if f.decision == "rejected")
    base = total_cands if total_cands > 0 else 1

    return {
        "class_id": class_id,
        "class_name": group.name if group else "",
        "total_videos": len(videos),
        "total_candidates": total_cands,
        "total_finals": total_finals,
        "accepted": accepted,
        "edited": edited,
        "rejected": rejected,
        "unreviewed": total_cands - total_finals,
        "acceptance_rate": round(accepted / base, 4),
        "edit_rate": round(edited / base, 4),
        "rejection_rate": round(rejected / base, 4),
        "support_metrics": calculate_support_metrics(all_candidates, all_finals),
        "candidate_retention": calculate_candidate_retention(all_candidates, all_finals, repo),
        "area_distribution": calculate_area_distribution(all_finals),
        "kicce_coverage": calculate_kicce_coverage(all_finals),
        "by_period": calculate_by_period(all_finals),
        "by_pseudonym": _group_by_pseudonym(all_finals, cand_map),
    }


def calculate_support_metrics(
    candidates: list[ObservationCandidate],
    finals: list[FinalRecord],
) -> dict:
    """AI 지원도 지표. AI 성능 점수가 아닌 교사 기록작성 지원 워크플로우 지표.

    - ai_support_ratio: 검토된 후보 중 AI 후보를 (그대로 채택 또는 수정해) 활용한 비율.
    - confidence_band_usage: 신뢰도 구간(high/mid/low)별 활용률(기각 아닌 비율).
    """
    cand_map = {c.id: c for c in candidates}
    reviewed = len(finals)
    used = sum(1 for f in finals if f.decision in ("accepted", "edited"))

    bands = {
        "high": {"reviewed": 0, "used": 0},
        "mid": {"reviewed": 0, "used": 0},
        "low": {"reviewed": 0, "used": 0},
    }
    for f in finals:
        c = cand_map.get(f.candidate_id)
        if c is None:
            continue
        band = "high" if c.confidence >= 0.7 else "mid" if c.confidence >= 0.4 else "low"
        bands[band]["reviewed"] += 1
        if f.decision != "rejected":
            bands[band]["used"] += 1
    for b in bands.values():
        b["usage_rate"] = round(b["used"] / b["reviewed"], 4) if b["reviewed"] else 0.0

    return {
        "reviewed": reviewed,
        "used": used,
        "ai_support_ratio": round(used / reviewed, 4) if reviewed else 0.0,
        "confidence_band_usage": bands,
    }


def calculate_by_period(finals: list[FinalRecord]) -> list[dict]:
    """주차(기간)별 확정 집계. period 정보가 없는 확정은 'unscheduled'로 묶는다."""
    buckets: dict[tuple, dict] = {}
    for f in finals:
        key = (f.period_start or "", f.period_end or "")
        b = buckets.setdefault(key, {
            "period_start": f.period_start, "period_end": f.period_end,
            "accepted": 0, "edited": 0, "rejected": 0, "total": 0,
        })
        b["total"] += 1
        if f.decision in b:
            b[f.decision] += 1
    return sorted(buckets.values(), key=lambda x: (x["period_start"] or ""))


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
            "scene_count": report["scene_count"],
            "frame_count": report["frame_count"],
            "kept_frame_count": report["kept_frame_count"],
        },
        "candidate_retention": report["candidate_retention"],
        "review_effort": report["review_effort"],
        "audit_completeness": report["audit_completeness"],
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

    # defense-in-depth: 향후 필드 추가로 민감 경로가 새면 export를 즉시 중단
    assert_no_sensitive_paths(export_data)

    repo.write_audit(AuditLog(
        id=f"audit_{video_id}_export_{uuid.uuid4().hex[:6]}",
        video_id=video_id,
        actor=actor,
        action="export",
        detail="research_report_export_json",
        created_at=datetime.now(),
    ))
    return json.dumps(export_data, ensure_ascii=False, indent=2)


def export_class_report_json(class_id: str, repo, actor: str = DEFAULT_ACTOR) -> str:
    """클래스 지원도 리포트를 JSON 문자열로 직렬화한다.

    - 미디어 경로(영상·프레임·클립·얼굴 참조사진)는 포함하지 않는다.
    - 유아 실명 없음(pseudonym_id 기준). export 실행 시 audit_log 기록.
    """
    report = build_class_report(class_id, repo)
    export_data = {
        "export_at": datetime.now().isoformat(),
        "class_id": report["class_id"],
        "class_name": report["class_name"],
        "summary": {
            k: report[k] for k in (
                "total_videos", "total_candidates", "total_finals",
                "accepted", "edited", "rejected", "unreviewed",
                "acceptance_rate", "edit_rate", "rejection_rate",
            )
        },
        "support_metrics": report["support_metrics"],
        "candidate_retention": report["candidate_retention"],
        "area_distribution": report["area_distribution"],
        "kicce_coverage": report["kicce_coverage"],
        "by_period": report["by_period"],
        "by_pseudonym": {
            pid: [
                {k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in rec.items()}
                for rec in recs
            ]
            for pid, recs in report["by_pseudonym"].items()
        },
    }
    # defense-in-depth: 미디어 경로(영상·프레임·클립·얼굴)가 새면 즉시 중단
    assert_no_sensitive_paths(export_data)

    repo.write_audit(AuditLog(
        id=f"audit_{class_id}_export_{uuid.uuid4().hex[:6]}",
        video_id=class_id, actor=actor, action="export",
        detail="research_class_report_export_json",
        created_at=datetime.now(),
    ))
    return json.dumps(export_data, ensure_ascii=False, indent=2)


def export_report_csv(video_id: str, repo, actor: str = DEFAULT_ACTOR) -> str:
    """확정 기록을 CSV 문자열로 직렬화한다.

    - 원본 영상 경로·프레임 이미지 경로는 포함하지 않는다.
    - export 실행 시 audit_log에 기록한다.
    """
    report = build_video_report(video_id, repo)

    # defense-in-depth: CSV 행 출처 데이터에 민감 경로가 없는지 사전 검증
    assert_no_sensitive_paths(report["by_pseudonym"])

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
