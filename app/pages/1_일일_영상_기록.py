import _bootstrap  # noqa: F401  — 프로젝트 루트를 sys.path에 추가

import uuid
from datetime import date, datetime

import streamlit as st

from core.config import (
    DEFAULT_ACTOR, CLIPS_DIR, FRAMES_DIR, VIDEOS_DIR,
    VISION_DRY_RUN, VISION_MAX_SCENES_PER_VIDEO, VISION_MIN_SCENE_DURATION_SEC,
    VISION_PROVIDER, VISION_DEFAULT_MAX_SCENES_TEACHER,
)
from core.schemas import AuditLog
from services.auto_analysis_service import run_auto_analysis
from services.observation_service import (
    generate_mock_observation_candidates,
    generate_observation_candidates_with_provider,
)
from services.mapping.mapping_service import map_candidates_for_video
from services.security_service import delete_video_and_related_data
from services.video_service import save_uploaded_video
from services.video_preprocess_service import preprocess_video, select_scenes_for_analysis
from services.clip_service import select_evidence_clips, extract_clips
from storage.sqlite_repository import SqliteRepository

st.set_page_config(
    page_title="일일 영상 기록",
    page_icon="📹",
    layout="wide",
)

from _auth import require_login, render_user_sidebar, render_consent_form, get_current_actor
from _responsive import inject_responsive_css

require_login()
inject_responsive_css()

st.title("📹 일일 영상 기록")
st.caption("교사 시점 스마트안경 영상을 업로드하면 **업로드 즉시 자동 분석**되어 관찰 후보가 누적됩니다.")

with st.expander("📌 이 시스템의 원칙 (클릭하여 펼치기)", expanded=False):
    st.markdown(
        "- **원본 영상은 로컬에만 저장**됩니다. 외부 서버로 전송되지 않습니다.\n"
        "- 업로드 및 분석 시 **감사 로그(audit_log)** 가 기록됩니다.\n"
        "- AI는 관찰 후보를 *제안*할 뿐, 기록을 자동 확정하지 않습니다.\n"
        "- 교사는 **매일 개별 후보를 검토하지 않습니다.** 수집 현황을 확인하고 1~2주 주기로 확정합니다.\n"
        "- 유아는 `child_A`, `child_B` 임시 ID로만 식별됩니다. 교사가 가명 ID와 매칭합니다.\n"
        "- **관찰수준·발달·평정 점수는 산출하지 않습니다.**"
    )

st.divider()


# ---------------------------------------------------------------------------
# 비전 어댑터 현황 배지
# ---------------------------------------------------------------------------
_provider_display = VISION_PROVIDER.lower()
_external_real = _provider_display in ("claude", "external", "gemini") and not VISION_DRY_RUN
if _provider_display == "mock":
    st.info("🔧 **비전 어댑터**: Mock (외부 API 미사용) — 시연·테스트 모드", icon="ℹ️")
elif not _external_real:
    st.warning(
        f"🧪 **비전 어댑터**: {_provider_display} — 테스트 모드 (실제 API 호출 없음)",
        icon="⚠️",
    )
else:
    st.error(
        f"🌐 **비전 어댑터**: {_provider_display} — **실제 외부 API 호출 활성화** · 비용 발생 주의",
        icon="🚨",
    )


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
render_user_sidebar()
_role = st.session_state.get("role", "teacher")


# ---------------------------------------------------------------------------
# 헬퍼 함수
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
                use_container_width=True,
            )
        else:
            col.caption(f"t={frm.t:.1f}s (파일 없음)")


def _show_selected_scene_thumbnails(scenes, repo):
    """선별된 장면의 첫 번째 kept 프레임 썸네일을 표시한다."""
    from pathlib import Path
    if not scenes:
        return
    cols = st.columns(min(len(scenes), 5))
    for i, scene in enumerate(scenes):
        frames = [f for f in repo.list_frames(scene.id) if f.kept]
        if frames and Path(frames[0].image_path).exists():
            cols[i].image(
                frames[0].image_path,
                caption=f"장면{i+1}  {scene.time_start:.1f}s–{scene.time_end:.1f}s",
                use_container_width=True,
            )
        else:
            cols[i].caption(f"장면{i+1}\n썸네일 없음")


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


