"""모바일 반응형 CSS 헬퍼.

inject_responsive_css()를 각 페이지 상단에서 호출하면
768px 이하 모바일 화면에서 컬럼 세로 스택·버튼 터치 영역 확보 등이 적용된다.
"""

import streamlit as st

_MOBILE_CSS = """
<style>
/* ── 모바일 기준선: 768px 이하 ─────────────────────────── */
@media (max-width: 768px) {

  /* st.columns() → 세로 스택 */
  [data-testid="column"] {
    width: 100% !important;
    flex: 1 1 100% !important;
    min-width: 100% !important;
  }

  /* 버튼 터치 영역 최소 48px, 전체 폭 */
  .stButton > button {
    min-height: 48px !important;
    font-size: 16px !important;
    width: 100% !important;
  }

  /* 메트릭 카드 2열 유지 (너무 좁아지지 않도록) */
  [data-testid="metric-container"] {
    min-width: 45% !important;
  }

  /* 텍스트 입력 16px — iOS Safari 자동 확대(zoom) 방지 */
  input[type="text"],
  input[type="password"],
  textarea,
  select {
    font-size: 16px !important;
  }

  /* 파일 업로더 드롭존 터치 여백 확대 */
  [data-testid="stFileUploaderDropzone"] {
    padding: 24px 16px !important;
  }

  /* expander 헤더 터치 영역 확대 */
  [data-testid="stExpander"] summary {
    padding: 14px 8px !important;
    font-size: 15px !important;
  }

  /* 슬라이더 트랙 터치 영역 확대 */
  [data-testid="stSlider"] {
    padding: 8px 0 !important;
  }

  /* radio 옵션 간격 확대 */
  [data-testid="stRadio"] label {
    padding: 6px 8px !important;
  }
}
</style>
"""


def inject_responsive_css() -> None:
    """반응형 CSS를 페이지에 주입한다. 모든 페이지 상단에서 1회 호출."""
    st.markdown(_MOBILE_CSS, unsafe_allow_html=True)
