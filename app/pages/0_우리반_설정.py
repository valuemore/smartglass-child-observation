import _bootstrap  # noqa: F401  — 프로젝트 루트를 sys.path에 추가

from collections import Counter
from datetime import datetime
from pathlib import Path

import streamlit as st

from core.config import DB_PATH, FACES_DIR
from services.class_service import (
    delete_child,
    register_child,
    register_class,
    set_child_face_consent,
    update_child_meta,
    update_child_photos,
)
from storage.sqlite_repository import SqliteRepository

st.set_page_config(
    page_title="우리반 설정",
    page_icon="🏫",
    layout="wide",
)

from _auth import get_current_actor, render_user_sidebar, require_login
from _responsive import inject_responsive_css

require_login()
inject_responsive_css()


# ---------------------------------------------------------------------------
# 저장소 초기화
# ---------------------------------------------------------------------------

@st.cache_resource
def get_repo() -> SqliteRepository:
    repo = SqliteRepository(DB_PATH)
    repo.init_schema()
    return repo


repo = get_repo()
render_user_sidebar()
_actor = get_current_actor()


# ---------------------------------------------------------------------------
# session_state 초기화
# ---------------------------------------------------------------------------

if "우리반_view" not in st.session_state:
    st.session_state["우리반_view"] = "list"
if "우리반_class" not in st.session_state:
    st.session_state["우리반_class"] = None
if "우리반_child" not in st.session_state:
    st.session_state["우리반_child"] = None


# ---------------------------------------------------------------------------
# 헬퍼 함수
# ---------------------------------------------------------------------------

def _calc_age(birth_date_str: str | None) -> str:
    if not birth_date_str:
        return ""
    try:
        birth = datetime.strptime(birth_date_str, "%Y-%m-%d")
        today = datetime.now()
        age = today.year - birth.year - (
            (today.month, today.day) < (birth.month, birth.day)
        )
        return f"만 {age}세"
    except ValueError:
        return ""


def _gender_label(gender: str | None) -> str:
    if gender == "male":
        return "남아"
    if gender == "female":
        return "여아"
    return "미입력"


def _gender_to_code(label: str) -> str | None:
    if label == "남아":
        return "male"
    if label == "여아":
        return "female"
    return None


def _go(view: str) -> None:
    st.session_state["우리반_view"] = view
    st.rerun()


NURI_AREAS = ["신체운동·건강", "의사소통", "사회관계", "예술경험", "자연탐구"]


# ===========================================================================
# 뷰 A: "list" — 우리반 목록 화면
# ===========================================================================