def _show_candidate_with_mappings(cand, mappings: list) -> None:
    """관찰 후보 + 누리/KICCE 매핑 후보를 함께 표시한다(모두 교사 검토 전 후보)."""
    st.markdown(f"**관찰 행동 (후보)**: {cand.observed_behavior}")
    st.markdown(f"**시각적 근거**: {cand.visual_evidence}")
    if cand.activity_context:
        st.caption(f"활동 맥락: {cand.activity_context}")

    nuri = [m for m in mappings if m.scale == "nuri"]
    kicce = [m for m in mappings if m.scale == "kicce"]

    if nuri:
        chips = "  ".join(f"`{m.area} ({m.confidence:.2f})`" for m in nuri)
        st.markdown(f"**누리 영역 후보**: {chips}")
    else:
        st.caption("누리 영역 후보: 없음")

    if kicce:
        st.markdown("**KICCE 문항 후보 (교사 검토 전)**")
        for m in kicce:
            st.markdown(
                f"- [문항 {m.item_id}] {m.item_text}  \n"
                f"  근거: {m.rationale} · 신뢰도 {m.confidence:.2f}"
            )
    else:
        st.caption("KICCE 문항 후보: 없음")


_STATUS_BADGE = {
    "queued": "⏳ 대기",
    "running": "🔄 분석 중",
    "done": "✅ 완료",
    "failed": "❌ 실패",
}


def _run_auto_analysis_with_progress(video_id: str, label: str) -> dict:
    """진행률 바를 표시하며 자동 분석을 실행한다(교사 흐름)."""
    bar = st.progress(0, text=f"{label}: 준비 중...")

    def _cb(pct: int, text: str) -> None:
        bar.progress(min(max(pct, 0), 100), text=f"{label}: {text}")

    result = run_auto_analysis(
        video_id, repo,
        frames_dir=FRAMES_DIR, clips_dir=CLIPS_DIR,
        actor=get_current_actor(), progress_cb=_cb,
        allow_external_real=False,  # 자동 분석은 외부 실호출하지 않는다(안전장치)
    )
    if result["status"] == "done":
        bar.progress(100, text=f"{label}: 완료")
    return result


# ===========================================================================
# 섹션 0: 관찰 메타데이터 입력 (업로드 전 필수)
# ===========================================================================
OBSERVATION_CONTEXTS = ["자유놀이", "야외활동", "실내놀이", "급식", "이야기나누기", "기타"]

st.subheader("관찰 메타데이터 입력")
st.caption("영상 업로드 전에 기관·클래스·촬영일·관찰 상황을 입력해주세요.")

with st.form("video_meta_form"):
    _meta_institution = st.text_input("기관명", placeholder="예: 행복유치원", help="필수 항목")
    _meta_class_name = st.text_input("클래스 이름", placeholder="예: 햇빛반", help="필수 항목")
    _meta_captured = st.date_input("촬영일", value=date.today(), help="누적·주간 묶음 기준일")
    _meta_obs_context = st.selectbox("관찰 상황", OBSERVATION_CONTEXTS)
    _meta_notes = st.text_area(
        "메모 (선택)",
        placeholder="관찰과 관련된 추가 정보를 입력해주세요.",
        max_chars=200,
    )
    _meta_submitted = st.form_submit_button("확인", use_container_width=True)

if _meta_submitted:
    if not _meta_institution.strip() or not _meta_class_name.strip():
        st.error("기관명과 클래스 이름은 필수 항목입니다.")
        st.stop()
    st.session_state["video_meta_confirmed"] = {
        "institution": _meta_institution.strip(),
        "class_name": _meta_class_name.strip(),
        "captured_date": _meta_captured.strftime("%Y-%m-%d"),
        "observation_context": _meta_obs_context,
        "notes": _meta_notes.strip(),
    }

