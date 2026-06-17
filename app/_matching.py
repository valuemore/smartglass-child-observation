"""유아 매칭 확정 공유 UI 컴포넌트.

수집균형·주간초안 페이지가 동일하게 사용한다.
- 얼굴 매칭 후보(proposed)를 교사가 확정/기각한다.
- 수동 매핑(temp_child_id → 등록 유아)을 지원한다.
- 확정 시 ChildMatch를 생성한다(자동 확정 금지 — 항상 교사 행동).
- 교사 판단을 돕기 위해 **등록 정면 사진 + 영상에서 추출한 얼굴 스냅샷**을 함께 보여준다.

도메인 로직은 repo(저장소)만 호출하며, 점수·실명을 다루지 않는다.
얼굴 스냅샷·참조사진은 로컬 전용이며 외부로 전송하지 않는다.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from pathlib import Path

import streamlit as st

from core.schemas import ChildMatch

_SNAP_PER_SCENE = 6   # 장면당 표시할 최대 얼굴 스냅샷 수
_SNAP_WIDTH = 90      # 스냅샷·참조사진 썸네일 폭(px)


@st.cache_data(show_spinner=False)
def _scene_face_snapshots(scene_id: str, frame_paths: tuple[str, ...]) -> list[bytes]:
    """장면의 kept 프레임에서 검출된 얼굴 크롭(JPEG bytes)을 반환한다.

    FACE_EMBED_PROVIDER=opencv 이고 모델이 가용할 때만 동작한다.
    실패(모델 미가용·검출 실패) 시 빈 리스트 → UI는 프레임 썸네일로 폴백한다.
    scene_id+frame_paths를 캐시 키로 사용해 매 렌더 재검출을 막는다.
    """
    try:
        from core.config import FACE_EMBED_PROVIDER
        if (FACE_EMBED_PROVIDER or "mock").lower() not in ("opencv", "sface"):
            return []
        from services.face.opencv_embedder import get_opencv_embedder
        emb = get_opencv_embedder()
    except Exception:
        return []

    crops: list[bytes] = []
    for p in frame_paths:
        try:
            if Path(p).exists():
                crops += emb.detect_face_crops(Path(p).read_bytes())
        except Exception:
            continue
        if len(crops) >= _SNAP_PER_SCENE:
            break
    return crops[:_SNAP_PER_SCENE]


def _scene_ids_for_temp(repo, video_id: str, temp_child_id: str) -> list[str]:
    """해당 temp_child_id가 등장한 장면 id 목록."""
    seen: list[str] = []
    for c in repo.list_candidates(video_id):
        if c.temp_child_id == temp_child_id and c.scene_id not in seen:
            seen.append(c.scene_id)
    return seen


def _temp_evidence(repo, video_id: str, temp_child_id: str) -> tuple[list[bytes], list[str], str]:
    """temp_child_id의 (얼굴 스냅샷, 프레임 썸네일 경로, 행동서술) 근거를 모은다."""
    snaps: list[bytes] = []
    frame_paths: list[str] = []
    behavior = ""
    for c in repo.list_candidates(video_id):
        if c.temp_child_id != temp_child_id:
            continue
        if not behavior and c.observed_behavior:
            behavior = c.observed_behavior.strip()[:80]
        kept = [f.image_path for f in repo.list_frames(c.scene_id) if f.kept]
        for fp in kept:
            if fp not in frame_paths and Path(fp).exists():
                frame_paths.append(fp)
        if len(snaps) < _SNAP_PER_SCENE:
            snaps += _scene_face_snapshots(c.scene_id, tuple(kept))
    return snaps[:_SNAP_PER_SCENE], frame_paths[:3], behavior


def _render_video_evidence(repo, video_id: str, temp_child_id: str) -> None:
    """영상 근거(얼굴 스냅샷 → 폴백: 프레임 썸네일) + 행동서술을 표시한다."""
    snaps, frame_paths, behavior = _temp_evidence(repo, video_id, temp_child_id)
    if behavior:
        st.caption(f"🧒 AI 관찰: {behavior}…")
    if snaps:
        st.caption("영상 속 검출된 얼굴(누가 누구인지는 교사가 판단)")
        cols = st.columns(min(len(snaps), _SNAP_PER_SCENE))
        for col, b in zip(cols, snaps):
            col.image(b, width=_SNAP_WIDTH)
    elif frame_paths:
        st.caption("영상 프레임(얼굴 자동 검출 실패 — 맥락 참고용)")
        cols = st.columns(len(frame_paths))
        for col, fp in zip(cols, frame_paths):
            col.image(fp, use_container_width=True)
    else:
        st.caption("표시할 영상 근거가 없습니다.")


def _render_reference_gallery(children) -> None:
    """등록 유아 정면 사진 갤러리(매칭 비교 기준)."""
    if not children:
        return
    st.caption("우리반 등록 유아(정면 사진) — 비교 기준")
    cols = st.columns(len(children))
    for col, ch in zip(cols, children):
        if ch.reference_photo_path and Path(ch.reference_photo_path).exists():
            col.image(ch.reference_photo_path, caption=ch.display_label, width=_SNAP_WIDTH)
        else:
            col.caption(f"{ch.display_label}\n(사진 없음)")


def render_matching_section(repo, videos, children, actor: str, key_prefix: str = "match") -> bool:
    """주어진 영상들의 미매칭 temp_child_id를 유아(가명)에 연결하는 UI를 그린다.

    등록 정면 사진 + 영상 얼굴 스냅샷을 함께 보여줘 교사가 비교·판단하게 한다.
    반환: 아직 매칭 대기 중인 항목이 하나라도 있으면 True.
    """
    if not videos:
        st.info("이 클래스에 업로드된 영상이 없습니다.")
        return False

    any_pending = False
    for v in videos:
        matched = {m.temp_child_id for m in repo.list_child_matches(v.id)}
        fmcs = [f for f in repo.list_face_match_candidates(v.id) if f.status == "proposed"]
        temp_ids = sorted({c.temp_child_id for c in repo.list_candidates(v.id)})
        unmatched_temps = [t for t in temp_ids if t not in matched]
        if not unmatched_temps:
            continue
        any_pending = True
        with st.expander(
            f"🎬 {v.filename}  ({v.captured_date or '-'}) — 매칭 필요 {len(unmatched_temps)}명",
            expanded=True,
        ):
            _render_reference_gallery(children)
            st.divider()

            fmc_by_temp = {f.temp_child_id: f for f in fmcs if f.temp_child_id not in matched}

            for t in unmatched_temps:
                st.markdown(f"#### {t}")
                _render_video_evidence(repo, v.id, t)

                # 1) 자동 얼굴 매칭 후보 (제안 유아 정면 사진 + 확정/기각)
                f = fmc_by_temp.get(t)
                if f is not None:
                    ch = repo.get_child(f.child_id)
                    label = ch.display_label if ch else f.child_id
                    pc1, pc2, pc3 = st.columns([2, 1, 1])
                    with pc1:
                        st.markdown(f"AI 제안: **{label}** (유사도 {f.confidence:.2f})")
                        if ch and ch.reference_photo_path and Path(ch.reference_photo_path).exists():
                            st.image(ch.reference_photo_path, caption=f"{label} 정면", width=_SNAP_WIDTH)
                    if pc2.button("✅ 확정", key=f"{key_prefix}_fmc_ok_{f.id}"):
                        repo.decide_face_match(f.id, status="confirmed", decided_by=actor)
                        if ch:
                            repo.set_child_match(ChildMatch(
                                id=f"cm_{v.id}_{f.temp_child_id}_{uuid.uuid4().hex[:4]}",
                                video_id=v.id, temp_child_id=f.temp_child_id,
                                pseudonym_id=ch.pseudonym_id, source="face_candidate_confirmed",
                                matched_by=actor, matched_at=datetime.now(),
                            ))
                        st.rerun()
                    if pc3.button("✕ 기각", key=f"{key_prefix}_fmc_no_{f.id}"):
                        repo.decide_face_match(f.id, status="rejected", decided_by=actor)
                        st.rerun()

                # 2) 수동 매핑 (직접 선택)
                if children:
                    mc1, mc2 = st.columns([3, 1])
                    pick = mc1.selectbox(
                        f"{t} → 유아 직접 선택",
                        options=["(선택 안 함)"] + [f"{c.display_label} ({c.pseudonym_id})" for c in children],
                        key=f"{key_prefix}_manual_{v.id}_{t}",
                    )
                    if mc2.button("매핑", key=f"{key_prefix}_manual_ok_{v.id}_{t}") and pick != "(선택 안 함)":
                        pseudo = pick.rsplit("(", 1)[-1].rstrip(")")
                        repo.set_child_match(ChildMatch(
                            id=f"cm_{v.id}_{t}_{uuid.uuid4().hex[:4]}",
                            video_id=v.id, temp_child_id=t, pseudonym_id=pseudo,
                            source="teacher", matched_by=actor, matched_at=datetime.now(),
                        ))
                        st.rerun()
                else:
                    st.caption("등록 유아가 없어 매핑할 수 없습니다. ‘우리반 설정’에서 유아를 등록하세요.")
                st.divider()

    return any_pending
