"""세션 기반 인증 헬퍼.

역할: "teacher" | "researcher" | None(비로그인)
session_state 키: "user", "role", "consent_given"

비밀번호는 .env의 TEACHER_USERS / RESEARCHER_USERS 환경변수로 관리한다.
형식: "user1:pass1,user2:pass2"
"""

from __future__ import annotations

import streamlit as st
from core.config import DEFAULT_ACTOR, RESEARCHER_USERS, TEACHER_USERS


def _parse_user_db() -> dict[str, str]:
    """환경변수에서 {username: role} 딕셔너리를 파싱한다."""
    db: dict[str, str] = {}
    for entry in TEACHER_USERS.split(","):
        entry = entry.strip()
        if ":" in entry:
            u, _ = entry.split(":", 1)
            db[u.strip()] = "teacher"
    for entry in RESEARCHER_USERS.split(","):
        entry = entry.strip()
        if ":" in entry:
            u, _ = entry.split(":", 1)
            db[u.strip()] = "researcher"
    return db


def _parse_password_db() -> dict[str, str]:
    """환경변수에서 {username: password} 딕셔너리를 파싱한다."""
    db: dict[str, str] = {}
    for entry in (TEACHER_USERS + "," + RESEARCHER_USERS).split(","):
        entry = entry.strip()
        if ":" in entry:
            u, p = entry.split(":", 1)
            db[u.strip()] = p.strip()
    return db


def check_credentials(username: str, password: str) -> str | None:
    """인증 성공 시 role("teacher"|"researcher") 반환, 실패 시 None."""
    pw_db = _parse_password_db()
    role_db = _parse_user_db()
    if pw_db.get(username) == password:
        return role_db.get(username)
    return None


def render_login_form() -> None:
    """로그인 폼을 렌더링한다. 성공 시 session_state["user"], ["role"] 세팅."""
    st.markdown("## 로그인")
    st.caption("사용자 이름과 비밀번호를 입력하세요.")
    with st.form("login_form", clear_on_submit=False):
        username = st.text_input("사용자 이름", key="login_username")
        password = st.text_input("비밀번호", type="password", key="login_password")
        submitted = st.form_submit_button("로그인", use_container_width=True)
    if submitted:
        role = check_credentials(username, password)
        if role:
            st.session_state["user"] = username
            st.session_state["role"] = role
            st.rerun()
        else:
            st.error("사용자 이름 또는 비밀번호가 올바르지 않습니다.")


def require_login(allowed_roles: list[str] | None = None) -> None:
    """로그인·역할 체크. 미충족 시 안내 후 st.stop().

    allowed_roles=None 이면 모든 로그인 사용자 허용.
    """
    if "user" not in st.session_state:
        render_login_form()
        st.stop()

    role = st.session_state.get("role", "")
    if allowed_roles and role not in allowed_roles:
        st.warning("이 페이지는 연구자 전용입니다. 교사 계정으로는 접근할 수 없습니다.")
        st.stop()


def render_consent_form() -> None:
    """서비스 이용 동의 폼.

    외부 비전 API 비용 발생 경고와 데이터 무보존 조건을 1회 확인한다.
    동의 완료 시 session_state["consent_given"] = True 세팅.
    이미 동의한 경우 아무것도 표시하지 않는다.
    """
    if st.session_state.get("consent_given"):
        return

    st.warning(
        "**외부 AI API 이용 고지**  \n"
        "아래 사항에 동의해야 AI 비전 분석을 실행할 수 있습니다."
    )
    c1 = st.checkbox(
        "실제 외부 API 호출이 발생하며 비용이 청구될 수 있음을 이해합니다.",
        key="_consent_cost",
    )
    c2 = st.checkbox(
        "외부 제공자의 데이터 무보존·학습 미사용 조건을 확인했습니다.",
        key="_consent_noret",
    )
    if st.button("동의하고 계속", disabled=not (c1 and c2), use_container_width=True):
        st.session_state["consent_given"] = True
        st.rerun()

    st.stop()


def get_current_actor() -> str:
    """현재 로그인 사용자명 반환. 미로그인이면 DEFAULT_ACTOR 폴백."""
    return st.session_state.get("user", DEFAULT_ACTOR)


def render_user_sidebar() -> None:
    """사이드바에 사용자 정보·로그아웃 버튼·QR코드를 렌더링한다."""
    from core.config import PUBLIC_URL

    user = st.session_state.get("user", "")
    role = st.session_state.get("role", "")
    role_label = "교사" if role == "teacher" else "연구자" if role == "researcher" else role

    st.sidebar.markdown(f"**{user}** ({role_label})")
    if st.sidebar.button("로그아웃", key="_logout_btn", use_container_width=True):
        for k in ["user", "role", "consent_given"]:
            st.session_state.pop(k, None)
        st.rerun()

    if PUBLIC_URL and PUBLIC_URL != "http://localhost:8501":
        try:
            import io
            import qrcode
            qr = qrcode.make(PUBLIC_URL)
            buf = io.BytesIO()
            qr.save(buf, format="PNG")
            buf.seek(0)
            with st.sidebar.expander("모바일 접속 QR"):
                st.image(buf, caption="스마트폰으로 스캔", width=160)
        except ImportError:
            pass