if "video_meta_confirmed" not in st.session_state:
    st.info("기관명과 클래스 이름을 입력하고 '확인' 버튼을 눌러야 영상 업로드를 진행할 수 있습니다.")
    st.stop()

_confirmed_meta = st.session_state["video_meta_confirmed"]
st.success(
    f"메타데이터 확인됨 — 기관: {_confirmed_meta['institution']} / "
    f"클래스: {_confirmed_meta['class_name']} / "
    f"촬영일: {_confirmed_meta.get('captured_date', '-')} / "
    f"관찰 상황: {_confirmed_meta['observation_context']}"
)

st.divider()


# ===========================================================================
# 섹션 1: 영상 업로드 + 업로드 즉시 자동 분석
# ===========================================================================
st.subheader("1단계: 영상 업로드 (업로드 즉시 자동 분석)")
st.caption("여러 3분 클립을 한 번에 업로드할 수 있습니다(현장 검증 배치 업로드).")

uploaded_files = st.file_uploader(
    "스마트안경 녹화 영상을 선택하세요 (여러 클립 선택 가능)",
    type=["mp4", "mov", "m4v", "avi"],
    accept_multiple_files=True,
    help="지원 형식: mp4, mov, m4v, avi. 원본 영상은 로컬 data/videos/ 에만 저장됩니다.",
)

if uploaded_files:
    results: list[dict] = []
    for uf in uploaded_files:
        state_key = f"saved_video_{uf.name}_{uf.size}"
        if state_key in st.session_state:
            v = st.session_state[state_key]
            results.append({"파일명": uf.name, "상태": "↺ 기존 저장됨", "영상 ID": v.id})
            continue
        try:
            meta = st.session_state.get("video_meta_confirmed", {})
            with st.spinner(f"업로드 중: {uf.name}"):
                video = save_uploaded_video(
                    file_bytes=uf.read(), filename=uf.name,
                    repo=repo, videos_dir=VIDEOS_DIR, actor=get_current_actor(),
                    institution=meta.get("institution", ""),
                    class_name=meta.get("class_name", ""),
                    observation_context=meta.get("observation_context", ""),
                    notes=meta.get("notes", ""),
                    captured_date=meta.get("captured_date"),
                )
            st.session_state[state_key] = video

            # 교사: 업로드 즉시 자동 분석. 연구자: 업로드만(수동 분석은 아래 섹션).
            if _role == "teacher":
                res = _run_auto_analysis_with_progress(video.id, uf.name)
                if res["status"] == "done":
                    note = (
                        "외부 실호출 동의 필요(후보 보류)" if res["vision_skipped"]
                        else f"관찰 후보 {res['candidates']}개"
                    )
                    results.append({"파일명": uf.name, "상태": f"✅ 자동 분석 완료 · {note}",
                                    "영상 ID": video.id})
                else:
                    results.append({"파일명": uf.name, "상태": f"❌ 분석 실패: {res['error']}",
                                    "영상 ID": video.id})
            else:
                results.append({"파일명": uf.name, "상태": "✅ 업로드됨(연구자: 수동 분석)",
                                "영상 ID": video.id})
        except Exception as e:
            results.append({"파일명": uf.name, "상태": f"❌ 업로드 실패: {e}", "영상 ID": "-"})

    st.dataframe(results, use_container_width=True, hide_index=True)
    st.caption("업로드·분석 기록이 감사 로그(audit_log)에 저장되었습니다. ✅")
else:
    st.info("영상 파일을 선택하면 업로드와 (교사 모드) 자동 분석이 진행됩니다.")

st.divider()


