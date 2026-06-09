"""척도 매핑 어댑터 Protocol — 척도 플러그 인터페이스.

1차 = 누리(nuri) / KICCE(kicce). 향후 친사회성·놀이몰입·자기조절 등으로 확장.
새 척도 추가 = ScaleMapper 구현 + resources/ 데이터 파일 추가.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from core.schemas import ObservationCandidate, ScaleMappingCandidate


@runtime_checkable
class ScaleMapper(Protocol):
    """척도 매핑 인터페이스. 관찰 후보 → 척도 후보 목록.

    결과는 후보이며 확정·점수화하지 않는다.
    """

    scale_name: str

    def map(self, candidate: ObservationCandidate) -> list[ScaleMappingCandidate]:
        """관찰 후보를 척도 후보 목록으로 변환한다. 매칭 없으면 빈 리스트."""
        ...
