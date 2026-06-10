import _bootstrap  # noqa: F401  — 프로젝트 루트를 sys.path에 추가

import uuid
from datetime import date, datetime, timedelta
from pathlib import Path

import streamlit as st

from core.schemas import ChildMatch, FinalRecord, KicceItemCandidate
from services.draft_service import generate_weekly_draft
from storage.sqlite_repository import SqliteRepository

st.set_page_config(
    page_title="주간 관찰초안",
    page_icon="✅",
    layout="wide",
)

from _auth import require_login, render_user_sidebar, get_current_actor
from _responsive import inject_responsive_css

require_login()
inject_responsive_css()

st.title("✅ 주간 관찰초안")
st.caption("1~2주 누적 후보를 바탕으로 AI가 만든 **관찰기록 초안**을 검토·수정·확정합니다.")

with st.expander("📌 이 화면의 원칙 (클릭하여 펼치기)", expanded=False):
    st.markdown(
        "- 초안은 **AI 후보**입니다. 교사가 검토·수정·확정해야 기록이 됩니다.\n"
        "- 얼굴 매칭은 **후보**로만 제시되며, 교사가 확정해야 유아(가명)에 연결됩니다.\n"
        "- 점수·발달·평정을 산출하지 않습니다.\n"
        "- 원본 영상·얼굴 데이터는 외부로 전송되지 않습니다."
    )

st.divider()


@st.cache_resource
def get_repo() -> SqliteRepository:
    from core.config import DB_PATH
    repo = SqliteRepository(DB_PATH)
    repo.init_schema()
    return repo


repo = get_repo()
render_user_sidebar()
_actor = get_current_actor()
_role = st.session_state.get("role", "teacher")
_owner = _actor if _role == "teacher" else None

# ---------------------------------------------------------------------------
# 클래스·기간 선택
# ---------------------------------------------------------------------------
_classes = repo.list_classes(teacher_owner=_owner)
if not _classes:
    st.info("등록된 클래스가 없습니다. ‘우리반 설정’에서 클래스·유아를 먼저 등록해주세요.")
    st.stop()

_opts = {f"{c.name} [{c.id}]": c.id for c in _classes}
col_a, col_b, col_c = st.columns([3, 2, 2])
_sel_label = col_a.selectbox("클래스", list(_opts.keys()), key="draft_class")
_class_id = _opts[_sel_label]
_span = col_b.radio("기간", ["1주", "2주"], horizontal=True, key="draft_span")
_end = col_c.date_input("종료일", value=date.today(), key="draft_end")
_days = 7 if _span == "1주" else 14
_start = _end - timedelta(days=_days - 1)
_ps, _pe = _start.strftime("%Y-%m-%d"), _end.strftime("%Y-%m-%d")
st.caption(f"집계 기간: {_ps} ~ {_pe}")

# 기간 내 영상
_period_videos = [
    v for v in repo.list_videos()
    if v.class_id == _class_id and v.captured_date and _ps <= v.captured_date <= _pe
]
_children = repo.list_children(_class_id)
_child_by_pseudo = {c.pseudonym_id: c for c in _children}

st.divider()

# ===========================================================================
# A. 유아 매칭 확정 (얼굴 매칭 후보 + 수동 매칭)
# ===========================================================================
st.subheader("A. 유아 매칭 확정")
st.caption("AI 얼굴 매칭은 후보입니다. 교사가 확정해야 유아(가명)에 연결되어 초안에 집계됩니다.")

if not _period_videos:
    st.info("이 기간에 업로드된 영상이 없습니다.")
else:
    _any_pending = False
    for v in _period_videos:
        matched = {m.temp_child_id for m in repo.list_child_matches(v.id)}
        fmcs = [f for f in repo.list_face_match_candidates(v.id) if f.status == "proposed"]
        temp_ids = sorted({c.temp_child_id for c in repo.list_candidates(v.id)})
        unmatched_temps = [t for t in temp_ids if t not in matched]
        if not unmatched_temps:
            continue
        _any_pending = True
        with st.expander(f"🎬 {v.filename}  ({v.captured_date}) — 미매칭 {len(unmatched_temps)}명", expanded=True):
            # 얼굴 매칭 후보
            for f in fmcs:
                if f.temp_child_id in matched:
                    continue
                ch = repo.get_child(f.child_id)
                label = ch.display_label if ch else f.child_id
                c1, c2, c3 = st.columns([3, 1, 1])
                c1.markdown(f"**{f.temp_child_id}** → 제안: **{label}** (유사도 {f.confidence:.2f})")
                if c2.button("확정", key=f"fmc_ok_{f.id}"):
                    repo.decide_face_match(f.id, status="confirmed", decided_by=_actor)
                    if ch:
                        repo.set_child_match(ChildMatch(
                            id=f"cm_{v.id}_{f.temp_child_id}_{uuid.uuid4().hex[:4]}",
                            video_id=v.id, temp_child_id=f.temp_child_id,
                            pseudonym_id=ch.pseudonym_id, source="face_candidate_confirmed",
                            matched_by=_actor, matched_at=datetime.now(),
                        ))
                    st.rerun()
                if c3.button("기각", key=f"fmc_no_{f.id}"):
                    repo.decide_face_match(f.id, status="rejected", decided_by=_actor)
                    st.rerun()

            # 수동 매칭 (얼굴 후보 없거나 보완)
            if _children:
                st.markdown("**수동 매칭**")
                for t in unmatched_temps:
                    mc1, mc2 = st.columns([3, 1])
                    pick = mc1.selectbox(
                        f"{t} → 유아 선택",
                        options=["(선택 안 함)"] + [f"{c.display_label} ({c.pseudonym_id})" for c in _children],
                        key=f"manual_{v.id}_{t}",
                    )
                    if mc2.button("매칭", key=f"manual_ok_{v.id}_{t}") and pick != "(선택 안 함)":
                        pseudo = pick.rsplit("(", 1)[-1].rstrip(")")
                        repo.set_child_match(ChildMatch(
                            id=f"cm_{v.id}_{t}_{uuid.uuid4().hex[:4]}",
                            video_id=v.id, temp_child_id=t, pseudonym_id=pseudo,
                            source="teacher", matched_by=_actor, matched_at=datetime.now(),
                        ))
                        st.rerun()
            else:
                st.caption("등록 유아가 없어 수동 매칭을 할 수 없습니다. ‘우리반 설정’에서 유아를 등록하세요.")
    if not _any_pending:
        st.success("이 기간의 모든 관찰 후보가 유아에 매칭되었습니다.", icon="✅")

