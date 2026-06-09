import _bootstrap  # noqa: F401  — 프로젝트 루트를 sys.path에 추가

import time
from pathlib import Path

import streamlit as st

from core.config import DEFAULT_ACTOR
from services.review_service import (
    finalize_candidate,
    list_review_candidates,
    list_final_records_for_video,
    log_video_access,
    mapping_to_kicce_item,
    set_child_match,
)
from storage.sqlite_repository import SqliteRepository

st.set_page_config(
    page_title="교사 검토 및 확정",
    page_icon="✅",
    layout="wide",
)

st.title("✅ 교사 검토 및 확정")
st.caption("AI가 생성한 관찰 후보를 검토하고 최종 관찰기록을 확정합니다.")

with st.expander("📌 검토 원칙 (클릭하여 펼치기)", expanded=False):
    st.markdown(
        "- 아래 AI 결과는 모두 **교사 검토 전 후보**입니다. 확정은 교사 액션으로만 발생합니다.\n"
        "- 유아는 `child_A`, `child_B` 임시 ID로 표시됩니다. **실명이 아닌 연구용 가명 ID**와 매칭하세요.\n"
        "- 후보를 **채택 / 수정 후 채택 / 기각**할 수 있습니다.\n"
        "- **관찰수준 점수는 산출하지 않습니다.**\n"
        "- 원본 영상 재생 시 접근 기록(audit_log)이 남습니다."
    )

st.divider()


# ---------------------------------------------------------------------------
# 저장소 singleton
# ---------------------------------------------------------------------------
@st.cache_resource
def get_repo() -> SqliteRepository:
    from core.config import DB_PATH
    repo = SqliteRepository(DB_PATH)
    repo.init_schema()
    return repo


repo = get_repo()


# ---------------------------------------------------------------------------
# 영상 선택 (관찰 후보가 있는 영상만)
# ---------------------------------------------------------------------------
videos_with_candidates = [v for v in repo.list_videos() if repo.list_candidates(v.id)]

if not videos_with_candidates:
    st.warning(
        "검토할 관찰 후보가 있는 영상이 없습니다. "
        "먼저 '업로드 및 분석' 화면에서 관찰 후보를 생성해주세요."
    )
    st.stop()

video_options = {f"{v.filename}  [{v.id}]": v.id for v in videos_with_candidates}
selected_label = st.selectbox(
    "검토할 영상을 선택하세요",
    options=list(video_options.keys()),
    key="review_video_select",
)
selected_video_id = video_options[selected_label]
selected_video = repo.get_video(selected_video_id)


# ---------------------------------------------------------------------------
# 영상 플레이어 + 접근 로그 (session_state 로 영상당 1회)
# ---------------------------------------------------------------------------
st.subheader("영상 플레이어")
if selected_video and selected_video.stored_path and Path(selected_video.stored_path).exists():
    st.video(selected_video.stored_path)
    access_key = f"video_access_logged_{selected_video_id}"
    if access_key not in st.session_state:
        log_video_access(selected_video_id, repo, actor=DEFAULT_ACTOR)
        st.session_state[access_key] = True
    st.caption("ℹ️ 원본 영상 접근이 감사 로그(access)에 기록되었습니다.")
else:
    st.info("영상 파일을 찾을 수 없습니다. 메타데이터만으로 검토를 진행합니다.")

st.divider()


# ---------------------------------------------------------------------------
# child 매칭 패널
# ---------------------------------------------------------------------------
st.subheader("유아 임시 ID ↔ 가명 ID 매칭")
st.caption("실명이 아닌 **연구용 가명 ID**를 입력하세요. (예: child_A → child_001)")

review_items = list_review_candidates(selected_video_id, repo)
temp_ids = sorted({item["candidate"].temp_child_id for item in review_items})
existing_matches = {m.temp_child_id: m.pseudonym_id for m in repo.list_child_matches(selected_video_id)}

with st.form("child_match_form"):
    inputs = {}
    cols = st.columns(min(3, len(temp_ids)) or 1)
    for i, tid in enumerate(temp_ids):
        with cols[i % len(cols)]:
            inputs[tid] = st.text_input(
                f"{tid} 의 가명 ID",
                value=existing_matches.get(tid, ""),
                placeholder="예: child_001",
                key=f"match_input_{tid}",
            )
    submitted = st.form_submit_button("💾 매칭 저장")
    if submitted:
        saved_n = 0
        for tid, pid in inputs.items():
            pid = pid.strip()
            if pid:
                set_child_match(selected_video_id, tid, pid, repo, actor=DEFAULT_ACTOR)
                saved_n += 1
        if saved_n:
            st.success(f"{saved_n}건의 가명 ID 매칭을 저장했습니다.")
            st.rerun()
        else:
            st.warning("입력된 가명 ID가 없습니다.")

