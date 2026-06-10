import _bootstrap  # noqa: F401  — 프로젝트 루트를 sys.path에 추가
import streamlit as st

st.set_page_config(
    page_title="연구자 리포트",
    page_icon="📊",
    layout="wide",
)

from _auth import require_login, render_user_sidebar
from _responsive import inject_responsive_css

require_login(allowed_roles=["researcher"])
inject_responsive_css()

import pandas as pd

from storage.sqlite_repository import SqliteRepository
from services.report_service import (
    build_video_report,
    export_report_json,
    export_report_csv,
)


# ─── 저장소 싱글톤 ───────────────────────────────────────────────────────────
@st.cache_resource
def get_repo():
    r = SqliteRepository()
    r.init_schema()
    return r


repo = get_repo()
render_user_sidebar()

# ─── 페이지 제목 ─────────────────────────────────────────────────────────────
st.title("📊 연구자 확인용 리포트")
st.caption("교사 확정 기록 기반 연구 분석 지표입니다. 관찰수준 점수는 산출하지 않습니다.")

# ─── 1. 안내 배너 ─────────────────────────────────────────────────────────────
with st.expander("📌 리포트 원칙 안내", expanded=False):
    st.markdown("""
    - 이 화면은 **연구자 확인용**입니다. 학부모 제공용 리포트가 아닙니다.
    - 모든 데이터는 **교사가 확정한 관찰기록** 기반입니다.
    - **관찰수준 점수·발달 점수·평정 점수는 산출하지 않습니다.**
    - 유아는 **연구용 가명 ID**(예: child_001)로만 표시됩니다. 실명은 포함되지 않습니다.
    - Export 파일에는 원본 영상 경로·프레임 이미지 경로가 포함되지 않습니다.
    """)

st.divider()

# ─── 2. 영상 선택 ─────────────────────────────────────────────────────────────
all_videos = repo.list_videos()
videos_with_cands = [v for v in all_videos if repo.list_candidates(v.id)]

if not videos_with_cands:
    st.warning(
        "분석된 영상이 없습니다. "
        "'업로드 & 분석' 화면에서 영상을 업로드하고 분석한 뒤 다시 확인해주세요."
    )
    st.stop()

video_options = {f"{v.filename}  ({v.id[-6:]})": v.id for v in videos_with_cands}
selected_label = st.selectbox("영상 선택", list(video_options.keys()), key="report_video")
video_id = video_options[selected_label]

# ─── 리포트 데이터 로드 ────────────────────────────────────────────────────────
report = build_video_report(video_id, repo)

if report["total_finals"] == 0:
    st.metric("AI 관찰 후보", report["total_candidates"])
    st.info(
        "아직 교사 확정 기록이 없습니다. "
        "'교사 검토' 화면에서 관찰 후보를 검토·확정해주세요."
    )
    st.stop()

# ─── 3. 리포트 요약 카드 ──────────────────────────────────────────────────────
st.subheader("📋 리포트 요약")
c1, c2, c3, c4 = st.columns(4)
c1.metric("AI 관찰 후보", report["total_candidates"])
c2.metric("교사 확정 기록", report["total_finals"])
c3.metric("채택률", f"{report['acceptance_rate']*100:.1f}%")
c4.metric("수정률", f"{report['edit_rate']*100:.1f}%")

video_meta = repo.get_video(video_id)
if video_meta and video_meta.duration_sec > 0:
    m, s = divmod(int(video_meta.duration_sec), 60)
    st.caption(f"영상: {video_meta.filename} · 길이: {m}분 {s}초")

st.divider()

# ─── 4. AI 후보 대비 교사 검토 결과 ───────────────────────────────────────────
st.subheader("🔍 AI 후보 대비 교사 검토 결과")
st.caption("AI 성능 평가가 아닌 교사 검토 워크플로우 분석 지표입니다.")

