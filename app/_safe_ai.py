"""Safe AI 패널 + AI비서(제한형) 사이드바 컴포넌트 (V2-7).

모든 페이지의 사이드바에 공통 렌더링된다(_auth.render_user_sidebar 에서 호출).
"""

from __future__ import annotations

import streamlit as st


def render_safe_ai_panel() -> None:
    """Safe AI 안내 패널 — 상시 노출."""
    with st.sidebar.expander("🛡️ Safe AI 안내", expanded=False):
        st.markdown(
            "- AI는 **후보·초안만** 제시합니다. **확정은 교사**가 합니다.\n"
            "- **점수·발달·평정을 산출하지 않습니다.**\n"
            "- 유아 **신원을 자동 확정하지 않습니다**(얼굴 매칭은 후보·동의 기반).\n"
            "- 원본 영상·얼굴 참조사진·임베딩은 **외부로 전송되지 않습니다.**\n"
            "- 영상·얼굴 데이터 접근·삭제는 **감사 로그에 기록**됩니다."
        )


def render_ai_assistant() -> None:
    """AI비서(제한형) — 검색·초안 수정 보조·보완 촬영 제안만."""
    actor = st.session_state.get("user", "")
    role = st.session_state.get("role", "teacher")
    if not actor:
        return

    with st.sidebar.expander("🤖 AI비서 (검색·초안 수정·촬영 제안)", expanded=False):
        st.caption(
            "기록 검색 / 초안 문구 다듬기 / 보완 촬영 제안만 합니다. "
            "기록 생성·확정·점수·신원은 하지 않습니다."
        )
        query = st.text_input("무엇을 도와드릴까요?", key="_ai_assistant_query",
                              placeholder="예: p_07 자연탐구 기록 찾아줘")
        if st.button("질문하기", key="_ai_assistant_send", use_container_width=True):
            if query.strip():
                try:
                    from core.config import DB_PATH
                    from services.assistant_service import handle_query
                    from storage.sqlite_repository import SqliteRepository
                    repo = SqliteRepository(DB_PATH)
                    repo.init_schema()
                    owner = actor if role == "teacher" else None
                    result = handle_query(repo, actor=actor, query=query, owner=owner)
                    st.session_state["_ai_assistant_last"] = result
                except Exception as e:
                    st.session_state["_ai_assistant_last"] = {
                        "response": f"처리 중 오류: {e}", "refused": True, "intent": "search",
                    }

        last = st.session_state.get("_ai_assistant_last")
        if last:
            if last.get("refused"):
                st.warning(last["response"])
            else:
                st.info(f"[{last.get('intent','')}] 응답")
                st.text(last["response"])
