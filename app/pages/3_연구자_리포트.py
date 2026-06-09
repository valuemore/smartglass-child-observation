import _bootstrap  # noqa: F401  — 프로젝트 루트를 sys.path에 추가
import streamlit as st

st.set_page_config(
    page_title="연구자 리포트",
    page_icon="📊",
    layout="wide",
)

st.title("📊 연구자 리포트")
st.caption("유아별 확정 관찰기록과 AI 후보 대비 교사 확정 비교 지표를 확인합니다.")

st.divider()

st.info(
    "**구현 예정 기능**\n\n"
    "- 유아별(가명 ID) 확정 관찰기록 모음 (시간순)\n"
    "- 누리과정 5영역 분포 차트\n"
    "- KICCE 문항 커버리지 (후보 기준)\n"
    "- AI 후보 vs 교사 확정 비교: 채택률·수정률·기각률·신뢰도 구간별 채택률\n"
    "- 활동 맥락 타임라인\n"
    "- JSON/CSV export + 발표용 요약"
)

st.warning("⚙️ 현재 단계: 기능 구현 전 (P0 골격)")

st.divider()
st.caption("📌 원칙: 관찰수준 점수는 산출하지 않습니다.")