def render_list() -> None:
    st.title("우리반 설정")

    # 클래스 목록 로드
    classes = repo.list_classes(teacher_owner=_actor)

    if not classes:
        st.info("아직 생성된 클래스가 없습니다. 아래에서 먼저 클래스를 만들어주세요.")
        _render_class_create_form()
        return

    # 클래스 selectbox
    class_opts = {c.name: c.id for c in classes}
    # session_state에 저장된 class_id가 유효하면 그것을 기본값으로
    saved_class_id = st.session_state.get("우리반_class")
    default_idx = 0
    if saved_class_id:
        ids = list(class_opts.values())
        if saved_class_id in ids:
            default_idx = ids.index(saved_class_id)

    selected_name = st.selectbox(
        "클래스 선택",
        list(class_opts.keys()),
        index=default_idx,
        key="list_class_select",
    )
    selected_class_id = class_opts[selected_name]
    st.session_state["우리반_class"] = selected_class_id

    # 선택 클래스 객체
    sel_class = next((c for c in classes if c.id == selected_class_id), None)
    if sel_class is None:
        st.error("클래스 정보를 불러올 수 없습니다.")
        return

    # 학급 헤더
    col_left, col_right = st.columns([3, 1])
    col_left.markdown(f"## {sel_class.name}")
    col_right.caption("기본반" if not sel_class.face_match_enabled else "얼굴매칭 ON")

    # 유아 목록
    children = repo.list_children(selected_class_id)

    # 통계 집계
    child_pseudonym_set = {ch.pseudonym_id for ch in children}
    all_records = repo.list_final_records(video_id=None)
    record_count_by_pid = Counter(
        r.pseudonym_id for r in all_records if r.pseudonym_id in child_pseudonym_set
    )
    total_records = sum(record_count_by_pid.values())

    now_month = datetime.now().month
    birthday_this_month = sum(
        1 for ch in children
        if ch.birth_date and len(ch.birth_date) >= 7
        and int(ch.birth_date[5:7]) == now_month
    )
    has_notes_count = sum(1 for ch in children if ch.notes and ch.notes.strip())

    # 통계 바
    stat1, stat2, stat3, stat4 = st.columns(4)
    stat1.metric("출석부", f"{len(children)}명")
    stat2.metric("이달 생일", f"{birthday_this_month}명")
    stat3.metric("특이사항", f"{has_notes_count}명")
    stat4.metric("총 기록", f"{total_records}건")

    st.divider()

    # 버튼 행
    btn_col_a, btn_col_b = st.columns(2)
    with btn_col_a:
        if st.button("우리반 앨범", use_container_width=True, key="btn_album"):
            st.info("준비 중인 기능입니다.")
    with btn_col_b:
        if st.button("아이 추가", use_container_width=True, key="btn_add_child", type="primary"):
            _go("add_child")

    # 카드 그리드
    if not children:
        st.info("이 클래스에 등록된 유아가 없습니다. '아이 추가' 버튼으로 등록하세요.")
    else:
        st.markdown("")
        card_cols = st.columns(3)
        for idx, ch in enumerate(children):
            rec_count = record_count_by_pid.get(ch.pseudonym_id, 0)
            with card_cols[idx % 3]:
                with st.container(border=True):
                    # 정면 사진
                    if ch.reference_photo_path and Path(ch.reference_photo_path).exists():
                        st.image(ch.reference_photo_path, width=100)
                    else:
                        st.markdown("👤")
                    st.markdown(f"**{ch.display_label}**")
                    st.caption(ch.pseudonym_id)
                    st.caption(f"활동기록 {rec_count}건")
                    if st.button(
                        "상세 보기",
                        key=f"view_child_{ch.id}",
                        use_container_width=True,
                    ):
                        st.session_state["우리반_child"] = ch.id
                        _go("profile")

    st.divider()

    # 클래스 관리 expander
    with st.expander("클래스 관리", expanded=False):
        _render_class_create_form()


def _render_class_create_form() -> None:
    """클래스 생성 폼 (list 뷰 내 재사용)."""
    with st.form("create_class_form"):
        _class_name = st.text_input("클래스 이름", placeholder="예: 햇빛반")
        _submit_class = st.form_submit_button("클래스 생성", use_container_width=True)
    if _submit_class:
        if not _class_name.strip():
            st.error("클래스 이름을 입력해주세요.")
        else:
            try:
                g = register_class(
                    repo,
                    name=_class_name,
                    teacher_owner=_actor,
                    actor=_actor,
                )
                st.success(f"클래스 '{g.name}'이(가) 생성되었습니다.")
                st.rerun()
            except Exception as e:
                st.error(f"클래스 생성 실패: {e}")


# ===========================================================================
# 뷰 B: "add_child" — 아이 추가하기 폼
# ===========================================================================