st.divider()


# ---------------------------------------------------------------------------
# 관찰 후보 카드 목록
# ---------------------------------------------------------------------------
st.subheader("AI 관찰 후보 (교사 검토 전)")

# 최신 매칭/확정 상태 재조회
review_items = list_review_candidates(selected_video_id, repo)

for item in review_items:
    cand = item["candidate"]
    nuri = item["nuri"]
    kicce = item["kicce"]
    pseudonym = item["pseudonym_id"]
    final = item["final_record"]

    pseudo_label = pseudonym if pseudonym else "미매칭"
    status_badge = ""
    if final is not None:
        status_badge = f"  ✅ 검토 완료 ({final.decision})"

    with st.expander(
        f"[{cand.temp_child_id} → {pseudo_label}]  "
        f"{cand.time_start:.1f}s – {cand.time_end:.1f}s  | 신뢰도 {cand.confidence:.2f}{status_badge}",
        expanded=(final is None),
    ):
        # 검토 소요 시간 타이머: 후보가 이 세션에서 처음 렌더된 시점 기록
        open_key = f"review_open_{cand.id}"
        if open_key not in st.session_state:
            st.session_state[open_key] = time.time()

        def _elapsed_seconds() -> int:
            return max(0, int(time.time() - st.session_state.get(open_key, time.time())))

        st.markdown(f"**관찰 행동 (후보)**: {cand.observed_behavior}")
        st.markdown(f"**시각적 근거**: {cand.visual_evidence}")
        if cand.activity_context:
            st.caption(f"활동 맥락: {cand.activity_context}")
        if cand.peer_relation:
            st.caption(f"또래 관계: {cand.peer_relation}")
        if cand.interaction:
            parts = []
            if cand.interaction.with_peers:
                parts.append(f"또래: {cand.interaction.with_peers}")
            if cand.interaction.with_teacher:
                parts.append(f"교사: {cand.interaction.with_teacher}")
            if cand.interaction.with_materials:
                parts.append(f"자료: {cand.interaction.with_materials}")
            if parts:
                st.caption("상호작용: " + " | ".join(parts))
        if cand.needs_teacher_review:
            st.warning("교사 확인 필요 (AI 후보)")

        # ── 누리 영역 후보 선택 ─────────────────────────────────────
        st.markdown("**누리 영역 후보 (교사 검토 전)** — 확정할 항목을 선택하세요")
        selected_areas = []
        if nuri:
            for m in nuri:
                checked = st.checkbox(
                    f"{m.area}  ·  근거: {m.rationale}  ·  신뢰도 {m.confidence:.2f}",
                    key=f"nuri_{cand.id}_{m.id}",
                )
                if checked:
                    selected_areas.append(m.area)
        else:
            st.caption("누리 영역 후보 없음")

        # ── KICCE 문항 후보 선택 ────────────────────────────────────
        st.markdown("**KICCE 문항 후보 (교사 검토 전)** — 확정할 문항을 선택하세요")
        selected_items = []
        if kicce:
            for m in kicce:
                checked = st.checkbox(
                    f"[문항 {m.item_id}] {m.item_text}  ·  신뢰도 {m.confidence:.2f}",
                    key=f"kicce_{cand.id}_{m.id}",
                )
                if checked:
                    selected_items.append(mapping_to_kicce_item(m))
        else:
            st.caption("KICCE 문항 후보 없음")

        if not selected_areas and not selected_items:
            st.caption("선택된 영역/문항 없음")

        # ── 수정 textarea ───────────────────────────────────────────
        edited_behavior = st.text_area(
            "행동 서술 (수정 후 채택 시 사용)",
            value=cand.observed_behavior,
            key=f"edit_{cand.id}",
        )

        # ── AI 후보 시각적 근거 적절성 (교사 판단, 유아 평가 아님) ──────
        adequacy_label = st.radio(
            "AI 후보의 시각적 근거 적절성 (선택)",
            options=["미평가", "적절", "부분", "부적절"],
            horizontal=True,
            key=f"adeq_{cand.id}",
            help="AI 후보가 제시한 시각적 근거의 품질 평가입니다. 유아 발달·관찰수준 점수가 아닙니다.",
        )
        _ADEQ_MAP = {"적절": "adequate", "부분": "partial", "부적절": "inadequate"}
        evidence_adequacy = _ADEQ_MAP.get(adequacy_label)
        st.caption("⏱ 검토 소요 시간은 후보 열람~확정 경과(유휴 포함) 근사값으로 기록됩니다.")

        if final is not None:
            st.info(
                f"이미 검토 완료된 후보입니다 (결정: {final.decision}). "
                "아래 버튼으로 재저장하면 기존 확정 기록을 덮어씁니다."
            )

        # ── 교사 액션 버튼 ──────────────────────────────────────────
        pid_for_save = pseudonym if pseudonym else cand.temp_child_id
        c1, c2, c3 = st.columns(3)

        with c1:
            if st.button("✅ 채택", key=f"accept_{cand.id}"):
                finalize_candidate(
                    candidate_id=cand.id, pseudonym_id=pid_for_save,
                    final_behavior=cand.observed_behavior,
                    confirmed_areas=selected_areas, confirmed_items=selected_items,
                    decision="accepted", repo=repo, actor=DEFAULT_ACTOR,
                    video_id=selected_video_id,
                    review_seconds=_elapsed_seconds(), evidence_adequacy=evidence_adequacy,
                )
                st.session_state.pop(open_key, None)
                st.success("채택하여 확정 기록을 저장했습니다.")
                st.rerun()

        with c2:
            if st.button("✏️ 수정 후 채택", key=f"edit_btn_{cand.id}"):
                finalize_candidate(
                    candidate_id=cand.id, pseudonym_id=pid_for_save,
                    final_behavior=edited_behavior,
                    confirmed_areas=selected_areas, confirmed_items=selected_items,
                    decision="edited", repo=repo, actor=DEFAULT_ACTOR,
                    video_id=selected_video_id,
                    review_seconds=_elapsed_seconds(), evidence_adequacy=evidence_adequacy,
                )
                st.session_state.pop(open_key, None)
                st.success("수정한 내용으로 확정 기록을 저장했습니다.")
                st.rerun()

        with c3:
            if st.button("🚫 기각", key=f"reject_{cand.id}"):
                finalize_candidate(
                    candidate_id=cand.id, pseudonym_id=pid_for_save,
                    final_behavior=cand.observed_behavior,
                    confirmed_areas=[], confirmed_items=[],
                    decision="rejected", repo=repo, actor=DEFAULT_ACTOR,
                    video_id=selected_video_id,
                    review_seconds=_elapsed_seconds(), evidence_adequacy=evidence_adequacy,
                )
                st.session_state.pop(open_key, None)
                st.success("기각 처리하여 기록을 저장했습니다.")
                st.rerun()

