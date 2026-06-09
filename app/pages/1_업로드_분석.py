import _bootstrap  # noqa: F401  — 프로젝트 루트를 sys.path에 추가
import streamlit as st

st.set_page_config(
    page_title="영상 업로드 및 분석",
    page_icon="📹",
    layout="wide",
)

st.title("📹 영상 업로드 및 배치 분석")
st.caption("교사 시점 영상을 업로드하고 AI 배치 분석을 실행합니다.")

st.divider()

st.info(
    "**구현 예정 기능**\n\n"
    "- 스마트안경 녹화 영상 파일 업로드\n"
    "- 영상 메타데이터 추출(길이·해상도·fps)\n"
    "- PySceneDetect 기반 장면 분할\n"
    "- 대표 프레임 추출 및 품질 필터(흐림 제거)\n"
    "- (보조) 오디오 분리 및 faster-whisper STT\n"
    "- 비전 LLM API 기반 구간별 관찰 후보 생성\n"
    "- 누리과정 5영역 분류 및 KICCE 문항 후보 매핑\n"
    "- 분석 진행 상태 표시"
)

st.warning("⚙️ 현재 단계: 기능 구현 전 (P0 골격)")