def render_add_child() -> None:
    if st.button("← 우리반", key="back_from_add"):
        _go("list")

    st.title("아이 추가하기")
    st.caption("실명을 입력하지 마세요. 표시 라벨(별명·이니셜 등)만 사용합니다.")

    # 선택된 클래스 가져오기
    class_id = st.session_state.get("우리반_class")
    sel_class = repo.get_class(class_id) if class_id else None
    if sel_class is None:
        st.warning("클래스를 먼저 선택해주세요.")
        if st.button("← 우리반으로", key="back_no_class"):
            _go("list")
        return

    with st.form("add_child_form_v2"):
        row1_col1, row1_col2 = st.columns(2)
        display_label = row1_col1.text_input(
            "표시 라벨 *",
            placeholder="예: 유아7 (실명 금지)",
        )
        birth_date_val = row1_col2.date_input(
            "생년월일",
            value=None,
            min_value=datetime(2015, 1, 1).date(),
            max_value=datetime.now().date(),
            format="YYYY-MM-DD",
        )

        gender_label = st.radio(
            "성별",
            ["남아", "여아", "미입력"],
            horizontal=True,
            index=2,
        )

        st.text_input(
            "반 이름",
            value=sel_class.name,
            disabled=True,
        )

        st.divider()
        st.markdown("##### 얼굴 사진 등록")
        st.info(
            "3각도 사진을 등록하면 AI가 영상에서 이 아이를 더 정확하게 인식합니다. "
            "사진 등록은 아래 동의 후 활성화됩니다."
        )

        col_f, col_r, col_l = st.columns(3)
        with col_f:
            st.markdown("**정면**")
            photo_front = st.file_uploader(
                "정면",
                type=["jpg", "jpeg", "png", "webp"],
                label_visibility="collapsed",
                key="add_photo_front",
            )
        with col_r:
            st.markdown("**우측면**")
            photo_right = st.file_uploader(
                "우측면",
                type=["jpg", "jpeg", "png", "webp"],
                label_visibility="collapsed",
                key="add_photo_right",
            )
        with col_l:
            st.markdown("**좌측면**")
            photo_left = st.file_uploader(
                "좌측면",
                type=["jpg", "jpeg", "png", "webp"],
                label_visibility="collapsed",
                key="add_photo_left",
            )

        st.divider()
        consent = st.checkbox(
            "AI 사진 자동 분류 사용에 동의합니다 (학부모 동의 필수)",
        )
        st.caption(
            "[안심] 사진은 AI 학습에 사용되지 않고, 오직 '우리 반 아이' 분류만을 위해 활용됩니다. "
            "민감정보(선택) 미동의 시 기능을 꺼주세요."
        )

        st.divider()
        st.markdown("##### 특이사항 메모")
        notes = st.text_area(
            "특이사항 메모",
            placeholder="아이의 특이사항 등을 메모하세요.",
            label_visibility="collapsed",
        )

        submit = st.form_submit_button(
            "등록하기",
            use_container_width=True,
            type="primary",
        )

    if submit:
        if not display_label.strip():
            st.error("표시 라벨은 필수입니다.")
            return

        has_photo = photo_front is not None or photo_right is not None or photo_left is not None
        if has_photo and not consent:
            st.error("사진을 등록하려면 AI 분류 동의가 필요합니다. 동의하거나 사진을 제거해주세요.")
            return

        # birth_date 변환
        birth_date_str: str | None = None
        if birth_date_val is not None:
            birth_date_str = str(birth_date_val)

        # gender 변환
        gender_code = _gender_to_code(gender_label)

        # 사진 바이트 읽기
        front_bytes: bytes | None = None
        right_bytes: bytes | None = None
        left_bytes: bytes | None = None
        photo_ext = "jpg"

        if photo_front is not None and consent:
            front_bytes = photo_front.read()
            photo_ext = photo_front.name.rsplit(".", 1)[-1] if "." in photo_front.name else "jpg"
        if photo_right is not None and consent:
            right_bytes = photo_right.read()
            if not photo_ext or photo_ext == "jpg":
                photo_ext = photo_right.name.rsplit(".", 1)[-1] if "." in photo_right.name else "jpg"
        if photo_left is not None and consent:
            left_bytes = photo_left.read()

        try:
            import uuid as _uuid
            # pseudonym_id 는 시스템이 자동 생성
            auto_pseudonym = f"p_{_uuid.uuid4().hex[:6]}"
            child = register_child(
                repo,
                class_id=sel_class.id,
                pseudonym_id=auto_pseudonym,
                display_label=display_label,
                reference_photo=front_bytes,
                photo_right=right_bytes,
                photo_left=left_bytes,
                photo_ext=photo_ext,
                consent=consent,
                consent_by=_actor,
                faces_dir=FACES_DIR,
                actor=_actor,
                birth_date=birth_date_str,
                gender=gender_code,
                notes=notes,
            )
            photo_msg = "사진 저장됨" if child.reference_photo_path else "사진 없음"
            st.success(f"'{child.display_label}' 등록 완료 ({child.pseudonym_id}) · {photo_msg}")
            _go("list")
        except Exception as e:
            st.error(f"유아 등록 실패: {e}")


# ===========================================================================
# 뷰 C: "profile" — 아이 정보 + 상세 프로필
# ===========================================================================

