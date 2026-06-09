"""누리·KICCE 매핑 오케스트레이션 서비스.

흐름:
1. repository.list_candidates(video_id) 로 관찰 후보 조회
2. 각 candidate 에 NuriClassifier → 누리 영역 후보
3. 각 candidate 에 KicceMapper(누리 영역 필터) → KICCE 문항 후보
4. ScaleMappingCandidate 로 변환
5. (중복 방지) candidate 별 기존 매핑 삭제 후 add_mappings 저장
6. audit_log 에 action="analyze", detail="nuri_kicce_mapping" 기록
7. 저장된 매핑 후보 반환

원칙: 결과는 후보다. 확정·점수화하지 않는다.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from core.config import DEFAULT_ACTOR
from core.schemas import AuditLog, ObservationCandidate, ScaleMappingCandidate
from services.mapping.kicce_mapper import KicceMapper
from services.mapping.nuri_classifier import NuriClassifier
from storage.sqlite_repository import SqliteRepository


def map_single_candidate(
    candidate: ObservationCandidate,
    nuri_classifier: NuriClassifier,
    kicce_mapper: KicceMapper,
) -> list[ScaleMappingCandidate]:
    """관찰 후보 1건 → 누리 영역 후보 + KICCE 문항 후보 목록.

    누리 분류 결과의 영역을 KICCE 매퍼의 1차 필터로 전달한다.
    """
    nuri_mappings = nuri_classifier.map(candidate)
    nuri_areas = [m.area for m in nuri_mappings if m.area]

    kicce_mappings = kicce_mapper.map(candidate, nuri_areas=nuri_areas)

    return nuri_mappings + kicce_mappings


def map_candidates_for_video(
    video_id: str,
    repo: SqliteRepository,
    nuri_classifier: Optional[NuriClassifier] = None,
    kicce_mapper: Optional[KicceMapper] = None,
    actor: str = DEFAULT_ACTOR,
) -> list[ScaleMappingCandidate]:
    """영상의 모든 관찰 후보에 누리·KICCE 매핑을 수행해 저장한다.

    - 같은 candidate 에 대해 기존 매핑을 먼저 삭제하고 재삽입한다(중복 방지).
    - 후보가 없으면 빈 리스트 반환(오류 없음).
    - 없는 video_id 는 ValueError.
    """
    if repo.get_video(video_id) is None:
        raise ValueError(f"video_id={video_id} 를 찾을 수 없습니다.")

    if nuri_classifier is None:
        nuri_classifier = NuriClassifier()
    if kicce_mapper is None:
        kicce_mapper = KicceMapper()

    candidates = repo.list_candidates(video_id)
    all_mappings: list[ScaleMappingCandidate] = []

    for candidate in candidates:
        mappings = map_single_candidate(candidate, nuri_classifier, kicce_mapper)
        # 중복 방지: 이 candidate 의 기존 매핑을 삭제한 뒤 재삽입
        repo.delete_mappings_for_candidate(candidate.id)
        if mappings:
            repo.add_mappings(mappings)
        all_mappings.extend(mappings)

    nuri_count = sum(1 for m in all_mappings if m.scale == "nuri")
    kicce_count = sum(1 for m in all_mappings if m.scale == "kicce")

    repo.write_audit(AuditLog(
        id=f"audit_{video_id}_mapping_{uuid.uuid4().hex[:6]}",
        video_id=video_id,
        actor=actor,
        action="analyze",
        detail=(
            f"nuri_kicce_mapping "
            f"candidates={len(candidates)} "
            f"nuri={nuri_count} kicce={kicce_count}"
        ),
        created_at=datetime.now(),
    ))

    return all_mappings