st.divider()


# ---------------------------------------------------------------------------
# 저장된 final_record 요약
# ---------------------------------------------------------------------------
st.subheader("확정 기록 요약")
finals = list_final_records_for_video(selected_video_id, repo)
if not finals:
    st.caption("아직 확정된 기록이 없습니다.")
else:
    accepted = sum(1 for f in finals if f.decision == "accepted")
    edited = sum(1 for f in finals if f.decision == "edited")
    rejected = sum(1 for f in finals if f.decision == "rejected")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("전체 확정", len(finals))
    c2.metric("채택", accepted)
    c3.metric("수정 채택", edited)
    c4.metric("기각", rejected)

    for f in finals:
        with st.container(border=True):
            st.markdown(
                f"**[{f.pseudonym_id}]** · 결정: **{f.decision}** · 수정 여부: {'예' if f.edited else '아니오'}"
            )
            st.markdown(f"최종 행동 서술: {f.final_behavior}")
            if f.confirmed_areas:
                st.caption("확정 누리 영역: " + ", ".join(f.confirmed_areas))
            if f.confirmed_items:
                st.caption(
                    "확정 KICCE 문항: "
                    + ", ".join(f"문항{i.item_id}" for i in f.confirmed_items)
                )
            st.caption(f"확정자: {f.confirmed_by} · {f.confirmed_at.strftime('%Y-%m-%d %H:%M')}")

st.divider()
st.caption(
    "📌 원칙: AI는 후보만 제시합니다. 관찰기록 확정은 교사가 수행합니다. "
    "관찰수준 점수는 산출하지 않습니다. 원본 영상은 외부로 전송되지 않습니다."
)