cols = st.columns(5)
cols[0].metric("전체 후보", report["total_candidates"])
cols[1].metric("채택", report["accepted"], help="원문 그대로 채택")
cols[2].metric("수정 채택", report["edited"], help="행동 서술 수정 후 채택")
cols[3].metric("기각", report["rejected"])
cols[4].metric("미검토", report["unreviewed"])

df_comp = pd.DataFrame({
    "구분":   ["채택", "수정 채택", "기각", "미검토"],
    "건수": [
        report["accepted"], report["edited"],
        report["rejected"], report["unreviewed"],
    ],
}).set_index("구분")
if df_comp["건수"].sum() > 0:
    st.bar_chart(df_comp, height=200)

st.divider()

# ─── 5. 누리 영역 분포 ────────────────────────────────────────────────────────
st.subheader("🌱 누리 영역 분포")
area_dist = report["area_distribution"]
if area_dist:
    df_area = pd.DataFrame(
        {"누리 영역": list(area_dist.keys()), "확정 건수": list(area_dist.values())}
    ).set_index("누리 영역").sort_values("확정 건수", ascending=False)
    st.dataframe(df_area, use_container_width=True)

    import altair as alt
    _area_chart = alt.Chart(df_area.reset_index()).mark_bar().encode(
        x=alt.X("확정 건수:Q"),
        y=alt.Y("누리 영역:N", sort="-x"),
        color=alt.Color("누리 영역:N", scale=alt.Scale(scheme="tableau10")),
        tooltip=["누리 영역", "확정 건수"],
    ).properties(height=200)
    st.altair_chart(_area_chart, use_container_width=True)
else:
    st.info("선택된 누리 영역 없음 — 교사 검토 화면에서 누리 영역 체크박스를 선택했는지 확인하세요.")

st.divider()

# ─── 6. KICCE 문항 커버리지 ───────────────────────────────────────────────────
st.subheader("📝 KICCE 문항 커버리지")
st.caption("확정 기록에서 선택된 KICCE 유아관찰척도 문항별 확정 건수입니다. 점수화하지 않습니다.")
kicce_cov = report["kicce_coverage"]
if kicce_cov:
    df_kicce = pd.DataFrame([
        {
            "문항 ID": str(k.get("item_id") or "-"),
            "문항 내용": k["item_text"],
            "확정 건수": k["count"],
        }
        for k in kicce_cov
    ])
    st.dataframe(df_kicce, use_container_width=True, hide_index=True)
else:
    st.info("선택된 KICCE 문항 없음 — 교사 검토 화면에서 KICCE 문항 체크박스를 선택했는지 확인하세요.")

st.divider()

# ─── 7. 유아별 확정 관찰기록 ─────────────────────────────────────────────────
st.subheader("👶 유아별 확정 관찰기록")
st.caption("연구용 가명 ID 기준으로 그룹화합니다. 실명은 표시되지 않습니다.")

DECISION_LABEL = {"accepted": "✅ 채택", "edited": "✏️ 수정 채택", "rejected": "❌ 기각"}

by_pseudo = report["by_pseudonym"]
if not by_pseudo:
    st.info("확정 기록이 없습니다.")
