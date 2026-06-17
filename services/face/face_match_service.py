"""얼굴 참조 매칭 후보 서비스 (V2-4).

원칙(보안·개인정보):
- **동의 기반**: 클래스 face_match_enabled 가 아니거나 동의(consent)·참조사진이 없으면 매칭하지 않는다.
- **로컬 전용**: 임베딩 비교는 로컬에서만 수행한다. 참조사진·임베딩을 외부로 전송하지 않는다.
- **후보만**: 산출물은 status="proposed" 후보다. 확정/기각은 교사가 한다(자동 확정 금지).
- confidence 는 얼굴 유사도이며 관찰수준/발달 점수가 아니다.
- 임베딩은 어댑터(FaceEmbedder) 뒤에서 계산한다. 기본은 MockFaceEmbedder(결정적 대체물).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from core.config import (
    DEFAULT_ACTOR, FACE_EMBED_PROVIDER, FACE_MATCH_FRAMES_PER_SCENE,
    FACE_MATCH_MIN_CONFIDENCE, FACE_MATCH_TOP_K,
)
from core.schemas import AuditLog, Child, FaceMatchCandidate
from services.face.base import (
    FaceEmbedder, cosine_similarity, decode_embedding, encode_embedding,
)
from storage.sqlite_repository import SqliteRepository


def _default_embedder() -> FaceEmbedder:
    """FACE_EMBED_PROVIDER 설정에 따라 임베더를 선택한다.

    - "opencv": 실제 로컬 얼굴 인식(YuNet+SFace). 로드 실패 시 mock으로 폴백.
    - 그 외("mock"): 결정적 Mock(테스트·시연용).
    """
    provider = (FACE_EMBED_PROVIDER or "mock").lower()
    if provider in ("opencv", "sface"):
        try:
            from services.face.opencv_embedder import get_opencv_embedder
            return get_opencv_embedder()
        except Exception:  # noqa: BLE001 — 모델 로드/다운로드 실패 시 mock 폴백
            import logging
            logging.getLogger(__name__).exception(
                "OpenCV 얼굴 임베더 초기화 실패 — mock으로 폴백합니다."
            )
    from services.face.mock_embedder import MockFaceEmbedder
    return MockFaceEmbedder()


def ensure_child_embedding(
    repo: SqliteRepository, child: Child, embedder: FaceEmbedder,
) -> Optional[list[float]]:
    """동의·정면사진이 있는 유아의 임베딩을 보장(없으면 계산·저장)하고 벡터를 반환한다.

    우측면·좌측면 사진이 있으면 각도별 임베딩의 **평균 벡터**를 저장한다.
    다각도일수록 측면 장면에서도 강건하게 인식된다.
    동의가 없거나 정면사진이 없으면 None.
    """
    if not child.face_match_consent or not child.reference_photo_path:
        return None
    existing = decode_embedding(child.face_embedding)
    if existing is not None:
        return existing

    # 다각도: 사용 가능한 각도별 임베딩 수집 (얼굴 미검출 → embed()==[]는 건너뜀)
    vecs: list[list[float]] = []
    for path_str in [
        child.reference_photo_path,   # 정면 (필수)
        child.photo_right_path,       # 우측면 (있으면 추가)
        child.photo_left_path,        # 좌측면 (있으면 추가)
    ]:
        if not path_str:
            continue
        p = Path(path_str)
        if not p.exists():
            continue
        emb = embedder.embed(p.read_bytes())
        if emb:  # 얼굴 검출 성공한 사진만 사용
            vecs.append(emb)

    if not vecs:
        return None

    # 평균 임베딩 — 다각도일수록 더 강건한 대표 벡터
    dim = len(vecs[0])
    averaged = [sum(v[j] for v in vecs) / len(vecs) for j in range(dim)]
    repo.set_child_embedding(child.id, encode_embedding(averaged))
    return averaged


def propose_matches(
    repo: SqliteRepository,
    video_id: str,
    embedder: Optional[FaceEmbedder] = None,
    actor: str = DEFAULT_ACTOR,
    min_confidence: float = FACE_MATCH_MIN_CONFIDENCE,
    top_k: int = FACE_MATCH_TOP_K,
) -> list[FaceMatchCandidate]:
    """영상의 검출 얼굴(대표 프레임)과 동의 유아 참조사진을 로컬 비교해 매칭 후보를 만든다.

    매칭을 수행하지 않는 경우(동의 OFF·등록 유아 없음·클래스 미연결)에는 빈 목록을 반환한다.
    재분석 멱등성: 기존 proposed 후보는 삭제하고 다시 만든다(confirmed/rejected 보존).
    """
    video = repo.get_video(video_id)
    if video is None:
        raise ValueError(f"video_id={video_id} 를 찾을 수 없습니다.")
    if embedder is None:
        embedder = _default_embedder()

    # 1) 클래스·동의 게이트
    if not video.class_id:
        return []
    group = repo.get_class(video.class_id)
    if group is None or not group.face_match_enabled:
        return []

    consented = [
        c for c in repo.list_children(video.class_id)
        if c.face_match_consent and c.reference_photo_path
    ]
    if not consented:
        return []

    # 2) 동의 유아 임베딩 보장
    child_vecs: dict[str, list[float]] = {}
    for ch in consented:
        vec = ensure_child_embedding(repo, ch, embedder)
        if vec is not None:
            child_vecs[ch.id] = vec
    if not child_vecs:
        return []

    # 3) temp_child_id별 표본 프레임 임베딩 → 유아별 최고 유사도 집계
    #    (한 temp_child_id 가 여러 장면에 등장하고, 장면당 상위 N장을 스캔해 검출 실패를 완화)
    best: dict[str, dict[str, float]] = {}  # temp_child_id -> {child_id: best_sim}
    for cand in repo.list_candidates(video_id):
        temp = cand.temp_child_id
        kept = [f for f in repo.list_frames(cand.scene_id) if f.kept][:FACE_MATCH_FRAMES_PER_SCENE]
        for frm in kept:
            fpath = Path(frm.image_path)
            if not fpath.exists():
                continue
            emb_v = embedder.embed(fpath.read_bytes())
            if not emb_v:  # 얼굴 미검출 프레임은 건너뜀
                continue
            slot = best.setdefault(temp, {})
            for child_id, cvec in child_vecs.items():
                sim = cosine_similarity(emb_v, cvec)
                if sim > slot.get(child_id, 0.0):
                    slot[child_id] = sim

    # 4) 후보 생성 — 영상 단위 1:1 그리디 배정.
    #    프레임은 장면 단위라 한 장면의 모든 temp_child_id가 같은 얼굴을 공유한다.
    #    독립적으로 best 유아를 뽑으면 모든 temp_id가 동일 유아로 쏠리므로,
    #    유사도 내림차순으로 (각 유아·각 temp_id를 최대 1회) 그리디 배정해 서로 다른 후보를 만든다.
    #    이는 AI '후보'이며 확정은 교사가 한다(임시 ID↔얼굴 위치 연결 정보가 없어 배정은 추정).
    now = datetime.now()
    pairs: list[tuple[float, str, str]] = []
    for temp, sims in best.items():
        for child_id, sim in sims.items():
            if sim >= min_confidence:
                pairs.append((sim, temp, child_id))
    pairs.sort(key=lambda x: x[0], reverse=True)

    used_temp: set[str] = set()
    used_child: set[str] = set()
    results: list[FaceMatchCandidate] = []
    for sim, temp, child_id in pairs:
        if temp in used_temp or child_id in used_child:
            continue
        used_temp.add(temp)
        used_child.add(child_id)
        results.append(FaceMatchCandidate(
            id=f"fmc_{video_id}_{temp}_{child_id}_{uuid.uuid4().hex[:6]}",
            video_id=video_id,
            temp_child_id=temp,
            child_id=child_id,
            confidence=round(sim, 4),
            status="proposed",
            created_at=now,
        ))

    # 5) 멱등 저장: 기존 proposed 삭제 후 재삽입
    repo.delete_face_match_candidates_for_video(video_id, only_proposed=True)
    if results:
        repo.add_face_match_candidates(results)

    # 6) 감사: 참조사진 접근 + 매칭 수행
    repo.write_audit(AuditLog(
        id=f"audit_{video_id}_facematch_{uuid.uuid4().hex[:6]}",
        video_id=video_id, actor=actor, action="face_match",
        detail=(
            f"face_match_proposed class={video.class_id} "
            f"consented_children={len(child_vecs)} proposed={len(results)} "
            f"min_conf={min_confidence} (local_only, not_transmitted)"
        ),
        created_at=now,
    ))
    repo.write_audit(AuditLog(
        id=f"audit_{video_id}_refaccess_{uuid.uuid4().hex[:6]}",
        video_id=video_id, actor=actor, action="reference_photo_access",
        detail=f"read_reference_photos count={len(child_vecs)} for_face_match",
        created_at=now,
    ))
    return results
