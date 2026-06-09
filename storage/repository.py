"""
저장소 추상 인터페이스 (Repository Protocol).

향후 PostgresRepository 등으로 교체 시 이 인터페이스를 유지한다.
"""

from __future__ import annotations

from typing import Optional, Protocol, runtime_checkable

from core.schemas import (
    AuditLog,
    ChildMatch,
    FinalRecord,
    Frame,
    ObservationCandidate,
    Scene,
    ScaleMappingCandidate,
    Video,
)


@runtime_checkable
class Repository(Protocol):
    """저장소 계약. 구현체(SQLite, PostgreSQL 등)는 이 인터페이스를 충족해야 한다."""

    def init_schema(self) -> None:
        """테이블·인덱스 등 DB 스키마를 초기화한다. 이미 존재하면 무시(idempotent)."""
        ...

    # ------------------------------------------------------------------
    # Video
    # ------------------------------------------------------------------

    def save_video(self, video: Video) -> None:
        """영상 메타데이터를 저장한다. 동일 id 가 있으면 덮어쓴다."""
        ...

    def get_video(self, video_id: str) -> Optional[Video]:
        """video_id 로 조회한다. 없으면 None."""
        ...

    def list_videos(self) -> list[Video]:
        """저장된 모든 영상을 반환한다."""
        ...

    # ------------------------------------------------------------------
    # Scene
    # ------------------------------------------------------------------

    def add_scenes(self, scenes: list[Scene]) -> None:
        """장면 분할 결과를 일괄 저장한다. 동일 id 는 무시(INSERT OR IGNORE)."""
        ...

    def list_scenes(self, video_id: str) -> list[Scene]:
        """video_id 에 속한 장면을 time_start 순으로 반환한다."""
        ...

    # ------------------------------------------------------------------
    # Frame
    # ------------------------------------------------------------------

    def add_frames(self, frames: list[Frame]) -> None:
        """추출 프레임을 일괄 저장한다. 동일 id 는 무시."""
        ...

    def list_frames(self, scene_id: str) -> list[Frame]:
        """scene_id 에 속한 프레임을 t(시각) 순으로 반환한다."""
        ...

    # ------------------------------------------------------------------
    # ObservationCandidate
    # ------------------------------------------------------------------

    def add_candidates(self, candidates: list[ObservationCandidate]) -> None:
        """AI 관찰 후보를 일괄 저장한다. 동일 id 는 무시."""
        ...

    def list_candidates(self, video_id: str) -> list[ObservationCandidate]:
        """video_id 에 속한 관찰 후보를 time_start 순으로 반환한다."""
        ...

    def delete_candidates_for_video(self, video_id: str) -> None:
        """video_id 에 속한 관찰 후보를 모두 삭제한다(후보 재생성 시 중복 방지).

        연결된 scale_mapping 도 함께 삭제해 고아 매핑이 남지 않게 한다.
        """
        ...

    # ------------------------------------------------------------------
    # ScaleMappingCandidate
    # ------------------------------------------------------------------

    def add_mappings(self, mappings: list[ScaleMappingCandidate]) -> None:
        """누리/KICCE 등 척도 매핑 후보를 일괄 저장한다. 동일 id 는 무시."""
        ...

    def list_mappings(self, candidate_id: str) -> list[ScaleMappingCandidate]:
        """candidate_id 에 속한 매핑 후보를 반환한다."""
        ...

    def delete_mappings_for_candidate(self, candidate_id: str) -> None:
        """candidate_id 에 속한 매핑 후보를 모두 삭제한다(매핑 재실행 시 중복 방지)."""
        ...

    # ------------------------------------------------------------------
    # ChildMatch
    # ------------------------------------------------------------------

    def set_child_match(self, match: ChildMatch) -> None:
        """임시 ID ↔ 가명 ID 매칭을 저장한다. 동일 (video_id, temp_child_id) 는 덮어쓴다."""
        ...

    def list_child_matches(self, video_id: str) -> list[ChildMatch]:
        """video_id 에 속한 임시 ID ↔ 가명 ID 매칭 목록을 반환한다."""
        ...

    # ------------------------------------------------------------------
    # FinalRecord
    # ------------------------------------------------------------------

    def save_final_record(self, record: FinalRecord) -> None:
        """교사 확정 관찰기록을 저장한다. 동일 id 는 덮어쓴다."""
        ...

    def list_final_records(self, video_id: Optional[str] = None) -> list[FinalRecord]:
        """확정 기록을 반환한다. video_id 지정 시 해당 영상만 필터링."""
        ...

    # ------------------------------------------------------------------
    # AuditLog
    # ------------------------------------------------------------------

    def write_audit(self, entry: AuditLog) -> None:
        """감사 로그를 기록한다."""
        ...

    def list_audit_logs(self, video_id: Optional[str] = None) -> list[AuditLog]:
        """감사 로그를 반환한다. video_id 지정 시 해당 영상만 필터링."""
        ...
