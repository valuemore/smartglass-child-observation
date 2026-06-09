import streamlit as st

from core.config import APP_CAPTION, APP_TITLE, DB_PATH
from storage.sqlite_repository import SqliteRepository

st.set_page_config(
    page_title=APP_TITLE,
    page_icon="👁️",
    layout="wide",
)


# ---------------------------------------------------------------------------
# 저장소 초기화 (앱 생애주기 1회, 재실행 시 재사용)
# ---------------------------------------------------------------------------

@st.cache_resource
def get_repo() -> SqliteRepository:
    repo = SqliteRepository(DB_PATH)
    repo.init_schema()
    return repo


repo = get_repo()

# ---------------------------------------------------------------------------
# 헤더
# ---------------------------------------------------------------------------

st.title(APP_TITLE)
st.caption(APP_CAPTION)

st.divider()

# ---------------------------------------------------------------------------
# 안내 배너
# ---------------------------------------------------------------------------

st.warning(
    "본 시스템은 스마트안경 교사 시점 영상에서 유아 행동 관찰 후보를 추출하는 **연구용 시연 시스템**입니다.",
    icon="🔬",
)
st.info(
    "AI 결과는 관찰기록 확정값이 아니라 **교사 검토 전 후보**입니다. 최종 관찰기록은 교사가 검토·수정·확정합니다.",
    icon="👩‍🏫",
)
st.info(
    "원본 영상 접근과 분석은 **감사 로그에 기록**됩니다.",
    icon="🔒",
)

st.divider()

# ---------------------------------------------------------------------------
# 영상 목록
# ---------------------------------------------------------------------------

st.subheader("등록된 영상 목록")

videos = repo.list_videos()

if not videos:
    st.info(
        "아직 등록된 영상이 없습니다. "
        "다음 단계에서 영상 업로드 기능을 구현합니다.",
        icon="📭",
    )
else:
    STATUS_LABEL = {
        "uploaded":  "업로드됨",
        "analyzing": "분석 중",
        "analyzed":  "분석 완료",
        "reviewed":  "검토 완료",
    }

    rows = []
    for v in videos:
        rows.append({
            "영상 ID": v.id,
            "파일명": v.filename,
            "상태": STATUS_LABEL.get(v.status, v.status),
            "등록일시": v.created_at.strftime("%Y-%m-%d %H:%M") if v.created_at else "-",
            "보관 기한": v.retention_until.strftime("%Y-%m-%d") if v.retention_until else "미설정",
        })

    st.dataframe(rows, use_container_width=True, hide_index=True)
    st.caption(f"총 {len(videos)}개 영상")

st.divider()

# ---------------------------------------------------------------------------
# 개발 단계 카드
# ---------------------------------------------------------------------------

st.subheader("개발 진행 상태")

col_cur, col_nxt = st.columns(2)
with col_cur:
    st.success("**현재 단계**: Home 화면과 저장소 연결", icon="✅")
with col_nxt:
    st.info("**다음 단계**: 영상 업로드 기능 구현", icon="⏭️")

st.divider()

# ---------------------------------------------------------------------------
# 시연 흐름
# ---------------------------------------------------------------------------

st.subheader("시연 흐름")

steps = [
    ("1단계", "교사 시점 영상 업로드", "스마트안경으로 촬영한 교사 1인칭 영상을 업로드합니다."),
    ("2단계", "장면 분할 및 프레임 추출", "영상을 장면 단위로 분할하고 대표 프레임을 추출합니다."),
    ("3단계", "비전 모델 기반 관찰 후보 생성", "프레임을 비전 LLM으로 분석해 유아 행동·상호작용·맥락 관찰 후보를 생성합니다."),
    ("4단계", "누리과정·KICCE 문항 후보 매핑", "관찰 후보를 누리과정 5영역으로 1차 분류하고, KICCE 유아관찰척도 문항 후보를 매핑합니다."),
    ("5단계", "교사 검토·수정·확정", "교사가 child_A/B ↔ 가명 ID를 매칭하고, 후보를 채택·수정·기각하여 최종 관찰기록을 확정합니다."),
    ("6단계", "연구자 리포트 확인", "누리 영역 분포, KICCE 문항 커버리지, AI 후보 대비 교사 확정 비교 지표를 확인합니다."),
]

cols = st.columns(3)
for i, (step, title, desc) in enumerate(steps):
    with cols[i % 3]:
        st.info(f"**{step}: {title}**\n\n{desc}")

st.divider()

# ---------------------------------------------------------------------------
# 핵심 원칙
# ---------------------------------------------------------------------------

st.subheader("핵심 원칙")
principles = [
    "AI 분석의 중심 데이터는 **영상**입니다. 오디오는 보조 증거로만 활용합니다.",
    "AI는 관찰기록을 **확정하지 않고 후보만 제시**합니다. 교사가 최종 검토·수정·확정합니다.",
    "AI는 유아를 자동 식별하지 않습니다. `child_A`, `child_B` 임시 ID를 부여하고 **교사가 가명 ID와 매칭**합니다.",
    "**KICCE 유아관찰척도 60문항 매핑은 이 시스템의 핵심 기능**입니다(후보 제시, 확정 아님).",
    "**관찰수준 점수는 산출하지 않습니다.**",
]
for p in principles:
    st.markdown(f"- {p}")
