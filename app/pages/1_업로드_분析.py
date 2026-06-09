import _bootstrap  # noqa: F401  — 프로젝트 루트를 sys.path에 추가

import streamlit as st

from core.config import APP_TITLE, DEFAULT_ACTOR, VIDEOS_DIR
from services.video_service import save_uploaded_video
from storage.sqlite_repository import SqliteRepository

st.set_page_config(
    page_title="영상 업로드 및 분석",
    page_icon="📹",
    layout="wide",
)

st.title("📹 영상 업로드 및 배치 분석")
st.caption("교사 시점 스마트안경 영상을 업로드하고 AI 배치 분석을 실행합니다.")

# ---------------------------------------------------------------------------
# 연구 원칙 안내
# ---------------------------------------------------------------------------
with st.expander("📌 이 시스템의 원칙 (클릭하여 펼치기)", expanded=False):
    st.markdown(
        "- **원본 영상은 로컬에만 저장**됩니다. 외부 서버로 전송되지 않습니다.\n"
        "- 업로드 즉시 **감사 로그(audit_log)** 가 기록됩니다.\n"
        "- AI는 관찰 후보를 *제안*할 뿐, 기록을 자동 확정하지 않습니다.\n"
        "- 유아는 `child_A`, `child_B` 임시 ID로만 식별됩니다. 교사가 가명 ID와 매칭합니다.\n"
        "- **관찰수준 점수는 산출하지 않습니다.**"
    )

st.divider()

# ---------------------------------------------------------------------------
# 저장소 — Home.py 와 동일한 singleton 사용
# ---------------------------------------------------------------------------
@st.cache_resource
def get_repo() -> SqliteRepository:
    from core.config import DB_PATH
    repo = SqliteRepository(DB_PATH)
    repo.init_schema()
    return repo

repo = get_repo()

# ---------------------------------------------------------------------------
# 파일 업로드 UI
# ---------------------------------------------------------------------------
st.subheader("1단계: 영상 파일 업로드")

uploaded_file = st.file_uploader(
    "스마트안경 녹화 영상을 선택하세요",
    type=["mp4", "mov", "m4v", "avi"],
    help="지원 형식: mp4, mov, m4v, avi. 원본 영상은 로컬 data/videos/ 에만 저장됩니다.",
)

if uploaded_file is not None:
    # 이중 저장 방지 — session_state에 이미 저장된 video_id 확인
    state_key = f"saved_video_{uploaded_file.name}_{uploaded_file.size}"

    if state_key not in st.session_state:
        with st.spinner("영상을 저장하고 메타데이터를 추출하는 중..."):
            file_bytes = uploaded_file.read()
            try:
                video = save_uploaded_video(
                    file_bytes=file_bytes,
                    filename=uploaded_file.name,
                    repo=repo,
                    videos_dir=VIDEOS_DIR,
                    actor=DEFAULT_ACTOR,
                )
                st.session_state[state_key] = video
            except Exception as e:
                st.error(f"업로드 실패: {e}")
                st.stop()

    video = st.session_state[state_key]

    # 업로드 결과 카드
    st.success("영상이 성공적으로 저장되었습니다.")
    with st.container(border=True):
        col1, col2 = st.columns(2)
        with col1:
            st.markdown(f"**파일명**: {video.filename}")
            st.markdown(f"**영상 ID**: `{video.id}`")
            st.markdown(f"**저장 경로**: `{video.stored_path}`")
        with col2:
            dur = f"{video.duration_sec:.1f}초" if video.duration_sec > 0 else "알 수 없음"
            fps = f"{video.fps:.2f} fps" if video.fps > 0 else "알 수 없음"
            res = f"{video.width}×{video.height}" if video.width > 0 else "알 수 없음"
            st.markdown(f"**길이**: {dur}")
            st.markdown(f"**FPS**: {fps}")
            st.markdown(f"**해상도**: {res}")
        st.markdown(
            f"**보관 기한**: {video.retention_until.strftime('%Y-%m-%d') if video.retention_until else '없음'}"
        )
        st.markdown("**감사 로그**: 업로드 기록이 저장되었습니다. ✅")

    st.divider()

    # ---------------------------------------------------------------------------
    # 2단계: 배치 분석 (현재 비활성)
    # ---------------------------------------------------------------------------
    st.subheader("2단계: 배치 분석 실행")
    st.info(
        "**분석 단계 (구현 예정)**\n\n"
        "- PySceneDetect 장면 분할\n"
        "- 대표 프레임 추출 및 흐림 필터\n"
        "- (보조) 오디오 분리 및 STT\n"
        "- 비전 LLM 구간별 관찰 후보 생성\n"
        "- 누리과정 5영역 분류 및 KICCE 문항 후보 매핑"
    )
    st.button(
        "🔍 배치 분석 시작",
        disabled=True,
        help="현재 단계에서는 비활성화되어 있습니다. P2 단계에서 구현됩니다.",
    )

else:
    st.info("영상 파일을 선택하면 업로드와 메타데이터 추출이 자동으로 진행됩니다.")

st.divider()
st.caption(
    "📌 원칙: AI 후보는 교사가 검토·확정합니다. 관찰수준 점수는 산출하지 않습니다. "
    "원본 영상은 외부로 전송되지 않습니다."
)
