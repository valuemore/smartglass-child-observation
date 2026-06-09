import _bootstrap  # noqa: F401  — 프로젝트 루트를 sys.path에 추가
import streamlit as st

st.set_page_config(
    page_title="교사 검토 및 확정",
    page_icon="✅",
    layout="wide",
)

st.title("✅ 교사 검토 및 확정")
st.caption("AI가 생성한 관찰 후보를 검토하고 최종 관찰기록을 확정합니다.")

st.divider()

st.info(
    "**구현 예정 기능**\n\n"
    "- 영상 플레이어 + 장면/구간 타임라인\n"
    "- `child_A`, `child_B` → 실제 가명 ID 매칭 (교사 수행)\n"
    "- 구간별 관찰 후보 카드: 시각적 근거 프레임·행동 서술·상호작용·맥락\n"
    "- 누리과정 영역 후보(칩) + KICCE 문항 후보(접이식) + 신뢰도 게이지\n"
    "- 후보 채택 / 수정(자유 편집) / 기각\n"
    "- 확정 저장 (작성자·시각 기록)"
)

st.warning("⚙️ 현재 단계: 기능 구현 전 (P0 골격)")

st.divider()
st.caption(
    "📌 원칙: AI는 후보만 제시합니다. 관찰기록 확정은 교사가 수행합니다. "
    "관찰수준 점수는 산출하지 않습니다."
)
