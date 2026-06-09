import _bootstrap  # noqa: F401  — 프로젝트 루트를 sys.path에 추가

import streamlit as st

from core.config import DEFAULT_ACTOR, FRAMES_DIR, VIDEOS_DIR
from services.observation_service import generate_mock_observation_candidates
from services.video_service import save_uploaded_video
from services.video_preprocess_service import preprocess_video
from storage.sqlite_repository import SqliteRepository

st.set_page_config(
    page_title="영상 업로드 및 분석",
    page_icon="📹",
    layout="wide",
)

st.title("📹 영상 업로드 및 배치 분석")
st.caption("교사 시점 스마트안경 영상을 업로드하고 AI 배치 분석을 실행합니다.")

with st.expander("📌 이 시스템의 원칙 (클릭하여 펼치기)", expanded=False):
    st.markdown(
        "- **원본 영상은 로컬에만 저장**됩니다. 외부 서버로 전송되지 않습니다.\n"
        "- 업로드 및 분석 시 **감사 로그(audit_log)** 가 기록됩니다.\n"
        "- AI는 관찰 후보를 *제안*할 뿐, 기록을 자동 확정하지 않습니다.\n"
        "- 유아는 `child_A`, `child_B` 임시 ID로만 식별됩니다. 교사가 가명 ID와 매칭합니다.\n"
        "- **관찰수준 점수는 산출하지 않습니다.**"
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
# 헬퍼 — 섹션 2·3 호출보다 먼저 정의
# ---------------------------------------------------------------------------
def _show_thumbnails(frames: list, title: str = "추출 프레임", max_items: int = 12) -> None:
    """품질 통과(kept=True) 프레임 썸네일을 최대 max_items 장 표시한다."""
    from pathlib import Path as _Path
    kept_frames = [f for f in frames if f.kept][:max_items]
    if not kept_frames:
        st.caption("품질 통과 프레임이 없습니다.")
        return
    st.markdown(f"**{title} (품질 통과)**")
    n_cols = min(4, len(kept_frames))
    cols = st.columns(n_cols)
    for i, frm in enumerate(kept_frames):
        col = cols[i % n_cols]
        img_path = _Path(frm.image_path)
        if img_path.exists():
            col.image(
                str(img_path),
                caption=f"t={frm.t:.1f}s  blur={frm.blur_score:.0f}",
                width='stretch',
            )
        else:
            col.caption(f"t={frm.t:.1f}s (파일 없음)")


def _show_candidate_card(cand) -> None:
    """AI 관찰 후보 1건을 카드 형태로 표시한다. 점수 필드 없음."""
    col1, col2 = st.columns([3, 1])
    with col1:
        st.markdown(f"**관찰 행동**: {cand.observed_behavior}")
        st.markdown(f"**시각적 근거**: {cand.visual_evidence}")
        if cand.activity_context:
            st.markdown(f"**활동 맥락**: {cand.activity_context}")
        if cand.peer_relation:
            st.markdown(f"**또래 관계**: {cand.peer_relation}")
        if cand.interaction:
            parts = []
            if cand.interaction.with_peers:
                parts.append(f"또래: {cand.interaction.with_peers}")
            if cand.interaction.with_teacher:
                parts.append(f"교사: {cand.interaction.with_teacher}")
            if cand.interaction.with_materials:
                parts.append(f"자료: {cand.interaction.with_materials}")
            if parts:
                st.markdown("**상호작용**: " + " | ".join(parts))
        if cand.audio_support:
            st.caption(f"보조 오디오: {cand.audio_support}")
    with col2:
        st.metric("임시 유아 ID", cand.temp_child_id)
        st.metric("신뢰도", f"{cand.confidence:.2f}")
        if cand.needs_teacher_review:
            st.warning("교사 확인 필요")


# ===========================================================================
# 섹션 1: 영상 파일 업로드
# ===========================================================================
st.subheader("1단계: 영상 파일 업로드")

uploaded_file = st.file_uploader(
    "스마트안경 녹화 영상을 선택하세요",
    type=["mp4", "mov", "m4v", "avi"],
    help="지원 형식: mp4, mov, m4v, avi. 원본 영상은 로컬 data/videos/ 에만 저장됩니다.",
)

if uploaded_file is not None:
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

    st.success("영상이 성공적으로 저장되었습니다.")
    with st.container(border=True):
        col1, col2 = st.columns(2)
        with col1:
            st.markdown(f"**파일명**: {video.filename}")
            st.markdown(f"**영상 ID**: `{video.id}`")
            st.markdown(f"**저장 경로**: `{video.stored_path}`")
        with col2:
            dur = f"{video.duration_sec:.1f}초" if video.duration_sec > 0 else "알 수 없음"
            fps_str = f"{video.fps:.2f} fps" if video.fps > 0 else "알 수 없음"
            res = f"{video.width}×{video.height}" if video.width > 0 else "알 수 없음"
            st.markdown(f"**길이**: {dur}")
            st.markdown(f"**FPS**: {fps_str}")
            st.markdown(f"**해상도**: {res}")
        st.markdown(
            f"**보관 기한**: {video.retention_until.strftime('%Y-%m-%d') if video.retention_until else '없음'}"
        )
        st.markdown("**감사 로그**: 업로드 기록이 저장되었습니다. ✅")

else:
    st.info("영상 파일을 선택하면 업로드와 메타데이터 추출이 자동으로 진행됩니다.")

st.divider()


# ===========================================================================
# 섹션 2: 장면 분할 및 프레임 추출
# ===========================================================================
st.subheader("2단계: 장면 분할 및 프레임 추출")
st.info("이번 단계는 비전 모델 입력을 위한 영상 전처리 단계입니다.")

videos = repo.list_videos()

if not videos:
    st.warning("업로드된 영상이 없습니다. 1단계에서 먼저 영상을 업로드해주세요.")
else:
    video_options = {f"{v.filename}  [{v.id}]": v.id for v in videos}
    selected_label = st.selectbox(
        "전처리할 영상을 선택하세요",
        options=list(video_options.keys()),
        key="preprocess_select",
    )
    selected_video_id = video_options[selected_label]
    selected_video = repo.get_video(selected_video_id)

    existing_scenes = repo.list_scenes(selected_video_id)
    if existing_scenes:
        existing_frames: list = []
        for sc in existing_scenes:
            existing_frames.extend(repo.list_frames(sc.id))
        kept_count = sum(1 for f in existing_frames if f.kept)
        st.success(
            f"이미 전처리 완료: 장면 {len(existing_scenes)}개 · "
            f"프레임 {len(existing_frames)}개 (품질 통과: {kept_count}개)"
        )
        try:
            _show_thumbnails(existing_frames)
        except Exception as thumb_err:
            st.warning(f"프레임 미리보기 표시 중 오류: {thumb_err}")

    run_btn = st.button(
        "🎬 장면 분할 및 프레임 추출 실행",
        disabled=(selected_video is None),
        key="run_preprocess",
    )

    if run_btn and selected_video is not None:
        # ── 전처리 실행 (별도 try/except) ──────────────────────────────
        scenes = None
        frames = None
        with st.spinner("장면 분할 및 프레임 추출 중... 영상 길이에 따라 수십 초 소요될 수 있습니다."):
            try:
                scenes, frames = preprocess_video(
                    video_id=selected_video_id,
                    repo=repo,
                    frames_dir=FRAMES_DIR,
                    actor=DEFAULT_ACTOR,
                )
            except Exception as e:
                st.error(f"전처리 실패: {e}")

        # ── 결과 표시 (전처리 성공 시에만) ─────────────────────────────
        if scenes is not None and frames is not None:
            kept = [f for f in frames if f.kept]
            has_fallback = any(f.blur_score < 50.0 and f.kept for f in frames)
            fallback_note = " (일부 fallback 선택 포함)" if has_fallback else ""
            st.success(
                f"전처리 완료! 장면 {len(scenes)}개 · 프레임 {len(frames)}개 · "
                f"품질 통과 {len(kept)}개{fallback_note}"
            )
            with st.container(border=True):
                col1, col2, col3 = st.columns(3)
                col1.metric("장면 수", len(scenes))
                col2.metric("추출 프레임", len(frames))
                col3.metric("품질 통과 (kept)", len(kept))
                if has_fallback:
                    st.caption(
                        "ℹ️ 일부 프레임은 blur 품질 기준 미달이나 "
                        "scene별 최소 1장 보장을 위해 fallback으로 선택되었습니다."
                    )
                st.markdown("**감사 로그**: 전처리(analyze) 기록이 저장되었습니다. ✅")

            # ── 썸네일 표시 (별도 try/except) ─────────────────────────
            try:
                _show_thumbnails(frames)
            except Exception as thumb_err:
                st.warning(f"프레임 미리보기 표시 중 오류: {thumb_err}")

st.divider()


# ===========================================================================
# 섹션 3: Mock 비전 관찰 후보 생성
# ===========================================================================
st.subheader("3단계: Mock 비전 관찰 후보 생성")
st.info(
    "전처리 완료된 영상에서 Mock 비전 어댑터로 관찰 후보를 생성합니다. "
    "실제 비전 LLM API는 호출되지 않습니다. "
    "AI는 후보를 제시하고 교사가 검토·확정합니다."
)

videos_with_scenes = [v for v in repo.list_videos() if repo.list_scenes(v.id)]

if not videos_with_scenes:
    st.warning(
        "전처리 완료된 영상이 없습니다. "
        "먼저 2단계에서 장면 분할 및 프레임 추출을 완료해주세요."
    )
else:
    mock_options = {f"{v.filename}  [{v.id}]": v.id for v in videos_with_scenes}
    mock_label = st.selectbox(
        "Mock 분석할 영상을 선택하세요",
        options=list(mock_options.keys()),
        key="mock_vision_select",
    )
    mock_video_id = mock_options[mock_label]

    # ── 기존 후보 표시 ─────────────────────────────────────────────────
    existing_cands = repo.list_candidates(mock_video_id)
    if existing_cands:
        st.success(f"AI 관찰 후보 (교사 검토 전): 이미 {len(existing_cands)}개 생성됨")
        for cand in existing_cands:
            with st.expander(
                f"[{cand.temp_child_id}]  {cand.time_start:.1f}s – {cand.time_end:.1f}s"
                f"  | 신뢰도 {cand.confidence:.2f}",
                expanded=False,
            ):
                _show_candidate_card(cand)

    mock_btn = st.button(
        "🔍 Mock 비전 관찰 후보 생성",
        key="run_mock_vision",
        help="실제 외부 API를 호출하지 않는 Mock 분석입니다.",
    )

    if mock_btn:
        new_cands = None
        with st.spinner("Mock 비전 분석 중..."):
            try:
                new_cands = generate_mock_observation_candidates(
                    video_id=mock_video_id,
                    repo=repo,
                    actor=DEFAULT_ACTOR,
                )
            except Exception as e:
                st.error(f"Mock 비전 분석 실패: {e}")

        if new_cands is not None:
            if new_cands:
                st.success(
                    f"AI 관찰 후보 (교사 검토 전) {len(new_cands)}개가 생성되었습니다."
                )
                for cand in new_cands:
                    with st.expander(
                        f"[{cand.temp_child_id}]  {cand.time_start:.1f}s – {cand.time_end:.1f}s"
                        f"  | 신뢰도 {cand.confidence:.2f}",
                        expanded=True,
                    ):
                        _show_candidate_card(cand)
                st.markdown("**감사 로그**: Mock 분석(analyze) 기록이 저장되었습니다. ✅")
            else:
                # ── 진단 정보 표시 ─────────────────────────────────────
                diag_scenes = repo.list_scenes(mock_video_id)
                diag_frames: list = []
                for sc in diag_scenes:
                    diag_frames.extend(repo.list_frames(sc.id))
                diag_kept = [f for f in diag_frames if f.kept]

                with st.container(border=True):
                    st.markdown("**후보 생성 진단 정보**")
                    c1, c2, c3 = st.columns(3)
                    c1.metric("scene 수", len(diag_scenes))
                    c2.metric("frame 수", len(diag_frames))
                    c3.metric("kept frame 수", len(diag_kept))

                if not diag_scenes:
                    st.warning(
                        "전처리된 장면이 없습니다. "
                        "2단계에서 장면 분할 및 프레임 추출을 먼저 실행해주세요."
                    )
                elif not diag_kept:
                    st.error(
                        "kept=True 프레임이 DB에 없습니다. "
                        "이전 전처리 데이터에 kept 값이 올바르게 저장되지 않았을 수 있습니다. "
                        "**2단계에서 해당 영상의 장면 분할 및 프레임 추출을 다시 실행해주세요.**"
                    )
                else:
                    st.error(
                        f"kept 프레임이 {len(diag_kept)}개 있는데 후보가 생성되지 않았습니다. "
                        "후보 생성 로직을 점검해주세요."
                    )

st.divider()
st.caption(
    "📌 원칙: AI 후보는 교사가 검토·확정합니다. 관찰수준 점수는 산출하지 않습니다. "
    "원본 영상은 외부로 전송되지 않습니다."
)
