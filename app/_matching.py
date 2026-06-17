"""유아 매칭 확정 공유 UI 컴포넌트.

수집균형·주간초안 페이지가 동일하게 사용한다.
- 얼굴 매칭 후보(proposed)를 교사가 확정/기각한다.
- 수동 매핑(temp_child_id → 등록 유아)을 지원한다.
- 확정 시 ChildMatch를 생성한다(자동 확정 금지 — 항상 교사 행동).

도메인 로직은 repo(저장소)만 호출하며, 점수·실명을 다루지 않는다.
"""

from __future__ import annotations

import uuid
from datetime import datetime

import streamlit as st

from core.schemas import ChildMatch


def render_matching_section(repo, videos, children, actor: str, key_prefix: str = "match") -> bool:
    """주어진 영상들의 미매칭 temp_child_id를 유아(가명)에 연결하는 UI를 그린다.

    반환: 아직 매칭 대기 중인 항목이 하나라도 있으면 True.
    """
    if not videos:
        st.info("이 클래스에 업로드된 영상이 없습니다.")
        return False

    any_pending = False
    for v in videos:
        matched = {m.temp_child_id for m in repo.list_child_matches(v.id)}
        fmcs = [f for f in repo.list_face_match_candidates(v.id) if f.status == "proposed"]
        temp_ids = sorted({c.temp_child_id for c in repo.list_candidates(v.id)})
        unmatched_temps = [t for t in temp_ids if t not in matched]
        if not unmatched_temps:
            continue
        any_pending = True
        with st.expander(
            f"🎬 {v.filename}  ({v.captured_date or '-'}) — 매칭 필요 {len(unmatched_temps)}명",
            expanded=True,
        ):
            # 1) 얼굴 매칭 후보 (있으면 확정/기각)
            for f in fmcs:
                if f.temp_child_id in matched:
                    continue
                ch = repo.get_child(f.child_id)
                label = ch.display_label if ch else f.child_id
                c1, c2, c3 = st.columns([3, 1, 1])
                c1.markdown(f"**{f.temp_child_id}** → 제안: **{label}** (유사도 {f.confidence:.2f})")
                if c2.button("확정", key=f"{key_prefix}_fmc_ok_{f.id}"):
                    repo.decide_face_match(f.id, status="confirmed", decided_by=actor)
                    if ch:
                        repo.set_child_match(ChildMatch(
                            id=f"cm_{v.id}_{f.temp_child_id}_{uuid.uuid4().hex[:4]}",
                            video_id=v.id, temp_child_id=f.temp_child_id,
                            pseudonym_id=ch.pseudonym_id, source="face_candidate_confirmed",
                            matched_by=actor, matched_at=datetime.now(),
                        ))
                    st.rerun()
                if c3.button("기각", key=f"{key_prefix}_fmc_no_{f.id}"):
                    repo.decide_face_match(f.id, status="rejected", decided_by=actor)
                    st.rerun()

            # 2) 수동 매핑 (얼굴 후보가 없거나 보완)
            if children:
                st.markdown("**수동 매핑**")
                for t in unmatched_temps:
                    mc1, mc2 = st.columns([3, 1])
                    pick = mc1.selectbox(
                        f"{t} → 유아 선택",
                        options=["(선택 안 함)"] + [f"{c.display_label} ({c.pseudonym_id})" for c in children],
                        key=f"{key_prefix}_manual_{v.id}_{t}",
                    )
                    if mc2.button("매핑", key=f"{key_prefix}_manual_ok_{v.id}_{t}") and pick != "(선택 안 함)":
                        pseudo = pick.rsplit("(", 1)[-1].rstrip(")")
                        repo.set_child_match(ChildMatch(
                            id=f"cm_{v.id}_{t}_{uuid.uuid4().hex[:4]}",
                            video_id=v.id, temp_child_id=t, pseudonym_id=pseudo,
                            source="teacher", matched_by=actor, matched_at=datetime.now(),
                        ))
                        st.rerun()
            else:
                st.caption("등록 유아가 없어 수동 매핑을 할 수 없습니다. ‘우리반 설정’에서 유아를 등록하세요.")

    return any_pending
