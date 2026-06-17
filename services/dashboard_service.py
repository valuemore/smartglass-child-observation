"""수집 균형 대시보드 서비스 (V2-5).

유아별·누리영역별 관찰자료 수집 현황을 집계해 부족한 영역·유아를 드러낸다.

원칙:
- Repository 인터페이스만 사용한다(SQLite 직접 접근 금지).
- 점수(score/level/rating)·발달 평가가 아니다. **관찰자료 수집량**만 집계한다.
- 유아 실명 없음. pseudonym_id(매칭됨) 또는 temp_child_id(미매칭) 기준.
- 매칭(child_match)이 없는 후보는 별도 '미매칭' 집계로 분리해 잘못된 병합을 피한다.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Optional

from core.config import DASHBOARD_MIN_PER_AREA

# 누리과정 5영역 정식 명칭 (resources/nuri_areas.json 과 일치)
NURI_AREAS: list[str] = ["신체운동·건강", "의사소통", "사회관계", "예술경험", "자연탐구"]


def _candidate_areas(candidate, mappings: list) -> set[str]:
    """관찰 후보의 누리 영역 집합. scale_mapping(nuri) 우선, 없으면 후보의 nuri_area_candidates."""
    areas = {m.area for m in mappings if m.scale == "nuri" and m.area in NURI_AREAS}
    if not areas:
        areas = {n.area for n in candidate.nuri_area_candidates if n.area in NURI_AREAS}
    return areas


def _empty_cells() -> dict:
    return {a: {"count": 0, "last_observed": None} for a in NURI_AREAS}


def _newer(a: Optional[str], b: Optional[str]) -> Optional[str]:
    """YYYY-MM-DD 문자열 중 더 최근 값(사전식 비교). None 안전."""
    if a is None:
        return b
    if b is None:
        return a
    return max(a, b)


def collection_status(
    repo,
    *,
    class_id: Optional[str] = None,
    owner: Optional[str] = None,
    min_per_area: Optional[int] = None,
) -> dict:
    """유아×누리영역 수집 현황 매트릭스를 구성한다.

    class_id 가 주어지면 해당 클래스 영상만, owner 가 주어지면 해당 교사 영상만 집계한다.
    (V2-2 클래스 등록 전에는 class_id=None 으로 미분류 영상까지 집계 가능.)

    반환 dict 주요 키:
      class_id, areas, min_per_area,
      children: [{child_key, label, resolved, cells{area:{count,last_observed,shortage}}, total, shortage_areas}],
      unmatched: {cells, total},                # 매칭 안 된 후보 집계
      area_totals: {area: count},               # 반 전체(매칭+미매칭) 영역별 합
      area_shortage: [area, ...],               # 반 전체에서 부족한 영역
      shortage_notes: [str, ...],               # 보완 안내 문장
      total_videos, total_candidates, matched_candidates, unmatched_candidates
    """
    threshold = min_per_area if min_per_area is not None else DASHBOARD_MIN_PER_AREA

    videos = repo.list_videos(owner=owner)
    if class_id is not None:
        videos = [v for v in videos if v.class_id == class_id]

    # 등록 유아(있으면) 기준 행을 미리 만든다 — 자료가 0인 유아도 노출되어야 한다.
    children_cells: dict[str, dict] = {}
    children_label: dict[str, str] = {}
    if class_id is not None:
        try:
            for ch in repo.list_children(class_id):
                children_cells[ch.pseudonym_id] = _empty_cells()
                children_label[ch.pseudonym_id] = ch.display_label or ch.pseudonym_id
        except Exception:
            pass  # child 테이블 미존재 등은 무시(미분류 모드)

    unmatched_cells = _empty_cells()
    total_candidates = 0
    matched_candidates = 0
    unmatched_candidates = 0
    no_area_candidates = 0  # 누리영역이 하나도 분류되지 않은 후보 수

    for v in videos:
        match_map = {m.temp_child_id: m.pseudonym_id for m in repo.list_child_matches(v.id)}
        observed_date = v.captured_date  # YYYY-MM-DD (없으면 None)
        for cand in repo.list_candidates(v.id):
            total_candidates += 1
            areas = _candidate_areas(cand, repo.list_mappings(cand.id))
            if not areas:
                no_area_candidates += 1
            obs = observed_date or (cand.created_at.strftime("%Y-%m-%d") if cand.created_at else None)

            pseudonym = match_map.get(cand.temp_child_id)
            if pseudonym is not None:
                matched_candidates += 1
                cells = children_cells.setdefault(pseudonym, _empty_cells())
                children_label.setdefault(pseudonym, pseudonym)
                target = cells
            else:
                unmatched_candidates += 1
                target = unmatched_cells

            for area in areas:
                target[area]["count"] += 1
                target[area]["last_observed"] = _newer(target[area]["last_observed"], obs)

    # 부족 플래그·합계 계산
    def _finalize(cells: dict) -> tuple[dict, int, list[str]]:
        total = 0
        shortage_areas = []
        for area in NURI_AREAS:
            cnt = cells[area]["count"]
            total += cnt
            cells[area]["shortage"] = cnt < threshold
            if cnt < threshold:
                shortage_areas.append(area)
        return cells, total, shortage_areas

    children = []
    for key, cells in children_cells.items():
        cells, total, shortage_areas = _finalize(cells)
        children.append({
            "child_key": key,
            "label": children_label.get(key, key),
            "resolved": True,
            "cells": cells,
            "total": total,
            "shortage_areas": shortage_areas,
        })
    children.sort(key=lambda c: c["label"])

    unmatched_cells, unmatched_total, _ = _finalize(unmatched_cells)

    # 반 전체 영역별 합 (매칭 + 미매칭)
    area_totals = {a: 0 for a in NURI_AREAS}
    for c in children:
        for a in NURI_AREAS:
            area_totals[a] += c["cells"][a]["count"]
    for a in NURI_AREAS:
        area_totals[a] += unmatched_cells[a]["count"]
    area_shortage = [a for a in NURI_AREAS if area_totals[a] < threshold]

    # 보완 안내 문장
    notes: list[str] = []
    for a in area_shortage:
        notes.append(
            f"반 전체에서 '{a}' 영역 관찰자료가 부족합니다 ({area_totals[a]}건, 권장 {threshold}건 이상). "
            f"다음 촬영에서 보완하세요."
        )
    for c in children:
        if c["shortage_areas"]:
            notes.append(
                f"'{c['label']}' 유아의 부족 영역: {', '.join(c['shortage_areas'])}. "
                f"해당 활동을 다음 촬영에서 보완하세요."
            )

    return {
        "class_id": class_id,
        "areas": NURI_AREAS,
        "min_per_area": threshold,
        "children": children,
        "unmatched": {"cells": unmatched_cells, "total": unmatched_total},
        "area_totals": area_totals,
        "area_shortage": area_shortage,
        "shortage_notes": notes,
        "total_videos": len(videos),
        "total_candidates": total_candidates,
        "matched_candidates": matched_candidates,
        "unmatched_candidates": unmatched_candidates,
        "no_area_candidates": no_area_candidates,
    }