# ===========================================================================
# 교사 모드: 분석 상태 대시보드 + 재시도
# ===========================================================================
if _role == "teacher":
    st.subheader("오늘 업로드한 영상 — 분석 상태")
    _my_videos = repo.list_videos(owner=get_current_actor())
    if not _my_videos:
        st.info("아직 업로드한 영상이 없습니다.")
    else:
        # 최근 업로드가 위로 오도록 역순
        for v in reversed(_my_videos):
            cand_n = len(repo.list_candidates(v.id))
            badge = _STATUS_BADGE.get(v.analysis_status, v.analysis_status)
            cols = st.columns([3, 2, 2, 2, 2])
            cols[0].markdown(f"**{v.filename}**  \n`{v.id}`")
            cols[1].markdown(f"촬영일\n\n{v.captured_date or '-'}")
            cols[2].markdown(f"상태\n\n{badge}")
            cols[3].markdown(f"진행률\n\n{v.progress}%")
            cols[4].markdown(f"관찰 후보\n\n{cand_n}개")
            if v.analysis_status == "failed":
                st.caption(f"⚠️ 실패 사유: {v.last_error or '알 수 없음'} (재시도 {v.retry_count}회)")
                if st.button("🔁 재시도", key=f"retry_{v.id}"):
                    res = _run_auto_analysis_with_progress(v.id, v.filename)
                    if res["status"] == "done":
                        st.success(f"재시도 성공 — 관찰 후보 {res['candidates']}개")
                    else:
                        st.error(f"재시도 실패: {res['error']}")
                    st.rerun()
            st.divider()
        st.caption(
            "교사는 매일 개별 후보를 검토하지 않습니다. "
            "‘수집 균형’에서 현황을 확인하고 1~2주 주기로 ‘주간 관찰초안’에서 확정하세요."
        )

        # ── 영상 삭제 (연구 종료 후 데이터 정리용) ──────────────────────────
        if _my_videos:
            st.divider()
            with st.expander("🗑️ 영상 삭제 (연구 종료 후 데이터 정리용)", expanded=False):
                st.warning(
                    "삭제하면 원본 영상과 모든 파생 데이터(프레임·관찰 후보·확정 기록 등)가 **영구 삭제**됩니다. "
                    "audit_log(접근·분석·삭제 이력)는 보존됩니다.",
                    icon="⚠️",
                )
                _del_options = {f"{v.filename}  [{v.id}]": v.id for v in _my_videos}
                _del_label = st.selectbox(
                    "삭제할 영상을 선택하세요",
                    options=list(_del_options.keys()),
                    key="del_video_select",
                )
                _del_video_id = _del_options[_del_label]
                _del_confirmed = st.checkbox(
                    "원본 영상과 모든 파생 프레임·관찰 후보·확정 기록을 삭제합니다. 이 작업은 되돌릴 수 없습니다.",
                    key="del_confirm_check",
                )
                if st.button(
                    "연구용 원본 영상 및 파생 프레임 삭제",
                    disabled=not _del_confirmed,
                    key="del_execute_btn",
                    type="primary",
                ):
                    try:
                        _del_result = delete_video_and_related_data(
                            _del_video_id, repo,
                            actor=get_current_actor(),
                            videos_dir=VIDEOS_DIR,
                            frames_dir=FRAMES_DIR,
                        )
                        st.success(
                            f"삭제 완료. 감사 로그(action=delete)에 기록되었습니다. "
                            f"(영상 파일 삭제: {_del_result['file_deleted']}, "
                            f"프레임 폴더 삭제: {_del_result['frames_dir_deleted']}, "
                            f"DB 행 삭제: {_del_result['db_rows_deleted']}건)",
                            icon="✅",
                        )
                        st.rerun()
                    except ValueError as e:
                        st.error(f"삭제 실패: {e}")