st.divider()

# ===========================================================================
# B. 주간 초안 생성
# ===========================================================================
st.subheader("B. 주간 초안 생성")
if st.button("📝 이 기간 주간 초안 생성", key="gen_draft", type="primary"):
    try:
        drafts = generate_weekly_draft(repo, _class_id, _ps, _pe, actor=_actor)
        st.success(f"초안 {len(drafts)}건 생성/갱신 (finalized 초안은 보존)")
    except Exception as e:
        st.error(f"초안 생성 실패: {e}")

st.divider()

# ===========================================================================
# C. 초안 검토·확정
# ===========================================================================
st.subheader("C. 초안 검토 및 확정")

# 클립 id → 경로 맵 (클래스 내 영상 전체)
_clip_path = {}
for v in repo.list_videos():
    if v.class_id == _class_id:
        for clip in repo.list_clips(v.id):
            _clip_path[clip.id] = clip.local_clip_path

_drafts = [
    d for d in repo.list_weekly_drafts(_class_id)
    if d.period_start == _ps and d.period_end == _pe
]
if not _drafts:
    st.info("이 기간의 초안이 없습니다. 매칭을 확정한 뒤 위에서 ‘주간 초안 생성’을 눌러주세요.")
else:
    for d in _drafts:
        label = _child_by_pseudo[d.pseudonym_id].display_label if d.pseudonym_id in _child_by_pseudo else d.pseudonym_id
        status_badge = "✅ 확정됨" if d.status == "finalized" else "🟡 검토 대기"
        with st.expander(f"[{label}] {d.area} — {status_badge}", expanded=(d.status != "finalized")):
            # 대표 근거 클립
            rep = [cid for cid in d.representative_clip_ids if _clip_path.get(cid) and Path(_clip_path[cid]).exists()]
            if rep:
                st.markdown("**대표 근거 클립**")
                cols = st.columns(min(len(rep), 3))
                for i, cid in enumerate(rep[:3]):
                    with cols[i]:
                        st.video(_clip_path[cid])
            else:
                st.caption("표시할 근거 클립이 없습니다(프레임 기반 후보).")

            # KICCE 후보 (원본 후보들의 매핑 집계)
            kicce_items: list[KicceItemCandidate] = []
            seen_items = set()
            for cid in d.source_candidate_ids:
                for m in repo.list_mappings(cid):
                    if m.scale == "kicce" and m.item_id not in seen_items:
                        seen_items.add(m.item_id)
                        kicce_items.append(KicceItemCandidate(
                            item_id=m.item_id, item_text=m.item_text,
                            rationale=m.rationale, confidence=m.confidence,
                        ))
            if kicce_items:
                st.markdown("**KICCE 문항 후보**")
                for k in kicce_items:
                    st.caption(f"- [문항 {k.item_id}] {k.item_text} · 신뢰도 {k.confidence:.2f}")

            # 초안 텍스트 (수정 가능)
            edited_text = st.text_area(
                "관찰기록 초안 (수정 가능)", value=d.draft_text,
                key=f"text_{d.id}", height=140,
                disabled=(d.status == "finalized"),
            )

            if d.status != "finalized":
                b1, b2, b3 = st.columns(3)
                _do_accept = b1.button("채택", key=f"acc_{d.id}")
                _do_edit = b2.button("수정 확정", key=f"edt_{d.id}")
                _do_reject = b3.button("기각", key=f"rej_{d.id}")
                if _do_accept or _do_edit or _do_reject:
                    decision = "rejected" if _do_reject else ("edited" if _do_edit else "accepted")
                    edited = _do_edit or (edited_text.strip() != d.draft_text.strip())
                    repo.save_final_record(FinalRecord(
                        id=f"rec_{d.id}_{uuid.uuid4().hex[:6]}",
                        candidate_id=(d.source_candidate_ids[0] if d.source_candidate_ids else d.id),
                        pseudonym_id=d.pseudonym_id,
                        weekly_draft_id=d.id,
                        period_start=d.period_start, period_end=d.period_end,
                        final_behavior=(edited_text.strip() or d.draft_text),
                        confirmed_areas=[d.area],
                        confirmed_items=(kicce_items if decision != "rejected" else []),
                        decision=decision, edited=bool(edited),
                        confirmed_by=_actor, confirmed_at=datetime.now(),
                    ))
                    repo.update_draft_status(d.id, "finalized")
                    st.success(f"확정 저장됨 ({decision})")
                    st.rerun()

st.divider()
st.caption(
    "📌 확정된 기록은 ‘연구자 리포트’에 반영됩니다. AI 후보는 교사 확정 전까지 기록이 아닙니다. "
    "점수·발달·평정은 산출하지 않습니다."
)
