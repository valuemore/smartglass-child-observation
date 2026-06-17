"""주간/격주 관찰기록 초안 서비스 (V2-6).

누적된 관찰 후보를 유아(가명)·누리영역별로 묶어 **초안(후보)** 을 생성한다.

원칙:
- Repository 인터페이스만 사용한다.
- 초안은 후보다. 교사가 검토·수정·확정(final_record)하기 전에는 기록이 아니다.
- 점수·발달·평정을 산출하지 않는다.
- 매칭(child_match)으로 가명에 연결된 후보만 유아별 초안에 포함한다(미매칭 제외).
- draft_text 는 결정적 템플릿 요약이다. LLM 기반 생성은 어댑터로 교체 가능(향후).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from core.config import DEFAULT_ACTOR
from core.schemas import AuditLog, WeeklyDraft
from services.dashboard_service import NURI_AREAS
from storage.sqlite_repository import SqliteRepository

# 영역 → 결정적 인덱스(초안 id 생성용)
_AREA_IDX = {a: i for i, a in enumerate(NURI_AREAS)}
# 대표 근거 클립 최대 개수
_MAX_REP_CLIPS = 3
# 초안 요약에 포함할 행동 서술 최대 개수
_MAX_BEHAVIORS = 5


def _candidate_areas(candidate, mappings: list) -> set[str]:
    areas = {m.area for m in mappings if m.scale == "nuri" and m.area in NURI_AREAS}
    if not areas:
        areas = {n.area for n in candidate.nuri_area_candidates if n.area in NURI_AREAS}
    return areas


def _in_period(captured: Optional[str], start: str, end: str) -> bool:
    if not captured:
        return False
    return start <= captured <= end


def build_interim_drafts(
    repo: SqliteRepository,
    class_id: str,
    owner: Optional[str] = None,
) -> list[dict]:
    """매칭된 후보를 유아·누리영역별로 묶어 **중간 관찰 초안**(미리보기)을 만든다.

    수집균형 '보완 안내'에서 즉시 보여주기 위한 **읽기 전용** 함수.
    DB에 저장하지 않고 감사 로그도 남기지 않는다(확정은 주간초안에서).

    반환: [{"pseudonym_id", "label", "total", "areas": [{"area","behaviors","count"}]}]
          유아 라벨 기준 정렬, 영역은 NURI_AREAS 순서.
    """
    children = {c.pseudonym_id: c for c in repo.list_children(class_id)}
    videos = [v for v in repo.list_videos(owner=owner) if v.class_id == class_id]

    # (pid, area) -> [cands]
    groups: dict[tuple[str, str], list] = {}
    for v in videos:
        matches = {m.temp_child_id: m.pseudonym_id for m in repo.list_child_matches(v.id)}
        for cand in repo.list_candidates(v.id):
            pid = matches.get(cand.temp_child_id)
            if pid is None:
                continue
            for area in _candidate_areas(cand, repo.list_mappings(cand.id)):
                groups.setdefault((pid, area), []).append(cand)

    by_child: dict[str, dict] = {}
    for (pid, area), cands in groups.items():
        ordered = sorted(cands, key=lambda c: c.confidence, reverse=True)
        behaviors: list[str] = []
        seen: set[str] = set()
        for c in ordered:
            b = c.observed_behavior.strip()
            if b and b not in seen:
                seen.add(b)
                behaviors.append(b)
            if len(behaviors) >= _MAX_BEHAVIORS:
                break
        entry = by_child.setdefault(pid, {
            "pseudonym_id": pid,
            "label": children[pid].display_label if pid in children else pid,
            "total": 0,
            "areas": [],
        })
        entry["total"] += len(cands)
        entry["areas"].append({"area": area, "behaviors": behaviors, "count": len(cands)})

    for entry in by_child.values():
        entry["areas"].sort(key=lambda a: _AREA_IDX.get(a["area"], 9))

    return sorted(by_child.values(), key=lambda e: e["label"])


def generate_weekly_draft(
    repo: SqliteRepository,
    class_id: str,
    period_start: str,
    period_end: str,
    pseudonym_ids: Optional[list[str]] = None,
    actor: str = DEFAULT_ACTOR,
) -> list[WeeklyDraft]:
    """기간 내 누적 후보를 유아·영역별로 묶어 주간 초안을 생성·저장한다.

    - 기간: captured_date 가 [period_start, period_end] 인 클래스 영상.
    - 매칭(child_match)된 후보만 유아별로 집계(미매칭 제외).
    - 멱등: 동일 (class, 기간, 유아, 영역) 초안 id 를 재사용해 덮어쓰되,
      이미 finalized 된 초안은 보존(덮어쓰지 않음).
    - 반환: 생성/갱신된 WeeklyDraft 목록(이미 finalized 라 건너뛴 것은 제외).
    """
    if repo.get_class(class_id) is None:
        raise ValueError(f"class_id={class_id} 클래스를 찾을 수 없습니다.")

    videos = [
        v for v in repo.list_videos()
        if v.class_id == class_id and _in_period(v.captured_date, period_start, period_end)
    ]

    # (pseudonym, area) -> {"cands": [(cand, video)], "clip_pool": [(conf, clip_id)]}
    groups: dict[tuple[str, str], dict] = {}
    for v in videos:
        matches = {m.temp_child_id: m.pseudonym_id for m in repo.list_child_matches(v.id)}
        for cand in repo.list_candidates(v.id):
            pid = matches.get(cand.temp_child_id)
            if pid is None:
                continue
            if pseudonym_ids and pid not in pseudonym_ids:
                continue
            areas = _candidate_areas(cand, repo.list_mappings(cand.id))
            clips = repo.get_clips_for_scene(cand.scene_id, v.id)
            for area in areas:
                g = groups.setdefault((pid, area), {"cands": [], "clip_pool": []})
                g["cands"].append(cand)
                for clip in clips:
                    g["clip_pool"].append((cand.confidence, clip.id))

    # finalized 초안은 보존 (재생성 시 덮어쓰지 않음)
    finalized_ids = {
        d.id for d in repo.list_weekly_drafts(class_id) if d.status == "finalized"
    }

    now = datetime.now()
    drafts: list[WeeklyDraft] = []
    for (pid, area), g in groups.items():
        draft_id = f"wd_{class_id}_{period_start}_{period_end}_{pid}_{_AREA_IDX.get(area, 9)}"
        if draft_id in finalized_ids:
            continue  # 교사 확정 보존

        cands = sorted(g["cands"], key=lambda c: c.confidence, reverse=True)
        behaviors = []
        seen = set()
        for c in cands:
            b = c.observed_behavior.strip()
            if b and b not in seen:
                seen.add(b)
                behaviors.append(b)
            if len(behaviors) >= _MAX_BEHAVIORS:
                break

        # 대표 근거 클립: 후보 신뢰도 높은 순, 중복 제거, 최대 3개
        rep_clips: list[str] = []
        for _conf, clip_id in sorted(g["clip_pool"], key=lambda x: x[0], reverse=True):
            if clip_id not in rep_clips:
                rep_clips.append(clip_id)
            if len(rep_clips) >= _MAX_REP_CLIPS:
                break

        draft_text = (
            f"[{period_start}~{period_end}] '{area}' 영역 관찰 초안 (AI 후보 · 교사 검토 전)\n"
            + "관찰된 행동: " + " / ".join(behaviors)
            + f"\n(근거 후보 {len(cands)}건)"
        )

        drafts.append(WeeklyDraft(
            id=draft_id, class_id=class_id, pseudonym_id=pid,
            period_start=period_start, period_end=period_end, area=area,
            draft_text=draft_text,
            source_candidate_ids=[c.id for c in cands],
            representative_clip_ids=rep_clips,
            status="generated", created_at=now,
        ))

    if drafts:
        repo.save_weekly_drafts(drafts)

    repo.write_audit(AuditLog(
        id=f"audit_{class_id}_draft_{uuid.uuid4().hex[:6]}",
        video_id=class_id, actor=actor, action="draft_generate",
        detail=(
            f"weekly_draft class={class_id} period={period_start}..{period_end} "
            f"videos={len(videos)} drafts={len(drafts)}"
        ),
        created_at=now,
    ))
    return drafts
