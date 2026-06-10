import _bootstrap  # noqa: F401  — 프로젝트 루트를 sys.path에 추가

import uuid
from datetime import datetime

import streamlit as st

from core.config import DEFAULT_ACTOR, FRAMES_DIR, VIDEOS_DIR, VISION_DRY_RUN, VISION_PROVIDER
from core.schemas import AuditLog
from services.observation_service import (
    generate_mock_observation_candidates,
    generate_observation_candidates_with_provider,
)
from services.mapping.mapping_service import map_candidates_for_video
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
# 비전 어댑터 현황 배지
# ---------------------------------------------------------------------------
_provider_display = VISION_PROVIDER.lower()
if _provider_display == "mock":
    st.info("🔧 **비전 어댑터**: Mock (외부 API 미사용) — 시연·테스트 모드", icon="ℹ️")
elif _provider_display in ("claude", "external") and VISION_DRY_RUN:
    st.warning(
        f"🧪 **비전 어댑터**: {"Claude (Anthropic)" if _provider_display == "claude" else "External"} (dry_run=True) — payload 검증만 수행, 실제 API 호출 없음",
        icon="⚠️",
    )
elif _provider_display in ("claude", "external") and not VISION_DRY_RUN:
    st.error(
        f"🌐 **비전 어댑터**: {"Claude (Anthropic)" if _provider_display == "claude" else "External"} (dry_run=False) — **실제 외부 API 호출 활성화** · 비용 발생 주의",
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


# ===========================================================================
# 섹션 1: 영상 파일 업로드
# ===========================================================================
st.subheader("1단계: 영상 파일 업로드")

st.caption("여러 3분 클립을 한 번에 업로드할 수 있습니다(현장 검증 배치 업로드).")

uploaded_files = st.file_uploader(
    "스마트안경 녹화 영상을 선택하세요 (여러 클립 선택 가능)",
    type=["mp4", "mov", "m4v", "avi"],
    accept_multiple_files=True,
    help="지원 형식: mp4, mov, m4v, avi. 원본 영상은 로컬 data/videos/ 에만 저장됩니다.",
)

if uploaded_files:
    results: list[dict] = []
    progress = st.progress(0.0, text="업로드 준비 중...")
    for i, uf in enumerate(uploaded_files):
        progress.progress(i / len(uploaded_files), text=f"업로드 중: {uf.name}")
        state_key = f"saved_video_{uf.name}_{uf.size}"
        if state_key in st.session_state:
            v = st.session_state[state_key]
            results.append({"파일명": uf.name, "상태": "↺ 기존 저장됨",
                            "영상 ID": v.id, "길이(초)": round(v.duration_sec, 1)})
            continue
        try:
            video = save_uploaded_video(
                file_bytes=uf.read(), filename=uf.name,
                repo=repo, videos_dir=VIDEOS_DIR, actor=DEFAULT_ACTOR,
            )
            st.session_state[state_key] = video
            results.append({"파일명": uf.name, "상태": "✅ 저장됨",
                            "영상 ID": video.id, "길이(초)": round(video.duration_sec, 1)})
        except Exception as e:
            results.append({"파일명": uf.name, "상태": "❌ 실패",
                            "영상 ID": "-", "길이(초)": f"오류: {e}"})
    progress.progress(1.0, text="업로드 완료")

    ok = sum(1 for r in results if r["상태"].startswith("✅"))
    skipped = sum(1 for r in results if r["상태"].startswith("↺"))
    failed = sum(1 for r in results if r["상태"].startswith("❌"))
    st.success(f"업로드 처리 완료 — 신규 {ok}건 · 기존 {skipped}건 · 실패 {failed}건")
    st.dataframe(results, use_container_width=True, hide_index=True)
    st.caption("각 영상의 업로드 기록이 감사 로그(audit_log)에 저장되었습니다. ✅")

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
# 섹션 3: AI 비전 관찰 후보 생성
# ===========================================================================
st.subheader("3단계: AI 비전 관찰 후보 생성")

_ai_prov = VISION_PROVIDER.lower()
_prov_label = (
    "Claude (Anthropic)" if _ai_prov == "claude"
    else ("External" if _ai_prov == "external" else "Mock")
)

if _ai_prov == "mock":
    st.info(
        "🔧 현재 Mock 어댑터로 동작합니다. 실제 API는 호출되지 않습니다.\n\n"
        "Claude 연동: `.env`에서 `VISION_PROVIDER=claude`, `ANTHROPIC_API_KEY=sk-ant-...` 를 설정하세요.",
        icon="ℹ️",
    )
elif VISION_DRY_RUN:
    st.warning(
        f"🧪 **{_prov_label}** (dry_run=True) — payload 빌드·guard 검증만 수행, 실제 API 미호출.",
        icon="⚠️",
    )
else:
    st.error(
        f"🌐 **{_prov_label}** (dry_run=False) — **실제 외부 API 호출, 비용 발생**. "
        "선별 프레임만 전송됩니다.",
        icon="🚨",
    )

_ai_videos = [v for v in repo.list_videos() if repo.list_scenes(v.id)]

if not _ai_videos:
    st.warning("전처리 완료된 영상이 없습니다. 2단계에서 장면 분할 및 프레임 추출을 먼저 실행해주세요.")
else:
    _ai_opts = {f"{v.filename}  [{v.id}]": v.id for v in _ai_videos}
    _ai_label = st.selectbox("분석할 영상을 선택하세요", list(_ai_opts.keys()), key="ai_vision_select")
    _ai_vid = _ai_opts[_ai_label]

    # 기존 후보 표시
    _existing_cands = repo.list_candidates(_ai_vid)
    if _existing_cands:
        st.success(f"AI 관찰 후보 (교사 검토 전): 이미 {len(_existing_cands)}개 생성됨")
        for _c in _existing_cands:
            with st.expander(
                f"[{_c.temp_child_id}]  {_c.time_start:.1f}s – {_c.time_end:.1f}s"
                f"  | 신뢰도 {_c.confidence:.2f}",
                expanded=False,
            ):
                _show_candidate_card(_c)

    # --- 실호출 게이트 (claude/external + dry_run=False) ---
    if _ai_prov != "mock" and not VISION_DRY_RUN:
        _reason = st.text_input(
            "실호출 사유 (필수, 감사 로그에 기록됨)",
            key="ai_real_reason",
            placeholder="예: 현장 검증 1회차 - 클립 분석",
        )
        _confirm_cost = st.checkbox(
            "실제 외부 API 호출과 비용 발생에 동의합니다.", key="ai_confirm_cost",
        )
        _confirm_noret = st.checkbox(
            "외부 제공자의 데이터 무보존·학습 미사용 조건을 확인했습니다.", key="ai_confirm_noret",
        )
        _ready = bool(_reason.strip()) and _confirm_cost and _confirm_noret
        if not _ready:
            st.caption("사유 입력과 두 확인 항목을 모두 충족해야 실행할 수 있습니다.")

        if st.button(
            f"🌐 {_prov_label} 실호출 분析 실행",
            key="run_ai_real",
            disabled=not _ready,
        ):
            repo.write_audit(AuditLog(
                id=f"audit_{_ai_vid}_approve_{uuid.uuid4().hex[:6]}",
                video_id=_ai_vid, actor=DEFAULT_ACTOR, action="analyze",
                detail=f"external_real_call_approved provider={_ai_prov} reason={_reason.strip()}",
                created_at=datetime.now(),
            ))
            with st.spinner(f"{_prov_label} API 호출 중... 영상 길이에 따라 수십 초 소요됩니다."):
                try:
                    _cands, _info = generate_observation_candidates_with_provider(
                        video_id=_ai_vid, repo=repo, actor=DEFAULT_ACTOR,
                    )
                    _mappings = map_candidates_for_video(_ai_vid, repo)
                    st.success(
                        f"분析 완료 — provider={_info['provider']}, "
                        f"model={_info['model']}, "
                        f"관찰 후보 {_info['stored']}개 저장 (폐기 {_info['discarded']}개), "
                        f"누리/KICCE 매핑 {len(_mappings)}건"
                    )
                    if _info.get("fallback_reason"):
                        st.caption(f"폴백 사유: {_info['fallback_reason']}")
                    st.markdown("**감사 로그**: 승인 사유 및 analyze 기록이 저장되었습니다. ✅")
                    for _c2 in _cands:
                        with st.expander(
                            f"[{_c2.temp_child_id}]  "
                            f"{_c2.time_start:.1f}s – {_c2.time_end:.1f}s  "
                            f"| 신뢰도 {_c2.confidence:.2f}",
                            expanded=True,
                        ):
                            _show_candidate_with_mappings(_c2, repo.list_mappings(_c2.id))
                except Exception as _e:
                    st.error(f"API 분析 실패: {_e}")

    # --- Mock 또는 dry_run 분析 버튼 ---
    else:
        _btn_label = (
            "🔍 Mock 비전 관찰 후보 생성" if _ai_prov == "mock"
            else f"🧪 {_prov_label} dry_run 분析 실행 (API 미호출)"
        )
        if st.button(_btn_label, key="run_ai_mock_or_dry"):
            with st.spinner("분析 중..."):
                try:
                    if _ai_prov == "mock":
                        _new_cands = generate_mock_observation_candidates(
                            video_id=_ai_vid, repo=repo, actor=DEFAULT_ACTOR,
                        )
                        _info_label = "Mock"
                        _fallback = None
                    else:
                        _new_cands, _info = generate_observation_candidates_with_provider(
                            video_id=_ai_vid, repo=repo, actor=DEFAULT_ACTOR,
                        )
                        _info_label = (
                            f"dry_run provider={_info['provider']} "
                            f"model={_info['model'] or '(미설정)'}"
                        )
                        _fallback = _info.get("fallback_reason")

                    if _new_cands:
                        _mappings = map_candidates_for_video(_ai_vid, repo)
                        st.success(
                            f"관찰 후보 {len(_new_cands)}개 생성 ({_info_label}), "
                            f"누리/KICCE 매핑 {len(_mappings)}건"
                        )
                        if _fallback:
                            st.caption(f"폴백 사유: {_fallback}")
                        for _c in _new_cands:
                            with st.expander(
                                f"[{_c.temp_child_id}]  {_c.time_start:.1f}s – {_c.time_end:.1f}s"
                                f"  | 신뢰도 {_c.confidence:.2f}",
                                expanded=True,
                            ):
                                _show_candidate_with_mappings(_c, repo.list_mappings(_c.id))
                        st.markdown("**감사 로그**: 분析(analyze) 기록이 저장되었습니다. ✅")
                    else:
                        _diag_scenes = repo.list_scenes(_ai_vid)
                        _diag_frames = []
                        for _sc in _diag_scenes:
                            _diag_frames.extend(repo.list_frames(_sc.id))
                        _diag_kept = [_f for _f in _diag_frames if _f.kept]
                        with st.container(border=True):
                            st.markdown("**후보 생성 진단 정보**")
                            _dc1, _dc2, _dc3 = st.columns(3)
                            _dc1.metric("scene 수", len(_diag_scenes))
                            _dc2.metric("frame 수", len(_diag_frames))
                            _dc3.metric("kept frame 수", len(_diag_kept))
                        if not _diag_scenes:
                            st.warning("전처리된 장면이 없습니다. 2단계를 먼저 실행해주세요.")
                        elif not _diag_kept:
                            st.error("kept=True 프레임이 없습니다. 2단계에서 재전처리를 실행해주세요.")
                        else:
                            st.error("후보 생성 로직을 점검해주세요.")
                except Exception as _e:
                    st.error(f"분析 실패: {_e}")

st.divider()
st.caption(
    "📌 원칙: AI 후보는 교사가 검토·확정합니다. 관찰수준 점수는 산출하지 않습니다. "
    "원본 영상은 외부로 전송되지 않습니다."
)