def render_profile() -> None:
    child_id = st.session_state.get("우리반_child")
    if not child_id:
        st.warning("선택된 유아가 없습니다.")
        if st.button("← 우리반", key="profile_no_child_back"):
            _go("list")
        return

    ch = repo.get_child(child_id)
    if ch is None:
        st.warning("유아 정보를 찾을 수 없습니다.")
        if st.button("← 우리반", key="profile_not_found_back"):
            _go("list")
        return

    sel_class = repo.get_class(ch.class_id)
    class_name = sel_class.name if sel_class else ch.class_id

    # 상단 버튼 행
    col_back, col_spacer, col_edit = st.columns([3, 4, 1])
    with col_back:
        if st.button("← 우리반", key="profile_back"):
            _go("list")
    with col_edit:
        if st.button("수정", key="profile_edit"):
            _go("edit_child")

    st.markdown("---")

    # ── 프로필 카드: 정면(크게) + 측면 2장(작게) + 기본 정보 ──────────────
    col_front, col_sides, col_info = st.columns([3, 2, 5])

    with col_front:
        st.caption("정면")
        if ch.reference_photo_path and Path(ch.reference_photo_path).exists():
            st.image(ch.reference_photo_path, use_container_width=True)
        else:
            st.markdown(
                "<div style='height:180px;background:#f0f2f6;border-radius:8px;"
                "display:flex;align-items:center;justify-content:center;"
                "color:#aaa;font-size:13px'>사진 없음</div>",
                unsafe_allow_html=True,
            )

    with col_sides:
        st.caption("우측면")
        if ch.photo_right_path and Path(ch.photo_right_path).exists():
            st.image(ch.photo_right_path, use_container_width=True)
        else:
            st.markdown(
                "<div style='height:84px;background:#f0f2f6;border-radius:6px;"
                "display:flex;align-items:center;justify-content:center;"
                "color:#aaa;font-size:12px'>없음</div>",
                unsafe_allow_html=True,
            )
        st.caption("좌측면")
        if ch.photo_left_path and Path(ch.photo_left_path).exists():
            st.image(ch.photo_left_path, use_container_width=True)
        else:
            st.markdown(
                "<div style='height:84px;background:#f0f2f6;border-radius:6px;"
                "display:flex;align-items:center;justify-content:center;"
                "color:#aaa;font-size:12px'>없음</div>",
                unsafe_allow_html=True,
            )

    with col_info:
        st.markdown(f"### {ch.display_label}")
        st.markdown(f"**{class_name}**")
        birth_str = ch.birth_date or "미등록"
        age_str = _calc_age(ch.birth_date)
        age_part = f" ({age_str})" if age_str else ""
        st.markdown(f"📅 {birth_str}{age_part}")
        st.markdown(f"성별: {_gender_label(ch.gender)}")
        st.caption(f"가명 ID: {ch.pseudonym_id}")
        if ch.notes and ch.notes.strip():
            st.info(ch.notes, icon="📋")

    # 특이사항 (사진이 없을 때만 별도 표시)
    if not (ch.notes and ch.notes.strip()):
        st.caption("등록된 특이사항이 없습니다.")

    # 얼굴 매칭 동의
    st.divider()
    st.markdown("#### 얼굴 매칭 동의")
    if ch.face_match_consent:
        st.success("동의 상태: 동의함")
        if ch.consent_at:
            st.caption(f"동의일: {ch.consent_at.strftime('%Y.%m.%d')}")
        if st.button("동의 철회", key="profile_revoke_consent"):
            try:
                set_child_face_consent(
                    repo, ch.id, consent=False, by=_actor,
                    faces_dir=FACES_DIR, actor=_actor,
                )
                st.warning("동의 철회 — 참조사진·임베딩이 삭제되었습니다.")
                st.rerun()
            except Exception as e:
                st.error(f"동의 철회 실패: {e}")
    else:
        st.info("동의 상태: 미동의 (얼굴 매칭 OFF)")
        if st.button("동의 설정", key="profile_grant_consent"):
            try:
                set_child_face_consent(
                    repo, ch.id, consent=True, by=_actor,
                    faces_dir=FACES_DIR, actor=_actor,
                )
                st.success("동의 설정 완료 (사진은 '수정' 화면에서 등록하세요)")
                st.rerun()
            except Exception as e:
                st.error(f"동의 설정 실패: {e}")

    # 활동 기록
    st.divider()
    st.markdown("#### 활동 기록")

    all_records = repo.list_final_records(video_id=None)
    child_records = [r for r in all_records if r.pseudonym_id == ch.pseudonym_id]

    # 기간 필터 탭
    period_tab_labels = ["전체", "이번 달", "지난 달", "기간 선택"]
    period_tabs = st.tabs(period_tab_labels)

    now = datetime.now()
    this_month_start = datetime(now.year, now.month, 1)
    if now.month == 1:
        last_month_start = datetime(now.year - 1, 12, 1)
        last_month_end = datetime(now.year, 1, 1)
    else:
        last_month_start = datetime(now.year, now.month - 1, 1)
        last_month_end = this_month_start

    # 기간 선택 입력값 (탭 외부에서 사전 선언)
    custom_start = None
    custom_end = None

    with period_tabs[3]:
        c_start_col, c_end_col = st.columns(2)
        custom_start = c_start_col.date_input(
            "시작일",
            value=None,
            key="profile_period_start",
        )
        custom_end = c_end_col.date_input(
            "종료일",
            value=None,
            key="profile_period_end",
        )

    def _filter_period(records, period_idx: int):
        if period_idx == 0:
            return records
        elif period_idx == 1:
            return [r for r in records if r.confirmed_at >= this_month_start]
        elif period_idx == 2:
            return [r for r in records if last_month_start <= r.confirmed_at < last_month_end]
        else:
            # 기간 선택
            filtered = records
            if custom_start:
                start_dt = datetime(custom_start.year, custom_start.month, custom_start.day)
                filtered = [r for r in filtered if r.confirmed_at >= start_dt]
            if custom_end:
                end_dt = datetime(custom_end.year, custom_end.month, custom_end.day, 23, 59, 59)
                filtered = [r for r in filtered if r.confirmed_at <= end_dt]
            return filtered

    # 누리영역 필터 탭
    area_tab_labels = ["전체"] + NURI_AREAS
    area_tabs = st.tabs(area_tab_labels)

    def _filter_area(records, area_idx: int):
        if area_idx == 0:
            return records
        target = NURI_AREAS[area_idx - 1]
        return [r for r in records if target in (r.confirmed_areas or [])]

    # 현재 활성 탭 인덱스를 session_state로 관리
    if "profile_period_idx" not in st.session_state:
        st.session_state["profile_period_idx"] = 0
    if "profile_area_idx" not in st.session_state:
        st.session_state["profile_area_idx"] = 0

    for pidx, ptab in enumerate(period_tabs):
        with ptab:
            period_filtered = _filter_period(child_records, pidx)
            for aidx, atab in enumerate(area_tabs):
                with atab:
                    filtered_records = _filter_area(period_filtered, aidx)

                    # 최근 활동 사진 그리드
                    st.markdown("##### 최근 활동 사진 (후보 / 교사 검토 전)")

                    @st.cache_data(ttl=60)
                    def get_activity_frames(candidate_ids_tuple: tuple) -> list[str]:
                        frames: list[str] = []
                        for cid in candidate_ids_tuple:
                            cand = repo.get_candidate(cid)
                            if cand is None:
                                continue
                            scene_frames = repo.list_frames(cand.scene_id)
                            kept = [f.image_path for f in scene_frames if f.kept and f.image_path]
                            frames.extend(kept)
                            if len(frames) >= 12:
                                break
                        return frames[:12]

                    candidate_ids = tuple(r.candidate_id for r in filtered_records[:30])
                    frame_paths = get_activity_frames(candidate_ids) if candidate_ids else []

                    if frame_paths:
                        frame_cols = st.columns(4)
                        for fi, fp in enumerate(frame_paths):
                            if Path(fp).exists():
                                with frame_cols[fi % 4]:
                                    st.image(fp, use_container_width=True)
                    else:
                        st.caption("활동 사진이 없습니다.")

                    # 최근 활동 기록 리스트
                    st.markdown("##### 최근 활동 기록 (후보 / 교사 검토 전)")
                    if not filtered_records:
                        st.caption("해당 기간·영역의 기록이 없습니다.")
                    else:
                        for rec in reversed(filtered_records[-20:]):
                            with st.container(border=True):
                                col_text, col_area = st.columns([4, 1])
                                col_text.markdown(rec.final_behavior)
                                col_text.caption(
                                    rec.confirmed_at.strftime("%Y.%m.%d %H:%M")
                                )
                                for area in (rec.confirmed_areas or []):
                                    col_area.markdown(f"`{area}`")

    # 삭제 확인 expander
    st.divider()
    with st.expander("이 아이 삭제", expanded=False):
        st.warning(
            "삭제하면 참조사진·매칭 후보가 모두 삭제됩니다. 확정 기록은 유지됩니다."
        )
        if st.button("삭제 확인", type="primary", key="confirm_delete"):
            try:
                delete_child(repo, ch.id, faces_dir=FACES_DIR, actor=_actor)
                st.session_state["우리반_child"] = None
                st.session_state["우리반_view"] = "list"
                st.rerun()
            except Exception as e:
                st.error(f"삭제 실패: {e}")


