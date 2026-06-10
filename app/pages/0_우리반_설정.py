import _bootstrap  # noqa: F401  — 프로젝트 루트를 sys.path에 추가

from pathlib import Path

import streamlit as st

from core.config import FACES_DIR
from services.class_service import (
    register_class, register_child, set_child_face_consent, delete_child,
)
from storage.sqlite_repository import SqliteRepository

st.set_page_config(
    page_title="우리반 설정",
    page_icon="🏫",
    layout="wide",
)

from _auth import require_login, render_user_sidebar, get_current_actor
from _responsive import inject_responsive_css

require_login()
inject_responsive_css()

st.title("🏫 우리반 설정")
st.caption("클래스와 유아(가명)를 등록합니다. 얼굴 매칭은 **기본 OFF**이며 연구 동의 시에만 사용합니다.")

with st.expander("📌 개인정보 보호 원칙 (클릭하여 펼치기)", expanded=False):
    st.markdown(
        "- **실명을 입력하지 마세요.** 유아는 **가명 ID**와 표시 라벨로만 등록합니다.\n"
        "- 얼굴 참조사진은 **동의한 경우에만** 로컬(`data/faces/`, 제한 접근)에 저장됩니다.\n"
        "- 동의를 철회하면 참조사진·임베딩이 **즉시 삭제**됩니다.\n"
        "- 얼굴 참조사진·임베딩·원본 영상은 **외부로 전송되지 않습니다.**\n"
        "- 모든 등록·동의 변경·삭제는 감사 로그에 기록됩니다."
    )

st.divider()


@st.cache_resource
def get_repo() -> SqliteRepository:
    from core.config import DB_PATH
    repo = SqliteRepository(DB_PATH)
    repo.init_schema()
    return repo


repo = get_repo()
render_user_sidebar()
_actor = get_current_actor()


# ===========================================================================
# 섹션 1: 클래스 생성
# ===========================================================================
st.subheader("1. 클래스(우리반) 생성")

with st.form("create_class_form"):
    _class_name = st.text_input("클래스 이름", placeholder="예: 햇빛반")
    _submit_class = st.form_submit_button("클래스 생성", use_container_width=True)

if _submit_class:
    if not _class_name.strip():
        st.error("클래스 이름을 입력해주세요.")
    else:
        try:
            g = register_class(repo, name=_class_name, teacher_owner=_actor, actor=_actor)
            st.success(f"클래스 생성됨: {g.name} [{g.id}]")
        except Exception as e:
            st.error(f"클래스 생성 실패: {e}")

_classes = repo.list_classes(teacher_owner=_actor)
if not _classes:
    st.info("아직 생성된 클래스가 없습니다. 먼저 클래스를 생성해주세요.")
    st.stop()

st.divider()


# ===========================================================================
# 섹션 2: 클래스 선택 + 유아 등록
# ===========================================================================
st.subheader("2. 유아 등록")

_opts = {f"{c.name} [{c.id}]": c.id for c in _classes}
_sel_label = st.selectbox("클래스 선택", list(_opts.keys()), key="cls_select")
_sel_class_id = _opts[_sel_label]

st.markdown("##### 새 유아 추가")
st.caption("⚠️ 실명을 입력하지 마세요. 가명 ID와 표시 라벨만 사용합니다.")

with st.form("add_child_form"):
    col1, col2 = st.columns(2)
    _pseudonym = col1.text_input("가명 ID (필수)", placeholder="예: p_07")
    _label = col2.text_input("표시 라벨 (선택)", placeholder="예: 유아7")

    _consent = st.checkbox(
        "이 유아에 대해 **얼굴 매칭 사용에 동의**합니다 (연구 동의 범위 내). "
        "체크해야 얼굴 참조사진을 저장할 수 있습니다.",
        key="add_child_consent",
    )
    _photo = st.file_uploader(
        "얼굴 참조사진 (동의 시에만 저장됨)",
        type=["jpg", "jpeg", "png", "webp"],
        key="add_child_photo",
        help="동의에 체크하지 않으면 사진은 저장되지 않습니다.",
    )
    _submit_child = st.form_submit_button("유아 등록", use_container_width=True)

if _submit_child:
    if not _pseudonym.strip():
        st.error("가명 ID는 필수입니다.")
    elif _photo is not None and not _consent:
        st.error("동의에 체크하지 않으면 사진을 저장할 수 없습니다. 동의하거나 사진을 제거해주세요.")
    else:
        try:
            photo_bytes = _photo.read() if (_photo is not None and _consent) else None
            photo_ext = (_photo.name.rsplit(".", 1)[-1] if _photo is not None else "jpg")
            child = register_child(
                repo, class_id=_sel_class_id,
                pseudonym_id=_pseudonym, display_label=_label,
                reference_photo=photo_bytes, photo_ext=photo_ext,
                consent=_consent, consent_by=_actor,
                faces_dir=FACES_DIR, actor=_actor,
            )
            _msg = "사진 저장됨" if child.reference_photo_path else "사진 없음"
            st.success(f"유아 등록됨: {child.display_label} ({child.pseudonym_id}) · {_msg}")
            st.rerun()
        except Exception as e:
            st.error(f"유아 등록 실패: {e}")

st.divider()


# ===========================================================================
# 섹션 3: 등록 유아 목록 + 동의 토글 + 삭제
# ===========================================================================
st.subheader("3. 등록 유아 목록")

_children = repo.list_children(_sel_class_id)
if not _children:
    st.info("이 클래스에 등록된 유아가 없습니다.")
else:
    for ch in _children:
        c1, c2, c3, c4, c5 = st.columns([2, 2, 2, 2, 2])
        c1.markdown(f"**{ch.display_label}**  \n`{ch.pseudonym_id}`")
        c2.markdown("얼굴 동의\n\n" + ("✅ 동의" if ch.face_match_consent else "⛔ 미동의"))
        c3.markdown("참조사진\n\n" + ("🖼️ 있음" if ch.reference_photo_path else "없음"))

        # 동의 토글/철회
        if ch.face_match_consent:
            if c4.button("동의 철회", key=f"revoke_{ch.id}"):
                set_child_face_consent(repo, ch.id, consent=False, by=_actor,
                                       faces_dir=FACES_DIR, actor=_actor)
                st.warning(f"{ch.display_label}: 동의 철회 — 참조사진·임베딩 삭제됨")
                st.rerun()
        else:
            if c4.button("동의 설정", key=f"grant_{ch.id}"):
                set_child_face_consent(repo, ch.id, consent=True, by=_actor,
                                       faces_dir=FACES_DIR, actor=_actor)
                st.info(f"{ch.display_label}: 동의 설정됨 (사진은 위에서 별도 등록)")
                st.rerun()

        if c5.button("🗑️ 삭제", key=f"del_{ch.id}"):
            delete_child(repo, ch.id, faces_dir=FACES_DIR, actor=_actor)
            st.warning(f"{ch.display_label} 삭제됨 (참조사진·매칭 후보 포함)")
            st.rerun()
        st.divider()

st.caption(
    "📌 얼굴 매칭은 동의한 유아에 대해서만, 다음 단계(얼굴 매칭 후보)에서 후보로만 제시됩니다. "
    "AI는 신원을 자동 확정하지 않으며 교사가 확정합니다."
)