# ===========================================================================
# 연구자 모드: 수동 전처리 + AI 분석 (실호출 동의 게이트 포함)
# ===========================================================================
else:
    # ----- 섹션 2: 장면 분할 및 프레임 추출 -----
    st.subheader("2단계: 장면 분할 및 프레임 추출 (연구자)")
    st.info("비전 모델 입력을 위한 영상 전처리 단계입니다.")
    videos_all = repo.list_videos(owner=None)
    if not videos_all:
        st.warning("업로드된 영상이 없습니다. 1단계에서 먼저 영상을 업로드해주세요.")
    else:
        video_options = {f"{v.filename}  [{v.id}]": v.id for v in videos_all}
        selected_label = st.selectbox(
            "전처리할 영상을 선택하세요", options=list(video_options.keys()),
            key="preprocess_select",
        )
        selected_video_id_pre = video_options[selected_label]
        selected_video_pre = repo.get_video(selected_video_id_pre)

        existing_scenes = repo.list_scenes(selected_video_id_pre)
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
            disabled=(selected_video_pre is None), key="run_preprocess",
        )
        if run_btn and selected_video_pre is not None:
            scenes = frames = None
            with st.spinner("장면 분할 및 프레임 추출 중..."):
                try:
                    scenes, frames = preprocess_video(
                        video_id=selected_video_id_pre, repo=repo,
                        frames_dir=FRAMES_DIR, actor=get_current_actor(),
                    )
                except Exception as e:
                    st.error(f"전처리 실패: {e}")
            if scenes is not None and frames is not None:
                kept = [f for f in frames if f.kept]
                st.success(
                    f"전처리 완료! 장면 {len(scenes)}개 · 프레임 {len(frames)}개 · 품질 통과 {len(kept)}개"
                )
                try:
                    _show_thumbnails(frames)
                except Exception as thumb_err:
                    st.warning(f"프레임 미리보기 표시 중 오류: {thumb_err}")

    st.divider()

    # ----- 섹션 3: AI 비전 관찰 후보 생성 -----
    st.subheader("3단계: AI 비전 관찰 후보 생성 (연구자)")
    _ai_videos = [v for v in repo.list_videos(owner=None) if repo.list_scenes(v.id)]
    if not _ai_videos:
        st.warning("전처리 완료된 영상이 없습니다. 2단계를 먼저 실행해주세요.")
    else:
        _ai_opts = {f"{v.filename}  [{v.id}]": v.id for v in _ai_videos}
        _ai_label = st.selectbox("분석할 영상을 선택하세요", list(_ai_opts.keys()), key="ai_vision_select")
        _ai_vid = _ai_opts[_ai_label]

        _all_scenes = repo.list_scenes(_ai_vid)
        _all_frames = [f for s in _all_scenes for f in repo.list_frames(s.id)]
        _kept_frames = [f for f in _all_frames if f.kept]

        _max_scenes_ui = st.slider(
            "분석할 최대 장면 수", min_value=1,
            max_value=max(len(_all_scenes), 1),
            value=min(VISION_MAX_SCENES_PER_VIDEO, max(len(_all_scenes), 1)),
            step=1, key="ai_max_scenes_slider",
            help="장면 수가 많을수록 API 호출 횟수와 비용이 증가합니다.",
        )
        _preview_scenes = select_scenes_for_analysis(
            _all_scenes, _all_frames, max_scenes=_max_scenes_ui,
            min_scene_duration=VISION_MIN_SCENE_DURATION_SEC,
        )
        _pc1, _pc2, _pc3 = st.columns(3)
        _pc1.metric("전체 장면", len(_all_scenes))
        _pc2.metric("전체 kept 프레임", len(_kept_frames))
        _pc3.metric("선별 장면", len(_preview_scenes))

        _existing_cands = repo.list_candidates(_ai_vid)
        if _existing_cands:
            st.success(f"AI 관찰 후보 (교사 검토 전): 이미 {len(_existing_cands)}개 생성됨")

        if _external_real:
            _reason = st.text_input(
                "실호출 사유 (필수, 감사 로그에 기록됨)", key="ai_real_reason",
                placeholder="예: 현장 검증 1회차 - 클립 분석",
            )
            _confirm_cost = st.checkbox("실제 외부 API 호출과 비용 발생에 동의합니다.", key="ai_confirm_cost")
            _confirm_noret = st.checkbox("외부 제공자의 데이터 무보존·학습 미사용 조건을 확인했습니다.", key="ai_confirm_noret")
            _ready = bool(_reason.strip()) and _confirm_cost and _confirm_noret
            if not _ready:
                st.caption("사유 입력과 두 확인 항목을 모두 충족해야 실행할 수 있습니다.")
            if st.button(f"🌐 {_provider_display} 실호출 분석 실행", key="run_ai_real", disabled=not _ready):
                repo.write_audit(AuditLog(
                    id=f"audit_{_ai_vid}_approve_{uuid.uuid4().hex[:6]}",
                    video_id=_ai_vid, actor=get_current_actor(), action="analyze",
                    detail=f"external_real_call_approved provider={_provider_display} reason={_reason.strip()}",
                    created_at=datetime.now(),
                ))
                with st.spinner(f"{_provider_display} API 호출 중..."):
                    try:
                        _cands, _info = generate_observation_candidates_with_provider(
                            video_id=_ai_vid, repo=repo, actor=get_current_actor(),
                            max_scenes=_max_scenes_ui,
                        )
                        _mappings = map_candidates_for_video(_ai_vid, repo)
                        st.success(
                            f"분석 완료 — 관찰 후보 {_info['stored']}개 저장, 매핑 {len(_mappings)}건"
                        )
                        for _c2 in _cands:
                            with st.expander(
                                f"[{_c2.temp_child_id}] {_c2.time_start:.1f}s–{_c2.time_end:.1f}s "
                                f"| 신뢰도 {_c2.confidence:.2f}", expanded=True,
                            ):
                                _show_candidate_with_mappings(_c2, repo.list_mappings(_c2.id))
                    except Exception as _e:
                        st.error(f"API 분석 실패: {_e}")
        else:
            _btn_label = (
                "🔍 Mock 비전 관찰 후보 생성" if _provider_display == "mock"
                else f"🧪 {_provider_display} 테스트 분석 실행 (API 미호출)"
            )
            if st.button(_btn_label, key="run_ai_mock_or_dry"):
                with st.spinner("분석 중..."):
                    try:
                        if _provider_display == "mock":
                            _new_cands = generate_mock_observation_candidates(
                                video_id=_ai_vid, repo=repo, actor=get_current_actor(),
                                max_scenes=_max_scenes_ui,
                            )
                        else:
                            _new_cands, _info = generate_observation_candidates_with_provider(
                                video_id=_ai_vid, repo=repo, actor=get_current_actor(),
                                max_scenes=_max_scenes_ui,
                            )
                        if _new_cands:
                            _mappings = map_candidates_for_video(_ai_vid, repo)
                            st.success(
                                f"관찰 후보 {len(_new_cands)}개 생성, 누리/KICCE 매핑 {len(_mappings)}건"
                            )
                            for _c in _new_cands:
                                with st.expander(
                                    f"[{_c.temp_child_id}] {_c.time_start:.1f}s–{_c.time_end:.1f}s "
                                    f"| 신뢰도 {_c.confidence:.2f}", expanded=True,
                                ):
                                    _show_candidate_with_mappings(_c, repo.list_mappings(_c.id))
                        else:
                            st.warning("후보가 생성되지 않았습니다. 전처리 상태를 확인해주세요.")
                    except Exception as _e:
                        st.error(f"분석 실패: {_e}")

st.divider()
st.caption(
    "📌 원칙: AI 후보는 교사가 검토·확정합니다. 관찰수준·발달·평정 점수는 산출하지 않습니다. "
    "원본 영상은 외부로 전송되지 않습니다."
)