else:
    import altair as alt

    # 타임라인: FinalRecord와 ObservationCandidate 조인
    _timeline_rows = []
    try:
        for _pid, _recs in by_pseudo.items():
            for _rec in _recs:
                _cand_id = _rec.get("candidate_id") if isinstance(_rec, dict) else getattr(_rec, "candidate_id", None)
                _decision = _rec.get("decision") if isinstance(_rec, dict) else getattr(_rec, "decision", None)
                if _cand_id and hasattr(repo, "get_candidate"):
                    _cand = repo.get_candidate(_cand_id)
                    if _cand:
                        _timeline_rows.append({
                            "유아": _pid,
                            "시작(초)": _cand.time_start,
                            "종료(초)": _cand.time_end,
                            "결정": _decision,
                        })
    except Exception:
        _timeline_rows = []

    if _timeline_rows:
        _df_timeline = pd.DataFrame(_timeline_rows)
        _timeline_chart = alt.Chart(_df_timeline).mark_bar(size=20).encode(
            x=alt.X("시작(초):Q"),
            x2="종료(초):Q",
            y=alt.Y("유아:N"),
            color=alt.Color("결정:N", scale=alt.Scale(
                domain=["accepted", "edited", "rejected"],
                range=["#2ecc71", "#f39c12", "#e74c3c"],
            )),
            tooltip=["유아", "시작(초)", "종료(초)", "결정"],
        ).properties(
            height=max(100, len(by_pseudo) * 50),
            title="유아별 확정 관찰기록 타임라인",
        )
        st.altair_chart(_timeline_chart, use_container_width=True)

    for pid, recs in sorted(by_pseudo.items()):
        with st.expander(f"👤 {pid}  —  {len(recs)}건", expanded=True):
            for rec in recs:
                label = DECISION_LABEL.get(rec["decision"], rec["decision"])
                ts = rec.get("time_start")
                te = rec.get("time_end")
                time_str = f" · ⏱ {ts:.1f}s ~ {te:.1f}s" if ts is not None else ""
                st.markdown(f"**{label}**{time_str}")
                st.write(f"**관찰 행동**: {rec['final_behavior']}")
                if rec.get("confirmed_areas"):
                    st.write(f"**누리 영역**: {', '.join(rec['confirmed_areas'])}")
                items = rec.get("confirmed_items") or []
                if items:
                    items_str = ", ".join(
                        f"[{i.get('item_id', '-')}] {i.get('item_text', '')}"
                        if isinstance(i, dict)
                        else f"[{getattr(i, 'item_id', '-')}] {getattr(i, 'item_text', '')}"
                        for i in items
                    )
                    st.write(f"**KICCE 문항**: {items_str}")
                confirmed_at = rec.get("confirmed_at", "")
                if hasattr(confirmed_at, "strftime"):
                    confirmed_at = confirmed_at.strftime("%Y-%m-%d %H:%M")
                edited_mark = " · 수정됨" if rec.get("edited") else ""
                st.caption(f"확정 일시: {confirmed_at}{edited_mark}")
                st.markdown("---")

st.divider()

# ─── 7b. 전처리 요약 ─────────────────────────────────────────────────────────
st.subheader("🎞 전처리 요약")
st.caption("영상 전처리 단계의 장면·프레임 산출 결과입니다.")
p1, p2, p3 = st.columns(3)
p1.metric("장면 수", report.get("scene_count", 0))
p2.metric("추출 프레임", report.get("frame_count", 0))
p3.metric("품질 통과 프레임", report.get("kept_frame_count", 0))

st.divider()

# ─── 7c. AI 후보 유지율 ──────────────────────────────────────────────────────
st.subheader("🔁 AI 후보 유지율")
st.caption(
    "AI가 제시한 누리 영역·KICCE 문항 후보 중 교사가 확정에 유지한 비율입니다. "
    "AI 성능 점수가 아니라 교사 판단 기여도(워크플로우) 지표입니다."
)
ret = report.get("candidate_retention", {})
r1, r2 = st.columns(2)
r1.metric(
    "누리 영역 후보 유지율",
    f"{ret.get('nuri_retention_rate', 0.0)*100:.1f}%",
    help=f"유지 {ret.get('nuri_retained', 0)} / 제시 {ret.get('nuri_suggested', 0)}",
)
r2.metric(
    "KICCE 문항 후보 유지율",
    f"{ret.get('kicce_retention_rate', 0.0)*100:.1f}%",
    help=f"유지 {ret.get('kicce_retained', 0)} / 제시 {ret.get('kicce_suggested', 0)}",
)
if ret.get("nuri_suggested", 0) == 0 and ret.get("kicce_suggested", 0) == 0:
    st.info(
        "AI 제시 후보(누리·KICCE 매핑)가 없어 유지율을 계산할 수 없습니다. "
        "'업로드 & 분석' 화면에서 누리·KICCE 후보 매핑을 먼저 실행하세요."
    )

st.divider()