# ===========================================================================
# 뷰 D: "edit_child" — 프로필 편집
# ===========================================================================

def render_edit_child() -> None:
    child_id = st.session_state.get("우리반_child")
    if not child_id:
        st.warning("선택된 유아가 없습니다.")
        if st.button("← 우리반", key="edit_no_child_back"):
            _go("list")
        return

    ch = repo.get_child(child_id)
    if ch is None:
        st.warning("유아 정보를 찾을 수 없습니다.")
        if st.button("← 우리반", key="edit_not_found_back"):
            _go("list")
        return

    sel_class = repo.get_class(ch.class_id)
    class_name = sel_class.name if sel_class else ch.class_id

    if st.button("← 프로필", key="edit_back"):
        _go("profile")

    st.title(f"'{ch.display_label}' 정보 수정")
    st.caption("실명을 입력하지 마세요. 표시 라벨(별명·이니셜 등)만 사용합니다.")

    # 기존 값 파싱
    existing_birth = None
    if ch.birth_date:
        try:
            existing_birth = datetime.strptime(ch.birth_date, "%Y-%m-%d").date()
        except ValueError:
            pass

    existing_gender_idx = 2
    if ch.gender == "male":
        existing_gender_idx = 0
    elif ch.gender == "female":
        existing_gender_idx = 1

    with st.form("edit_child_form"):
        row1_col1, row1_col2 = st.columns(2)
        display_label = row1_col1.text_input(
            "표시 라벨 *",
            value=ch.display_label,
        )
        birth_date_val = row1_col2.date_input(
            "생년월일",
            value=existing_birth,
            min_value=datetime(2015, 1, 1).date(),
            max_value=datetime.now().date(),
            format="YYYY-MM-DD",
        )

        gender_label = st.radio(
            "성별",
            ["남아", "여아", "미입력"],
            horizontal=True,
            index=existing_gender_idx,
        )

        st.text_input("반 이름", value=class_name, disabled=True)

        # 사진 수정 (동의 시에만)
        if ch.face_match_consent:
            st.divider()
            st.markdown("##### 얼굴 사진 수정")
            st.info(
                "새 사진을 올리면 기존 사진을 교체합니다. "
                "변경하지 않을 각도는 비워두세요."
            )

            # 현재 사진 미리보기: 정면(크게) + 측면 2장(작게 세로)
            pv_front, pv_sides, pv_pad = st.columns([3, 2, 3])
            with pv_front:
                st.caption("현재 정면")
                if ch.reference_photo_path and Path(ch.reference_photo_path).exists():
                    st.image(ch.reference_photo_path, use_container_width=True)
                else:
                    st.caption("없음")
            with pv_sides:
                st.caption("우측면")
                if ch.photo_right_path and Path(ch.photo_right_path).exists():
                    st.image(ch.photo_right_path, use_container_width=True)
                else:
                    st.caption("없음")
                st.caption("좌측면")
                if ch.photo_left_path and Path(ch.photo_left_path).exists():
                    st.image(ch.photo_left_path, use_container_width=True)
                else:
                    st.caption("없음")

            st.markdown("")
            col_f, col_r, col_l = st.columns(3)
            with col_f:
                st.markdown("**정면 교체**")
                new_front = st.file_uploader(
                    "정면 교체",
                    type=["jpg", "jpeg", "png", "webp"],
                    label_visibility="collapsed",
                    key="edit_photo_front",
                )
            with col_r:
                st.markdown("**우측면 교체**")
                new_right = st.file_uploader(
                    "우측면 교체",
                    type=["jpg", "jpeg", "png", "webp"],
                    label_visibility="collapsed",
                    key="edit_photo_right",
                )
            with col_l:
                st.markdown("**좌측면 교체**")
                new_left = st.file_uploader(
                    "좌측면 교체",
                    type=["jpg", "jpeg", "png", "webp"],
                    label_visibility="collapsed",
                    key="edit_photo_left",
                )
        else:
            new_front = None
            new_right = None
            new_left = None
            st.info("얼굴 매칭 동의가 없어 사진을 수정할 수 없습니다. 프로필 화면에서 동의 설정 후 이용하세요.")

        st.divider()
        st.markdown("##### 특이사항 메모")
        notes = st.text_area(
            "특이사항 메모",
            value=ch.notes or "",
            placeholder="아이의 특이사항 등을 메모하세요.",
            label_visibility="collapsed",
        )

        submit = st.form_submit_button(
            "저장하기",
            use_container_width=True,
            type="primary",
        )

    if submit:
        if not display_label.strip():
            st.error("표시 라벨은 필수입니다.")
            return

        birth_date_str: str | None = None
        if birth_date_val is not None:
            birth_date_str = str(birth_date_val)

        gender_code = _gender_to_code(gender_label)

        try:
            update_child_meta(
                repo,
                child_id=ch.id,
                display_label=display_label,
                birth_date=birth_date_str,
                gender=gender_code,
                notes=notes,
                actor=_actor,
            )
        except Exception as e:
            st.error(f"정보 수정 실패: {e}")
            return

        # 사진 변경 처리 (동의 시에만)
        if ch.face_match_consent and (new_front or new_right or new_left):
            # 정면사진은 register_child 방식 대신 update_child_photos 활용
            # 정면(reference_photo) 교체는 update_child_photos에 포함되지 않으므로
            # front는 별도 파일 교체 로직으로 처리
            try:
                photo_ext = "jpg"
                right_bytes: bytes | None = None
                left_bytes: bytes | None = None
                front_bytes: bytes | None = None

                if new_front is not None:
                    front_bytes = new_front.read()
                    photo_ext = new_front.name.rsplit(".", 1)[-1] if "." in new_front.name else "jpg"
                if new_right is not None:
                    right_bytes = new_right.read()
                    photo_ext = new_right.name.rsplit(".", 1)[-1] if "." in new_right.name else photo_ext
                if new_left is not None:
                    left_bytes = new_left.read()

                # 정면 사진 교체 (참조사진 경로 직접 갱신)
                if front_bytes is not None:
                    ext = photo_ext.lower().lstrip(".")
                    dest_dir = Path(FACES_DIR) / ch.class_id
                    dest_dir.mkdir(parents=True, exist_ok=True)
                    new_front_path = str(dest_dir / f"{ch.id}.{ext}")
                    # 기존 정면 사진 삭제
                    if ch.reference_photo_path:
                        old_p = Path(ch.reference_photo_path)
                        if old_p.exists() and "faces" in old_p.parts:
                            old_p.unlink(missing_ok=True)
                    Path(new_front_path).write_bytes(front_bytes)
                    # DB 갱신 (set_face_consent는 경로를 비우므로 직접 update_child_meta 우회)
                    # repo 직접 접근으로 reference_photo_path만 갱신
                    with repo._connect() as conn:
                        conn.execute(
                            "UPDATE child SET reference_photo_path = ?, face_embedding = NULL WHERE id = ?",
                            (new_front_path, ch.id),
                        )

                # 우측면·좌측면 교체
                if right_bytes is not None or left_bytes is not None:
                    update_child_photos(
                        repo,
                        child_id=ch.id,
                        photo_right=right_bytes,
                        photo_left=left_bytes,
                        photo_ext=photo_ext,
                        faces_dir=FACES_DIR,
                        actor=_actor,
                    )

                st.success("사진이 업데이트되었습니다. (임베딩 재계산 예약됨)")
            except Exception as e:
                st.error(f"사진 수정 실패: {e}")
                return

        st.success("수정 완료!")
        _go("profile")


# ===========================================================================
# 라우터
# ===========================================================================

_view = st.session_state.get("우리반_view", "list")

if _view == "list":
    render_list()
elif _view == "add_child":
    render_add_child()
elif _view == "profile":
    render_profile()
elif _view == "edit_child":
    render_edit_child()
else:
    st.session_state["우리반_view"] = "list"
    st.rerun()
