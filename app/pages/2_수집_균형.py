import _bootstrap  # noqa: F401  — 프로젝트 루트를 sys.path에 추가

import streamlit as st

from services.dashboard_service import collection_status, NURI_AREAS
from storage.sqlite_repository import SqliteRepository

st.set_page_config(
    page_title="수집 균형",
    page_icon="📊",
    layout="wide",
)

from _auth import require_login, render_user_sidebar, get_current_actor
from _responsive import inject_responsive_css
from _matching import render_matching_section

require_login()
inject_responsive_css()

st.title("📊 수집 균형")
st.caption("유아별·누리영역별 관찰자료 **수집 현황**을 확인하고, 부족한 부분을 다음 촬영에서 보완하세요.")

with st.expander("📌 이 화면의 원칙 (클릭하여 펼치기)", expanded=False):
    st.markdown(
        "- 이 수치는 **관찰자료 수집량**입니다. 발달·관찰수준·평정 **점수가 아닙니다.**\n"
        "- 교사는 **매일 개별 후보를 검토하지 않습니다.** 현황만 보고 촬영을 보완합니다.\n"
        "- 유아는 가명 ID로만 표시됩니다. 매칭되지 않은 후보는 '미매칭'으로 분리 집계됩니다.\n"
        "- 누적 후보 확정은 ‘주간 관찰초안’ 화면에서 1~2주 주기로 진행합니다."
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
_role = st.session_state.get("role", "teacher")
# 교사는 본인 영상만, 연구자는 전체
_owner = get_current_actor() if _role == "teacher" else None


# ---------------------------------------------------------------------------
# 클래스 선택 (등록 클래스가 있으면 선택, 없으면 미분류 전체)
# ---------------------------------------------------------------------------
try:
    _classes = repo.list_classes(teacher_owner=_owner)
except Exception:
    _classes = []

_class_id = None
if _classes:
    _opts = {"전체 (미분류 포함)": None}
    for c in _classes:
        _opts[f"{c.name} [{c.id}]"] = c.id
    _label = st.selectbox("클래스 선택", list(_opts.keys()), key="dash_class_select")
    _class_id = _opts[_label]
else:
    st.info(
        "등록된 클래스가 없어 **미분류 영상 전체**를 집계합니다. "
        "‘우리반 설정’에서 클래스·유아를 등록하면 유아별 현황이 정확해집니다."
    )

_min = st.slider(
    "영역별 권장 최소 관찰자료 수 (이 값 미만이면 '부족' 표시)",
    min_value=1, max_value=10, value=2, step=1, key="dash_min_per_area",
)

status = collection_status(repo, class_id=_class_id, owner=_owner, min_per_area=_min)

st.divider()


# ---------------------------------------------------------------------------
# 요약 지표
# ---------------------------------------------------------------------------
c1, c2, c3, c4 = st.columns(4)
c1.metric("집계 영상", status["total_videos"])
c2.metric("관찰 후보(누적)", status["total_candidates"])
c3.metric("유아 연결됨", status["matched_candidates"], help="가명 유아에 연결된 관찰 후보 수입니다.")
c4.metric(
    "유아 미지정", status["unmatched_candidates"],
    help=(
        "아직 가명 유아에 연결되지 않은 관찰 후보입니다. "
        "아래 ‘유아 매칭’에서 연결하면 유아별 현황에 반영됩니다. "
        "(누리영역 분류 유무와는 무관합니다.)"
    ),
)

if status["total_candidates"] == 0:
    st.warning(
        "아직 누적된 관찰 후보가 없습니다. ‘일일 영상 기록’에서 영상을 업로드하면 자동 분석되어 누적됩니다.",
        icon="📭",
    )


# ---------------------------------------------------------------------------
# 유아 매칭 (관찰 후보 → 등록 유아 연결) — 미지정이 있을 때만 노출
# ---------------------------------------------------------------------------
if _class_id is not None and status["unmatched_candidates"] > 0:
    st.subheader("유아 매칭 — 관찰 후보를 우리반 유아에 연결")
    st.caption(
        "AI는 임시 ID(child_A 등)로만 식별합니다. 교사가 등록 유아(가명)에 연결해야 "
        "유아별 수집 현황·매트릭스에 반영됩니다. 얼굴 매칭은 후보이며 확정은 교사가 합니다."
    )
    _class_videos = [v for v in repo.list_videos(owner=_owner) if v.class_id == _class_id]
    _class_children = repo.list_children(_class_id)
    render_matching_section(repo, _class_videos, _class_children, actor=get_current_actor(), key_prefix="dash")
    st.divider()


# ---------------------------------------------------------------------------
# 반 전체 영역별 분포
# ---------------------------------------------------------------------------
st.subheader("반 전체 — 누리영역별 수집 분포")
st.caption(
    "‘해당 후보 수’는 그 누리영역에 해당하는 관찰 후보의 수입니다. "
    "한 후보가 여러 영역에 해당할 수 있어, 영역 합계가 관찰 후보 수보다 클 수 있습니다."
)
_area_rows = [
    {
        "누리영역": a,
        "해당 후보 수": status["area_totals"][a],
        "상태": "⚠️ 부족" if a in status["area_shortage"] else "✅ 충분",
    }
    for a in NURI_AREAS
]
st.dataframe(_area_rows, use_container_width=True, hide_index=True)
if status.get("no_area_candidates", 0) > 0:
    st.caption(
        f"ℹ️ 누리영역이 분류되지 않은 후보 {status['no_area_candidates']}건은 위 영역 합계에 포함되지 않습니다."
    )
try:
    st.bar_chart({a: status["area_totals"][a] for a in NURI_AREAS})
except Exception:
    pass


# ---------------------------------------------------------------------------
# 유아 × 누리영역 매트릭스
# ---------------------------------------------------------------------------
st.subheader("유아 × 누리영역 매트릭스")

def _cell_text(cell: dict) -> str:
    n = cell["count"]
    mark = "⚠️" if cell.get("shortage") else ""
    return f"{n}{mark}"

if status["children"]:
    matrix_rows = []
    for ch in status["children"]:
        row = {"유아(가명)": ch["label"]}
        for a in NURI_AREAS:
            row[a] = _cell_text(ch["cells"][a])
        row["합계"] = ch["total"]
        matrix_rows.append(row)
    st.dataframe(matrix_rows, use_container_width=True, hide_index=True)
    st.caption("셀 숫자는 해당 유아·누리영역의 관찰 후보 수입니다. ⚠️ 표시는 권장 최소치 미만(부족) 셀입니다.")
else:
    st.info(
        "유아별 행이 없습니다. 위 ‘유아 매칭’에서 관찰 후보를 등록 유아(가명)에 연결하면 "
        "유아별 현황이 표시됩니다."
    )

# 유아 미지정 후보 안내 (영역별 분포 대신 행동 유도)
if status["unmatched_candidates"] > 0:
    st.info(
        f"유아 미지정 관찰 후보가 {status['unmatched_candidates']}건 있습니다. "
        "위 ‘유아 매칭’에서 연결하면 유아별 현황·매트릭스에 반영됩니다.",
        icon="🔗",
    )


# ---------------------------------------------------------------------------
# 보완 안내
# ---------------------------------------------------------------------------
st.subheader("보완 안내")
if status["shortage_notes"]:
    for note in status["shortage_notes"]:
        st.warning(note, icon="🎯")
else:
    st.success("현재 기준에서 부족한 영역·유아가 없습니다. 균형 있게 수집되고 있습니다.", icon="✅")

st.divider()
st.caption(
    "📌 이 화면은 수집량 현황입니다. 점수·발달·평정을 산출하지 않습니다. "
    "최종 관찰기록은 ‘주간 관찰초안’에서 교사가 확정합니다."
)