# ─── 7c-2. 교사 검토 노력 ────────────────────────────────────────────────────
st.subheader("🧑‍🏫 교사 검토 노력")
st.caption(
    "검토 소요 시간과 AI 후보 시각적 근거 적절성입니다. "
    "AI 성능 점수·유아 평가가 아닌 검토 워크플로우 지표입니다."
)
effort = report.get("review_effort", {})
e1, e2 = st.columns(2)
_avg = effort.get("review_seconds_avg")
e1.metric(
    "평균 검토 소요(초)",
    f"{_avg:.1f}" if _avg is not None else "—",
    help=f"시간 기록된 확정 {effort.get('reviewed_with_timing', 0)}건 기준 (열람~확정, 유휴 포함 근사값)",
)
e2.metric("누적 검토 소요(초)", effort.get("review_seconds_total", 0))

adeq = effort.get("evidence_adequacy_distribution", {})
if adeq:
    _ADEQ_LABEL = {"adequate": "적절", "partial": "부분", "inadequate": "부적절", "unrated": "미평가"}
    df_adeq = pd.DataFrame(
        {
            "근거 적절성": [_ADEQ_LABEL.get(k, k) for k in ["adequate", "partial", "inadequate", "unrated"]],
            "건수": [adeq.get(k, 0) for k in ["adequate", "partial", "inadequate", "unrated"]],
        }
    ).set_index("근거 적절성")
    st.dataframe(df_adeq, use_container_width=True)
    if df_adeq["건수"].sum() > 0:
        st.bar_chart(df_adeq, height=200)

st.divider()

# ─── 7d. 감사 로그 완전성 ────────────────────────────────────────────────────
st.subheader("🛡 감사 로그 완전성")
st.caption("이 영상에 대해 upload·access·analyze·export·delete 액션이 기록됐는지 점검합니다.")
audit_comp = report.get("audit_completeness", {})
_ACTION_LABEL = {
    "upload": "업로드", "access": "접근", "analyze": "분석",
    "export": "내보내기", "delete": "삭제",
}
audit_rows = [
    {
        "액션": f"{_ACTION_LABEL.get(a, a)} ({a})",
        "기록 여부": "✅ 기록됨" if audit_comp.get(a, {}).get("present") else "⚠️ 없음",
        "건수": audit_comp.get(a, {}).get("count", 0),
    }
    for a in ("upload", "access", "analyze", "export", "delete")
]
st.dataframe(pd.DataFrame(audit_rows), use_container_width=True, hide_index=True)
missing = audit_comp.get("missing_actions", [])
if missing:
    st.warning(
        "미기록 액션: " + ", ".join(missing)
        + " — 해당 동작을 아직 수행하지 않았거나 계측되지 않았을 수 있습니다."
    )
else:
    st.success("5종 감사 액션이 모두 기록되어 있습니다.")

st.divider()

# ─── 8. Export ────────────────────────────────────────────────────────────────
st.subheader("💾 리포트 내보내기")
st.caption("원본 영상 경로·프레임 이미지 경로는 포함되지 않습니다.")

col_json, col_csv = st.columns(2)

with col_json:
    if st.button("JSON 생성", key=f"btn_json_{video_id}"):
        st.session_state[f"json_data_{video_id}"] = export_report_json(video_id, repo)
    if f"json_data_{video_id}" in st.session_state:
        st.download_button(
            label="📥 JSON 다운로드",
            data=st.session_state[f"json_data_{video_id}"].encode("utf-8"),
            file_name=f"research_report_{video_id}.json",
            mime="application/json",
            key=f"dl_json_{video_id}",
        )

with col_csv:
    if st.button("CSV 생성", key=f"btn_csv_{video_id}"):
        st.session_state[f"csv_data_{video_id}"] = export_report_csv(video_id, repo)
    if f"csv_data_{video_id}" in st.session_state:
        st.download_button(
            label="📥 CSV 다운로드",
            data=st.session_state[f"csv_data_{video_id}"].encode("utf-8-sig"),
            file_name=f"research_report_{video_id}.csv",
            mime="text/csv",
            key=f"dl_csv_{video_id}",
        )
